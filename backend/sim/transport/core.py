# -*- coding: utf-8 -*-
"""
传输层主体: TransportLayer (连接接纳 + 查询接口 + 逐 tick 编排)
====================================================================
职责: 报文发送入口 (rscspa 连接接纳)、节点缓冲/链路计账查询、
半双工推进编排与超时检查; 逐跳搬运细节在 relay.RelayMixin。
分层: 业务逻辑层 —— 只经引擎公开属性读拓扑, 不做 I/O。
"""
import logging   # 标准库: 模块日志 (发送受理/拒绝)
import random  # 标准库: 自动遥测随机化 (默认关闭) 与 sensor 抽样
import time    # 标准库: monotonic 时钟 (前端飞行插值用)

from collections import deque   # 标准库: 节点发送缓冲 (node_queues)

from ..config import ROBOT_ID, TICK_PHYS_S   # 协议标识: 机器人会移动, 不承载数据报文; 物理拍节拍 (飞行插值分母)
from ..rl import rl_plan                     # B 组实验: Q-learning 信道决策器 (A/B 开关见 _plan)
from ..routing import rscspa    # 路由算法: 资源约束最短路径 (连接接纳选路)
from .model import (AUTO_TELEMETRY, DEFAULT_TIMEOUT, MAX_CONCURRENT,   # 节拍上限
                    QUEUE_LIMIT_BYTES,                                 # 缓冲上限
                    Message, Segment)                                  # 报文实体
from .relay import RelayMixin   # 逐跳推进/绕行/超时 (混入)

log = logging.getLogger(__name__)   # 本模块日志器


