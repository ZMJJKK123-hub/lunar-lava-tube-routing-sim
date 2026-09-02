# -*- coding: utf-8 -*-
"""
仿真引擎状态机 4.0 —— 多分支迷宫熔岩管网络
  - 宏观地质: 主干隧道 + 双环路 + 死胡同岔路 + 10 座天然腔室(交汇点)
  - 腔室中央生成连接天地的巨型石柱 (严格 LOS 遮挡, 柱后节点被迫绕行)
  - 52 根通信桩智能散布: 隧道沿线 / 腔室内壁 / 石柱背后 / 死胡同深处
  - LOS 视距检测(巨石 + 石柱多球近似) -> 带环路的 mesh 拓扑
  - 算法事件流 + 通俗解说 + ACO 信息素 + 自愈模式机
"""
import asyncio
import math
import random
import time
from collections import deque

from .node import Node
from . import physics
from .routing import routing_step, rscspa

ROBOT_ENABLED = False     # 巡检机器人暂时关闭 (置 True 即恢复)
TICK_PHYS_S = 0.25
TICK_BROADCAST_S = 0.2
HEALING_HOLD_TICKS = 4

SEED = 42
UWB_RANGE = 30.0

# ---------------------------------------------------------------------------
# 宏观骨架模板: 腔室 (x,y,z,半径) / 隧道 (腔室A, 腔室B, 管径)
# 拓扑: C0(洞口)→C1→C2→C3 主干; C1↔C4↔C0 右环; C2↔C6↔C1 左环;
#       C2→C5 死胡同; C3→C7 死胡同; C3→C8 深腔 → C9 死胡同尖
# ---------------------------------------------------------------------------
# 2D 溶洞模板: (x, z, 半径) —— 俯视平面地质 (算法层与 3D 版完全一致)
# 纯 2D 沙盘: 一张大画布 (单一大腔室), 无隧道/无巨柱 ——
# 遮挡只来自散布的大石头 (互不重叠, 挡了就是挡了)
_CHAMBERS = [
    (650, -500, 880),     # 唯一大腔室: 圆心 (x,z) 半径 r, 覆盖整个战场
]
_TUNNELS = []
_PILLARS = []

# (旧多腔室/隧道/巨柱模板已移除 —— 纯 2D 沙盘采用单一竞技场)


def _cross(ox, oz, ax, az, bx, bz):
    return (bx - ox) * (az - oz) - (ax - ox) * (bz - oz)


def _seg2d_intersect(p1, p2, w1, w2) -> bool:
    """2D 线段相交判定 (叉积法): 节点连线 vs 墙体"""
    d1 = _cross(w1[0], w1[1], p2[0], p2[1], p1[0], p1[1])
    d2 = _cross(w1[0], w1[1], w2[0], w2[1], p1[0], p1[1])
    d3 = _cross(p1[0], p1[1], w2[0], w2[1], w1[0], w1[1])
    d4 = _cross(p1[0], p1[1], p2[0], p2[1], w1[0], w1[1])
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _seg_blocked_by_sphere(p1, p2, c, R) -> bool:
    dx, dy, dz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
    fx, fy, fz = p1[0] - c[0], p1[1] - c[1], p1[2] - c[2]
    a = dx * dx + dy * dy + dz * dz
    if a < 1e-9:
        return fx * fx + fy * fy + fz * fz < R * R
    tt = max(0.0, min(1.0, -(fx * dx + fy * dy + fz * dz) / a))
    qx, qy, qz = fx + tt * dx, fy + tt * dy, fz + tt * dz
    return qx * qx + qy * qy + qz * qz < R * R


def _bez(p0, p1, p2, t):
    """二次贝塞尔采样 (隧道曲线)"""
    u = 1 - t
    return (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            u * u * p0[2] + 2 * u * t * p1[2] + t * t * p2[2])


