# -*- coding: utf-8 -*-
"""
几何原语: 2D 线段相交 / 3D 线段-球相交 (LOS 遮挡判定的数学基座)
====================================================================
职责: 纯函数几何库 —— 无状态、无副作用, 供世界层 (LOS 重算) 与网络层
(遮挡成因标注) 共用; 机器人的 2D 简化版在 robot/motion.py 自持。
"""


def _cross(ox, oz, ax, az, bx, bz):
    """2D 叉积 (b-o) x (a-o) 的 z 分量 —— 线段相交判定的基元。

    Args: o/原点, a/b 两点 (各拆为 x,z 分量)。Returns: float, 有符号面积2倍。
    Globals Used: None。Calls: None。
    """
    return (bx - ox) * (az - oz) - (ax - ox) * (bz - oz)


def _seg2d_intersect(p1, p2, w1, w2) -> bool:
    """2D 线段相交判定 (严格叉积法): 节点连线 vs 墙体。
    d1/d2: 墙两端点分别在连线 p1->p2 两侧; d3/d4: 线两端点分别在墙 w1->w2 两侧;
    双侧同时成立 = 真穿越。端点恰触墙 (d=0) 或共线不算相交 (严格判定)。

    Args: p1/p2: 连线两端 (x,z); w1/w2: 墙两端 (x,z)。
    Returns: bool。Globals Used: None。Calls: _cross。
    """
    d1 = _cross(p1[0], p1[1], p2[0], p2[1], w1[0], w1[1])
    d2 = _cross(p1[0], p1[1], p2[0], p2[1], w2[0], w2[1])
    d3 = _cross(w1[0], w1[1], w2[0], w2[1], p1[0], p1[1])
    d4 = _cross(w1[0], w1[1], w2[0], w2[1], p2[0], p2[1])
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _seg_blocked_by_sphere(p1, p2, c, R) -> bool:
    """3D 线段是否穿入球体 (点到线段最近距离 < R) —— 巨石/巨柱遮挡判定。

    Args: p1/p2: 线段两端 (x,y,z); c: 球心 (x,y,z); R: 球半径。
    Returns: bool (相切不算, 严格小于)。Globals Used: None。Calls: None。
    """
    dx, dy, dz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
    fx, fy, fz = p1[0] - c[0], p1[1] - c[1], p1[2] - c[2]
    a = dx * dx + dy * dy + dz * dz
    if a < 1e-9:
        return fx * fx + fy * fy + fz * fz < R * R   # 零长线段: 点在球内则挡
    tt = max(0.0, min(1.0, -(fx * dx + fy * dy + fz * dz) / a))
    qx, qy, qz = fx + tt * dx, fy + tt * dy, fz + tt * dz
    return qx * qx + qy * qy + qz * qz < R * R
