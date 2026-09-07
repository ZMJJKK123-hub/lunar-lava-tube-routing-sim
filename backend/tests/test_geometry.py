# -*- coding: utf-8 -*-
"""几何原语回归: 线段相交 / 线段-圆相交 (引擎与机器人两套实现口径一致)。"""
import unittest   # 标准库: 测试框架

from sim.engine.world import _seg2d_intersect, _seg_blocked_by_sphere
from sim.robot.motion import _seg_circle_hit, _seg2d_hit


class TestSegIntersect(unittest.TestCase):
    """2D 线段相交: 严格双侧判定 (真穿越/平行/端点接触)"""

    def test_true_cross(self):
        """十字交叉必相交"""
        self.assertTrue(_seg2d_intersect((0, 0), (10, 10), (0, 10), (10, 0)))

    def test_parallel_no_hit(self):
        """平行线不相交"""
        self.assertFalse(_seg2d_intersect((0, 0), (10, 0), (0, 5), (10, 5)))

    def test_disjoint(self):
        """远离的两段不相交"""
        self.assertFalse(_seg2d_intersect((0, 0), (1, 1), (5, 5), (9, 9)))

    def test_endpoint_touch_not_counted(self):
        """端点恰触不算严格相交 (d=0 分支)"""
        self.assertFalse(_seg2d_intersect((0, 0), (10, 0), (10, 0), (10, 5)))


class TestSegSphere(unittest.TestCase):
    """3D 线段-球相交: 巨石遮挡判定的几何根基"""

    def test_through_center(self):
        """穿心必挡"""
        self.assertTrue(_seg_blocked_by_sphere((-10, 0, 0), (10, 0, 0), (0, 0, 0), 5))

    def test_far_miss(self):
        """远离不挡"""
        self.assertFalse(_seg_blocked_by_sphere((-10, 0, 0), (10, 0, 0), (0, 0, 50), 5))

    def test_tangent_outside(self):
        """最近点恰在球外 (距离==R) 不算穿入 (严格小于)"""
        self.assertFalse(_seg_blocked_by_sphere((-10, 0, 0), (10, 0, 0), (0, 5, 0), 5))

    def test_degenerate_point_inside(self):
        """零长线段 (a<1e-9 分支): 点在球内则挡"""
        self.assertTrue(_seg_blocked_by_sphere((1, 1, 1), (1, 1, 1), (0, 0, 0), 5))

    def test_robot_2d_consistent(self):
        """机器人 2D 圆判定与引擎 3D 球判定同口径 (y=0 平面)"""
        self.assertTrue(_seg_circle_hit((0, 0), (10, 0), (5, 0), 2))
        self.assertFalse(_seg_circle_hit((0, 0), (10, 0), (50, 50), 2))
        self.assertTrue(_seg2d_hit((0, 0), (10, 10), (0, 10), (10, 0)))


if __name__ == "__main__":
    unittest.main()
