# -*- coding: utf-8 -*-
"""账本回归: 统一排出调度 / 出块权校验 / 整链择优收敛 / 追块。"""
import unittest   # 标准库: 测试框架

from sim.blockchain.model import SKIP_AFTER, Block, Transaction
from sim.blockchain.chain_node import ChainNode

IDS = [f"N{i:02d}" for i in range(6)]   # 6 节点共识名单


def _genesis():
    """全同源创世块"""
    return Block(index=0, prev_hash="0" * 64, tick=0,
                 creator="GENESIS", transactions=[])


def _miner_for(index: int, tick: int) -> str:
    """统一排他调度公式 (与实现同一口径)"""
    return IDS[(index + tick // SKIP_AFTER) % len(IDS)]


def _mine(cn: ChainNode, tick: int, txs=None) -> Block:
    """按公式由'正确出块人'构造衔接 cn 链尾的下一块"""
    nxt = cn.height + 1
    return Block(index=nxt, prev_hash=cn.tail.block_hash, tick=tick,
                 creator=_miner_for(nxt, tick), transactions=txs or [])


class TestScheduling(unittest.TestCase):
    """统一排他调度: (高度, 时间窗) -> 全网唯一出块人"""

    def test_validator_accepts_correct_miner(self):
        """公式指定的出块人 -> _valid_next 通过"""
        cn = ChainNode(IDS[0], IDS, _genesis())
        blk = _mine(cn, tick=SKIP_AFTER)          # win=1
        self.assertTrue(cn._valid_next(blk))

    def test_validator_rejects_wrong_miner(self):
        """非公式出块人 -> 拒绝 (零通信复算)"""
        cn = ChainNode(IDS[0], IDS, _genesis())
        nxt = 1
        wrong = next(i for i in IDS if i != _miner_for(nxt, SKIP_AFTER))
        blk = Block(index=nxt, prev_hash=cn.tail.block_hash, tick=SKIP_AFTER,
                    creator=wrong, transactions=[])
        self.assertFalse(cn._valid_next(blk))

    def test_window_rotation(self):
        """时间窗轮转: 同一高度在相邻窗口的出块人不同 (Leader 阵亡可跳过)"""
        w1 = _miner_for(1, SKIP_AFTER)
        w2 = _miner_for(1, SKIP_AFTER * 2)
        self.assertNotEqual(w1, w2)


class TestAdoptChain(unittest.TestCase):
    """整链择优: 更长者胜 / 同高尾部哈希小者胜 (确定性收敛)"""

    def _two_chains(self):
        """构造两条各自合法的 1..2 高度竞争链"""
        a, b = ChainNode(IDS[0], IDS, _genesis()), ChainNode(IDS[1], IDS, _genesis())
        a1 = _mine(a, SKIP_AFTER, [Transaction("N00", 1, 1, {"soc": 1})])
        a._accept(a1)
        b1 = _mine(b, SKIP_AFTER * 2, [Transaction("N01", 1, 1, {"soc": 2})])
        b._accept(b1)
        return a, b

    def test_longer_chain_wins(self):
        """分叉愈合: 更长的链被采纳 (世界状态重放)"""
        a, b = self._two_chains()
        a2 = _mine(a, SKIP_AFTER * 3)
        a._accept(a2)                              # _accept 无返回值 (上链即成功)
        self.assertEqual(a.height, 2)
        # b 收到 a 的整链 (块1+块2) -> 采纳并重放
        adopted = b._adopt_chain(a.chain[1:])
        self.assertTrue(adopted)
        self.assertEqual(b.height, a.height)
        self.assertIn("N00", b.world_state)      # 重放后可见 a 链交易

    def test_tie_break_deterministic(self):
        """同高: 尾部哈希小者胜 —— 双方对称执行必然收敛"""
        a, b = self._two_chains()
        if a.tail.block_hash <= b.tail.block_hash:
            low, high = a, b
        else:
            low, high = b, a
        should = low.tail.block_hash < high.tail.block_hash   # 采纳前取期望 (采纳会改写 high)
        self.assertTrue(high._adopt_chain(low.chain[1:]) is should)
        self.assertFalse(low._adopt_chain(high.chain[1:]))


class TestCatchup(unittest.TestCase):
    """追块: 心跳/落后节点经 SYNC_RESP 连续补链"""

    def test_sync_resp_catchup(self):
        """落后节点收到批次响应 -> _accept 补链追平"""
        fast, slow = ChainNode(IDS[0], IDS, _genesis()), ChainNode(IDS[1], IDS, _genesis())
        for k in range(3):                        # fast 独自长 3 块
            blk = _mine(fast, SKIP_AFTER * (k + 1))
            self.assertTrue(fast._valid_next(blk))
            fast._accept(blk)
        self.assertEqual(fast.height, 3)
        # 模拟 slow 收到 fast 的 SYNC_RESP 批次 (from_index=0)
        blocks = [Block.from_dict(b.to_dict()) for b in fast.chain[1:]]
        accepted = 0
        for blk in blocks:
            if slow._valid_next(blk):
                slow._accept(blk)
                accepted += 1
            else:
                break
        self.assertEqual(accepted, 3)
        self.assertEqual(slow.height, fast.height)
        self.assertEqual(slow.tail.block_hash, fast.tail.block_hash)


class TestReplay(unittest.TestCase):
    """世界状态重放: seq 单调, 旧交易不回放"""

    def test_seq_monotonic(self):
        """同节点旧 seq 交易被拒 (防乱序/防重放)"""
        cn = ChainNode("N00", IDS, _genesis())
        t2 = Transaction("N05", 2, 10, {"soc": 50})
        t1 = Transaction("N05", 1, 5, {"soc": 60})
        self.assertTrue(cn._apply_tx(t2))
        self.assertFalse(cn._apply_tx(t1))        # 旧 seq 拒绝
        self.assertEqual(cn.world_state["N05"]["soc"], 50)


if __name__ == "__main__":
    unittest.main()
