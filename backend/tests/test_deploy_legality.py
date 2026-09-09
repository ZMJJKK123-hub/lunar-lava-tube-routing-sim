# -*- coding: utf-8 -*-
"""
落点合法性与合法择点单测
================================
覆盖: _deploy_ok_at 三条件 (出管桩为恒真不测/压石/钉距) / _deploy_ok 跟随
机器人当前位置 / _legal_spot 只挑"合法且罩得住目标 (0.6x 半径)"的历史点位
(治: 挑完最佳点位才发现站的位置放不了钉)。
运行: cd backend && python -m unittest tests.test_deploy_legality
"""
import unittest   # 标准库: 测试框架

from sim.robot.assist import AssistMixin            # 被测: _legal_spot
from sim.robot.constants import DEPLOY_GAP          # 钉距
from sim.robot.deploy import DeployMixin            # 被测: _deploy_ok_at/_deploy_ok


class _Node:   # 测试桩: 落点判定所需的最小节点面
    def __init__(self, nid, x, z, role="node"):
        self.id, self.x, self.z, self.role = nid, x, z, role


class _Eng:    # 测试桩: obstacles/nodes/tick (_in_tube 恒真)
    def __init__(self, obstacles=(), beacons=()):
        self.obstacles = list(obstacles)
        self.nodes = {b.id: b for b in beacons}
        self.tick = 0

    def _in_tube(self, p):
        return True


class _Robot(AssistMixin, DeployMixin):   # 测试桩: 判定层宿主
    def __init__(self, eng, x=0.0, z=0.0):
        self.eng = eng
        self.node = _Node("ROBOT", x, z, "robot")
        self.trail = []


class TestDeployOkAt(unittest.TestCase):
    """点位合法性: 压石禁地与钉距间隔。"""

    def test_rock_and_gap_conditions(self):
        eng = _Eng(obstacles=[{"x": 500.0, "z": 0.0, "r": 60.0}],
                   beacons=[_Node("BEACON-01", -500.0, 0.0, "beacon")])
        rb = _Robot(eng, 0.0, 0.0)
        self.assertTrue(rb._deploy_ok_at(0, 0))                 # 干净区
        self.assertFalse(rb._deploy_ok_at(500, 0))              # 压石 (r+30 内)
        self.assertTrue(rb._deploy_ok_at(500, 100))             # 石头禁区外
        self.assertFalse(rb._deploy_ok_at(-500, 0))             # 犯钉距 (<100m)
        self.assertTrue(rb._deploy_ok_at(-500 - DEPLOY_GAP, 0))  # 钉距边界之外
        rb.node.x, rb.node.z = 500.0, 0.0
        self.assertFalse(rb._deploy_ok())                       # 跟随当前位置


class TestLegalSpot(unittest.TestCase):
    """合法择点: 禁地/远点不入选, 只剩合法近点; 全禁则 None (放弃有据)。"""

    def test_filters_illegal_and_far(self):
        eng = _Eng(obstacles=[{"x": 100.0, "z": 0.0, "r": 60.0}])
        rb = _Robot(eng)
        rb.trail = [
            (100.0, 0.0, True, 9, 0),      # 压石禁地: 可见数再高也不选
            (0.0, 50.0, True, 5, 1),       # 合法且罩得住目标
            (0.0, 500.0, True, 9, 2),      # 超出 0.6x 半径: 钉罩不住目标
        ]
        spot = rb._legal_spot((0.0, 0.0))
        self.assertIsNotNone(spot)
        self.assertEqual((spot[0], spot[1], spot[2]), (0.0, 50.0, 5))

    def test_all_illegal_returns_none(self):
        eng = _Eng(obstacles=[{"x": 0.0, "z": 0.0, "r": 60.0}])
        rb = _Robot(eng)
        rb.trail = [(0.0, 0.0, True, 9, 0)]   # 唯一历史点在禁地
        self.assertIsNone(rb._legal_spot((0.0, 0.0)))


if __name__ == "__main__":
    unittest.main()
