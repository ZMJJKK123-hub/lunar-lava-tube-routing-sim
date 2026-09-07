# -*- coding: utf-8 -*-
"""
链同步协议 (SyncMixin): 收包分发 / 追块 / 分叉愈合
====================================================
职责: ChainNode 的通信协议栈 —— TX/BLOCK 泛洪处理、SYNC_REQ/SYNC_RESP
防熵追块、同高度竞争块触发的整链择优愈合 (限频防响应风暴)。
"""
import random  # 标准库: 整链响应抽样 (40%) 抑制风暴

from .model import (FORK_REQ_CD, FORK_RESP_CD, RESP_CD, SYNC_BATCH,  # 限频/批量
                    Block, Transaction, _mk_packet)                   # 实体与信封


class SyncMixin:
    """ChainNode 的同步协议混入。

    属性要求 (由 ChainNode.__init__ 提供): self.id/chain/mempool/seen/
    fork_mode/fork_req_tick/resp_cd/out 与 _net (网络编排引用)。

    调用链: network.step -> handle_packet -> (_on_tx | _on_block |
    _on_sync_req | _on_sync_resp) -> (_accept | _adopt_chain | _relay)。
    """

    # ---------- 收包 ----------
    def handle_packet(self, pkt: dict, tick: int) -> list:
        """泛洪包入口: 去重后按类型分发; 返回本节点待发包"""
        self.out = []
        if not self._seen_mark(pkt["msg_id"]):
            return []
        t = pkt["type"]
        if t == "TX":
            self._on_tx(pkt)
        elif t == "BLOCK":
            self._on_block(pkt, tick)
        elif t == "SYNC_REQ":
            self._on_sync_req(pkt, tick)
        elif t == "SYNC_RESP":
            self._on_sync_resp(pkt, tick)
        out, self.out = self.out, []
        return out

    def _on_tx(self, pkt):
        """交易泛洪: 新交易入池并续泛洪; 已知/过时不再转发"""
        tx = Transaction.from_dict(pkt["payload"]["tx"])
        if tx.tx_id in self.mempool or tx.seq <= self.latest_seq.get(tx.robot_id, 0):
            return
        self.mempool[tx.tx_id] = tx
        self._relay(pkt)

    def _on_block(self, pkt, tick: int):
        """区块泛洪: 合法续链则接受; 同高度竞争块触发整链择优; 更高则追块"""
        blk = Block.from_dict(pkt["payload"]["block"])
        if blk.index <= self.height:
            return                              # 旧块
        if self._valid_next(blk):
            self._accept(blk)
            self.fork_mode = False
            self._relay(pkt)
            return
        if blk.index == self.height + 1:
            # 同高度竞争块 (分叉): prev 对不上或出块权不符 -> 整链择优
            # (限频内不重发; fork_mode 只作标记, 不阻止后续重试, 防响应丢失死锁)
            if tick - self.fork_req_tick > FORK_REQ_CD:
                self.fork_mode = True
                self.fork_req_tick = tick
                self.out.append(_mk_packet(
                    "SYNC_REQ", self.id, {"from_index": 0, "fork": True}))
        else:
            # 对方领先 -> 追块
            self.out.append(_mk_packet(
                "SYNC_REQ", self.id, {"from_index": self.height, "fork": False}))

    def _on_sync_req(self, pkt, tick: int):
        """同步请求: 请求也泛洪转发 (低 TTL); 持更优链的远端节点同样可响应"""
        if pkt["ttl"] > 1:
            fwd = dict(pkt); fwd["ttl"] = min(pkt["ttl"] - 1, 8)
            self.out.append(fwd)
        src = pkt["src_node"]
        if pkt["payload"].get("fork"):
            # 整链响应: 抽样 40% + 限频, 抑制几十个节点同时要全链的风暴
            if self.height <= 0 or random.random() > 0.4:
                return
            if tick - self.resp_cd.get(src, -999) < FORK_RESP_CD:
                return
            self.resp_cd[src] = tick
            blocks = [b.to_dict() for b in self.chain[1:]]
            self.out.append(_mk_packet(
                "SYNC_RESP", self.id,
                {"from_index": 0, "fork": True, "total": self.height,
                 "blocks": blocks, "to": src}))
            return
        start = pkt["payload"].get("from_index", 0)
        if self.height <= start:
            return                              # 我没有它要的块
        if tick - self.resp_cd.get(src, -999) < RESP_CD:
            return
        self.resp_cd[src] = tick
        chunk = [b.to_dict() for b in self.chain[start + 1:start + 1 + SYNC_BATCH]]
        self.out.append(_mk_packet(
            "SYNC_RESP", self.id,
            {"from_index": start, "fork": False, "total": self.height,
             "blocks": chunk, "to": src}))

    def _on_sync_resp(self, pkt, tick: int):
        """同步响应: 非目标节点照常泛洪; 目标节点走愈合/追块两条路径"""
        if pkt["payload"].get("to") not in (None, self.id):
            self._relay(pkt)                    # 非目标节点照常泛洪
            return
        blocks = [Block.from_dict(b) for b in pkt["payload"]["blocks"]]
        # ---- 整链响应: 分叉愈合 (一次性择优) ----
        if pkt["payload"].get("fork") or pkt["payload"].get("from_index") == 0:
            healed = self._adopt_chain(blocks)
            self.fork_mode = False
            if healed and self._net:
                self._net.stats["fork_heals"] += 1
                self._net.notify("chain_heal", "ok",
                                 f"🩹 {self.id} 分叉愈合: 采纳更优链 "
                                 f"(高度 {self.height})")
            return
        # ---- 普通追块: 连续补链 ----
        frm = pkt["payload"].get("from_index")
        if frm != self.height:
            return                              # 批次错位, 等心跳重试
        accepted = 0
        for blk in blocks:
            if self._valid_next(blk):
                self._accept(blk)
                accepted += 1
            else:
                break
        if accepted == 0 and blocks \
                and blocks[0].index == self.height + 1 \
                and blocks[0].prev_hash != self.tail.block_hash:
            # 首块就衔接不上 -> 本地链在分叉上 -> 触发整链择优愈合
            if tick - self.fork_req_tick > FORK_REQ_CD:
                self.fork_mode = True
                self.fork_req_tick = tick
                self.out.append(_mk_packet(
                    "SYNC_REQ", self.id, {"from_index": 0, "fork": True}))
            return
        if accepted and self._net:
            self._net.stats["catchups"] += 1
        if self.height < pkt["payload"].get("total", 0):
            self.out.append(_mk_packet(
                "SYNC_REQ", self.id,
                {"from_index": self.height, "fork": False}))
