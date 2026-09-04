# -*- coding: utf-8 -*-
"""
传输层: 连接接纳 + store-and-forward 数据传输
================================================================
报文生命周期:
  1) 连接接纳 (零时间开销的"握手"): send_message 瞬间跑 rscspa 选路,
     有路 = 连接建立, 报文立即出发; 无路 = NO_PATH, 报文拒绝。
  2) 数据传输: 报文**整包不分段**, 沿路径逐跳 store-and-forward;
     每 hop 1 tick, 按当前 BER 对整包字节掷骰判损坏(含 14B ACK 开销),
     坏则重传; 队列/缓冲/逐跳计数全部按报文完整字节数计 —— 负载语义
     与分段时代完全等价 (2KB 报文仍占 2048B 缓冲), 只是画面一包一点。
  3) 传完直接收场 (无 FIN 挥手): DELIVERED 事件 + 结果信号。
- 半双工: 每 tick 每节点只推进一个报文, 排队即真实拥塞。
- 链路中断: 报文从当前位置重新 rscspa 绕行 (传输级自愈)。
- 结果信号: DELIVERED / TIMEOUT / NO_PATH / BUFFER_FULL / MAX_RETRIES,
  全部走 events + results 双通道; 超时必然带 TIMEOUT 信号。
"""
import random
import time
from collections import deque

from .routing import rscspa

RETRIES_MAX = 3             # 每包每跳最大重传次数
QUEUE_LIMIT_BYTES = 8192    # 节点发送缓冲上限 (queue_pct = 积压/8192*100)
MAX_CONCURRENT = 6          # 在途报文上限 (防洪)
DEFAULT_TIMEOUT = 90        # 报文超时 (tick, 1 tick = 0.25s)
ACK_BYTES = 14              # 每跳数据 ACK 开销
# 自动遥测开关: 开启时每 6 tick 自动从 sensor 发遥测给 sink。
# 默认关闭 —— 流量完全由用户手动发起, 便于观察单条报文的传输过程
AUTO_TELEMETRY = False


def _damage_prob(ber: float, nbytes: int) -> float:
    """nbytes 字节经 BER 信道至少错 1 比特的概率: 1-(1-ber)^(8n)"""
    p = min(max(ber, 0.0), 0.5)
    return 1.0 - (1.0 - p) ** (nbytes * 8)


class Segment:
    """一个完整报文 (整包), 沿路径逐跳搬运"""
    __slots__ = ("mid", "seq", "nbytes", "cur", "nxt", "wire", "retries",
                 "guard", "hops")

    def __init__(self, mid, seq, nbytes, cur, nxt):
        self.mid = mid
        self.seq = seq
        self.nbytes = nbytes
        self.cur = cur
        self.nxt = nxt
        self.wire = False      # True = 本 tick 正在线上 (前端画移动方块)
        self.retries = 0
        self.guard = -1        # 最近一次起飞的 tick (防同 tick 双重推进)
        self.hops = 0          # 已飞完的跳数 (前端全程进度 = (hops+t)/总跳数)


class Message:
    def __init__(self, mid, src, dst, total, path, channels, created, deadline):
        self.id = mid
        self.src, self.dst = src, dst
        self.total = total
        self.created = created
        self.deadline = deadline
        self.status = "INFLIGHT"
        self.stage = "DATA"     # 连接接纳在发送瞬间完成, 直接进入数据阶段
        self.path = list(path)
        self.chan = {}                       # edge(排序元组) -> 信道
        for k in range(len(path) - 1):
            self.chan[tuple(sorted((path[k], path[k + 1])))] = channels[k]
        self.total_segs = 1                  # 整包单段: 不再按 MSS 分段
        self.done = 0                        # 已送达终点的分段数
        self.retries = 0
        self.hops_done = 0
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.reroutes = 0
        self.path_history = [list(path)]


