# -*- coding: utf-8 -*-
"""
账本网络编排: BlockchainNetwork (拓扑投递 + 节点驱动 + 快照导出)
====================================================================
职责: 挂在 SimulationEngine 上, 复用引擎链路表做多跳泛洪投递,
驱动全部 ChainNode 的遥测/心跳/出块, 并导出账本侧边栏快照。
"""
import json      # 标准库: 泛洪包字节计账 (payload 规范 JSON 长度)
import logging   # 标准库: notify 的异常显式记录 (不静默吞噬)

from ..config import ROBOT_ID                    # 协议标识: 机器人非共识常开
from .chain_node import ChainNode                # 链上节点 (含 SyncMixin)
from .model import (GENESIS_PREV, HEARTBEAT_EVERY, Block,     # 创世/心跳/区块
                    TELEMETRY_EVERY, SYNC_PREFIX_BLOCKS)      # 遥测周期/前缀容差

log = logging.getLogger(__name__)   # 本模块日志器 (异常可见, 不打断仿真)


class BlockchainNetwork:
    """挂在 SimulationEngine 上: 提供拓扑与遥测数据, 驱动全部 ChainNode。

    核心属性:
    - eng: 引擎引用 (links 拓扑 + nodes 遥测源); sorted_ids: 共识名单;
    - nodes: nid -> ChainNode (含机器人/道钉等非共识注册者);
    - inflight: [(from_node, pkt)] 下一 tick 投递队列;
    - stats: blocks/fork_heals/catchups 计数;
    - tx_load: 节点 -> 本 tick 链上待发字节 (计入 queue_pct)。

    执行链路: engine.run_forever -> step (_deliver_inflight + _drive_nodes)
    -> ChainNode.handle_packet/try_mine -> engine.snapshot -> export_info。
    """

    def __init__(self, engine):
        self.eng = engine
        self.sorted_ids = sorted(engine.nodes.keys())
        self.genesis = Block(index=0, prev_hash=GENESIS_PREV, tick=0,
                             creator="GENESIS", transactions=[])
        self.nodes = {nid: ChainNode(nid, self.sorted_ids, self.genesis)
                      for nid in self.sorted_ids}
        for nd in self.nodes.values():
            nd._net = self
        self.inflight: list = []                # (from_node, pkt) 下一 tick 投递
        self.stats = {"blocks": 0, "fork_heals": 0, "catchups": 0}
        self._adj: dict = {}
        self.tx_load: dict = {}                 # 节点 -> 本 tick 链上待发字节 (计入 queue_pct)
        self._size_cache: dict = {}             # msg_id -> 报文字节 (同一泛洪包全网只算一次)

    # ---- 拓扑: 复用引擎链路表 (遮挡/画墙/断链全部生效) ----
    def _neighbors(self, nid):
        """节点的一跳邻居 (本 tick 缓存; 断链即时生效)"""
        if nid in self._adj:
            return self._adj[nid]
        out = []
        for (a, b), l in self.eng.links.items():
            if not l["up"]:
                continue
            if a == nid:
                out.append(b)
            elif b == nid:
                out.append(a)
        self._adj[nid] = out
        return out

    def _alive(self, nid):
        """共识活性: 机器人常开 (大电池; 非共识节点)"""
        if nid == ROBOT_ID:
            return True
        n = self.eng.nodes.get(nid)
        return n is not None and n.state != "DEAD"

    def register_node(self, nid: str):
        """注册非共识节点 (机器人/道钉): 完整链同步与转发, 但不进轮值名单
        sorted_ids —— 永不遥测、永不出块、不进账本侧边栏。"""
        if nid not in self.nodes:
            nd = ChainNode(nid, self.sorted_ids, self.genesis)
            nd._net = self
            self.nodes[nid] = nd

    def _telemetry_payload(self, nid):
        """节点遥测交易负载 (链上世界状态的字段契约)"""
        n = self.eng.nodes[nid]
        return {"x": n.x, "z": n.z, "soc": round(n.battery_soc, 1),
                "temp": round(n.temp_c, 1), "state": n.state,
                "queue": round(n.queue_pct, 1), "radio": n.radio,
                "hop": n.hop_count}

    def _pkt_bytes(self, pkt) -> int:
        """报文体积 ≈ payload 规范 JSON 长度; 按 msg_id 缓存
        (同一泛洪包全网数千跳, 只序列化一次)"""
        b = self._size_cache.get(pkt["msg_id"])
        if b is None:
            b = len(json.dumps(pkt["payload"], default=str,
                               separators=(",", ":")))
            if len(self._size_cache) > 4096:
                self._size_cache.clear()
            self._size_cache[pkt["msg_id"]] = b
        return b

    # ---- 事件上报 (前端 EventLog 直观可见同步过程) ----
    def notify(self, type_, sev, msg):
        """经引擎事件总线上报; 失败显式记录日志 (不打断仿真)"""
        try:
            self.eng._emit(type_, sev, msg)
        except Exception:
            log.exception("chain notify failed: %s", msg)

    # ---- 每 tick 主循环 ----
    def step(self, tick: int):
        """编排一拍: 投递上拍泛洪包 -> 节点主动行为 (遥测/心跳/出块)"""
        self._adj = {}
        load = self._deliver_inflight(tick)
        self._drive_nodes(tick, load)
        self.tx_load = load

    def _deliver_inflight(self, tick: int) -> dict:
        """投递上一 tick 的包 (一跳一 tick); 只上报真实转发的跳 (r 标记)。
        吸收跳 (seen 去重后不再转发) 每 tick 上万, 上报会淹没有效流量
        且把排在投递序列末尾的 BLOCK 挤出总线 —— 故不进总线。"""
        emitted = []
        load: dict = {}     # 节点 -> 本 tick 链上待发字节 (驱动 queue_pct/PAMAS/耗电)
        for from_node, pkt in self.inflight:
            for nb in self._neighbors(from_node):
                if not self._alive(nb):
                    continue
                outs = self.nodes[nb].handle_packet(pkt, tick)
                for out in outs:
                    emitted.append((nb, out))
                    load[nb] = load.get(nb, 0) + self._pkt_bytes(out)
                if outs:
                    self.eng.vis_packet(from_node, nb, pkt["type"], relayed=True)
        self.inflight = emitted
        return load

    def _drive_nodes(self, tick: int, load: dict):
        """节点主动行为: 遥测 / 心跳 / 出块 (全部错峰; 字节同步记账)"""
        for i, nid in enumerate(self.sorted_ids):
            if not self._alive(nid):
                continue
            node = self.nodes[nid]
            if (tick + i) % TELEMETRY_EVERY == 0:
                self.inflight.append(
                    (nid, node.emit_telemetry(tick, self._telemetry_payload(nid))))
                load[nid] = load.get(nid, 0) + self._pkt_bytes(self.inflight[-1][1])
            if (tick + i) % HEARTBEAT_EVERY == 5:
                self.inflight.append((nid, node.emit_heartbeat(tick)))
                load[nid] = load.get(nid, 0) + self._pkt_bytes(self.inflight[-1][1])
            if node.try_mine(tick):
                self.stats["blocks"] += 1
                self.inflight.append((nid, node.out.pop()))
                load[nid] = load.get(nid, 0) + self._pkt_bytes(self.inflight[-1][1])
                if self.stats["blocks"] % 6 == 0:
                    self.notify("chain_block", "info",
                                f"⛓ 链高度 {node.height} (出块 {nid}, "
                                f"含 {len(node.tail.transactions)} 笔) "
                                f"— 全网账本持续增长")

    # ---- 快照导出 (前端账本侧边栏) ----
    def export_info(self):
        """账本快照: 高度分布/一致性指标/世界状态/分叉差异"""
        per, h_max = self._ledger_rows()
        base, alive, per_alive = self._consensus_base(per, h_max)
        ref_hashes, world, tip = self._reference_chain(alive, h_max, base)
        diffs = self._fork_diffs(h_max, base)
        lag1 = sum(1 for r in per_alive if r["h"] >= h_max - 1)   # 距顶<=1块
        agree = sum(1 for nid in alive
                    if self.nodes[nid].tail.block_hash in ref_hashes)
        return {"h_max": h_max, "n": len(self.sorted_ids), "na": len(alive),
                "aligned": sum(1 for r in per_alive if r["h"] == h_max),
                "lag1": lag1,
                "agree": agree,
                "base_hash": base, "tip": tip, "per": per,
                "world": world, "diffs": diffs, "stats": dict(self.stats)}

    def _ledger_rows(self):
        """全部共识节点的 (id, 高度, 状态哈希, mempool) 行"""
        per = []
        h_max = 0
        for nid in self.sorted_ids:
            nd = self.nodes[nid]
            per.append({"id": nid, "h": nd.height,
                        "sh": nd.state_hash()[:8], "mp": len(nd.mempool)})
            h_max = max(h_max, nd.height)
        return per, h_max

    def _consensus_base(self, per, h_max):
        """基准 = 活跃节点中链顶层(h_max)多数派状态哈希。
        全网一致性用「链前缀」判定 (见 agree): 节点链尾哈希落在基准链最近
        SYNC_PREFIX_BLOCKS 块内 = "我的链是基准链的前缀 (允许落后 K-1 块)"。
        正常传播波 (落后 1~2 块) 仍算一致, 横幅不闪; 分叉侧即使只落后 1 块,
        其块哈希也不在基准链前缀内 -> 立即判不一致, 横幅转"同步中"。
        (旧方案按"状态哈希相位"判定, 传播波与真分叉不可区分, 横幅几乎恒绿)"""
        alive = {nid for nid in self.sorted_ids if self._alive(nid)}
        top, ph = {}, {}
        for r in per:
            if r["id"] not in alive:
                continue
            if r["h"] == h_max:
                top[r["sh"]] = top.get(r["sh"], 0) + 1
            elif r["h"] == h_max - 1:
                ph[r["sh"]] = ph.get(r["sh"], 0) + 1
        base = max(top, key=top.get) if top else (max(ph, key=ph.get) if ph else "")
        per_alive = [r for r in per if r["id"] in alive]
        return base, alive, per_alive

    def _reference_chain(self, alive, h_max, base):
        """基准链参考: 前缀哈希集 + 全网世界状态 + 链尖摘要"""
        ref_hashes, world, tip = set(), {}, None
        for nid in self.sorted_ids:
            nd = self.nodes[nid]
            if nid in alive and nd.height == h_max and nd.state_hash()[:8] == base:
                ref_hashes = {b.block_hash for b in nd.chain[-SYNC_PREFIX_BLOCKS:]}
                world = {rid: dict(st) for rid, st in nd.world_state.items()}
                tip = {"index": nd.tail.index, "creator": nd.tail.creator,
                       "tick": nd.tail.tick,
                       "txs": len(nd.tail.transactions),
                       "hash": nd.tail.block_hash[:8]}
                break
        return ref_hashes, world, tip

    def _fork_diffs(self, h_max, base):
        """真分叉节点的世界状态差异 (同高度不同哈希)。
        落后节点前端显示"追赶中"即可 —— 出块传播波几乎常驻,
        逐帧整份下发曾使快照膨胀到 238KB。"""
        diffs = {}
        for nid in self.sorted_ids:
            nd = self.nodes[nid]
            if (nd.height == h_max and nd.state_hash()[:8] != base):
                diffs[nid] = {rid: dict(st)
                              for rid, st in nd.world_state.items()}
                if len(diffs) >= 12:
                    break
        return diffs
