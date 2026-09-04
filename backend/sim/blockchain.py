# -*- coding: utf-8 -*-
"""
区块链全网状态同步模拟 (轮询 PoA + 泛洪传播 + 追块/分叉愈合)
================================================================
目标: 每个节点各自维护"全网所有节点参数"的世界状态 (World State),
在多跳 Mesh 拓扑 (遮挡/画墙/断链全部生效) 上安全同步、不可篡改。

核心设计 (全部只依赖链内共识数据, 各节点视角恒一致, 不用本地时钟):
- 调度: leader(H) = 全体ID排序[H % N], 纯高度轮询。
- Leader 阵亡: 块龄 (blk.tick - 上块.tick, 链内可验) >= SKIP_AFTER 时
  任何节点可出"空块"推进高度, 轮询继续, 无需通信。
- 分叉愈合: 同高度竞争块 (分区两边各自出块所致) -> 请求对方完整链
  (链长几十块, 一次给清), 从创世整链重验, 按
  "更高者胜 / 同高尾部哈希小者胜" 的全序规则择优 —— 双方对称执行,
  必然收敛到同一条链。
- 追块: 收到 index > 本地高度的块, 或周期性 SYNC_REQ 心跳 (错峰),
  邻居按请求高度回批 (<= SYNC_BATCH 块), 逐块验证补链。
- 泛洪: seen LRU 缓存 + TTL 抑制风暴, 一跳一 tick;
  SYNC_RESP 非目标节点也转发 (否则响应到不了远端)。
- 世界状态: 链 = 有序日志; world_state 只在上链时按序重放,
  tx.seq > latest_seq[robot] 才应用 (防乱序/防重放)。
"""
import hashlib
import json
import random
from dataclasses import dataclass

# ---------------- 可调常量 ----------------
TTL = 12                    # 泛洪包生存跳数
SEEN_MAX = 4096             # 已见消息缓存上限 (LRU)
MAX_TX_PER_BLOCK = 16       # 单块打包交易上限 (每窗口产~12笔, 留余量)
MIN_BLOCK_GAP = 12          # 两块最小间隔 tick (12 tick = 传播约6tick, 留全绿稳定期)
SKIP_AFTER = 12             # 块龄达到该值 -> 任何节点可出空块推进 (Leader 阵亡兜底)
TELEMETRY_EVERY = 60        # 每节点遥测周期 (错峰; 与出块吞吐平衡防积压)
HEARTBEAT_EVERY = 20        # 周期性 SYNC_REQ 防熵心跳 (错峰)
SYNC_BATCH = 12             # 普通追块单批最大区块数
RESP_CD = 5                 # 追块响应限频 (同一请求方, tick)
FORK_RESP_CD = 6           # 整链响应限频 (同一请求方, tick)
FORK_REQ_CD = 8             # fork 请求重发限频 (自身, tick)
GENESIS_PREV = "0" * 64


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _hash(obj) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


# ================= 数据模型 =================
@dataclass
class Transaction:
    robot_id: str           # 来源节点
    seq: int                # 该节点遥测单调递增序列号
    tick: int               # 产生时的仿真 tick
    payload: dict           # 遥测: 坐标/SoC/温度/状态/队列/电台...
    tx_id: str = ""

    def __post_init__(self):
        if not self.tx_id:
            self.tx_id = _hash({"robot_id": self.robot_id, "seq": self.seq,
                                "tick": self.tick, "payload": self.payload})

    def to_dict(self):
        return {"robot_id": self.robot_id, "seq": self.seq, "tick": self.tick,
                "payload": self.payload, "tx_id": self.tx_id}

    @staticmethod
    def from_dict(d) -> "Transaction":
        return Transaction(d["robot_id"], d["seq"], d["tick"],
                           d["payload"], d["tx_id"])


@dataclass
class Block:
    index: int              # 区块高度 (genesis = 0)
    prev_hash: str          # 前序区块哈希
    tick: int               # 出块时的仿真 tick
    creator: str            # 出块者节点 ID
    transactions: list      # Transaction 列表 (空块 = [])
    block_hash: str = ""

    def __post_init__(self):
        if not self.block_hash:
            self.block_hash = _hash({
                "index": self.index, "prev_hash": self.prev_hash,
                "tick": self.tick, "creator": self.creator,
                "txs": [t.to_dict() for t in self.transactions]})

    def to_dict(self):
        return {"index": self.index, "prev_hash": self.prev_hash,
                "tick": self.tick, "creator": self.creator,
                "txs": [t.to_dict() for t in self.transactions],
                "block_hash": self.block_hash}

    @staticmethod
    def from_dict(d) -> "Block":
        return Block(d["index"], d["prev_hash"], d["tick"], d["creator"],
                     [Transaction.from_dict(t) for t in d["txs"]],
                     d["block_hash"])


