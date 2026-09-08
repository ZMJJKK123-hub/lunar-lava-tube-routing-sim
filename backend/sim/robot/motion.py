# -*- coding: utf-8 -*-
"""
机器人运动学: 2D 几何检测 + 贴墙避障移动 + 路点巡逻 (MotionMixin)
====================================================================
职责:
- 模块级几何原语: 叉积 / 线段相交 / 线段-圆相交 (与引擎墙体判定同一算法);
- MotionMixin: PatrolRobot 的全部"身体能力" —— 视线判定/本体碰撞判定/
  贴墙滑动移动/随机路点/绕障环形搜索/面包屑记录。
依赖: 只读引擎的 walls/obstacles/pillar_spheres/chambers 数据 (不反向被依赖)。
"""
import math    # 标准库: 三角/距离计算, 支撑全部几何原语
import random  # 标准库: 随机路点采样 (极坐标均匀撒点)

from .constants import (HISTORIC_SPOT_DECAY, RANGE,   # 历史择点半衰期/通信半径
                        SCOUT_RADIUS_MAX, SCOUT_RADIUS_MIN,   # 侦察环带外/内半径
                        SCOUT_WAYPOINTS, TRAIL_MAX, SPEED)    # 采样路点数/轨迹上限/速度


def plan_robot_path(p_from, p_to):
    """路径规划占位符: 当前直线返回 [起点, 终点]。
    保持签名 (起点, 终点) -> [路径点序列], 可整体替换为 A*/RCSPA/势场等。

    Args: p_from/p_to: (x, z) 世界坐标点。Returns: list[(x, z)] 路径点序列。
    Globals Used: None。Calls: None。
    """
    return [tuple(p_from), tuple(p_to)]