class TransportLayer:
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
        q = self.node_queues.get(nid)
        return sum(s.nbytes for s in q) if q else 0

    def queue_pct(self, nid: str, extra_bytes: int = 0) -> float:
        """真实队列积压率: (缓冲中在途字节 + 链上待发字节) / 上限"""
        return min(100.0, (self.node_bytes(nid) + extra_bytes) / QUEUE_LIMIT_BYTES * 100.0)

    def link_summary(self, edge) -> dict:
        return self.link_stats.get(
            tuple(sorted(edge)),
            {"tx": 0, "rx": 0, "pkts": 0, "retries": 0, "drops": 0})

    def inflight(self):
        return [m for m in self.messages.values() if m.status == "INFLIGHT"]

    def active_traffic(self):
        """在途报文 -> engine.traffic (源/目的标记环绘制)"""
        return [{"src": m.src, "path": m.path, "bytes": m.total}
                for m in self.messages.values() if m.status == "INFLIGHT"]

    def active_nodes_edges(self):
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
        adj = {}
        for (a, b), l in self.eng.links.items():
            if not l["up"]:
                continue
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
        return rscspa(self._adj(), src, dst, n_channels=3, K=3,
                      busy_edge=self._busy_channels())

    def send_message(self, src, dst, payload_bytes,
                     timeout_ticks=DEFAULT_TIMEOUT, kind="telemetry"):
        """
        发送入口: 立即返回受理结果。
        连接接纳 (= 握手语义, 零时间开销): rscspa 选到路 = 连接建立,
        报文整包即刻出发; 选不到路 = NO_PATH 拒绝。
        最终 DELIVERED / TIMEOUT 等结果信号通过 events 与 results 双通道给出。
        """
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

    def _step_segment(self, nid: str, q: deque):
        s = q[0]
        m = self.messages.get(s.mid)
        if m is None or m.status != "INFLIGHT":
            q.popleft()
            return
        if s.wire and s.guard == self.eng.tick:
            return              # 本 tick 刚在中继续飞 (cut-through), 让它飞完这一拍
        # ---- 结算上一 tick 的在途传输 ----
        if s.wire:
            link = self.eng.links.get(tuple(sorted((s.cur, s.nxt))))
            if link is None or not link["up"]:
                s.wire = False
                self._reroute_or_fail(s, m)
                return
            st = self._stats((s.cur, s.nxt))
            ok = (random.random() >= _damage_prob(link["ber"], s.nbytes)
                  and random.random() >= _damage_prob(link["ber"], ACK_BYTES))
            if ok:
                st["rx"] += s.nbytes
                m.rx_bytes += s.nbytes
                m.hops_done += 1
                s.hops += 1
                q.popleft()
                s.wire = False
                self._arrive(s, m)
                return
            s.retries += 1
            m.retries += 1
            st["retries"] += 1
            s.wire = False
            if s.retries > RETRIES_MAX:
                st["drops"] += 1
                q.popleft()
                self._purge(m)
                self._record(m, m.src, m.dst, m.total, "MAX_RETRIES",
                             self.eng.tick - m.created, m.retries, s.cur,
                             f"整包连续重传失败")
                self.eng._emit("msg_fail", "error",
                               f"✗ 报文#{m.id} 在 {s.cur}↔{s.nxt} "
                               f"连续 {RETRIES_MAX} 次损坏, 报文作废",
                               msg_id=m.id, node=s.cur)
                return
        # ---- 发起新一跳 ----
        if s.nxt is None or s.nxt not in self.eng.nodes:
            q.popleft()
            self._arrive(s, m)
            return
        link = self.eng.links.get(tuple(sorted((s.cur, s.nxt))))
        if link is None or not link["up"]:
            self._reroute_or_fail(s, m)
            return
        st = self._stats((s.cur, s.nxt))
        st["tx"] += s.nbytes + ACK_BYTES
        st["pkts"] += 1
        m.tx_bytes += s.nbytes + ACK_BYTES
        s.wire = True

    # ================= 绕行 / 送达 / 失败 =================
    def _arrive(self, s: Segment, m: Message):
        """报文成功落到 s.nxt: 终点则记交付, 否则入中继队继续转发"""
        if s.nxt == m.dst:
            m.done += 1
            if m.done >= m.total_segs:
                self._record(m, m.src, m.dst, m.total, "DELIVERED",
                             self.eng.tick - m.created, m.retries, None, "")
                self.eng._emit("msg_delivered", "ok",
                               f"✓ 报文#{m.id} 送达 {m.dst}: {m.total}B, "
                               f"{self.eng.tick - m.created} tick, 重传 {m.retries} 次"
                               + (f", 中途绕行 {m.reroutes} 次" if m.reroutes else ""),
                               msg_id=m.id, node=m.dst)
                self.messages.pop(m.id, None)
            return
        # 中继入队 (缓冲满 -> 整报文作废)
        if self.node_bytes(s.nxt) + s.nbytes > QUEUE_LIMIT_BYTES:
            self._purge(m)
            self._record(m, m.src, m.dst, m.total, "BUFFER_FULL",
                         self.eng.tick - m.created, m.retries, s.nxt, "中继缓冲溢出")
            self.eng._emit("msg_fail", "error",
                           f"✗ 报文#{m.id} 中继 {s.nxt} 缓冲溢出, 报文作废",
                           msg_id=m.id, node=s.nxt)
            return
        try:
            idx = m.path.index(s.nxt)
            nxt = m.path[idx + 1]
        except ValueError:
            self._reroute_from(s.nxt, m)
            return
        s.cur, s.nxt, s.wire = s.nxt, nxt, False
        q2 = self.node_queues.setdefault(s.cur, deque())
        if not q2:
            # 中继队列空闲: 立即续飞下一跳 (cut-through 直通转发)
            # —— 报文全程贴线飞行不再"消失一拍", 画面连续;
            #    队列忙则正常停靠排队 (真实拥塞)
            link = self.eng.links.get(tuple(sorted((s.cur, s.nxt))))
            if link is not None and link["up"]:
                st = self._stats((s.cur, s.nxt))
                st["tx"] += s.nbytes + ACK_BYTES
                st["pkts"] += 1
                m.tx_bytes += s.nbytes + ACK_BYTES
                s.wire = True
                s.guard = self.eng.tick
        q2.append(s)

    def _reroute_or_fail(self, s: Segment, m: Message):
        """报文的下一跳链路已断: 从当前位置重规划"""
        if self._reroute_from(s.cur, m):
            try:
                idx = m.path.index(s.cur)
                s.nxt = m.path[idx + 1] if idx + 1 < len(m.path) else m.dst
            except ValueError:
                s.nxt = m.path[1] if m.path[0] == s.cur else s.nxt

    def _reroute_from(self, at: str, m: Message) -> bool:
        res = self._plan(at, m.dst)
        if res is None:
            self._purge(m)
            self._record(m, m.src, m.dst, m.total, "NO_PATH",
                         self.eng.tick - m.created, m.retries, at, "数据中断且无替代路径")
            self.eng._emit("msg_no_path", "error",
                           f"✗ 报文#{m.id} 滞留 {at}: 后续无可达路径, 报文作废",
                           msg_id=m.id, node=at)
            return False
        m.path = res["path"]
        m.reroutes += 1
        m.path_history.append(res["path"])
        for k in range(len(m.path) - 1):
            m.chan[tuple(sorted((m.path[k], m.path[k + 1])))] = res["channels"][k]
        # 报文改道: 在新路径上的按新路径取下一跳, 不在的送回 at
        for q in self.node_queues.values():
            for seg in q:
                if seg.mid != m.id:
                    continue
                if seg.cur in m.path:
                    i = m.path.index(seg.cur)
                    seg.nxt = m.path[i + 1] if i + 1 < len(m.path) else m.dst
                    seg.wire = False
                else:
                    seg.cur, seg.nxt, seg.wire = at, m.path[1], False
        self.eng._emit("msg_reroute", "warn",
                       f"⟳ 报文#{m.id} 链路中断, 从 {at} 重新绕行: "
                       f"{' → '.join(m.path)}",
                       msg_id=m.id, node=at)
        return True

    def _timeout(self, m: Message):
        """超时: 找出滞留位置, 必然返回 TIMEOUT 信号"""
        stuck = None
        left = 0
        for q in self.node_queues.values():
            for s in q:
                if s.mid == m.id:
                    left += 1
                    if stuck is None:
                        stuck = s.cur
        self._purge(m)
        self._record(m, m.src, m.dst, m.total, "TIMEOUT",
                     self.eng.tick - m.created, m.retries, stuck, "数据传输阶段")
        self.eng._emit("msg_timeout", "error",
                       f"⏱ TIMEOUT: 报文#{m.id} {m.src}→{m.dst} 超时未送达 "
                       f"({left} 段滞留, 最远推进至 {stuck})",
                       narration=f"⏱ 一份从 {self.eng._zh(m.src)} 发往 "
                                 f"{self.eng._zh(m.dst)} 的报文超时,"
                                 f"传输层已放弃并回传超时信号。",
                       msg_id=m.id, node=stuck, signal="TIMEOUT")

    def _purge(self, m: Message):
        """把该报文从所有节点队列清除"""
        self.messages.pop(m.id, None)
        for q in self.node_queues.values():
            if any(s.mid == m.id for s in q):
                keep = [s for s in q if s.mid != m.id]
                q.clear()
                q.extend(keep)

    # ================= 内部 =================
    def _stats(self, edge):
        key = tuple(sorted(edge))
        st = self.link_stats.get(key)
        if st is None:
            st = self.link_stats[key] = {"tx": 0, "rx": 0, "pkts": 0,
                                         "retries": 0, "drops": 0}
        return st

    def _record(self, m, src, dst, total, status, ticks, retries, stuck, note):
        """统一结果信号记录 (results 通道)"""
        self.results.append({
            "msg_id": m.id if m else None,
            "src": src, "dst": dst, "status": status, "signal": status,
            "bytes": total, "ticks": ticks, "retries": retries,
            "stage": getattr(m, "stage", "") if m else "",
            "hops": m.hops_done if m else 0,
            "reroutes": m.reroutes if m else 0,
            "tx_bytes": m.tx_bytes if m else 0,
            "rx_bytes": m.rx_bytes if m else 0,
            "stuck_at": stuck, "note": note,
        })