class TransportLayer(RelayMixin):
    """职责: 端到端报文传输服务 (挂在 SimulationEngine 上)。

    核心属性:
    - eng: 仿真引擎引用 (读 links/nodes/tick, 写事件);
    - messages: 报文表 {id: Message}; node_queues: 节点发送缓冲;
    - link_stats: 链路计账 {边: tx/rx/pkts/retries/drops};
    - results: 已完结报文结果信号 (deque maxlen=50)。

    调用链: engine.run_forever -> step (自动遥测+半双工推进+超时)
    -> _step_segment (RelayMixin) -> engine.snapshot -> active_packets。
    """

    def __init__(self, engine):
        self.eng = engine
        self.messages: dict[int, Message] = {}
        self.node_queues: dict[str, deque] = {}   # nid -> deque[Segment]
        self.link_stats: dict[tuple, dict] = {}   # edge -> 计数器
        self.results = deque(maxlen=50)           # 已完结报文的结果信号
        self._next_id = 1
        self._tick_at = 0.0                       # 最近一次 step 的墙钟(方块插值用)

    # ================= 查询接口 =================
    def node_bytes(self, nid: str) -> int:
        """节点发送缓冲中的在途字节数。

        Args: nid: 节点 id。Returns: int, 字节 (无缓冲返回 0)。
        Globals Used: None。Calls: None。
        """
        q = self.node_queues.get(nid)
        return sum(s.nbytes for s in q) if q else 0

    def queue_pct(self, nid: str, extra_bytes: int = 0) -> float:
        """真实队列积压率: (缓冲在途字节 + 链上待发字节) / 上限。
        Args: nid: 节点 id; extra_bytes: 链上待发字节 (已按配额封顶)。
        Returns: float ∈ [0,100]。Globals Used: QUEUE_LIMIT_BYTES。Calls: node_bytes。
        """
        return min(100.0, (self.node_bytes(nid) + extra_bytes) / QUEUE_LIMIT_BYTES * 100.0)

    def link_summary(self, edge) -> dict:
        """链路传输计账 (无记录时返回零值表)。

        Args: edge: (a, b) 节点对 (内部按排序元组查键)。Returns: dict
        {tx/rx/pkts/retries/drops}。Globals Used: None。Calls: None。
        """
        return self.link_stats.get(
            tuple(sorted(edge)),
            {"tx": 0, "rx": 0, "pkts": 0, "retries": 0, "drops": 0})

    def inflight(self):
        """在途报文列表。

        Args: None。
        Returns: list[Message] (status=INFLIGHT)。Globals Used: None。Calls: None。
        """
        return [m for m in self.messages.values() if m.status == "INFLIGHT"]

    def active_traffic(self):
        """在途报文 -> engine.traffic (源/目的标记环绘制)。

        Args: None。
        Returns: list[{src, path, bytes}]。Globals Used: None。Calls: None。
        """
        return [{"src": m.src, "path": m.path, "bytes": m.total}
                for m in self.messages.values() if m.status == "INFLIGHT"]

    def active_nodes_edges(self):
        """缓冲非空节点集 + 占用边集 (PAMAS 收发判定与信道忙碌表用)。

        Args: None。
        Returns: (set[节点id], set[排序边元组])。Globals Used: None。Calls: None。
        """
        nodes, edges = set(), set()
        for q in self.node_queues.values():
            for s in q:
                nodes.add(s.cur)
                if s.nxt:
                    nodes.add(s.nxt)
                    edges.add(tuple(sorted((s.cur, s.nxt))))
        return nodes, edges

    def active_packets(self):
        """在途 DATA 报文 -> 前端动画数据 (t=本跳进度 0..1; -1=停驻排队)。
        握手控制帧在底层真实运行但不下发 —— 画面只演数据包本体。

        Args: None。
        Returns: list[dict] —— a/b/t/kind/bytes/chan/msg/seg/ph(已飞跳数)/
        path(完整路径, 前端全程折线插值用)。
        Globals Used: None。Calls: None。
        """
        frac = 0.0
        if self._tick_at:
            frac = min(1.0, max(0.0, (self.eng._anim_now() - self._tick_at) / TICK_PHYS_S))
        out = []
        for q in self.node_queues.values():
            for s in q:
                m = self.messages.get(s.mid)
                ch = m.chan.get(tuple(sorted((s.cur, s.nxt)))) if m else None
                # ph + path: 前端按 (已飞跳数 + 本跳进度)/总跳数 得到全程单调进度,
                # 沿路径折线插值 —— 消除 tick(0.25s) 与广播(0.2s) 节拍混叠导致的瞬移
                if s.wire:
                    out.append({"a": s.cur, "b": s.nxt, "t": round(frac, 3),
                                "kind": "DATA", "bytes": s.nbytes, "chan": ch,
                                "msg": s.mid, "seg": s.seq, "ph": s.hops,
                                "path": m.path if m else [s.cur, s.nxt]})
                else:
                    out.append({"a": s.cur, "b": s.cur, "t": -1,
                                "kind": "DATA", "bytes": s.nbytes, "chan": ch,
                                "msg": s.mid, "seg": s.seq, "ph": s.hops,
                                "path": m.path if m else [s.cur]})
        return out

    def summary(self) -> dict:
        """传输层总账 (快照 transport 字段)。

        Args: None。
        Returns: dict {totals: 五项字节/包计数 + delivered/timeout/inflight,
        results: 最近 12 条结果信号}。Globals Used: None。Calls: inflight。
        """
        tot = {"tx": 0, "rx": 0, "pkts": 0, "retries": 0, "drops": 0}
        for st in self.link_stats.values():
            for k in tot:
                tot[k] += st[k]
        return {
            "totals": {**tot,
                       "delivered": sum(1 for r in self.results if r["status"] == "DELIVERED"),
                       "timeout": sum(1 for r in self.results if r["status"] == "TIMEOUT"),
                       "inflight": len(self.inflight())},
            "results": list(self.results)[-12:],
        }

    # ================= 发送 =================
    def _adj(self):
        """路由邻接表: 活跃链路 (机器人边除外 —— 数据走道钉不走移动资产)"""
        adj = {}
        for (a, b), l in self.eng.links.items():
            if not l["up"]:
                continue
            if ROBOT_ID in (a, b):
                continue          # 机器人会移动, 不承载数据报文 —— 数据走道钉
            adj.setdefault(a, []).append((b, l["cost_ab"]))
            adj.setdefault(b, []).append((a, l["cost_ba"]))
        return adj

    def _busy_channels(self):
        """在途报文占用的信道 (供 rscspa 干扰排斥, 新报文自动避开)"""
        busy = {}
        for q in self.node_queues.values():
            for s in q:
                m = self.messages.get(s.mid)
                if not m or not s.nxt:
                    continue
                ch = m.chan.get(tuple(sorted((s.cur, s.nxt))))
                if ch is not None:
                    busy.setdefault(frozenset((s.cur, s.nxt)), set()).add(ch)
        return busy

    def _plan(self, src, dst):
        """连接接纳选路: A/B 开关 —— eng.rl_channels 开启走 Q-learning
        选道 (路径 Dijkstra), 默认走 RCSPA (3 信道, K=3, 避忙碌信道)"""
        if getattr(self.eng, "rl_channels", False):
            return rl_plan(self, src, dst)
        return rscspa(self._adj(), src, dst, n_channels=3, K=3,
                      busy_edge=self._busy_channels())

    def send_message(self, src, dst, payload_bytes,
                     timeout_ticks=DEFAULT_TIMEOUT, kind="telemetry"):
        """发送入口: 连接接纳 (rscspa 选到路=连接建立, 零时间开销) ->
        整包即刻出发; 无路=NO_PATH 拒绝。结果信号走 events+results 双通道。

        Args: src/dst: 源/目的节点 id; payload_bytes: 字节数;
              timeout_ticks: 超时拍数; kind: 业务类别 (telemetry/user)。
        Returns: dict —— 成功 {ok, msg_id, path, channels, segments};
                 拒绝 {ok:False, signal: NO_SUCH_NODE/SRC_DEAD/BUSY/NO_PATH}。
        Globals Used: MAX_CONCURRENT/DEFAULT_TIMEOUT。Calls: inflight/_plan/
        Message+Segment 构造 / engine._emit。
        """
        eng = self.eng
        if src not in eng.nodes or dst not in eng.nodes:
            log.warning("报文拒绝 %s->%s: NO_SUCH_NODE", src, dst)
            return {"ok": False, "signal": "NO_SUCH_NODE"}
        if eng.nodes[src].state == "DEAD":
            log.warning("报文拒绝 %s->%s: SRC_DEAD", src, dst)
            return {"ok": False, "signal": "SRC_DEAD"}
        if len(self.inflight()) >= MAX_CONCURRENT:
            log.warning("报文拒绝 %s->%s: BUSY (在途满)", src, dst)
            return {"ok": False, "signal": "BUSY"}
        res = self._plan(src, dst)
        if res is None:
            self._record(None, src, dst, payload_bytes, "NO_PATH", 0, 0, None, "")
            eng._emit("msg_no_path", "error",
                      f"✗ {src} → {dst}: 全网无可达路径, 连接被拒绝",
                      src=src, dst=dst, node=src)
            return {"ok": False, "signal": "NO_PATH"}
        mid = self._next_id
        self._next_id += 1
        m = Message(mid, src, dst, int(payload_bytes), res["path"],
                    res["channels"], eng.tick, eng.tick + timeout_ticks)
        self.messages[mid] = m
        # 连接接纳通过: 报文整包 (不再分段) 进入源节点发送队列;
        # 队列/逐跳计数仍按完整字节数计, 负载语义与分段时代等价
        self.node_queues.setdefault(src, deque()).append(
            Segment(mid, 0, m.total, src, m.path[1]))
        log.info("报文#%s %s->%s %dB 受理: %d跳 信道%s",
                 mid, src, dst, m.total, len(m.path) - 1, res["channels"])
        eng._emit("msg_sent", "info",
                  f"📤 {src} → {dst}: 连接建立, {m.total}B 整包出发 "
                  f"(路径 {len(m.path) - 1} 跳, 信道 {''.join(map(str, res['channels']))})",
                  src=src, dst=dst, msg_id=mid)
        return {"ok": True, "msg_id": mid, "path": m.path,
                "channels": res["channels"], "segments": m.total_segs}

    # ================= 逐 tick 推进 =================
    def step(self):
        """编排一拍: 自动遥测(默认关) -> 半双工逐节点推进 -> 超时检查。
        Args: None。Returns: None (副作用: 队列/计账/结果信号)。
        Globals Used: AUTO_TELEMETRY。Calls: _step_segment / _timeout。
        """
        eng = self.eng
        self._tick_at = time.monotonic()
        # B 组实验: 结果信号驱动 Q 表结算 (开关关闭时零开销)
        if eng.rl_channels and eng.rl_learner is not None:
            eng.rl_learner.drain(self)
        # 1) 自动遥测 (默认关闭, 见 AUTO_TELEMETRY)
        if AUTO_TELEMETRY and eng.tick % 6 == 0:
            sensors = [nid for nid, n in eng.nodes.items()
                       if n.role == "sensor" and n.state != "DEAD"
                       and eng.routes.get(nid, {}).get("hop_count", -1) > 0]
            random.shuffle(sensors)
            for nid in sensors[:2]:
                self.send_message(nid, eng.sink_id, random.choice([512, 1024, 1536]))
        # 2) 半双工推进: 每 tick 每节点一个发送名额, 排队即真实拥塞
        for nid in sorted(self.node_queues):
            sq = self.node_queues.get(nid)
            if sq:
                self._step_segment(nid, sq)
        # 3) 超时检查: 必然发出 TIMEOUT 信号
        for m in list(self.messages.values()):
            if m.status == "INFLIGHT" and eng.tick > m.deadline:
                self._timeout(m)
