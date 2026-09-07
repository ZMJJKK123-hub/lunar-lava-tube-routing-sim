# -*- coding: utf-8 -*-
"""
链上节点核心: ChainNode (链/世界状态/出块权校验/择优采纳)
============================================================
职责: 单节点视角的一条链 + 世界状态重放 + 统一排他调度出块;
收包分发与同步协议在 sync.SyncMixin。
"""
import logging   # 标准库: 模块日志 (出块/整链采纳)

from .model import (MAX_TX_PER_BLOCK, MIN_BLOCK_GAP, SEEN_MAX,       # 调度/去重
                    SKIP_AFTER, Block, Transaction, _hash, _mk_packet)  # 实体与信封
from .sync import SyncMixin   # 收包处理: TX/BLOCK/SYNC_REQ/SYNC_RESP

log = logging.getLogger(__name__)   # 本模块日志器


class ChainNode(SyncMixin):
    """职责: 每个仿真节点内运行的一条链 + 世界状态 + 通信栈。

    核心属性:
    - chain: 区块列表 (chain[0]=创世); world_state: robot_id -> 最新遥测;
    - latest_seq: 已应用最大序号 (防乱序/防重放);
    - mempool: 待打包交易; seen: 泛洪去重 LRU; my_seq: 自身遥测序号;
    - fork_mode/fork_req_tick/resp_cd: 分叉愈合与限频状态; out: 待发包。

    调用链: network.step -> handle_packet (SyncMixin) / try_mine
    -> _accept/_adopt_chain -> _apply_tx/_replay_all (世界状态演进)。
    """

    def __init__(self, nid: str, sorted_ids: list, genesis: Block):
        self.id = nid
        self.sorted_ids = sorted_ids
        self.chain = [genesis]
        self.world_state: dict = {}         # robot_id -> 最新遥测 (含 seq/tick)
        self.latest_seq: dict = {}          # robot_id -> 已应用最大 seq
        self.mempool: dict = {}             # tx_id -> Transaction
        self.seen: dict = {}                # msg_id -> True (LRU)
        self.my_seq = 0
        self.fork_mode = False              # 分叉愈合中
        self.fork_req_tick = -999           # 上次 fork 请求 tick (限频)
        self.resp_cd: dict = {}             # 请求方 -> 上次响应 tick (限频)
        self.out: list = []
        self._net = None

    # ---------- 基础 ----------
    @property
    def height(self):
        """本地链高度 (创世=0)。

        Args: None。
        Returns: int。Globals Used: None。Calls: None。
        """
        return len(self.chain) - 1

    @property
    def tail(self):
        """本地链尾块。

        Args: None。
        Returns: Block 实例。Globals Used: None。Calls: None。
        """
        return self.chain[-1]

    def state_hash(self):
        """世界状态指纹 (一致性对齐判定用)。

        Args: None。
        Returns: str, SHA-256 hex —— 由 world_state 与 latest_seq 共同决定,
        同高度同哈希 = 视为账本一致。Globals Used: model._hash。
        Calls: _hash。
        """
        return _hash({"ws": self.world_state, "seq": self.latest_seq})

    def _seen_mark(self, mid) -> bool:
        """泛洪去重: 首见返回 True 并登记; 超 LRU 上限批量淘汰"""
        if mid in self.seen:
            return False
        self.seen[mid] = True
        if len(self.seen) > SEEN_MAX:
            for k in list(self.seen)[:SEEN_MAX // 8]:
                self.seen.pop(k, None)
        return True

    def _relay(self, pkt):
        """泛洪转发: TTL>1 才续命一跳"""
        if pkt["ttl"] <= 1:
            return
        fwd = dict(pkt)
        fwd["ttl"] -= 1
        self.out.append(fwd)

    # ---------- 世界状态 ----------
    def _apply_tx(self, tx: Transaction) -> bool:
        """应用一笔交易: seq 单调递增才生效 (防乱序/防重放)"""
        if tx.seq <= self.latest_seq.get(tx.robot_id, 0):
            return False
        self.latest_seq[tx.robot_id] = tx.seq
        self.world_state[tx.robot_id] = {**tx.payload, "seq": tx.seq,
                                         "tick": tx.tick}
        return True

    def _replay_all(self):
        """整链重放世界状态 (分叉愈合采纳新链后调用)"""
        self.world_state, self.latest_seq = {}, {}
        for blk in self.chain[1:]:
            for tx in blk.transactions:
                self._apply_tx(tx)

    # ---------- 校验 / 上链 ----------
    def _valid_next(self, blk: Block) -> bool:
        """衔接本地链尾的合法性: index/prev/出块权/哈希 (全部链内可验)"""
        if blk.index != len(self.chain) or blk.prev_hash != self.tail.block_hash:
            return False
        dt = blk.tick - self.tail.tick
        if dt <= 0 or len(blk.transactions) > MAX_TX_PER_BLOCK:
            return False
        # 统一排他调度: 出块人 = sorted[(index + 时间窗) % N], 每个时间窗
        # (SKIP_AFTER tick) 内唯一 -> 矿工/验证者零歧义, 无并发分叉
        win = blk.tick // SKIP_AFTER
        expect = self.sorted_ids[(blk.index + win) % len(self.sorted_ids)]
        return blk.creator == expect

    def _accept(self, blk: Block):
        """上链: 追加区块 + 按序应用交易 + 清理 mempool"""
        self.chain.append(blk)
        for tx in blk.transactions:
            self._apply_tx(tx)
            self.mempool.pop(tx.tx_id, None)

    def _adopt_chain(self, blocks: list) -> bool:
        """整链择优采纳: 更高者胜 / 同高尾部哈希小者胜 (全序, 双方必然收敛)"""
        if not blocks:
            return False
        better = len(blocks) > self.height or (
            len(blocks) == self.height
            and blocks[-1].block_hash < self.tail.block_hash)
        if not better:
            return False
        prev = self.chain[0]
        if blocks[0].prev_hash != prev.block_hash:
            return False                      # 创世不同源
        for b in blocks:
            if b.prev_hash != prev.block_hash:
                return False
            if Block.from_dict(b.to_dict()).block_hash != b.block_hash:
                return False                  # 内容被篡改
            if b.transactions:                # 出块权校验 (统一调度公式)
                win = b.tick // SKIP_AFTER
                if b.creator != self.sorted_ids[
                        (b.index + win) % len(self.sorted_ids)]:
                    return False              # 非法出块者
            prev = b
        old_h = self.height
        self.chain = [self.chain[0]] + blocks
        self._replay_all()
        log.info("整链采纳 %s: 高度 %d -> %d (更优链胜出)",
                 self.id, old_h, self.height)
        for tx in [t for blk in blocks for t in blk.transactions]:
            self.mempool.pop(tx.tx_id, None)
        return True

    # ---------- 出块 ----------
    def try_mine(self, tick: int) -> bool:
        """轮到自己 ∧ (有交易 ∧ 间隔达标) -> 出块; 块龄超时 -> 空块推进。

        Args: tick: 当前仿真 tick (出块权公式的时间窗参数)。
        Returns: bool —— True=本拍出了块 (新块已上链并进待发包队列)。
        Globals Used: SKIP_AFTER/MIN_BLOCK_GAP/MAX_TX_PER_BLOCK (统一排他调度)。
        Calls: Block 构造 / _accept / _mk_packet。
        """
        nxt = self.tail.index + 1
        dt = tick - self.tail.tick
        # 统一排他调度: 本时间窗轮到我出块才出手 (有交易带交易,
        # 没交易且块龄超时出空块推进; 都不满足则等待)
        win = tick // SKIP_AFTER
        if self.sorted_ids[(nxt + win) % len(self.sorted_ids)] != self.id:
            return False
        if self.mempool:
            if dt < MIN_BLOCK_GAP:
                return False
            txs = sorted(self.mempool.values(),
                         key=lambda t: (t.tick, t.robot_id))[:MAX_TX_PER_BLOCK]
        elif dt >= SKIP_AFTER + MIN_BLOCK_GAP:
            txs = []                          # 空块: 长时间无交易, 推进轮询
        else:
            return False
        blk = Block(index=nxt, prev_hash=self.tail.block_hash,
                    tick=tick, creator=self.id, transactions=txs)
        log.info("出块 #%d by %s: txs=%d mempool余=%d (tick=%d)",
                 nxt, self.id, len(txs), len(self.mempool) - len(txs), tick)
        self._accept(blk)
        self.out.append(_mk_packet("BLOCK", self.id, {"block": blk.to_dict()}))
        return True

    # ---------- 主动行为 ----------
    def emit_telemetry(self, tick: int, data: dict) -> dict:
        """产生自身遥测交易并入池 + 广播 (RobotNode.generate_telemetry 语义)。

        Args: tick: 产生时刻; data: TelemetryPayload 遥测字典。
        Returns: dict —— TX 泛洪信封 (_mk_packet 格式), 由 network 投递。
        Globals Used: None。Calls: Transaction 构造 / _mk_packet。
        """
        self.my_seq += 1
        tx = Transaction(self.id, self.my_seq, tick, data)
        self.mempool[tx.tx_id] = tx
        return _mk_packet("TX", self.id, {"tx": tx.to_dict()})

    def emit_heartbeat(self, tick: int) -> dict:
        """周期性防熵: 比我高的邻居会回 SYNC_RESP。

        Args: tick: 产生时刻 (未入体, 仅对齐签名)。
        Returns: dict —— SYNC_REQ 泛洪信封 (报本地高度)。
        Globals Used: None。Calls: _mk_packet。
        """
        return _mk_packet("SYNC_REQ", self.id,
                          {"from_index": self.height, "fork": False})
