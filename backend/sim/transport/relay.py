# -*- coding: utf-8 -*-
"""
传输层逐跳推进引擎 (RelayMixin)
====================================
职责: TransportLayer 的"搬运工" —— 半双工逐 tick 推进、BER 掷骰损坏/
重传、中继入队 (cut-through 直通)、断链绕行重规划、超时与统一结果记录。
依赖: model.Segment/Message 与常量, 引擎 links (链路表)。
"""
import logging   # 标准库: 模块日志 (送达/失败/绕行/超时)
import random  # 标准库: BER 掷骰 (整包与 ACK 损坏判定)

from collections import deque                      # 标准库: 中继入队 (发送缓冲)

from ..config import ROBOT_ID                       # 协议标识: 机器人合法中继
from .model import (ACK_BYTES, QUEUE_LIMIT_BYTES,  # 每跳开销/缓冲上限
                    RETRIES_MAX, _damage_prob)      # 重传上限/BER 损坏概率

log = logging.getLogger(__name__)   # 本模块日志器


class RelayMixin:
    """职责: TransportLayer 的逐跳推进混入。

    属性要求 (由 TransportLayer.__init__ 提供): self.eng (引擎引用),
    self.messages (报文表), self.node_queues (节点发送缓冲)。

    调用链: core.step -> _step_segment -> (_settle_transit | _launch_next_hop)
    -> _arrive -> (_cut_through | _reroute_from); step -> _timeout。
    """

    def _step_segment(self, nid: str, q):
        """半双工推进: 每节点每 tick 结算一个队头报文 (先结算再起飞)"""
        s = q[0]
        m = self.messages.get(s.mid)
        if m is None or m.status != "INFLIGHT":
            q.popleft()
            return
        if s.wire and s.guard == self.eng.tick:
            return              # 本 tick 刚在中继续飞 (cut-through), 让它飞完这一拍
        if s.wire and self._settle_transit(s, m, q):
            return              # 上一跳已结算 (送达/作废/待重传), 本拍结束
        self._launch_next_hop(s, m, q)

    def _settle_transit(self, s, m, q) -> bool:
        """结算上一 tick 的在途传输: 链路已断->绕行; 掷骰损坏->重传计数;
        超过重传上限->整包作废 (MAX_RETRIES)。返回 True = 本拍结束。"""
        link = self.eng.links.get(tuple(sorted((s.cur, s.nxt))))
        if link is None or not link["up"]:
            s.wire = False
            self._reroute_or_fail(s, m)
            return True
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
            return True
        s.retries += 1
        m.retries += 1
        st["retries"] += 1
        s.wire = False
        if s.retries > RETRIES_MAX:
            st["drops"] += 1
            q.popleft()
            self._purge(m)
            log.warning("报文#%s MAX_RETRIES @%s<->%s (BER=%.1e)",
                        m.id, s.cur, s.nxt, link["ber"])
            self._record(m, m.src, m.dst, m.total, "MAX_RETRIES",
                         self.eng.tick - m.created, m.retries, s.cur,
                         f"整包连续重传失败")
            self.eng._emit("msg_fail", "error",
                           f"✗ 报文#{m.id} 在 {s.cur}↔{s.nxt} "
                           f"连续 {RETRIES_MAX} 次损坏, 报文作废",
                           msg_id=m.id, node=s.cur)
            return True
        return False

    def _launch_next_hop(self, s, m, q):
        """发起新一跳: 终点已到->送达结算; 链路缺失->绕行; 正常->上线"""
        if s.nxt is None or (s.nxt not in self.eng.nodes and s.nxt != ROBOT_ID):
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
    def _arrive(self, s, m):
        """报文成功落到 s.nxt: 终点则记交付, 否则入中继队继续转发"""
        if s.nxt == m.dst:
            m.done += 1
            if m.done >= m.total_segs:
                log.info("报文#%s 送达 %s: %dtick 重传%d 绕行%d",
                         m.id, m.dst, self.eng.tick - m.created,
                         m.retries, m.reroutes)
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
            log.warning("报文#%s BUFFER_FULL @%s", m.id, s.nxt)
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
            self._cut_through(s, m)
        q2.append(s)

    def _cut_through(self, s, m):
        """中继队列空闲: 立即续飞下一跳 (cut-through 直通转发)
        —— 报文全程贴线飞行不再"消失一拍", 画面连续;
           队列忙则正常停靠排队 (真实拥塞)"""
        link = self.eng.links.get(tuple(sorted((s.cur, s.nxt))))
        if link is None or not link["up"]:
            return
        st = self._stats((s.cur, s.nxt))
        st["tx"] += s.nbytes + ACK_BYTES
        st["pkts"] += 1
        m.tx_bytes += s.nbytes + ACK_BYTES
        s.wire = True
        s.guard = self.eng.tick

    def _reroute_or_fail(self, s, m):
        """报文的下一跳链路已断: 从当前位置重规划"""
        if self._reroute_from(s.cur, m):
            try:
                idx = m.path.index(s.cur)
                s.nxt = m.path[idx + 1] if idx + 1 < len(m.path) else m.dst
            except ValueError:
                s.nxt = m.path[1] if m.path[0] == s.cur else s.nxt

    def _reroute_from(self, at: str, m) -> bool:
        """从 at 到终点重新 rscspa 选路; 无路 -> NO_PATH 作废。返回是否成功。"""
        res = self._plan(at, m.dst)
        if res is None:
            self._purge(m)
            log.warning("报文#%s NO_PATH 滞留@%s", m.id, at)
            self._record(m, m.src, m.dst, m.total, "NO_PATH",
                         self.eng.tick - m.created, m.retries, at, "数据中断且无替代路径")
            self.eng._emit("msg_no_path", "error",
                           f"✗ 报文#{m.id} 滞留 {at}: 后续无可达路径, 报文作废",
                           msg_id=m.id, node=at)
            return False
        m.path = res["path"]
        m.reroutes += 1
        m.path_history.append(res["path"])
        log.info("报文#%s 绕行@%s: 新路径 %s", m.id, at, " -> ".join(m.path))
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

    def _timeout(self, m):
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
        log.warning("报文#%s TIMEOUT %s->%s 滞留@%s",
                    m.id, m.src, m.dst, stuck)
        self._record(m, m.src, m.dst, m.total, "TIMEOUT",
                     self.eng.tick - m.created, m.retries, stuck, "数据传输阶段")
        self.eng._emit("msg_timeout", "error",
                       f"⏱ TIMEOUT: 报文#{m.id} {m.src}→{m.dst} 超时未送达 "
                       f"({left} 段滞留, 最远推进至 {stuck})",
                       narration=f"⏱ 一份从 {self.eng._zh(m.src)} 发往 "
                                 f"{self.eng._zh(m.dst)} 的报文超时,"
                                 f"传输层已放弃并回传超时信号。",
                       msg_id=m.id, node=stuck, signal="TIMEOUT")

    def _purge(self, m):
        """把该报文从所有节点队列清除"""
        self.messages.pop(m.id, None)
        for q in self.node_queues.values():
            if any(s.mid == m.id for s in q):
                keep = [s for s in q if s.mid != m.id]
                q.clear()
                q.extend(keep)

    # ================= 内部 =================
    def _stats(self, edge):
        """链路计数器 (键一律排序元组)"""
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
