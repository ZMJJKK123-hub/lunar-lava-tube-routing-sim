# -*- coding: utf-8 -*-
"""
巡检机器人: SOS 听测 + 道钉投放 (物理搭桥自愈) —— 独立模块, 引擎只挂配置
================================================================
职责边界 (与引擎低耦合):
  - 本模块持有机器人全部逻辑: 移动状态机 / SOS 判定与信标 / 道钉投放;
  - 引擎每 tick 只调两个挂点: inject_links(links) [建边后, Dijkstra 前]
    与 tick(tick) [路由算完后]; ROBOT_ENABLED=False 时零痕迹;
  - 机器人是"全同步观察者": 持有完整链但不出块不遥测 (chain_net 注册为
    非共识节点, 不进轮值名单); 孤岛数据经它/道钉回流主网。

SOS 语义: 无线电层广播信标 —— hop<0 连续 SOS_ARM_TICKS 的节点每
SOS_BEACON_EVERY tick 发一帧 (按节点编号错峰), 300m+LOS 内可闻;
机器人只信耳朵 (覆盖内听测), 不感知全局分裂 (全局态势由账本侧边栏承担)。

状态机: PATROL(随机巡逻) -> 听到覆盖内 SOS -> RESCUE(赶往) ->
"SOS 可闻 且 自身连通主网"同时成立 -> 原地投放道钉(永久中继) -> 孤岛自愈。
"""
import math
import random

from .node import Node
from . import physics

ROBOT_ID = "ROBOT"
RANGE = 300.0            # 通信半径 (世界米; = 30 sim x WORLD_SCALE)
SPEED = 60.0             # 移动速度 (世界单位/tick)
SOS_ARM_TICKS = 4        # 连续失联 N tick 才开始呼救 (防瞬断误报)
SOS_BEACON_EVERY = 10    # SOS 信标节奏 (tick, 按节点编号错峰)
DEPLOY_GAP = 100.0       # 距既有道钉 < 此值不重复投放
RESCUE_PATIENCE = 80     # 救援超时 (tick): 物理不可救则放弃回巡逻
RESCUE_DEAF = 120        # 放弃后的"耳聋期" (tick): 撤离期间不再听测, 防无限重试
ROBOT_CHAIN_INTEL = True  # 链上情报: 用自身世界状态的心跳超时, 主动侦查失联节点
                           # (False = 纯耳朵模式, 只听 300m 内 SOS —— 教学对比用)
STALE_AFTER = 150          # 遥测停更超过此 tick 视为失联嫌疑 (遥测周期 60)
INVESTIGATE_COOLDOWN = 300 # 查无实据(已死/深隔断)后的冷却, 防反复空趟
TRAIL_MAX = 240            # 面包屑轨迹上限 (tick): (x, z, 是否连通主网)
ROBOT_LINK_PENALTY = 50.0  # 机器人边代价罚: 健康流量永不借道 (走它不如绕路),
                          # 只有孤岛 (无路可走) 才经它回流 -> 桥接检测精确
BEACON_STOCK = 6         # 携带道钉数


def plan_robot_path(p_from, p_to):
    """路径规划占位符: 当前直线返回 [起点, 终点]。
    保持签名 (起点, 终点) -> [路径点序列], 可整体替换为 A*/RCSPA/势场等。"""
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