def _mk_packet(ptype: str, src: str, payload: dict, ttl: int = TTL) -> dict:
    """泛洪信封: msg_id 内容哈希 + 随机盐 (同内容重发也是新包)"""
    return {"msg_id": _hash({"t": ptype, "src": src, "salt": random.random()}),
            "type": ptype, "src_node": src, "from_node": src,
            "ttl": ttl, "payload": payload}


# ================= 链上节点 =================
class ChainNode:
    """每个仿真节点内运行的一条链 + 世界状态 + 通信栈"""

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
        return len(self.chain) - 1

    @property
    def tail(self):
        return self.chain[-1]

    def state_hash(self):
        return _hash({"ws": self.world_state, "seq": self.latest_seq})

    def _seen_mark(self, mid) -> bool:
        if mid in self.seen:
            return False
        self.seen[mid] = True
        if len(self.seen) > SEEN_MAX:
            for k in list(self.seen)[:SEEN_MAX // 8]:
                self.seen.pop(k, None)
        return True

    def _relay(self, pkt):
        if pkt["ttl"] <= 1:
            return
        fwd = dict(pkt)
        fwd["ttl"] -= 1
        self.out.append(fwd)

    # ---------- 世界状态 ----------
    def _apply_tx(self, tx: Transaction) -> bool:
        if tx.seq <= self.latest_seq.get(tx.robot_id, 0):
            return False
        self.latest_seq[tx.robot_id] = tx.seq
        self.world_state[tx.robot_id] = {**tx.payload, "seq": tx.seq,
                                         "tick": tx.tick}
        return True

    def _replay_all(self):
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
        self.chain = [self.chain[0]] + blocks
        self._replay_all()
        for tx in [t for blk in blocks for t in blk.transactions]:
            self.mempool.pop(tx.tx_id, None)
        return True

    # ---------- 出块 ----------
    def try_mine(self, tick: int) -> bool:
        """轮到自己 ∧ (有交易 ∧ 间隔达标) -> 出块; 块龄超时 -> 空块推进"""
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
        self._accept(blk)
        self.out.append(_mk_packet("BLOCK", self.id, {"block": blk.to_dict()}))
        return True

    # ---------- 收包 ----------
    def handle_packet(self, pkt: dict, tick: int) -> list:
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
        tx = Transaction.from_dict(pkt["payload"]["tx"])
        if tx.tx_id in self.mempool or tx.seq <= self.latest_seq.get(tx.robot_id, 0):
            return
        self.mempool[tx.tx_id] = tx
        self._relay(pkt)

    def _on_block(self, pkt, tick: int):
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
                    "SYNC_REQ", self.id,
                    {"from_index": 0, "fork": True}))
        else:
            # 对方领先 -> 追块
            self.out.append(_mk_packet(
                "SYNC_REQ", self.id,
                {"from_index": self.height, "fork": False}))

    def _on_sync_req(self, pkt, tick: int):
        # 请求也泛洪转发 (低 TTL): 拥有更优链的远端节点同样可响应
        if pkt["ttl"] > 1:
            fwd = dict(pkt); fwd["ttl"] = min(pkt["ttl"] - 1, 8)
            self.out.append(fwd)
        src = pkt["src_node"]
        if pkt["payload"].get("fork"):
            # 整链响应: 抽样 30% + 限频, 抑制几十个节点同时要全链的风暴
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

    # ---------- 主动行为 ----------
    def emit_telemetry(self, tick: int, data: dict) -> dict:
        self.my_seq += 1
        tx = Transaction(self.id, self.my_seq, tick, data)
        self.mempool[tx.tx_id] = tx
        return _mk_packet("TX", self.id, {"tx": tx.to_dict()})

    def emit_heartbeat(self, tick: int) -> dict:
        """周期性防熵: 比我高的邻居会回 SYNC_RESP"""
        return _mk_packet("SYNC_REQ", self.id,
                          {"from_index": self.height, "fork": False})


