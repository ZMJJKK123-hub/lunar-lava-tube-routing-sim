# -*- coding: utf-8 -*-
"""引擎冒烟: 世界构建 / 快照契约 / 画墙拓扑 / 同种子重置确定性 / 长跑收敛。"""
import unittest   # 标准库: 测试框架

from sim.config import SINK_ID   # 汇聚节点 ID
from sim.engine import SimulationEngine
from tests.helper import step_ticks

SNAP_KEYS = ("tick", "nodes", "links", "routes", "traffic", "robot",
             "transport", "packets", "chain", "stats", "events", "wave")


class TestWorldBoot(unittest.TestCase):
    """世界构建契约"""

    @classmethod
    def setUpClass(cls):
        """共享引擎 (世界构建一次)"""
        cls.eng = SimulationEngine()

    def test_sixty_nodes_and_coverage(self):
        """60 根通信桩; sink 存在; 覆盖率达标 (≥96)"""
        self.assertEqual(len(self.eng.nodes), 60)
        self.assertIn(SINK_ID, self.eng.nodes)
        self.assertGreaterEqual(self.eng._coverage(), 96.0)

    def test_snapshot_contract(self):
        """快照包含全部前端消费的字段"""
        snap = self.eng.snapshot()
        for k in SNAP_KEYS:
            self.assertIn(k, snap, f"snapshot 缺字段 {k}")
        self.assertIn("coverage_pct", snap["stats"])
        self.assertIn("h_max", snap["chain"])


class TestWallsAndReset(unittest.TestCase):
    """上帝操作: 画墙切拓扑 / 重置确定性"""

    def setUp(self):
        """每例全新世界 (画墙/重置是破坏性操作)"""
        self.eng = SimulationEngine()

    def test_wall_cuts_links(self):
        """贯通墙切断链路; 拆墙恢复 (只统计静态节点边: 机器人在移动)"""
        def fixed_links():
            return sum(1 for k in self.eng.links if "ROBOT" not in k)
        before = fixed_links()
        c = self.eng.chambers[0]
        self.eng.add_wall(c["x"], c["z"] - c["rz"], c["x"], c["z"] + c["rz"])
        self.assertLess(fixed_links(), before)    # 有边被切断
        self.eng.remove_wall(0)
        self.assertEqual(fixed_links(), before)

    def test_reset_deterministic(self):
        """同种子重置: 巨石位置逐块一致 (世界完全可复现)"""
        rocks0 = [(o["x"], o["z"], o["r"]) for o in self.eng.obstacles]
        self.eng.reset()
        rocks1 = [(o["x"], o["z"], o["r"]) for o in self.eng.obstacles]
        self.assertEqual(rocks0, rocks1)
        self.assertEqual(self.eng.tick, 0)


class TestLongRun(unittest.TestCase):
    """长跑收敛: 链增长 / 存活 / 模式机回到非自愈态"""

    def test_600_ticks_converge(self):
        """600 tick 后: 链高度显著增长, 覆盖率仍达标"""
        eng = SimulationEngine()
        step_ticks(eng, 600)
        info = eng.chain_net.export_info()
        self.assertGreaterEqual(info["h_max"], 40)     # ~1块/12tick 节奏
        self.assertGreaterEqual(info["agree"], info["na"] * 0.8)   # 前缀一致≥80%
        self.assertGreaterEqual(eng._coverage(), 90.0)
        self.assertIn(eng.mode, ("STABLE", "CONVERGED"))


if __name__ == "__main__":
    unittest.main()
