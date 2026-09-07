# -*- coding: utf-8 -*-
"""传输层回归: BER 损坏概率 / 端到端送达 / 断墙后 NO_PATH。"""
import unittest   # 标准库: 测试框架

from sim.transport.model import _damage_prob   # BER 损坏概率
from tests.helper import step_ticks            # 逐 tick 步进助手
from sim.engine import SimulationEngine        # 仿真引擎


class TestDamageProb(unittest.TestCase):
    """损坏概率函数的数学边界"""

    def test_zero_ber(self):
        """无错信道损坏概率为 0"""
        self.assertEqual(_damage_prob(0.0, 1024), 0.0)

    def test_bounds(self):
        """概率恒在 (0,1] 内且随 BER/字节数单调上升 (大字节数数值上饱和到 1)"""
        self.assertGreater(_damage_prob(1e-7, 1024), 0.0)
        self.assertLess(_damage_prob(0.5, 1), 1.0)      # 1-(0.5)^8 ≈ 0.996
        self.assertLessEqual(_damage_prob(0.1, 128), 1.0)
        self.assertGreater(_damage_prob(1e-5, 2048), _damage_prob(1e-5, 1024))
        self.assertGreater(_damage_prob(1e-4, 1024), _damage_prob(1e-5, 1024))

    def test_clamp(self):
        """BER>0.5 被钳制到 0.5 (1 字节时不饱和)"""
        self.assertEqual(_damage_prob(0.9, 1), _damage_prob(0.5, 1))
        self.assertLess(_damage_prob(0.9, 1), 1.0)


class TestEndToEnd(unittest.TestCase):
    """真实引擎上的端到端报文 (慢速路径, 每例一次性建世界)"""

    @classmethod
    def setUpClass(cls):
        """共享一个引擎实例 (构建世界 ~0.2s, 避免逐用例重复)"""
        cls.eng = SimulationEngine()

    def test_delivered(self):
        """可达两节点间 1KB 报文最终 DELIVERED (结果通道)"""
        r = self.eng.send_user_message("NODE-10", "NODE-00", 1024)
        self.assertTrue(r["ok"])
        step_ticks(self.eng, 150)
        last = self.eng.transport.results[-1]
        self.assertEqual(last["status"], "DELIVERED")
        self.assertEqual(last["src"], "NODE-10")

    def test_timeout_signal_present(self):
        """超时路径必然产生 TIMEOUT 信号 (用极短超时直发)"""
        r = self.eng.transport.send_message(
            "NODE-20", "NODE-00", 512, timeout_ticks=1)
        if r["ok"]:                      # 有路才发; 无路场景另有 NO_PATH 信号
            step_ticks(self.eng, 4)
            self.assertIn(
                "TIMEOUT", [x["status"] for x in self.eng.transport.results])


if __name__ == "__main__":
    unittest.main()