# ---------- 模块私有几何 (与引擎解耦: 只读引擎的墙/石/腔室数据) ----------
def _cross2(o, a, b):
    """2D 叉积 (a-o) x (b-o)"""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _seg2d_hit(p1, p2, w1, w2) -> bool:
    """2D 线段相交 (标准双侧判定, 与引擎墙体判定同一算法)"""
    d1, d2 = _cross2(p1, p2, w1), _cross2(p1, p2, w2)
    d3, d4 = _cross2(w1, w2, p1), _cross2(w1, w2, p2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _seg_circle_hit(p1, p2, c, r) -> bool:
    """线段是否进入圆内 (点到线段最近距离 < r)"""
    dx, dz = p2[0] - p1[0], p2[1] - p1[1]
    fx, fz = p1[0] - c[0], p1[1] - c[1]
    a = dx * dx + dz * dz
    if a < 1e-9:
        return fx * fx + fz * fz < r * r
    t = max(0.0, min(1.0, -(fx * dx + fz * dz) / a))
    qx, qz = fx + t * dx, fz + t * dz
    return qx * qx + qz * qz < r * r


class MotionMixin:
    """职责: PatrolRobot 的运动学能力混入。

    属性要求 (由 PatrolRobot.__init__ 提供):
    - eng: 仿真引擎引用 (只读其 walls/obstacles/pillar_spheres/chambers);
    - node: 机器人伪节点 (x/z 坐标的真身);
    - trail: 面包屑轨迹列表; _slide: 贴墙绕行方向记忆; waypoint: 巡逻路点。

    调用链: _move_toward/_rand_waypoint 由巡逻与救援状态机每 tick 驱动;
    _los_clear 同时服务于链路注入 (inject_links) 与 SOS 听测 (senses)。
    """

    # ---------- 几何检测 (只读引擎的 walls/obstacles/chambers) ----------
    def _hit_wall(self, p1, p2) -> bool:
        """p1->p2 是否穿越任意用户墙体"""
        for w in self.eng.walls:
            if _seg2d_hit(p1, p2, (w["x1"], w["z1"]), (w["x2"], w["z2"])):
                return True
        return False

    def _los_clear(self, p1, p2) -> bool:
        """两点 (x,z) 视线: 巨石(0.85r)/巨柱/腔壁/墙体 —— 链路注入与 SOS 听测用
        (与引擎 _recompute_los 同一套几何口径)"""
        eng = self.eng
        for o in eng.obstacles:
            if _seg_circle_hit(p1, p2, (o["x"], o["z"]), o["r"] * 0.85):
                return False
        for s in eng.pillar_spheres:
            if _seg_circle_hit(p1, p2, (s[0], s[2]), s[3]):
                return False
        for k in range(1, 5):
            t = k / 5
            if not eng._in_tube((p1[0] + (p2[0] - p1[0]) * t, 0.0,
                                 p1[1] + (p2[1] - p1[1]) * t)):
                return False
        return not self._hit_wall(p1, p2)

    def _walk_blocked(self, p1, p2) -> bool:
        """本体碰撞: 腔壁越界 / 巨石实体半径+车宽 12 / 墙体相交
        (与 _los_clear 的区别: 巨石按实体半径判, 车撞不上石头)"""
        eng = self.eng
        if not (eng._in_tube((p1[0], 0.0, p1[1]))
                and eng._in_tube((p2[0], 0.0, p2[1]))):
            return True
        for o in eng.obstacles:
            if _seg_circle_hit(p1, p2, (o["x"], o["z"]), o["r"] + 12.0):
                return True
        return self._hit_wall(p1, p2)

    # ---------- 移动 ----------
    def _near(self, p, dist=30.0):
        """距点位 p 是否在 dist 内 (到达判定)"""
        return math.hypot(self.node.x - p[0], self.node.z - p[1]) <= dist

    def _advance_to_target(self):
        """朝当前 target 移动一拍 (经 plan_robot_path 规划, 当前为直线)"""
        dest = (self.target[1], self.target[2])
        for wp in plan_robot_path((self.node.x, self.node.z), dest)[1:]:
            self._move_toward(wp)

    def _rand_waypoint(self):
        """腔室内随机巡逻路点 (极坐标均匀撒点, 避开巨石内部)"""
        c = self.eng.chambers[0]
        for _ in range(30):            # 避开巨石内部采样
            ang = random.uniform(0, math.pi * 2)
            rr = math.sqrt(random.uniform(0.05, 0.85))
            p = (c["x"] + math.cos(ang) * rr * c["r"] * 0.92,
                 c["z"] + math.sin(ang) * rr * c["rz"] * 0.92)
            if not any(math.hypot(p[0] - o["x"], p[1] - o["z"]) < o["r"] + 20
                       for o in self.eng.obstacles):
                return p
        return (c["x"], c["z"])

    def _move_toward(self, dest):
        """避障移动: 直行优先; 被挡时锁定一个绕行方向 (记忆) 逐级加大偏角,
        平滑贴墙滑动 —— 避免每拍重新抢角导致"撞墙抖动"。全挡才换路点。"""
        cur = (self.node.x, self.node.z)
        d = math.hypot(dest[0] - cur[0], dest[1] - cur[1])
        if d < 1.0:
            return
        step = min(SPEED, d)
        base = math.atan2(dest[1] - cur[1], dest[0] - cur[0])
        nxt = (cur[0] + math.cos(base) * step, cur[1] + math.sin(base) * step)
        if not self._walk_blocked(cur, nxt):
            self._slide = 0
            self._stuck = 0
            self.node.x, self.node.z = nxt
            return
        if self._slide == 0:
            self._slide = 1            # 首次被挡: 默认向左绕
        for sign in (self._slide, -self._slide):
            for k in (1, 2, 3, 4, 5):
                ang = base + sign * 0.45 * k
                nxt = (cur[0] + math.cos(ang) * step,
                       cur[1] + math.sin(ang) * step)
                if not self._walk_blocked(cur, nxt):
                    self._slide = sign
                    self._stuck = 0
                    self.node.x, self.node.z = nxt
                    return
        self._slide = 0
        self._stuck += 1               # 全向受阻 (长墙围困): 任务态据此放弃
        self.waypoint = None           # 巡逻态: 换路点

    # ---------- 定位辅助 ----------
    def _orbit_spot(self, tgt_xy):
        """绕目标环形搜索"双向可见"点位: 半径 250/190/140 三圈 x 12 方位,
        要求 点位->目标 LOS 通 且 点位看得见任一可达节点 (治巨石挡视线)。
        返回距机器人最近的合格点位, 无则 None。"""
        eng = self.eng
        tx, tz = tgt_xy
        best, bd = None, None
        for radius in (250.0, 190.0, 140.0):
            for k in range(12):
                ang = k / 12 * math.pi * 2
                px = tx + math.cos(ang) * radius
                pz = tz + math.sin(ang) * radius
                if not eng._in_tube((px, 0.0, pz)):
                    continue
                if any(math.hypot(px - o["x"], pz - o["z"]) < o["r"] + 15
                       for o in eng.obstacles):
                    continue
                if not self._los_clear((px, pz), (tx, tz)):
                    continue
                sees_net = any(
                    n.state != "DEAD"
                    and eng.routes.get(n.id, {}).get("hop_count", -1) >= 0
                    and math.hypot(px - n.x, pz - n.z) <= RANGE
                    and self._los_clear((px, pz), (n.x, n.z))
                    for n in eng.nodes.values())
                if not sees_net:
                    continue
                d = math.hypot(px - self.node.x, pz - self.node.z)
                if bd is None or d < bd:
                    bd, best = d, (px, pz)
        return best

    def _last_net_crumb(self):
        """来路上最近一个"见过主网"的点位 -> (x, z) 或 None"""
        for x, z, conn, *_ in reversed(self.trail):
            if conn:
                return (x, z)
        return None

    def _vis_count(self) -> int:
        """当前位置的本地可见节点数 (300m+LOS, 电台自身观测, 非上帝视角)
        —— 面包屑记录与历史择点的打分依据。"""
        cnt = 0
        for n in self.eng.nodes.values():
            if n.state == "DEAD":
                continue
            if (math.hypot(self.node.x - n.x, self.node.z - n.z) <= RANGE
                    and self._los_clear((self.node.x, self.node.z), (n.x, n.z))):
                cnt += 1
        return cnt

    def _scout_waypoints(self, tgt_xy):
        """侦察采样路点: 目标周围环带 (SCOUT_RADIUS_MIN~MAX) 内拒绝采样出
        SCOUT_WAYPOINTS 个合法点 (管内/避石, 与 _orbit_spot 同一合法性口径),
        按离机器人由近及远排序 —— 加固落钉前的主动踩点路线。"""
        eng = self.eng
        tx, tz = tgt_xy
        pts = []
        for _ in range(SCOUT_WAYPOINTS * 4):          # 拒绝采样: 超额尝试
            ang = random.uniform(0, math.pi * 2)
            rr = random.uniform(SCOUT_RADIUS_MIN, SCOUT_RADIUS_MAX)
            p = (tx + math.cos(ang) * rr, tz + math.sin(ang) * rr)
            if not eng._in_tube((p[0], 0.0, p[1])):
                continue
            if any(math.hypot(p[0] - o["x"], p[1] - o["z"]) < o["r"] + 15
                   for o in eng.obstacles):
                continue
            # 墙后不采: 机器人->采样点穿墙即不可达 (长墙围困会白撞)
            if self._hit_wall((self.node.x, self.node.z), p):
                continue
            pts.append(p)
            if len(pts) >= SCOUT_WAYPOINTS:
                break
        pts.sort(key=lambda q: math.hypot(q[0] - self.node.x, q[1] - self.node.z))
        return pts

    def _best_historic_spot(self, tgt_xy):
        """目标 2x 通信半径内的历史面包屑择优: 得分 = 可见数 x 新近度半衰
        权重 (旧观测随墙体拆除/节点死亡自然贬值)。返回 (x, z, 可见数)
        最高分点位或 None —— 落钉前"一钉多益"的选点依据。"""
        best, bs = None, 0.0
        tx, tz = tgt_xy
        now = self.eng.tick
        for x, z, _conn, vis, t in self.trail:
            if math.hypot(x - tx, z - tz) > 2 * RANGE:
                continue               # 太远: 钉够不着目标, 服务不了本次加固
            score = vis * (0.5 ** ((now - t) / HISTORIC_SPOT_DECAY))
            if score > bs:
                bs, best = score, (x, z, vis)
        return best

    def _crumb(self):
        """面包屑: 记录 (位置, 此处能否看见主网, 可见节点数, tick)
        —— 走一步看一步, 择点/回撤共用的历史保存点。"""
        if len(self.trail) >= TRAIL_MAX:
            self.trail.pop(0)
        self.trail.append((round(self.node.x, 1), round(self.node.z, 1),
                           self._connected(), self._vis_count(), self.eng.tick))
