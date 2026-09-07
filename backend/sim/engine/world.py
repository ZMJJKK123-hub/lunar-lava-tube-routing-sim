# -*- coding: utf-8 -*-
"""
世界层: 地质模板 + 几何原语 + 节点/巨石散布 + LOS 视距检测 (WorldMixin)
======================================================================
职责: 仿真引擎的"地形部门" —— 扁椭圆熔岩管画布的地质生成 (含失败换种子
重试保障覆盖率)、26 块互不重叠巨石与 60 根通信桩散布、以及决定 mesh 拓扑
的 LOS 三重检测 (巨石球体相交 / 管内采样 / 用户墙体)。
依赖: node.Node (创建节点), physics.WORLD_SCALE, config.SEED/UWB_RANGE。
"""
import logging   # 标准库: 模块日志 (世界构建/LOS 重算摘要)
import math   # 标准库: 距离/三角, 支撑撒点与 LOS 几何
import random  # 标准库: 地质抖动与撒点 (独立 Random 实例, 种子可复现)

from ..config import SEED, SINK_ID, UWB_RANGE   # 世界种子/sink标识/UWB名义半径
from ..node import Node                # 节点数据类: 生成 60 根通信桩
from .. import physics                 # 物理层: WORLD_SCALE 世界尺度
from .geometry import _seg2d_intersect, _seg_blocked_by_sphere   # 遮挡几何原语

log = logging.getLogger(__name__)   # 本模块日志器


# ---------------------------------------------------------------------------
# 宏观骨架模板: 腔室 (x,y,z,半径) / 隧道 (腔室A, 腔室B, 管径) / 巨柱
# 2D 溶洞模板: (x, z, 长半轴 rx, 短半轴 rz) —— 扁椭圆腔室 = 熔岩管平面示意图
# 纯 2D 沙盘: 一张横向扁长的大画布 (单一大腔室), 无隧道/无巨柱 ——
# 遮挡只来自散布的大石头 (互不重叠, 挡了就是挡了)
# ---------------------------------------------------------------------------
_CHAMBERS = [
    (650, -500, 1300, 600),   # 唯一大腔室: 横向扁长 (~2.2:1), 似熔岩管俯视轮廓
]
_TUNNELS = []     # 隧道模板 (当前空: 纯 2D 竞技场不使用; 旧多腔室模板已移除)
_PILLARS = []     # 巨柱模板 (当前空: 纯 2D 竞技场不使用)

