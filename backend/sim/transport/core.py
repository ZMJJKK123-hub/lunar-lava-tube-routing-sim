# -*- coding: utf-8 -*-
"""
传输层主体: TransportLayer (连接接纳 + 查询接口 + 逐 tick 编排)
====================================================================
职责: 报文发送入口 (rscspa 连接接纳)、节点缓冲/链路计账查询、
半双工推进编排与超时检查; 逐跳搬运细节在 relay.RelayMixin。
分层: 业务逻辑层 —— 只经引擎公开属性读拓扑, 不做 I/O。
"""
import random  # 标准库: 自动遥测随机化 (默认关闭) 与 sensor 抽样
import time    # 标准库: monotonic 时钟 (前端飞行插值用)

from collections import deque   # 标准库: 节点发送缓冲 (node_queues)

from ..config import ROBOT_ID   # 协议标识: 机器人会移动, 不承载数据报文
from ..routing import rscspa    # 路由算法: 资源约束最短路径 (连接接纳选路)
from .model import (AUTO_TELEMETRY, DEFAULT_TIMEOUT, MAX_CONCURRENT,   # 节拍上限
                    QUEUE_LIMIT_BYTES,                                 # 缓冲上限
                    Message, Segment)                                  # 报文实体
from .relay import RelayMixin   # 逐跳推进/绕行/超时 (混入)


class TransportLayer(RelayMixin):
    """端到端报文传输服务 (挂在 SimulationEngine 上)。

    核心属性:
    - eng: 仿真引擎引用 (读 links/nodes/tick, 写事件);
    - messages: 报文表 {id: Message}; node_queues: 节点发送缓冲;
    - link_stats: 链路计账 {边: tx/rx/pkts/retries/drops};
    - results: 已完结报文结果信号 (deque maxlen=50)。

    执行链路: engine.run_forever -> step (自动遥测+半双工推进+超时)
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
        """节点发送缓冲中的在途字节数"""
        q = self.node_queues.get(nid)
        return sum(s.nbytes for s in q) if q else 0

    def queue_pct(self, nid: str, extra_bytes: int = 0) -> float:
        """真实队列积压率: (缓冲中在途字节 + 链上待发字节) / 上限"""
        return min(100.0, (self.node_bytes(nid) + extra_bytes) / QUEUE_LIMIT_BYTES * 100.0)

    def link_summary(self, edge) -> dict:
        """链路传输计账 (无记录时返回零值表)"""
        return self.link_stats.get(
            tuple(sorted(edge)),
            {"tx": 0, "rx": 0, "pkts": 0, "retries": 0, "drops": 0})

    def inflight(self):
        """在途报文列表"""
        return [m for m in self.messages.values() if m.status == "INFLIGHT"]

    def active_traffic(self):
        """在途报文 -> engine.traffic (源/目的标记环绘制)"""
        return [{"src": m.src, "path": m.path, "bytes": m.total}
                for m in self.messages.values() if m.status == "INFLIGHT"]

    def active_nodes_edges(self):
        """缓冲非空节点集 + 占用边集 (PAMAS 收发判定与信道忙碌表用)"""
        nodes, edges = set(), set()
        for q in self.node_queues.values():
            for s in q:
                nodes.add(s.cur)
                if s.nxt:
                    nodes.add(s.nxt)
                    edges.add(tuple(sorted((s.cur, s.nxt))))
        return nodes, edges

    def active_packets(self):
        """在途 DATA 报文 -> 前端动画数据。
        t 为本跳进度 0..1 (tick 内墙钟插值); t=-1 表示停驻在节点 a 排队。
        握手控制帧(SYN/SYNACK/ACK)在底层真实运行 (消耗 tick 与字节),
        但不下发 —— 画面只呈现数据包本体, 协议过程交给事件日志解说。"""
        frac = 0.0
        if self._tick_at:
            frac = min(1.0, max(0.0, (time.monotonic() - self._tick_at) / 0.25))
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
        """传输层总账 (快照 transport 字段)"""
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
        """连接接纳选路: RCSPA (3 信道, 复用距离 K=3, 避开忙碌信道)"""
        return rscspa(self._adj(), src, dst, n_channels=3, K=3,
                      busy_edge=self._busy_channels())

    def send_message(self, src, dst, payload_bytes,
                     timeout_ticks=DEFAULT_TIMEOUT, kind="telemetry"):
        """发送入口: 立即返回受理结果。
        连接接纳 (= 握手语义, 零时间开销): rscspa 选到路 = 连接建立,
        报文整包即刻出发; 选不到路 = NO_PATH 拒绝。
        最终 DELIVERED / TIMEOUT 等结果信号通过 events 与 results 双通道给出。"""
        eng = self.eng
        if src not in eng.nodes or dst not in eng.nodes:
            return {"ok": False, "signal": "NO_SUCH_NODE"}
        if eng.nodes[src].state == "DEAD":
            return {"ok": False, "signal": "SRC_DEAD"}
        if len(self.inflight()) >= MAX_CONCURRENT:
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
        eng._emit("msg_sent", "info",
                  f"📤 {src} → {dst}: 连接建立, {m.total}B 整包出发 "
                  f"(路径 {len(m.path) - 1} 跳, 信道 {''.join(map(str, res['channels']))})",
                  src=src, dst=dst, msg_id=mid)
        return {"ok": True, "msg_id": mid, "path": m.path,
                "channels": res["channels"], "segments": m.total_segs}

    # ================= 逐 tick 推进 =================
    def step(self):
        """编排一拍: 自动遥测(默认关) -> 半双工逐节点推进 -> 超时检查"""
        eng = self.eng
        self._tick_at = time.monotonic()
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
