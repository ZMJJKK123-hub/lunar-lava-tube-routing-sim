# -*- coding: utf-8 -*-
"""
账本数据模型: 常量 + 交易/区块实体 + 哈希与泛洪信封
====================================================
区块链全网状态同步模拟 (统一排他调度 PoA + 泛洪传播 + 追块/分叉愈合)。
核心设计 (全部只依赖链内数据, 出块方与校验方用同一公式零通信复算):
- 统一排他调度 (时间窗轮转 PoA): 出块人 = sorted_ids[(index + win) % N],
  win = tick // SKIP_AFTER —— 每个 (高度, 时间窗) 组合全网唯一出块人,
  天然无并发分叉; Leader 阵亡时其窗口自动空过, 轮序后继在下一窗口顶上。
- 出块条件: 轮到自己 ∧ (mempool 非空 ∧ 块龄 ≥ MIN_BLOCK_GAP); 长时间
  无交易且块龄 ≥ SKIP_AFTER + MIN_BLOCK_GAP 时轮到的节点出"空块"推进。
- 分叉愈合: 同高度竞争块 (分区两边各自出块所致) -> 请求对方完整链
  (持有者抽样 40% + 限频响应, 链长几十块一次给清), 从创世整链重验, 按
  "更高者胜 / 同高尾部哈希小者胜" 的全序规则择优 —— 双方对称执行,
  必然收敛到同一条链。
- 追块: 收到 index > 本地高度的块, 或周期性 SYNC_REQ 心跳 (错峰),
  邻居按请求高度回批 (<= SYNC_BATCH 块), 逐块验证补链。
- 泛洪: seen LRU 缓存 + TTL 抑制风暴, 一跳一 tick;
  SYNC_RESP 非目标节点也转发 (否则响应到不了远端)。
- 世界状态: 链 = 有序日志; world_state 只在上链时按序重放,
  tx.seq > latest_seq[robot] 才应用 (防乱序/防重放)。
"""
import hashlib  # 标准库: SHA-256 内容哈希 (交易/区块/状态指纹)
import json     # 标准库: 规范化 JSON 序列化 (哈希前定序) + 字节计账
import random   # 标准库: 泛洪信封随机盐 (同内容重发也是新包)

# ---------------- 账本可调常量 ----------------
TTL = 12                    # 泛洪包生存跳数
SEEN_MAX = 4096             # 已见消息缓存上限 (LRU)
MAX_TX_PER_BLOCK = 16       # 单块打包交易上限 (每窗口产~12笔, 留余量)
MIN_BLOCK_GAP = 12          # 两块最小间隔 tick (12 tick = 传播约6tick, 留全绿稳定期)
SKIP_AFTER = 12             # 块龄达到该值 -> 任何节点可出空块推进 (Leader 阵亡兜底)
TELEMETRY_EVERY = 60        # 每节点遥测周期 (错峰; 与出块吞吐平衡防积压)
HEARTBEAT_EVERY = 20        # 周期性 SYNC_REQ 防熵心跳 (错峰)
SYNC_BATCH = 12             # 普通追块单批最大区块数
RESP_CD = 5                 # 追块响应限频 (同一请求方, tick)
FORK_RESP_CD = 6            # 整链响应限频 (同一请求方, tick)
FORK_REQ_CD = 8             # fork 请求重发限频 (自身, tick)
SYNC_PREFIX_BLOCKS = 3      # 前缀一致容差: 链尾落在基准链最近 K 块内算一致 (允许落后 K-1)
GENESIS_PREV = "0" * 64     # 创世块的前序哈希占位