class SimulationEngine:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.sink_id = None
        self.history = deque(maxlen=300)
        self.tick = 0
        self.links: dict = {}
        self.prev_links: dict = {}
        self.routes: dict = {}
        self.prev_routes: dict = {}
        self.traffic: list[dict] = []
        self.link_load: dict[tuple, float] = {}
        self.events: deque = deque(maxlen=120)
        self._event_seq = 0
        self.last_narration: dict | None = None
        self.wave: dict = {}
        self.mode = "STABLE"
        self._stable_ticks = 0
        self.disaster: str | None = None
        self.robot = None      # 巡检机器人 (动态移动信源)
        self.walls: list = []  # 用户在 2D 俯视图上画的墙体
        self.heal_started_tick = 0
        self._pre_collapse_routes: dict = {}

        # 地质生成 (失败自动换种子重试, 保证覆盖率)
        for attempt in range(8):
            self._seed = SEED + attempt * 1000
            self._rng = random.Random(self._seed)
            self._build_geology()
            self._recompute_los()
            self.compute_network(quiet=True)
            if self._coverage() >= 96.0:
                break

    # ==================================================================
    # 地质: 腔室 / 隧道 / 巨柱 / 散布节点
    # ==================================================================
    def _build_geology(self):
        rng = self._rng
        self.chambers = []
        for i, (x, z, r) in enumerate(_CHAMBERS):
            self.chambers.append({
                "id": i,
                "x": x + rng.uniform(-30, 30),
                "y": 0.0,
                "z": z + rng.uniform(-30, 30),
                "r": r + rng.uniform(-15, 25),
            })
        # 隧道: 两腔室间二次贝塞尔, 控制点带随机垂向摆动
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
        # 巨柱: 腔室中央连接天地。直径约为腔室直径的 0.38 (远小于溶洞),
        # 高度 2.06r 严格贯通腔室地面与顶部
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

        self._spawn_nodes()
        self._spawn_obstacles()
        self._recompute_los()

    def _tunnel_point(self, ti: int, t: float, off_r: float, theta: float):
        """2D: 隧道曲线上的点 + 法向偏移"""
        tu = self.tunnels[ti]
        p0, pm, p2 = tuple(tu["a"]), tuple(tu["mid"]), tuple(tu["b"])
        c = _bez(p0, pm, p2, t)
        d1 = _bez(p0, pm, p2, min(1, t + 0.02))
        d0 = _bez(p0, pm, p2, max(0, t - 0.02))
        tx, tz = d1[0] - d0[0], d1[2] - d0[2]
        n = math.hypot(tx, tz) or 1.0
        nx, nz = -tz / n, tx / n          # 单位法向
        s = math.sin(math.radians(theta))
        return (c[0] + nx * off_r * s, 0.0, c[2] + nz * off_r * s)

    def _spawn_nodes(self):
        """60 根通信桩: 大画布内随机散布 (最小间距, 避开石头), sink 在左上"""
        rng = self._rng
        self.nodes.clear()
        c = self.chambers[0]
        R = c["r"] * 0.92
        # 先放石头 (互不重叠的大块, 挡了就是挡了)
        self.obstacles = []
        placed_rocks = []
        tries = 0
        while len(placed_rocks) < 26 and tries < 900:
            tries += 1
            ang = rng.uniform(0, math.pi * 2)
            rr = math.sqrt(rng.uniform(0.05, 0.72)) * R
            x, z = c["x"] + math.cos(ang) * rr, c["z"] + math.sin(ang) * rr
            r = rng.uniform(55, 130)
            if any(math.dist((x, z), (q[0], q[1])) < r + q[2] + 110 for q in placed_rocks):
                continue
            if math.dist((x, z), (c["x"] - R * 0.72, c["z"] - R * 0.55)) < r + 160:
                continue                       # 让出 sink 区域
            placed_rocks.append((x, z, r))
            self.obstacles.append({
                "x": round(x, 1), "y": 0.0, "z": round(z, 1),
                "r": round(r, 1), "h": 0.0,
                "shape": rng.choice(["spike", "slab", "shard"]),
                "rot": round(rng.uniform(0, 360), 1),
            })

        def add(id_, x, z, role):
            depth = min(1.0, math.hypot(x - c["x"], z - c["z"]) / R)
            n = Node(
                id=id_, x=round(x, 1), y=0.0, z=round(z, 1), role=role,
                temp_c=round(rng.uniform(-55, 8) - depth * 15, 1),
                battery_mah=rng.uniform(9000, 12000),
                tilt_deg=rng.uniform(0, 5),
                radiation_rad=rng.uniform(0, 300) + depth * 800,
                ant_gain_dbi=5.0,
            )
            self.nodes[n.id] = n

        # sink: 左上开阔处
        self.sink_id = "NODE-00"
        add("NODE-00", c["x"] - R * 0.72, c["z"] - R * 0.55, "sink")

        # 其余节点: 随机撒点 (最小间距 + 避石头)
        count, tries = 0, 0
        while count < 59 and tries < 4000:
            tries += 1
            ang = rng.uniform(0, math.pi * 2)
            rr = math.sqrt(rng.uniform(0.02, 0.94)) * R
            x = c["x"] + math.cos(ang) * rr
            z = c["z"] + math.sin(ang) * rr
            if any(math.dist((x, z), (o["x"], o["z"])) < o["r"] + 70 for o in self.obstacles):
                continue
            if any(math.dist((x, z), (n.x, n.z)) < 105 for n in self.nodes.values()):
                continue
            count += 1
            role = "sensor" if count % 3 == 0 else "relay"
            add(f"NODE-{count:02d}", x, z, role)

    def _spawn_obstacles(self):
        """巨石已在 _spawn_nodes 中生成 (互不重叠); 此处保留接口不再撒石"""
        pass

    def _in_tube(self, p) -> bool:
        """纯 2D 沙盘: 点在唯一大腔室圆内即合法 (边界外=岩壁)"""
        for c in self.chambers:
            if math.dist(p, (c["x"], c["y"], c["z"])) < c["r"] * 0.99:
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
        """LOS: 巨石 + 石柱多球 + 管内几何约束. 被遮挡节点对永不建边 -> mesh 拓扑"""
        from .physics import WORLD_SCALE
        self.blocked_pairs = set()
        ids = list(self.nodes)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = self.nodes[ids[i]], self.nodes[ids[j]]
                if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) > UWB_RANGE * 1.6 * WORLD_SCALE:
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

    def export_geology(self) -> dict:
        """地质数据一次性下发前端渲染 (隧道曲线/腔室/巨柱)"""
        return {
            "chambers": [{k: c[k] for k in ("id", "x", "y", "z", "r")} for c in self.chambers],
            "tunnels": self.tunnels,
            "pillars": self.pillars,
            "obstacles": self.obstacles,
            "walls": self.walls,
        }

    # ==================================================================
    # 事件 / 解说
    # ==================================================================
    def _emit(self, type_: str, severity: str, msg: str, narration: str | None = None, **payload):
        self._event_seq += 1
        self.events.append({
            "id": self._event_seq, "tick": self.tick, "type": type_,
            "severity": severity, "msg": msg, "narration": narration, **payload,
        })
        # 关键解说单独保留, 不受事件滚动队列挤出 -> 前端解说员始终可播
        if narration and type_ in ("disaster", "node_dead", "healing_start",
                                   "converged", "isolated", "rejoin"):
            self.last_narration = {"id": self._event_seq, "text": narration}

    @staticmethod
    def _zh(nid: str) -> str:
        return f"{nid.split('-')[1]}号"

    # ==================================================================
    # 核心: 链路 + 路由 + 模式机
    # ==================================================================
    def compute_network(self, quiet: bool = False):
        nodes = list(self.nodes.values())
        links = {}
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i], nodes[j]
                key = (a.id, b.id)
                if key in self.blocked_pairs:      # LOS 遮挡 (巨石/石柱)
                    continue
                lab = physics.link_budget(a, b)
                lba = physics.link_budget(b, a)
                if lab is None or lba is None:
                    continue
                load = self.link_load.get(key, 0.0)
                links[key] = {
                    **lab,
                    "cost_ab": physics.link_cost(a, b, lab, load),
                    "cost_ba": physics.link_cost(b, a, lba, load),
                    "load": round(load, 2),
                }
                a.snr_db, b.snr_db = lab["snr_db"], lba["snr_db"]
                a.ber, b.ber = lab["ber"], lba["ber"]

        if not quiet:
            for key, l in links.items():
                pl = self.prev_links.get(key)
                if pl and pl["up"] and not l["up"]:
                    reason = ("SNR=%.1fdB" % l["snr_db"] if l["snr_db"] < 5
                              else "BER=%.1e" % l["ber"] if l["ber"] >= 1e-3
                              else "margin=%.1fdB" % l["margin_db"])
                    self._emit("link_down", "error",
                               f"✖ 链路熔断 {key[0]} ↔ {key[1]} ({reason})",
                               narration=f"⚠️ {self._zh(key[0])} 与 {self._zh(key[1])} 之间的信道质量恶化"
                                         f"(信噪比跌至 {l['snr_db']}dB,低于解调门限),链路熔断。",
                               a=key[0], b=key[1])
                elif pl and not pl["up"] and l["up"]:
                    self._emit("link_up", "ok",
                               f"✔ 链路恢复 {key[0]} ↔ {key[1]} (SNR={l['snr_db']}dB)",
                               a=key[0], b=key[1])

        self.links = links
        self.routes, self.wave = routing_step(nodes, links, self.sink_id)

        usage: dict[tuple, int] = {}
        for r in self.routes.values():
            path = r.get("path") or []
            for k in range(len(path) - 1):
                key = tuple(sorted((path[k], path[k + 1])))
                usage[key] = usage.get(key, 0) + 1
        for key in set(list(usage) + list(self.link_load)):
            self.link_load[key] = 0.82 * self.link_load.get(key, 0.0) + 0.18 * usage.get(key, 0)

        if not quiet:
            for nid, r in self.routes.items():
                pr = self.prev_routes.get(nid)
                if pr is None:
                    continue
                if pr["hop_count"] > 0 and r["hop_count"] > 0 and pr["path"] != r["path"]:
                    self._emit("reroute", "warn",
                               f"⟳ {nid} 重路由: {len(pr['path'])-1}跳 → {len(r['path'])-1}跳 "
                               f"({' → '.join(r['path'])})",
                               node=nid, old_path=pr["path"], new_path=r["path"])
                if pr["hop_count"] > 0 and r["hop_count"] < 0:
                    self._emit("isolated", "error",
                               f"☠ {nid} 失联, 成为孤岛节点",
                               narration=f"⚠️ {self._zh(nid)} 与所有邻居失去联系,成为信息孤岛——"
                                         f"它周围的所有通路都被切断了。", node=nid)
                if pr["hop_count"] < 0 and r["hop_count"] > 0:
                    self._emit("rejoin", "ok",
                               f"✦ {nid} 重新入网 (hop={r['hop_count']})",
                               narration=f"✅ {self._zh(nid)} 通过新路径重新回到网络。",
                               node=nid)

        for n in self.nodes.values():
            n.neighbors = sum(1 for (a, b), l in links.items()
                              if n.id in (a, b) and l["up"])
            n.hop_count = self.routes.get(n.id, {}).get("hop_count", -1)
        for n in self.nodes.values():
            on_path = any(n.id in key for key in usage)
            if on_path:
                bad_link = any(
                    n.id in (a, b) and (l["snr_db"] < 10 or not l["up"])
                    for (a, b), l in links.items())
                n.queue_pct = min(100.0, n.queue_pct + (8.0 if bad_link else 1.5))
                if n.queue_pct > 85 and not quiet and random.random() < 0.3:
                    self._emit("congestion", "warn",
                               f"⚠ {n.id} 队列积压 {n.queue_pct:.0f}% (吞吐瓶颈)",
                               narration=f"⚠️ {self._zh(n.id)} 的数据包排队越来越长(积压 "
                                         f"{n.queue_pct:.0f}%),算法正在考虑分流。", node=n.id)
            if n.state not in ("DEAD", "SEU_RESET"):
                n.state = "DEGRADED" if (n.queue_pct > 70 or n.snr_db < 8) else "ACTIVE"

        # 活跃呼叫接纳: 传感器以占空比轮流发起呼叫 (模拟 RCSPA 的"新呼叫接纳"),
        # 静默周期内的节点不在任何路径上 -> PAMAS 判定其休眠省电
        sensors = [nid for nid, n in self.nodes.items()
                   if n.role == "sensor" and n.state != "DEAD"
                   and self.routes.get(nid, {}).get("hop_count", -1) > 0]
        if self.tick % 6 == 0 or not getattr(self, "_active_calls", None):
            random.shuffle(sensors)
            self._active_calls = set(sensors[:max(4, len(sensors) // 2)])
        else:  # 剔除失联/死亡
            self._active_calls &= set(sensors)
        self.traffic = [
            {"src": nid, "path": self.routes[nid]["path"]}
            for nid in sorted(self._active_calls)
        ]

        # ---- PAMAS 独立关机判定: 激活路径外的节点若邻居正在收发 -> 休眠省电 ----
        active_nodes = {self.sink_id}
        active_edges = set()
        for tr in self.traffic:
            path = tr.get("path") or []
            for k in range(len(path) - 1):
                active_edges.add(tuple(sorted((path[k], path[k + 1]))))
            active_nodes.update(path)
        if self.robot and self.robot.get("route"):
            rp = self.robot["route"].get("path") or []
            active_nodes.update(rp)
            for k in range(len(rp) - 1):
                active_edges.add(tuple(sorted((rp[k], rp[k + 1]))))
        # 物理邻接表 (载波监听用)
        phys_adj = {}
        for (a, b) in self.links:
            phys_adj.setdefault(a, set()).add(b)
            phys_adj.setdefault(b, set()).add(a)
        for n in self.nodes.values():
            if n.state == "DEAD":
                n.radio = "IDLE"
            elif n.id in active_nodes:
                n.radio = "TXRX"
            else:
                # PAMAS 独立关机判定: 监听到邻居正在收发且自身无数据 -> 关闭电台
                nbr_busy = any(m in active_nodes for m in phys_adj.get(n.id, ()))
                n.radio = "SLEEP" if nbr_busy else "IDLE"

        # 视距架构数据: 每节点最近 3 个"近在咫尺却被岩壁/巨石挡住"的邻居
        # (图中无边, 必须经中继绕行) —— 前端用红色断裂虚线呈现
        block_adj = {}
        for (x, y) in self.blocked_pairs:
            block_adj.setdefault(x, []).append(y)
            block_adj.setdefault(y, []).append(x)
        self.blocked_info = {}
        for n in self.nodes.values():
            lst = []
            for oid in block_adj.get(n.id, ()):
                o = self.nodes.get(oid)
                if not o:
                    continue
                d = physics.sim_distance(n, o)
                if d <= 31.0:
                    lst.append((d, oid))
            lst.sort()
            nxt = self.routes.get(n.id, {}).get("next_hop")
            info = []
            for d, oid in lst[:3]:
                o = self.nodes.get(oid)
                if o is None:
                    continue
                pa, pb = (n.x, n.y, n.z), (o.x, o.y, o.z)
                if any(_seg_blocked_by_sphere(pa, pb, (ob["x"], ob["y"], ob["z"]), ob["r"] * 0.85)
                       for ob in self.obstacles):
                    cause = "巨石遮挡"
                elif any(_seg_blocked_by_sphere(pa, pb, (s[0], s[1], s[2]), s[3])
                         for s in self.pillar_spheres):
                    cause = "巨柱遮挡"
                elif not self._seg_in_tube(pa, pb):
                    cause = "岩壁阻隔"
                else:
                    cause = "信道质量"
                info.append({"id": oid, "d": round(d, 1), "via": nxt, "cause": cause})
            self.blocked_info[n.id] = info

        self._robot_step()
        self._robot_plan()

        # 收敛判定: 只有"结构性变化"(链路生死/节点失联)才触发或重置自愈;
        # ACO 信息素引起的等价路径微调不算新灾害, 保证收敛解说能落地
        all_keys = set(links) | set(self.prev_links)
        structural = (not quiet and any(
            links.get(k, {}).get("up", False)
            != self.prev_links.get(k, {}).get("up", False)
            for k in all_keys))
        if structural and self.mode != "HEALING":
            self.mode = "HEALING"
            self._stable_ticks = 0
            self.heal_started_tick = self.tick
            if not self._pre_collapse_routes:
                self._pre_collapse_routes = {k: dict(v) for k, v in self.routes.items()}
            if not quiet:
                self._emit("healing_start", "info",
                           "◎ 拓扑变化检测 → 网络进入自愈状态, Dijkstra 波前重扩散",
                           narration="◎ 多智能体算法已察觉网络拓扑突变,正在启动「波前扩散」搜索——"
                                     "从洞口基站出发逐层探索所有岔路,为数据寻找新的绕行通道…")
        elif self.mode == "HEALING":
            if structural:
                self._stable_ticks = 0
            else:
                self._stable_ticks += 1
                if self._stable_ticks >= HEALING_HOLD_TICKS:
                    self.mode = "CONVERGED"
                    dt = self.tick - self.heal_started_tick
                    if not quiet:
                        moved = [nid for nid in self.routes
                                 if (self._pre_collapse_routes.get(nid) or {}).get("path")
                                 != self.routes[nid].get("path")
                                 and self.routes[nid].get("hop_count", -1) > 0]
                        extra = ""
                        if moved:
                            sample = min(moved, key=lambda x: len(self.routes[x]["path"]))
                            extra = (f"以 {self._zh(sample)} 为例,数据在岔路口拐弯,新路径为 "
                                     f"{' → '.join(self._zh(p) for p in self.routes[sample]['path'])}。")
                        self._emit("converged", "ok",
                                   f"◆ 全网路由收敛 ✓ (自愈耗时 {dt} tick, 覆盖率 {self._coverage()}%)",
                                   narration=f"✅ 网络自愈完成!数据流已切换到备用洞穴通道,{extra}"
                                             f"全程仅用时 {dt} 个仿真周期,覆盖率 {self._coverage()}%。")
                    self._pre_collapse_routes = {}
        elif self.mode == "CONVERGED":
            self.mode = "STABLE"

        self.prev_links = {k: v for k, v in links.items()}
        self.prev_routes = {k: dict(v) for k, v in self.routes.items()}

    def _coverage(self) -> float:
        reach = sum(1 for r in self.routes.values() if r.get("hop_count", -1) >= 0)
        return round(reach / len(self.nodes) * 100, 1)

    # ==================================================================
    # 巡检机器人: 动态移动信源, 沿拓扑边插值游走 + RCSPA 实时重规划
    # ==================================================================
    def _init_robot(self):
        succ = [p for r in self.routes.values() if (p := r.get("path")) and len(p) > 1]
        u, v = (succ[0][0], succ[0][1]) if succ else (self.sink_id, self.sink_id)
        self.robot = {
            "u": u, "v": v, "t": 0.0, "speed": 14.0,
            "pos": [self.nodes[u].x, self.nodes[u].y, self.nodes[u].z],
            "route": None, "visited": {u},
        }

    def _robot_adj(self):
        adj = {}
        for (a, b), l in self.links.items():
            if not l["up"]:
                continue
            adj.setdefault(a, []).append((b, l["cost_ab"]))
            adj.setdefault(b, []).append((a, l["cost_ba"]))
        return adj

    def _busy_channels(self):
        """当前主干流量占用的信道 (供 RCSPA 干扰惩罚/排斥绕行)"""
        busy = {}
        for r in self.routes.values():
            path = r.get("path") or []
            if r.get("hop_count", -1) <= 0 or not path:
                continue
            for k in range(len(path) - 1):
                key = frozenset((path[k], path[k + 1]))
                i = int(path[k].split("-")[1]); j = int(path[k + 1].split("-")[1])
                busy.setdefault(key, set()).add((i + j) % 3)
        return busy

    def _robot_plan(self):
        """RCSPA: 以机器人前方节点为源, 重规划至洞口的资源约束最短路径"""
        if not ROBOT_ENABLED or not self.robot:
            return
        ahead = self.robot["v"]
        if ahead not in self.nodes or self.nodes[ahead].state == "DEAD":
            ahead = self.robot["u"]
        adj = self._robot_adj()
        res = rscspa(adj, ahead, self.sink_id, n_channels=3, K=3,
                     busy_edge=self._busy_channels())
        self.robot["route"] = res

    def _robot_step(self, dt: float = 2.5):
        if not ROBOT_ENABLED:
            return
        if not self.robot:
            self._init_robot()
        rb = self.robot
        u, v = self.nodes.get(rb["u"]), self.nodes.get(rb["v"])
        if not u or not v or u.state == "DEAD" or v.state == "DEAD" or u is v:
            live = [n.id for n in self.nodes.values() if n.state != "DEAD"]
            rb["u"] = rb["v"] = live[0] if live else self.sink_id
            rb["t"] = 0.0
            return
        dist_uv = math.dist((u.x, u.y, u.z), (v.x, v.y, v.z)) or 1.0
        rb["t"] += rb["speed"] * dt / dist_uv
        if rb["t"] >= 1.0:
            # 抵达节点: 记录 + 事件解说 + 选择下一条边 (优先未访问, 探索岔路)
            rb["t"] = 0.0
            rb["u"] = rb["v"]
            rb["visited"].add(rb["u"])
            adj = self._robot_adj().get(rb["u"], [])
            cands = [b for b, _w in adj if self.nodes[b].state != "DEAD"]
            fresh = [b for b in cands if b not in rb["visited"]]
            pool = fresh or cands or [rb["u"]]
            rb["v"] = random.choice(pool)
            self._emit("robot_arrive", "info",
                       f"🤖 机器人抵达 {rb['u']}, 重规划至洞口 (RCSPA)",
                       narration=f"🤖 巡检机器人抵达 {self._zh(rb['u'])} 节点,正在实时重新规划回洞口的"
                                 f"中继路线——资源约束最短路径算法会避开繁忙信道,选择总发射功率最低的路径。",
                       node=rb["u"])
        uu, vv = self.nodes[rb["u"]], self.nodes[rb["v"]]
        t = rb["t"]
        rb["pos"] = [uu.x + (vv.x - uu.x) * t, uu.y + (vv.y - uu.y) * t, uu.z + (vv.z - uu.z) * t]

    # ==================================================================
    # 上帝模式 / 灾害
    # ==================================================================
    def apply_override(self, node_id: str, params: dict):
        node = self.nodes.get(node_id)
        if node is None:
            return {"ok": False, "error": "no such node"}
        for k, v in params.items():
            try:
                node.apply_override(k, v)
            except KeyError as e:
                return {"ok": False, "error": str(e)}
        narration = None
        if node.state != "DEAD" and (node.temp_c >= 100 or node.battery_soc <= 3):
            cause = "温度突破 100°C 临界值,芯片烧毁" if node.temp_c >= 100 else "电量耗尽"
            node.state = "DEAD"
            narration = (f"☠ 惨剧发生:{self._zh(node_id)} {cause},节点当场报废(现场已冒烟)。"
                         f"多智能体算法将立即绕开它重建路由。")
            self._emit("node_dead", "error",
                       f"☠ {node_id} 临界报废 ({cause})", narration=narration, node=node_id)
        elif params:
            self._emit("override", "info",
                       f"⚑ 上帝模式: {node_id} 参数覆写 {params}")
        self.compute_network()
        return {"ok": True}

    def add_wall(self, x1: float, z1: float, x2: float, z2: float):
        """2D 俯视图添加墙体 -> 重算视距拓扑, 被切断的边从图中消失, 路由绕行"""
        self.walls.append({"x1": round(x1, 1), "z1": round(z1, 1),
                           "x2": round(x2, 1), "z2": round(z2, 1)})
        before = set(self.blocked_pairs)
        self._recompute_los()
        newly = len(set(self.blocked_pairs) - before)
        self._emit("wall_added", "warn",
                   f"🧱 新增墙体, 视线切断 {newly} 对节点直连",
                   narration=f"🧱 一堵岩壁插入网络!{newly} 对彼此可见的节点被墙体隔断,"
                             f"它们之间的边已从图中移除——算法正在重算路由,数据将绕行中继节点…")
        self.compute_network()

    def move_obstacle(self, idx: int, x: float, z: float):
        """2D 沙盘巨石拖拽: 移动障碍 -> LOS 重算 -> 被切断的链路从图中消失"""
        if not (0 <= idx < len(self.obstacles)):
            return {"ok": False, "error": "bad index"}
        if not self._in_tube((x, 0.0, z)):
            return {"ok": False, "error": "outside cave"}
        o = self.obstacles[idx]
        before = set(self.blocked_pairs)
        o["x"], o["y"], o["z"] = round(x, 1), 0.0, round(z, 1)
        self._recompute_los()
        newly = len(set(self.blocked_pairs) - before)
        if newly:
            self._emit("obstacle_moved", "warn",
                       f"🪨 巨石移位, 视线切断 {newly} 对节点直连",
                       narration=f"🪨 巨石被移动!它切断了 {newly} 对节点之间的视线——"
                                 f"相关链路已从图中移除,算法正在重算路由,数据将绕行中继…")
        self.compute_network()
        return {"ok": True, "cut": newly}

    def remove_wall(self, index: int):
        """撤销第 index 堵墙 (按加入顺序 0..n-1) -> LOS 重算"""
        if not (0 <= index < len(self.walls)):
            return {"ok": False, "error": "bad index"}
        self.walls.pop(index)
        self._recompute_los()
        self._emit("wall_removed", "ok", f"🧱 墙体 #{index} 已拆除",
                   narration="🧱 一堵岩壁被拆除,被它隔断的视线恢复,算法正在回归更优的直连路径。")
        self.compute_network()
        return {"ok": True}

    def clear_walls(self):
        self.walls = []
        self._recompute_los()
        self._emit("wall_cleared", "ok", "🧱 墙体已清除, 视距拓扑还原",
                   narration="🧱 岩壁已移除,节点视线恢复,算法正在回归最优直连路径。")
        self.compute_network()

    def inject_disaster(self, kind: str | None):
        self.disaster = kind
        if kind is None:
            return
        if kind == "collapse":
            self._collapse()
            return
        if kind == "kill_backbone":
            self._kill_backbone()
            return
        name = {"thermal_surge": "热浪", "solar_flare": "太阳耀斑",
                "random_kill": "陨石撞击"}[kind]
        self._emit("disaster", "error", f"☄ 灾害注入: {name}",
                   narration=f"☄ {name}来袭!全网络节点同时承受极端应力,请观察各节点的"
                             f"实时指标变化与算法的应对。")
        targets = list(self.nodes.values())
        if kind == "thermal_surge":
            for n in targets:
                n.temp_c += random.uniform(35, 70)
        elif kind == "solar_flare":
            for n in targets:
                n.radiation_rad += random.uniform(8000, 20000)
        elif kind == "random_kill":
            victim = random.choice([n for n in targets if n.id != self.sink_id])
            victim.state = "DEAD"
            self._emit("node_dead", "error", f"☠ {victim.id} 被击毁",
                       narration=f"☄ 一颗陨石击中 {self._zh(victim.id)},该节点当场损毁。"
                                 f"算法正在评估损失并重建路由…", node=victim.id)
        self.compute_network()

    def _kill_backbone(self):
        """摧毁主干道中继: 精确打击当前流量最集中的节点, 强制岔路绕行"""
        self._pre_collapse_routes = {k: dict(v) for k, v in self.routes.items()}
        # 找承载流量最大的中继 (非 sink)
        load_of = {}
        for r in self.routes.values():
            for nid in (r.get("path") or [])[1:-1]:
                load_of[nid] = load_of.get(nid, 0) + 1
        if not load_of:
            return
        victim_id = max(load_of, key=load_of.get)
        victim = self.nodes[victim_id]
        victim.state = "DEAD"
        self._emit("node_dead", "error",
                   f"☠ 主干道中继 {victim_id} 被摧毁 (承载 {load_of[victim_id]} 条流)",
                   narration=f"☠ 警报:主干道上的关键中继节点 {self._zh(victim_id)} 被摧毁!"
                             f"它原本承载着 {load_of[victim_id]} 条数据流。请观察 3D 画面——"
                             f"数据流将在岔路口拐弯,沿曲折的备用洞穴通道继续向洞口传输…",
                   node=victim_id)
        self.compute_network()

    def _collapse(self):
        """洞顶塌方: 在最繁忙主干链路中点砸落巨石"""
        self._pre_collapse_routes = {k: dict(v) for k, v in self.routes.items()}
        live = [(k, l) for k, l in self.links.items() if l["up"]]
        if not live:
            return
        key, _lk = max(live, key=lambda kv: self.link_load.get(kv[0], 0.0))
        a, b = self.nodes[key[0]], self.nodes[key[1]]
        mid = ((a.x + b.x) / 2, 0.0, (a.z + b.z) / 2)
        for n in self.nodes.values():
            if n.id in key or n.state == "DEAD":
                continue
            if math.dist(mid, (n.x, n.y, n.z)) < 70.0:
                mid = (mid[0] + 50.0, mid[1], mid[2] + 50.0)
                break
        self.obstacles.append({
            "x": round(mid[0], 2), "y": round(mid[1], 2), "z": round(mid[2], 2),
            "r": 38.0, "h": 0.0, "shape": "boulder", "rot": random.uniform(0, 360),
        })
        self._recompute_los()
        self._emit("disaster", "error", f"☄ 洞顶塌方: 巨石阻断 {key[0]} ↔ {key[1]}",
                   narration=f"⚠️ 警报:熔岩管洞顶发生塌方!一块巨石砸落,正好阻断了 "
                             f"{self._zh(key[0])} 与 {self._zh(key[1])} 节点之间的主干信道"
                             f"(视线被完全遮挡,信噪比归零)。多智能体算法正在感知断裂…",
                   a=key[0], b=key[1])
        self.compute_network()

    # ==================================================================
    def snapshot(self) -> dict:
        alive = [n for n in self.nodes.values() if n.state != "DEAD"]
        avg_snr = (sum(n.snr_db for n in alive) / len(alive)) if alive else 0
        avg_soc = (sum(n.battery_soc for n in alive) / len(alive)) if alive else 0
        snap = {
            "tick": self.tick,
            "disaster": self.disaster,
            "mode": self.mode,
            "wave": self.wave,
            "events": list(self.events)[-40:],
            "last_narration": self.last_narration,
            "obstacles": self.obstacles,
            "walls": self.walls,
            "links": [
                {
                    "a": a, "b": b,
                    "snr_db": l["snr_db"], "up": l["up"],
                    "margin_db": l["margin_db"], "ber": l["ber"],
                    "cost": l["cost_ab"], "band": l["band"], "load": l["load"],
                }
                for (a, b), l in self.links.items()
                if physics.distance(self.nodes[a], self.nodes[b]) < 70 * physics.WORLD_SCALE
            ],
            "nodes": {nid: {**n.to_dict(), "blocked_nbrs": self.blocked_info.get(nid, [])}
                      for nid, n in self.nodes.items()},
            "routes": self.routes,
            "traffic": self.traffic,
            "robot": ({
                "x": round(self.robot["pos"][0], 1),
                "y": round(self.robot["pos"][1], 1),
                "z": round(self.robot["pos"][2], 1),
                "u": self.robot["u"], "v": self.robot["v"], "t": round(self.robot["t"], 3),
                "route": self.robot.get("route"),
            } if self.robot else None),
            "stats": {
                "alive": len(alive), "total": len(self.nodes),
                "reachable": sum(1 for r in self.routes.values() if r.get("hop_count", -1) >= 0),
                "coverage_pct": self._coverage(),
                "avg_snr_db": round(avg_snr, 1), "avg_soc_pct": round(avg_soc, 1),
                "max_hop": self.wave.get("max_hop", 0),
                "total_flow": len(self.traffic),
                "obstacles": len(self.obstacles),
                "blocked_pairs": len(self.blocked_pairs),
                "mean_degree": round(2 * len(self.links) / max(1, len(self.nodes)), 2),
            },
        }
        self.history.append({"t": self.tick, **snap["stats"]})
        return snap

    async def run_forever(self, broadcaster):
        next_phys, next_bcast = 0.0, 0.0
        while True:
            now = time.monotonic()
            if now >= next_phys:
                self.tick += 1
                try:
                    for n in self.nodes.values():
                        n.step(dt_hours=0.004)
                    self.compute_network()
                except Exception as e:      # 单 tick 异常不杀死引擎
                    import traceback
                    print("[engine] tick error (ignored):", repr(e))
                    traceback.print_exc()
                next_phys = now + TICK_PHYS_S
            if now >= next_bcast:
                try:
                    await broadcaster(self.snapshot())
                except Exception as e:
                    print("[engine] broadcast error (ignored):", repr(e))
                next_bcast = now + TICK_BROADCAST_S
            await asyncio.sleep(0.02)


ENGINE = SimulationEngine()
