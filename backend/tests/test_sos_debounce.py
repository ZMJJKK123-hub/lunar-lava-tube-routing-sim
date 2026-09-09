# -*- coding: utf-8 -*-
"""
SOS 呼救消抖与目标冷却单测
================================
覆盖: 布防计数 (连续 SOS_ARM_TICKS 拍)/解除消抖 (连续 SOS_DISARM_TICKS 拍,
单拍闪回不解除)/计数器复位 (解除后可重新布防)/听测尊重目标冷却表/
_target_recovered 三态门 (消抖未过=仍在救 / 已解除或刚落钉=收队)。
运行: cd backend && python -m unittest tests.test_sos_debounce
"""
import unittest   # 标准库: 测试框架

from sim.robot.constants import SOS_ARM_TICKS, SOS_DISARM_TICKS   # 布防/消抖拍数
from sim.robot.senses import SenseMixin   # 被测: _hear 冷却听测
from sim.robot.sos import SosMixin        # 被测: 布防/解除消抖/恢复判定


class _Node:   # 测试桩: 呼救判定所需的最小节点面
    def __init__(self, nid, x=0.0, z=0.0, state="ALIVE"):
        self.id, self.x, self.y, self.z = nid, x, 0.0, z
        self.role, self.state = "node", state


class _Eng:    # 测试桩: 引擎公开状态的最小面 (routes/nodes/tick/事件)
    def __init__(self, nodes):
        self.nodes = {n.id: n for n in nodes}
        self.routes = {nid: {"hop_count": -1} for nid in self.nodes}
        self.sink_id = "NODE-00"
        self.tick = 0

    def _emit(self, *a, **k):      # 事件总线桩: 丢弃
        pass

    def _zh(self, nid):            # 别名桩: 原样返回
        return nid

    def vis_packet(self, *a, **k):   # 信标呈现桩: 丢弃
        pass


class _Robot(SosMixin, SenseMixin):   # 测试桩: 判定层的宿主 (无运动/无引擎耦合)
    def __init__(self, eng):
        self.eng = eng
        self.node = _Node("ROBOT")
        self.sos_active = set()
        self._iso, self._rec = {}, {}
        self._checked_until = {}
        self._deployed_at = -1

    def _los_clear(self, p1, p2):  # 视线桩: 无遮挡
        return True


def _world(hop=-1):
    """单节点世界: NODE-01 (与桩机器人同点, 信标可达圈恒真)。"""
    eng = _Eng([_Node("NODE-01")])
    eng.routes["NODE-01"] = {"hop_count": hop}
    return eng, _Robot(eng)


class TestSosDebounce(unittest.TestCase):
    """布防/解除的对称消抖矩阵。"""

    def test_arm_after_consecutive_misses(self):
        """连续失联恰在第 SOS_ARM_TICKS 拍布防, 此前不呼救。"""
        eng, rb = _world()
        for t in range(1, SOS_ARM_TICKS):
            rb._update_sos(t)
            self.assertNotIn("NODE-01", rb.sos_active)
        rb._update_sos(SOS_ARM_TICKS)
        self.assertIn("NODE-01", rb.sos_active)

    def test_flap_recovery_keeps_sos(self):
        """布防后恢复 < 消抖拍数: 不解除 (治"派去-召回"拉锯), 计数器复位。"""
        eng, rb = _world()
        for t in range(1, SOS_ARM_TICKS + 1):
            rb._update_sos(t)
        eng.routes["NODE-01"] = {"hop_count": 0}
        for t in range(SOS_ARM_TICKS + 1, SOS_ARM_TICKS + SOS_DISARM_TICKS):
            rb._update_sos(t)
            self.assertIn("NODE-01", rb.sos_active)   # 消抖窗口内不解除
        eng.routes["NODE-01"] = {"hop_count": -1}
        rb._update_sos(SOS_ARM_TICKS + SOS_DISARM_TICKS)
        self.assertIn("NODE-01", rb.sos_active)       # 闪回后仍布防
        self.assertEqual(rb._rec, {})                 # 恢复计数已清零

    def test_disarm_then_rearm_counters_reset(self):
        """连续恢复满消抖拍数才解除; 解除后失联可重新布防 (计数器已复位)。"""
        eng, rb = _world()
        for t in range(1, SOS_ARM_TICKS + 1):
            rb._update_sos(t)
        eng.routes["NODE-01"] = {"hop_count": 0}
        for t in range(SOS_ARM_TICKS + 1, SOS_ARM_TICKS + SOS_DISARM_TICKS):
            rb._update_sos(t)
        rb._update_sos(SOS_ARM_TICKS + SOS_DISARM_TICKS)   # 恢复满 N 拍
        self.assertNotIn("NODE-01", rb.sos_active)
        self.assertEqual(rb._rec, {})
        eng.routes["NODE-01"] = {"hop_count": -1}
        for t in range(SOS_ARM_TICKS + SOS_DISARM_TICKS + 1,
                       SOS_ARM_TICKS + SOS_DISARM_TICKS + SOS_ARM_TICKS):
            rb._update_sos(t)
            self.assertNotIn("NODE-01", rb.sos_active)
        rb._update_sos(SOS_ARM_TICKS * 2 + SOS_DISARM_TICKS)
        self.assertIn("NODE-01", rb.sos_active)       # 重新布防成功


class TestHearCooldown(unittest.TestCase):
    """实时听测尊重 _giveup 登记的目标冷却表。"""

    def test_cooled_target_skipped(self):
        """冷却中的呼救目标被跳过; 解除冷却后按最近者优先恢复可听。"""
        eng = _Eng([_Node("NODE-01", x=50.0), _Node("NODE-02", x=100.0)])
        rb = _Robot(eng)
        rb.sos_active = {"NODE-01", "NODE-02"}
        rb._checked_until = {"NODE-01": 100}          # NODE-01 冷却至 t100
        eng.tick = 50
        self.assertEqual(rb._hear()[0], "NODE-02")    # 跳过冷却目标
        rb._checked_until = {}                        # 冷却结束
        self.assertEqual(rb._hear()[0], "NODE-01")    # 最近者优先


class TestTargetRecoveredGate(unittest.TestCase):
    """救援层"真恢复"判定门: 消抖未过不收队。"""

    def test_gate_three_states(self):
        """可达但呼救未解除=仍在救; 已解除或刚落钉=收队; 失联=不在条件内。"""
        eng, rb = _world(hop=0)
        rb.sos_active = {"NODE-01"}
        rb._deployed_at = -1
        eng.tick = 100
        self.assertFalse(rb._target_recovered("NODE-01"))  # 消抖未过
        rb.sos_active = set()
        self.assertTrue(rb._target_recovered("NODE-01"))   # 呼救已解除
        rb.sos_active = {"NODE-01"}
        rb._deployed_at = eng.tick - 1                     # 刚落钉完事
        self.assertTrue(rb._target_recovered("NODE-01"))
        eng.routes["NODE-01"] = {"hop_count": -1}          # 仍失联
        self.assertFalse(rb._target_recovered("NODE-01"))


if __name__ == "__main__":
    unittest.main()