def _canonical(obj) -> str:
    """规范化序列化: 键排序 + 紧凑分隔符 (哈希确定性的前提)"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _hash(obj) -> str:
    """对象 SHA-256 摘要 (hex)"""
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


# ================= 数据模型 =================
class Transaction:
    """遥测交易: 某节点某时刻的一次状态上报。

    职责: 承载一笔不可变的链上数据 (构造即定 tx_id), 提供泛洪序列化。

    属性: robot_id=来源节点; seq=该节点单调递增序号; tick=产生时刻;
    payload=遥测字典 (坐标/SoC/温度/状态/队列/电台); tx_id=内容哈希。

    调用链: ChainNode.emit_telemetry 构造 -> _mk_packet 泛洪 ->
    SyncMixin._on_tx 反序列化 -> try_mine 打包进块 -> _apply_tx 重放世界状态。
    """

    def __init__(self, robot_id: str, seq: int, tick: int, payload: dict,
                 tx_id: str = ""):
        self.robot_id = robot_id           # 来源节点
        self.seq = seq                     # 该节点遥测单调递增序列号
        self.tick = tick                   # 产生时的仿真 tick
        self.payload = payload             # 遥测: 坐标/SoC/温度/状态/队列/电台...
        if not tx_id:
            tx_id = _hash({"robot_id": robot_id, "seq": seq,
                           "tick": tick, "payload": payload})
        self.tx_id = tx_id

    def to_dict(self):
        """序列化 (泛洪传输与哈希复算共用同一表示)。

        Returns: dict, 五字段完整表示。Globals Used: None。Calls: None。
        """
        return {"robot_id": self.robot_id, "seq": self.seq, "tick": self.tick,
                "payload": self.payload, "tx_id": self.tx_id}

    @staticmethod
    def from_dict(d) -> "Transaction":
        """从泛洪包载荷反序列化。

        Args: d: to_dict 的输出 dict。Returns: Transaction 实例 (tx_id 沿用不重算)。
        Globals Used: None。Calls: None。
        """
        return Transaction(d["robot_id"], d["seq"], d["tick"],
                           d["payload"], d["tx_id"])


class Block:
    """区块: 有序日志的一个格子。

    职责: 承载一批交易的不可变容器 (构造即定 block_hash), 提供泛洪序列化。

    属性: index=高度 (genesis=0); prev_hash=前序哈希; tick=出块时刻;
    creator=出块者; transactions=交易列表 (空块=[]); block_hash=内容哈希。

    调用链: ChainNode.try_mine 构造 -> BLOCK 泛洪 -> _on_block 校验上链 /
    _adopt_chain 整链重验; network._drive_nodes 周期驱动出块。
    """

    def __init__(self, index: int, prev_hash: str, tick: int, creator: str,
                 transactions: list, block_hash: str = ""):
        self.index = index                 # 区块高度 (genesis = 0)
        self.prev_hash = prev_hash         # 前序区块哈希
        self.tick = tick                   # 出块时的仿真 tick
        self.creator = creator             # 出块者节点 ID
        self.transactions = transactions   # Transaction 列表 (空块 = [])
        if not block_hash:
            block_hash = _hash({
                "index": index, "prev_hash": prev_hash,
                "tick": tick, "creator": creator,
                "txs": [t.to_dict() for t in transactions]})
        self.block_hash = block_hash

    def to_dict(self):
        """序列化 (泛洪传输与哈希复算共用同一表示)。

        Returns: dict, 六字段完整表示 (txs 为逐笔交易的 to_dict)。Globals
        Used: None。Calls: Transaction.to_dict。
        """
        return {"index": self.index, "prev_hash": self.prev_hash,
                "tick": self.tick, "creator": self.creator,
                "txs": [t.to_dict() for t in self.transactions],
                "block_hash": self.block_hash}

    @staticmethod
    def from_dict(d) -> "Block":
        """从泛洪包载荷反序列化。

        Args: d: to_dict 的输出 dict。Returns: Block 实例 (哈希沿用不重算)。
        Globals Used: None。Calls: Transaction.from_dict。
        """
        return Block(d["index"], d["prev_hash"], d["tick"], d["creator"],
                     [Transaction.from_dict(t) for t in d["txs"]],
                     d["block_hash"])


def _mk_packet(ptype: str, src: str, payload: dict, ttl: int = TTL) -> dict:
    """泛洪信封: msg_id 内容哈希 + 随机盐 (同内容重发也是新包)"""
    return {"msg_id": _hash({"t": ptype, "src": src, "salt": random.random()}),
            "type": ptype, "src_node": src, "from_node": src,
            "ttl": ttl, "payload": payload}