class WorldMixin:
    """职责: SimulationEngine 的世界层混入。

    属性要求 (由 SimulationEngine.__init__ 提供): self._rng (种子随机源),
    self.nodes/obstacles/walls/chambers/tunnels/pillars/pillar_spheres/
    blocked_pairs/sink_id (地质数据全部挂在引擎实例上, 供各层与前端读取)。

    调用链: SimulationEngine.__init__ -> _build_geology -> (_make_chambers
    -> _place_rocks -> _spawn_nodes -> _recompute_los); 用户操作 (画墙/拖石)
    -> _recompute_los -> compute_network (network 模块)。
    """

    # ==================================================================
    # 地质: 腔室 / 隧道 / 巨柱 / 散布节点
    # ==================================================================
    def _build_geology(self):
        """地质装配: 腔室 (含模板抖动) -> 隧道/巨柱 (模板为空) -> 节点+巨石+LOS"""
        rng = self._rng
        self.chambers = []
        for i, (x, z, rx, rz) in enumerate(_CHAMBERS):
            self.chambers.append({
                "id": i,
                "x": x + rng.uniform(-30, 30),
                "y": 0.0,
                "z": z + rng.uniform(-30, 30),
                "r": rx + rng.uniform(-15, 25),    # x 半轴 (长轴)
                "rz": rz + rng.uniform(-15, 25),   # 短半轴 (短轴, 压扁)
            })
        # 隧道: 两腔室间二次贝塞尔 (当前模板为空, 保留装配通路)
        self.tunnels = []
        for (a, b, r) in _TUNNELS:
            ca, cb = self.chambers[a], self.chambers[b]
            p0 = (ca["x"], ca["y"], ca["z"])
            p2 = (cb["x"], cb["y"], cb["z"])
            mid = ((p0[0] + p2[0]) / 2 + rng.uniform(-90, 90), 0.0,
                   (p0[2] + p2[2]) / 2 + rng.uniform(-90, 90))
            self.tunnels.append({
                "a": [round(p0[0], 2), 0.0, round(p0[2], 2)],
                "mid": [round(mid[0], 2), 0.0, round(mid[2], 2)],
                "b": [round(p2[0], 2), 0.0, round(p2[2], 2)],
                "r": r * 10.0, "ca": a, "cb": b,
            })
        # 巨柱: 腔室中央连接天地 (当前模板为空, 保留装配通路)
        self.pillars = []
        self.pillar_spheres = []
        for (ci, _ratio) in _PILLARS:
            c = self.chambers[ci]
            pr = c["r"] * 0.19
            px = c["x"] + rng.uniform(-25, 25)
            pz = c["z"] + rng.uniform(-25, 25)
            self.pillars.append({
                "x": round(px, 2), "y": 0.0, "z": round(pz, 2),
                "r": round(pr, 2), "h": 0.0, "chamber": ci,
            })
            # 2D 巨柱 = 单个圆形遮挡体
            self.pillar_spheres.append((px, 0.0, pz, pr * 1.05))
        self._place_rocks()
        self._spawn_nodes()
        self._recompute_los()
        log.info("地质生成: 腔室=%d 巨石=%d 通信桩=%d 巨柱=%d",
                 len(self.chambers), len(self.obstacles), len(self.nodes),
                 len(self.pillars))

    def _place_rocks(self):
        """26 块互不重叠巨石: 极坐标撒点 (避 sink 区/腔壁/彼此), 挡了就是挡了"""
        rng = self._rng
        c = self.chambers[0]
        RX, RZ = c["r"] * 0.92, c["rz"] * 0.92   # 长轴/短轴撒布范围 (扁椭圆)
        sink_xy = (c["x"] - RX * 0.72, c["z"] - RZ * 0.55)
        self.obstacles = []
        placed_rocks = []
        tries = 0
        while len(placed_rocks) < 26 and tries < 900:
            tries += 1
            ang = rng.uniform(0, math.pi * 2)
            rr = math.sqrt(rng.uniform(0.05, 0.72))
            x = c["x"] + math.cos(ang) * rr * RX
            z = c["z"] + math.sin(ang) * rr * RZ
            r = rng.uniform(55, 130)
            if ((abs(x - c["x"]) + r) / c["r"] > 0.96
                    or (abs(z - c["z"]) + r) / c["rz"] > 0.96):
                continue                       # 石头不得戳出椭圆腔壁
            if any(math.dist((x, z), (q[0], q[1])) < r + q[2] + 110 for q in placed_rocks):
                continue
            if math.dist((x, z), sink_xy) < r + 160:
                continue                       # 让出 sink 区域
            placed_rocks.append((x, z, r))
            self.obstacles.append({
                "x": round(x, 1), "y": 0.0, "z": round(z, 1),
                "r": round(r, 1), "h": 0.0,
                "shape": rng.choice(["spike", "slab", "shard"]),
                "rot": round(rng.uniform(0, 360), 1),
            })
        self._sink_xy = sink_xy                # sink 落点 (供 _spawn_nodes 使用)

    def _spawn_nodes(self):
        """60 根通信桩: sink 在左端开阔处, 其余扁椭圆内随机散布
        (最小间距 + 避石头), 1/3 为 sensor 其余 relay"""
        rng = self._rng
        self.nodes.clear()
        c = self.chambers[0]
        RX, RZ = c["r"] * 0.92, c["rz"] * 0.92   # 长轴/短轴撒布范围 (扁椭圆)

        def add(id_, x, z, role):
            depth = min(1.0, math.hypot((x - c["x"]) / RX, (z - c["z"]) / RZ))
            n = Node(
                id=id_, x=round(x, 1), y=0.0, z=round(z, 1), role=role,
                temp_c=round(rng.uniform(-55, 8) - depth * 15, 1),
                battery_mah=rng.uniform(9000, 12000),
                tilt_deg=rng.uniform(0, 5),
                radiation_rad=rng.uniform(0, 300) + depth * 800,
                ant_gain_dbi=5.0,
            )
            self.nodes[n.id] = n

        # sink: 左端开阔处
        self.sink_id = SINK_ID
        add(SINK_ID, self._sink_xy[0], self._sink_xy[1], "sink")

        # 其余节点: 随机撒点 (最小间距 + 避石头)
        count, tries = 0, 0
        while count < 59 and tries < 4000:
            tries += 1
            ang = rng.uniform(0, math.pi * 2)
            rr = math.sqrt(rng.uniform(0.02, 0.94))
            x = c["x"] + math.cos(ang) * rr * RX
            z = c["z"] + math.sin(ang) * rr * RZ
            if any(math.dist((x, z), (o["x"], o["z"])) < o["r"] + 70 for o in self.obstacles):
                continue
            if any(math.dist((x, z), (n.x, n.z)) < 105 for n in self.nodes.values()):
                continue
            count += 1
            role = "sensor" if count % 3 == 0 else "relay"
            add(f"NODE-{count:02d}", x, z, role)

    def _in_tube(self, p) -> bool:
        """纯 2D 沙盘: 点在唯一大腔室(扁椭圆)内即合法 (椭圆外=岩壁)"""
        for c in self.chambers:
            nx = (p[0] - c["x"]) / c["r"]
            nz = (p[2] - c["z"]) / c["rz"]
            if nx * nx + nz * nz < 0.99 ** 2:
                return True
        return False

    def _seg_in_tube(self, p1, p2) -> bool:
        """线段全程采样必须在腔室内 (出圆即穿岩)"""
        for k in range(1, 5):
            t = k / 5
            q = tuple(p1[i] + (p2[i] - p1[i]) * t for i in range(3))
            if not self._in_tube(q):
                return False
        return True

    def _recompute_los(self):
        """LOS: 巨石 + 石柱多球 + 管内几何约束 + 用户墙体.
        被遮挡节点对永不建边 -> mesh 拓扑"""
        self.blocked_pairs = set()
        ids = list(self.nodes)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = self.nodes[ids[i]], self.nodes[ids[j]]
                if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) > UWB_RANGE * 1.6 * physics.WORLD_SCALE:
                    continue
                pa, pb = (a.x, a.y, a.z), (b.x, b.y, b.z)
                hit = any(_seg_blocked_by_sphere(pa, pb, (o["x"], o["y"], o["z"]), o["r"] * 0.85)
                          for o in self.obstacles)
                if not hit:
                    hit = any(_seg_blocked_by_sphere(pa, pb, (s[0], s[1], s[2]), s[3])
                              for s in self.pillar_spheres)
                if hit or not self._seg_in_tube(pa, pb):
                    self.blocked_pairs.add((a.id, b.id))
        # (注: 早期版本的"架构性强制遮挡"补丁已移除 ——
        #  视距阻挡现在完全由真实几何驱动: 巨石 + 巨柱 + 管内检测 + 用户墙体)
        # 用户墙体: 俯视 x-z 平面线段, 与节点连线相交 -> 视线切断
        for w in self.walls:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    key = (ids[i], ids[j])
                    if key in self.blocked_pairs:
                        continue
                    a2, b2 = self.nodes[ids[i]], self.nodes[ids[j]]
                    if _seg2d_intersect((a2.x, a2.z), (b2.x, b2.z),
                                        (w["x1"], w["z1"]), (w["x2"], w["z2"])):
                        self.blocked_pairs.add(key)
        log.info("LOS 重算: 被遮挡节点对=%d 墙体=%d", len(self.blocked_pairs),
                 len(self.walls))

    def export_geology(self) -> dict:
        """地质数据一次性下发前端渲染 (隧道曲线/腔室/巨柱)。

        Args: None。
        Returns: dict —— chambers(腔室轮廓)/tunnels/pillars/obstacles(巨石)/
        walls(用户墙体), WS 建连时随 geology 帧发送一次。
        Globals Used: 模板 _CHAMBERS/_TUNNELS/_PILLARS (经实例化后的属性)。
        Calls: None (纯读取)。
        """
        return {
            "chambers": [{k: c[k] for k in ("id", "x", "y", "z", "r", "rz")}
                         for c in self.chambers],
            "tunnels": self.tunnels,
            "pillars": self.pillars,
            "obstacles": self.obstacles,
            "walls": self.walls,
        }