# ================= 网络编排 =================
class BlockchainNetwork:
    """挂在 SimulationEngine 上: 提供拓扑与遥测数据, 驱动全部 ChainNode"""

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

    # ---- 拓扑: 复用引擎链路表 (遮挡/画墙/断链全部生效) ----
    def _neighbors(self, nid):
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
        n = self.eng.nodes.get(nid)
        return n is not None and n.state != "DEAD"

    def _telemetry_payload(self, nid):
        n = self.eng.nodes[nid]
        return {"x": n.x, "z": n.z, "soc": round(n.battery_soc, 1),
                "temp": round(n.temp_c, 1), "state": n.state,
                "queue": round(n.queue_pct, 1), "radio": n.radio,
                "hop": n.hop_count}

    # ---- 事件上报 (前端 EventLog 直观可见同步过程) ----
    def notify(self, type_, sev, msg):
        try:
            self.eng._emit(type_, sev, msg)
        except Exception:
            pass

    # ---- 每 tick 主循环 ----
    def step(self, tick: int):
        self._adj = {}
        emitted = []
        # 1) 投递上一 tick 的包 (一跳一 tick)
        for from_node, pkt in self.inflight:
            for nb in self._neighbors(from_node):
                if not self._alive(nb):
                    continue
                for out in self.nodes[nb].handle_packet(pkt, tick):
                    emitted.append((nb, out))
        self.inflight = emitted
        # 2) 节点主动行为: 遥测 / 心跳 / 出块 (全部错峰)
        for i, nid in enumerate(self.sorted_ids):
            if not self._alive(nid):
                continue
            node = self.nodes[nid]
            if (tick + i) % TELEMETRY_EVERY == 0:
                self.inflight.append(
                    (nid, node.emit_telemetry(tick, self._telemetry_payload(nid))))
            if (tick + i) % HEARTBEAT_EVERY == 5:
                self.inflight.append((nid, node.emit_heartbeat(tick)))
            if node.try_mine(tick):
                self.stats["blocks"] += 1
                self.inflight.append((nid, node.out.pop()))
                if self.stats["blocks"] % 6 == 0:
                    self.notify("chain_block", "info",
                                f"⛓ 链高度 {node.height} (出块 {nid}, "
                                f"含 {len(node.tail.transactions)} 笔) "
                                f"— 全网账本持续增长")

    # ---- 快照导出 (前端账本侧边栏) ----
    def export_info(self):
        per = []
        h_max = 0
        for nid in self.sorted_ids:
            nd = self.nodes[nid]
            per.append({"id": nid, "h": nd.height,
                        "sh": nd.state_hash()[:8], "mp": len(nd.mempool)})
            h_max = max(h_max, nd.height)
        # 基准 = 链顶层(h_max)多数派哈希; 父层(h_max-1)多数派哈希单独取。
        # 黄波传播期间新/旧两个合法相位 (链顶态 / 父块态) 都计入"状态一致",
        # 横幅不闪; 只有真分叉 (两相都不匹配) 才不一致。
        top, ph = {}, {}
        for r in per:
            if r["h"] == h_max:
                top[r["sh"]] = top.get(r["sh"], 0) + 1
            elif r["h"] == h_max - 1:
                ph[r["sh"]] = ph.get(r["sh"], 0) + 1
        base = max(top, key=top.get) if top else (max(ph, key=ph.get) if ph else "")
        parent_sh = max(ph, key=ph.get) if ph else ""
        world, tip = {}, None
        for nid in self.sorted_ids:
            nd = self.nodes[nid]
            if nd.height == h_max and nd.state_hash()[:8] == base:
                world = {rid: dict(st) for rid, st in nd.world_state.items()}
                tip = {"index": nd.tail.index, "creator": nd.tail.creator,
                       "tick": nd.tail.tick,
                       "txs": len(nd.tail.transactions),
                       "hash": nd.tail.block_hash[:8]}
                break
        diffs = {}
        for nid in self.sorted_ids:
            nd = self.nodes[nid]
            if nd.state_hash()[:8] != base:
                diffs[nid] = {rid: dict(st)
                              for rid, st in nd.world_state.items()}
                if len(diffs) >= 12:
                    break
        lag1 = sum(1 for r in per if r["h"] >= h_max - 1)   # 传播容差: 距顶<=1块
        return {"h_max": h_max, "n": len(self.sorted_ids),
                "aligned": sum(1 for r in per if r["h"] == h_max),
                "lag1": lag1,
                "agree": sum(1 for r in per
                             if r["sh"] == base or r["sh"] == parent_sh),
                "base_hash": base, "tip": tip, "per": per,
                "world": world, "diffs": diffs, "stats": dict(self.stats)}