class PatrolRobot:
    def __init__(self, engine):
        self.eng = engine
        c = engine.chambers[0]
        self.node = Node(id=ROBOT_ID, x=c["x"], y=0.0, z=c["z"], role="robot",
                         battery_mah=120000.0, battery_capacity=120000.0,
                         ant_gain_dbi=5.0)
        self.node.radio = "TXRX"
        self.state = "PATROL"
        self.waypoint = None
        self.target = None                  # (nid, x, z) RESCUE 目标
        self.stock = BEACON_STOCK
        self._deployed = 0
        self._rescue_since = 0
        self._deaf_until = 0
        self._checked_until: dict = {}   # nid -> 已核查冷却截止 tick
        self._deployed_at = -1           # 最近一次落钉 tick
        self._slide = 0                  # 沿墙绕行方向记忆 (0=直行, ±1=左/右)
        self.trail: list = []            # 面包屑: [(x,z,conn)] 核查/救援/回撤途中逐 tick 记录
        self._iso: dict[str, int] = {}      # nid -> 连续失联 tick 数
        self.sos_active: set[str] = set()   # 正在呼救的节点
        # 全同步观察者入链: 转发/追块全真, 但不在共识名单 (不出块/不遥测)
        engine.chain_net.register_node(ROBOT_ID)


    # ---------- 几何检测 (模块私有; 只读引擎的 walls/obstacles/chambers) ----------
    def _hit_wall(self, p1, p2) -> bool:
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

    # ======== 挂点①: 链路注入 (compute_network 建边后, Dijkstra 前) ========
    def inject_links(self, links: dict):
        """ROBOT<->覆盖内 (300m+LOS) 节点的链路注入 links。
        注入点位于链路生死事件之后 -> 机器人移动引起的边翻动不产生事件。"""
        rp = self.node
        for n in self.eng.nodes.values():
            if n.state == "DEAD":
                continue
            key = tuple(sorted((n.id, ROBOT_ID)))   # 全网统一: 排序键
            if key in links:
                continue
            if physics.distance(rp, n) > RANGE:
                continue
            if not self._los_clear((rp.x, rp.z), (n.x, n.z)):
                continue
            lab = physics.link_budget(n, rp)
            lba = physics.link_budget(rp, n)
            if lab is None or lba is None:
                continue
            # 代价罚: 机器人是"最后手段"中继 —— 正常流量绕行都更便宜,
            # 唯有无路可走的孤岛才会经由它 (也使桥接检测不会误判)
            if key[0] == n.id:
                c_ab = physics.link_cost(n, rp, lab, 0.0) + ROBOT_LINK_PENALTY
                c_ba = physics.link_cost(rp, n, lba, 0.0) + ROBOT_LINK_PENALTY
            else:
                c_ab = physics.link_cost(rp, n, lba, 0.0) + ROBOT_LINK_PENALTY
                c_ba = physics.link_cost(n, rp, lab, 0.0) + ROBOT_LINK_PENALTY
            links[key] = {**lab, "load": 0.0, "cost_ab": c_ab, "cost_ba": c_ba}

    # ======== 挂点②: 每 tick 推进 (路由算完后) ========
    def tick(self, tick: int):
        self._update_sos(tick)
        self._advance(tick)

    # ---- SOS: 判定 / 信标 / 停发 ----
    def _update_sos(self, tick: int):
        eng = self.eng
        for n in eng.nodes.values():
            if n.role == "beacon" or n.id == eng.sink_id or n.state == "DEAD":
                continue
            hop = eng.routes.get(n.id, {}).get("hop_count", -1)
            if hop >= 0:
                if n.id in self.sos_active:
                    self.sos_active.discard(n.id)
                    eng._emit("sos_stop", "ok", f"✔ {n.id} 重新可达, SOS 停发",
                              narration=f"✅ {eng._zh(n.id)} 重新接回网络,呼救解除。",
                              node=n.id)
                self._iso.pop(n.id, None)
                continue
            self._iso[n.id] = self._iso.get(n.id, 0) + 1
            if self._iso[n.id] == SOS_ARM_TICKS:
                self.sos_active.add(n.id)
                eng._emit("sos_start", "error",
                          f"🆘 {n.id} 失联 {SOS_ARM_TICKS} tick, 开始广播 SOS",
                          narration=f"🆘 {eng._zh(n.id)} 已连续失联,开始向外广播 SOS 求援信号"
                                    f"——巡检机器人若巡至其通信范围内就能听到。",
                          node=n.id)
            # 信标帧: 每 N tick 一帧 (错峰), 300m+LOS 内可闻 -> 渲染总线上报
            if (n.id in self.sos_active
                    and (tick + int(n.id.split("-")[1])) % SOS_BEACON_EVERY == 0):
                self._emit_beacons(n)

    def _emit_beacons(self, n):
        """SOS 信标的物理呈现: 覆盖内最多 3 个邻居 + (若在圈内) 机器人"""
        sent = 0
        for m in self.eng.nodes.values():
            if m.id == n.id or sent >= 3:
                continue
            if physics.distance(n, m) > RANGE:
                continue
            if not self._los_clear((n.x, n.z), (m.x, m.z)):
                continue
            self.eng.vis_packet(n.id, m.id, "SOS", relayed=False)
            sent += 1
        if (physics.distance(n, self.node) <= RANGE
                and self._los_clear((n.x, n.z),
                                        (self.node.x, self.node.z))):
            self.eng.vis_packet(n.id, ROBOT_ID, "SOS", relayed=False)

    def _connected(self) -> bool:
        """当前位置是否看得见可达 (hop>=0) 邻居 —— "主网在望" (面包屑采样用)"""
        eng = self.eng
        for n in eng.nodes.values():
            if n.state == "DEAD":
                continue
            if eng.routes.get(n.id, {}).get("hop_count", -1) < 0:
                continue
            if (physics.distance(self.node, n) <= RANGE
                    and self._los_clear((self.node.x, self.node.z), (n.x, n.z))):
                return True
        return False

    def _crumb(self):
        """面包屑: 记录 (位置, 此处能否看见主网) —— 走一步看一步"""
        if len(self.trail) >= TRAIL_MAX:
            self.trail.pop(0)
        self.trail.append((round(self.node.x, 1), round(self.node.z, 1),
                           self._connected()))

    def _on_mission_for(self, nid) -> bool:
        """同一目标的救援/核查/回撤是否正在进行 (防链上情报每拍重触发)"""
        return (self.state in ("RESCUE", "INVESTIGATE", "FALLBACK")
                and self.target is not None and self.target[0] == nid)

    def _start_mission(self, state, nid, tick):
        """开启/切换任务: 换目标才清面包屑 (INVESTIGATE<->FALLBACK 交接保留)"""
        if not (self.target and self.target[0] == nid):
            self.trail = []
        self.state = state
        self._rescue_since = tick
        if state == "RESCUE":
            self.eng._emit("robot_rescue", "info",
                           f"🤖 机器人听到 {nid} 的 SOS, 前往救援",
                           narration=f"🤖 巡检机器人听到了 {self.eng._zh(nid)} 的呼救!"
                                     f"正在赶往事发区域,准备投放道钉搭建中继。",
                           node=nid)
        else:
            self.eng._emit("robot_investigate", "info",
                           f"🔎 链上心跳超时: {nid} 已 {STALE_AFTER}+ tick 未上报, 前往核查",
                           narration=f"🔎 机器人的账本发现 {self.eng._zh(nid)} 很久没有上链心跳了"
                                     f"——可能已失联。它正循着最后一次上报的位置前去看个究竟。",
                           node=nid)

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
        for x, z, conn in reversed(self.trail):
            if conn:
                return (x, z)
        return None

    def _hear(self):
        """听测: 覆盖内 (300m+LOS) 最近的呼救节点 -> (nid, node) 或 None"""
        best, bd = None, RANGE
        for nid in self.sos_active:
            n = self.eng.nodes.get(nid)
            if n is None:
                continue
            d = physics.distance(self.node, n)
            if d <= bd and self._los_clear((self.node.x, self.node.z),
                                               (n.x, n.z)):
                bd, best = d, (nid, n)
        return best

    # ---- 链上情报: 心跳超时侦查 (机器人是全同步观察者, 这是它的本职) ----
    def _chain_intel(self, tick):
        """扫自身链上世界状态: 遥测停更超期的存活节点 = 失联嫌疑 (带最后已知
        坐标)。返回 (距离, nid, x, z) 最近者或 None。
        注意: 这是滞后情报 (失联 ~2.5 个遥测周期后显形), SOS 才是零滞时确认。"""
        if not ROBOT_CHAIN_INTEL:
            return None
        me = self.eng.chain_net.nodes.get(ROBOT_ID)
        if me is None:
            return None
        best = None
        for nid, st in me.world_state.items():
            if tick - st.get("tick", 0) < STALE_AFTER:
                continue
            if st.get("state") == "DEAD":
                continue
            if tick < self._checked_until.get(nid, 0):
                continue
            n = self.eng.nodes.get(nid)
            if n is None or n.state == "DEAD":
                continue
            if self.eng.routes.get(nid, {}).get("hop_count", -1) >= 0:
                continue               # 路由可达 (链只是慢): 不值得出任务
            sx, sz = st.get("x", n.x), st.get("z", n.z)
            d = math.hypot(sx - self.node.x, sz - self.node.z)
            if best is None or d < best[0]:
                best = (d, nid, sx, sz)
        return best

    # ---- 状态机与移动 ----
    def _advance(self, tick: int):
        eng = self.eng
        # 桥接检测: 有真实节点的路径正经过机器人 (代价罚保证只有孤岛会这样)
        # -> 此地此刻已被机器人物理验证可搭桥, 落道钉固化, 机器人继续巡逻
        if (tick >= self._deaf_until and self.stock > 0 and self._deploy_ok()
                and any(ROBOT_ID in (r.get("path") or [])
                        for nid, r in eng.routes.items() if nid != ROBOT_ID)):
            self._deploy_beacon()

        heard = (self._hear()
                 if tick >= self._deaf_until and self.state != "FALLBACK"
                 else None)
        if heard:
            # 实时 SOS 优先 (零滞时); 同一目标进行中不重复开任务
            if not self._on_mission_for(heard[0]):
                self._start_mission("RESCUE", heard[0], tick)
            self.target = (heard[0], heard[1].x, heard[1].z)
        else:
            # 链上情报: 无实时 SOS 时, 朝最近失联嫌疑的最后已知坐标侦查
            susp = self._chain_intel(tick) if tick >= self._deaf_until else None
            if susp:
                _, nid, sx, sz = susp
                if not self._on_mission_for(nid):
                    self._start_mission("INVESTIGATE", nid, tick)
                if self.state != "FALLBACK":       # 回撤/重定位中不覆盖目标点位
                    self.target = (nid, sx, sz)
            elif self.state in ("RESCUE", "INVESTIGATE"):
                self.state = "PATROL"
                self.target = None
                eng._emit("robot_patrol", "info", "🤖 无呼救与嫌疑, 恢复巡逻")

        # 面包屑: 救援/核查/回撤途中逐 tick 记录 (位置, 是否连通主网)
        if self.state in ("RESCUE", "INVESTIGATE", "FALLBACK"):
            self._crumb()

        if self.state in ("RESCUE", "INVESTIGATE", "FALLBACK") and self.target:
            tid = self.target[0]
            bridging_now = any(ROBOT_ID in (r.get("path") or [])
                               for nid, r in eng.routes.items() if nid != ROBOT_ID)
            # 可桥性预判: 目标 2x通信半径内不存在任何可达节点 -> 单钉必不够
            bridgeable = any(
                eng.routes.get(n.id, {}).get("hop_count", -1) >= 0
                and math.hypot(n.x - self.target[1], n.z - self.target[2]) <= 2 * RANGE
                for n in eng.nodes.values() if n.id != tid)
            arrived_dry = (self.state != "FALLBACK" and not bridging_now
                           and self._near((self.target[1], self.target[2]), 250))
            if eng.routes.get(tid, {}).get("hop_count", -1) >= 0:
                # 目标已恢复可达 (常为机器人自身路过桥接)。
                # 若此刻正由机器人本体桥着而道钉未落地 (落点被巨石卡住等),
                # 不撤离 —— 继续朝目标微调位置, 下一拍重试落钉
                if (not bridging_now or self.stock == 0
                        or self._deployed_at == tick):
                    self.state = "PATROL"
                    self.target = None
                    eng._emit("robot_patrol", "info",
                              f"🤖 {tid} 已恢复可达, 机器人撤离")
            elif (not bridging_now and self.state == "INVESTIGATE"
                  and self._near((self.target[1], self.target[2]), 30)):
                # 到场无 SOS 且仍不可达: 节点已死或深隔断 -> 冷却登记防空转
                self._checked_until[tid] = tick + INVESTIGATE_COOLDOWN
                self.state = "PATROL"
                self.target = None
                eng._emit("robot_checked", "info",
                          f"🔎 {tid} 现场无呼救 (已死或超出可桥范围), 登记冷却",
                          node=tid)
            elif arrived_dry:
                # 到场无桥: ①绕目标环形搜索"双向可见"点位 (治巨石挡视线);
                # ②退而求面包屑 —— 来路上最近的"见过主网"点位
                spot = self._orbit_spot((self.target[1], self.target[2]))
                how = "绕障重定位到双向可见点"
                if spot is None:
                    spot = self._last_net_crumb()
                    how = "循来路回撤到有网点位"
                if (spot is not None
                        and math.hypot(spot[0] - self.target[1],
                                       spot[1] - self.target[2]) <= 2 * RANGE):
                    self.state = "FALLBACK"
                    self._rescue_since = tick
                    self.target = (tid, spot[0], spot[1])
                    eng._emit("robot_fallback", "info",
                              f"🤖 {how} ({spot[0]:.0f},{spot[1]:.0f}) 尝试搭桥",
                              narration=f"🤖 机器人换个角度接近——"
                                        f"绕到能同时看见 {eng._zh(tid)} 和主网络的位置搭桥。")
                else:
                    self._giveup(eng, tid, tick, "到场无法连通且无可用备选点位")
            elif (tick - self._rescue_since > RESCUE_PATIENCE
                  or not bridgeable):
                why = "目标周边无可达节点" if not bridgeable else "救援超时"
                self._giveup(eng, tid, tick, why)
            elif self.state == "FALLBACK":
                # 回撤到已知有网点位; 途中桥接检测自动尝试 (两端同时在望即落钉)
                if self._near((self.target[1], self.target[2]), 40):
                    self._giveup(eng, tid, tick, "回撤点仍无法桥接")
                elif tick - self._rescue_since > RESCUE_PATIENCE:
                    self._giveup(eng, tid, tick, "回撤超时")
                else:
                    dest = (self.target[1], self.target[2])
                    for wp in plan_robot_path((self.node.x, self.node.z), dest)[1:]:
                        self._move_toward(wp)
            else:
                dest = (self.target[1], self.target[2])
                for wp in plan_robot_path((self.node.x, self.node.z), dest)[1:]:
                    self._move_toward(wp)
        else:
            if self.waypoint is None or self._near(self.waypoint):
                self.waypoint = self._rand_waypoint()
            self._move_toward(self.waypoint)

    def _giveup(self, eng, tid, tick, why):
        self.state = "PATROL"
        self.target = None
        self._deaf_until = tick + RESCUE_DEAF
        self._checked_until[tid] = tick + INVESTIGATE_COOLDOWN
        eng._emit("robot_giveup", "warn",
                  f"🤖 放弃救援 ({why}): 超出单枚道钉可桥范围, 机器人撤离",
                  narration="🤖 机器人无法同时连通两侧网络,"
                            "本次救援放弃——它将驶离该区域继续巡逻。")

    def _near(self, p, dist=30.0):
        return math.hypot(self.node.x - p[0], self.node.z - p[1]) <= dist

    def _rand_waypoint(self):
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
                    self.node.x, self.node.z = nxt
                    return
        self._slide = 0
        self.waypoint = None           # 四面受阻: 换路点

    # ---- 道钉投放 ----
    def _deploy_ok(self) -> bool:
        eng = self.eng
        if not eng._in_tube((self.node.x, 0.0, self.node.z)):
            return False
        for o in eng.obstacles:            # 不砸在石头上
            if math.hypot(self.node.x - o["x"], self.node.z - o["z"]) < o["r"] + 30:
                return False
        for n in eng.nodes.values():       # 距既有道钉留间隔
            if (n.role == "beacon"
                    and math.hypot(self.node.x - n.x, self.node.z - n.z) < DEPLOY_GAP):
                return False
        return True

    def _deploy_beacon(self):
        eng = self.eng
        self._deployed += 1
        self._deployed_at = eng.tick
        bid = f"BEACON-{self._deployed:02d}"
        b = Node(id=bid, x=round(self.node.x, 1), y=0.0, z=round(self.node.z, 1),
                 role="beacon", temp_c=-35.0, tilt_deg=0.0,
                 ant_gain_dbi=5.0, radiation_rad=0.0)
        eng.nodes[bid] = b
        eng.chain_net.register_node(bid)   # 全同步哑节点: 转发链包, 不出块不遥测
        self.stock -= 1
        eng._emit("beacon_deploy", "ok",
                  f"📍 投放道钉 {bid} (库存余 {self.stock} 枚), 永久中继入网",
                  narration=f"📍 巡检机器人在此投放了一根备用通信桩作中转!"
                            f"道钉将常驻此地,失联区域的数据将从它身上重新接回主网。",
                  node=bid)

    # ---- 快照 ----
    def export(self) -> dict:
        return {"x": round(self.node.x, 1), "y": 0.0, "z": round(self.node.z, 1),
                "state": self.state,
                "target": self.target[0] if self.target else None,
                "stock": self.stock, "sos": sorted(self.sos_active),
                "trail": self.trail[::2]}
