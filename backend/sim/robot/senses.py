# -*- coding: utf-8 -*-
"""
机器人感知与任务挑选 (SenseMixin)
====================================
职责: PatrolRobot 的"耳目" —— SOS 听测/链上情报侦查/主网在望判定/
任务开启判定。只读取引擎与账本状态, 不做任何移动或状态迁移决策
(决策在 robot.py 的状态机)。
依赖: MotionMixin._los_clear (视线判定), physics.distance (测距)。
"""
import logging   # 标准库: 模块日志 (SOS/情报/任务)
import math   # 标准库: 嫌疑目标距离计算 (_chain_intel)

from ..config import MIN_DEGREE, ROBOT_ID         # 度数安全线/协议标识: 自身节点 ID
from .. import physics                         # 物理层: distance/link_budget
from .constants import (RANGE, SOS_ARM_TICKS, SOS_BEACON_EVERY,  # 听测节拍
                        ROBOT_CHAIN_INTEL, STALE_AFTER,          # 情报开关/超时
                        FRAGILE_FRESH_TICKS)                     # 链上弱链情报新鲜窗口

log = logging.getLogger(__name__)   # 本模块日志器


class SenseMixin:
    """职责: PatrolRobot 的感知能力混入。

    属性要求 (由 PatrolRobot.__init__ 提供): self.eng (引擎引用),
    self.node (机器人伪节点), self.sos_active (呼救节点集合),
    self._iso (连续失联计数), self._checked_until (核查冷却表),
    self.trail (面包屑), self.state/self.target (任务状态)。

    调用链: tick() -> _update_sos (呼救判定) ; _advance -> _hear (实时)
    / _chain_intel (滞后情报) -> _start_mission (开任务)。
    """

    def _update_sos(self, tick: int):
        """逐节点判定失联: hop<0 连续 SOS_ARM_TICKS -> 呼救; 恢复可达或报废 -> 停发"""
        eng = self.eng
        for n in eng.nodes.values():
            if n.role == "beacon" or n.id == eng.sink_id:
                continue
            if n.state == "DEAD":
                # 报废即摘除: 否则 sos_active 残留 -> 画面一直画死人的 SOS 环,
                # 且 _hear 不查存活会把机器人引向尸体
                if n.id in self.sos_active:
                    self.sos_active.discard(n.id)
                    log.info("SOS 终止 %s: 节点已报废", n.id)
                    eng._emit("sos_stop", "info", f"🕯 {n.id} 已报废, SOS 信标静默",
                              node=n.id)
                self._iso.pop(n.id, None)
                continue
            hop = eng.routes.get(n.id, {}).get("hop_count", -1)
            if hop >= 0:
                if n.id in self.sos_active:
                    self.sos_active.discard(n.id)
                    log.info("SOS 解除 %s: 重新可达", n.id)
                    eng._emit("sos_stop", "ok", f"✔ {n.id} 重新可达, SOS 停发",
                              narration=f"✅ {eng._zh(n.id)} 重新接回网络,呼救解除。",
                              node=n.id)
                self._iso.pop(n.id, None)
                continue
            self._iso[n.id] = self._iso.get(n.id, 0) + 1
            if self._iso[n.id] == SOS_ARM_TICKS:
                self.sos_active.add(n.id)
                log.warning("SOS 启动 %s: 连续失联 %d tick", n.id, SOS_ARM_TICKS)
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

    def real_degree(self, nid) -> int:
        """节点真实活跃链路数: 剔除机器人自身注入的边 (临时冗余, 一走即逝)。
        度数判定 (弱链听测/加固完成) 必须用它 —— 机器人站在目标身边时,
        自身边会把 n.neighbors 顶高, 令目标"看起来安全"而错失帮助。"""
        return sum(1 for (a, b), l in self.eng.links.items()
                   if nid in (a, b) and l["up"] and ROBOT_ID not in (a, b))

    def _hear_fragile(self):
        """弱链听测: 覆盖内 (300m+LOS) 正以高功率自举且真实链路低于安全线的
        最近节点 -> (nid, node) 或 None。物理依据: 自举 = 发射功率比额定高
        数倍, 近邻一耳即辨; 度数已回安全线(仅滞回未回落)或已呼救的不选 ——
        前者无需帮助, 后者让位 SOS 主通道 (优先级更高)。"""
        best, bd = None, RANGE
        for n in self.eng.nodes.values():
            if n.state == "DEAD" or n.role == "beacon":
                continue
            if not n.power_boosted or n.id in self.sos_active:
                continue
            if self.real_degree(n.id) >= MIN_DEGREE:
                continue
            if self.eng.tick < self._checked_until.get(n.id, 0):
                continue               # 加固冷却中 (刚帮过/落点受限)
            d = physics.distance(self.node, n)
            if d <= bd and self._los_clear((self.node.x, self.node.z),
                                               (n.x, n.z)):
                bd, best = d, (n.id, n)
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
        if best:
            log.info("链上情报命中 %s: 遥测停更 %d tick 距离 %.0fm -> 前往核查",
                     best[1], tick - me.world_state[best[1]].get("tick", 0), best[0])
        return best

    def _chain_fragile(self, tick):
        """扫自身链上世界状态: 遥测新鲜且 pboost=True 的存活节点 = 弱链加固
        候选 (带最后已知坐标)。返回 (距离, nid, x, z) 最近者或 None。
        与 _chain_intel 对偶: 那个找"消失的", 这个找"还在喊弱的";
        情报滞后至多一个遥测周期, 到场由 _assist_step 现场复核 (已恢复则撤离)。"""
        if not ROBOT_CHAIN_INTEL:
            return None
        me = self.eng.chain_net.nodes.get(ROBOT_ID)
        if me is None:
            return None
        best = None
        for nid, st in me.world_state.items():
            age = tick - st.get("tick", 0)
            if age < 0 or age > FRAGILE_FRESH_TICKS:
                continue               # 过期情报: pboost 可能已不成立
            if not st.get("pboost") or st.get("state") == "DEAD":
                continue
            if st.get("hop", -1) < 0:
                continue               # 孤岛归 SOS/失联核查管 (优先级更高)
            if nid in self.sos_active:
                continue
            if tick < self._checked_until.get(nid, 0):
                continue               # 加固冷却中
            n = self.eng.nodes.get(nid)
            if n is not None and (n.state == "DEAD" or not n.power_boosted):
                continue               # 现场已知已恢复/已死: 不为旧情报跑腿
            sx, sz = st.get("x", 0.0), st.get("z", 0.0)
            d = math.hypot(sx - self.node.x, sz - self.node.z)
            if best is None or d < best[0]:
                best = (d, nid, sx, sz)
        if best:
            log.info("链上弱链情报命中 %s: pboost 遥测龄 %d tick 距离 %.0fm -> 前往加固",
                     best[1], tick - me.world_state[best[1]].get("tick", 0), best[0])
        return best

    # ---- 任务生命周期 ----
    def _on_mission_for(self, nid) -> bool:
        """同一目标的救援/核查/加固/回撤是否正在进行 (防每拍重触发)"""
        return (self.state in ("RESCUE", "INVESTIGATE", "FALLBACK", "ASSIST")
                and self.target is not None and self.target[0] == nid)

    def _start_mission(self, state, nid, tick, via="ear"):
        """开启/切换任务: 换目标才清面包屑 (INVESTIGATE<->FALLBACK 交接保留);
        via 标记情报来源 (ear=听测 / chain=账本) 仅供事件文案区分。"""
        if not (self.target and self.target[0] == nid):
            self.trail = []
        self._assist_spot = None         # 上一次任务的加固择点不跨任务复用
        self._scout_until, self._scout_wps = 0, []   # 侦察状态同样不跨任务
        log.info("任务开启 %s -> %s 目标=%s via=%s (tick=%d)",
                 self.state, state, nid, via, tick)
        self.state = state
        self._rescue_since = tick
        if state == "RESCUE":
            self.eng._emit("robot_rescue", "info",
                           f"🤖 机器人听到 {nid} 的 SOS, 前往救援",
                           narration=f"🤖 巡检机器人听到了 {self.eng._zh(nid)} 的呼救!"
                                     f"正在赶往事发区域,准备投放道钉搭建中继。",
                           node=nid)
        elif state == "ASSIST":
            if via == "chain":
                self.eng._emit("robot_assist", "info",
                               f"🤖 账本发现 {nid} 正以高功率自救, 循链上坐标前往加固",
                               narration=f"🤖 机器人的区块链账本里, {self.eng._zh(nid)} "
                                         f"的上报带着自举标记——即使远在听测圈外,"
                                         f"它也能循着链上坐标赶去投放道钉。", node=nid)
            else:
                self.eng._emit("robot_assist", "info",
                               f"🤖 听到 {nid} 正以高功率自救 (链路不足), 前往投钉加固",
                               narration=f"🤖 机器人的电台听到 {self.eng._zh(nid)} "
                                         f"正在拼命放大功率保持连线——它赶过去投放一根道钉,"
                                         f"帮这根通信桩分担压力。", node=nid)
        else:
            self.eng._emit("robot_investigate", "info",
                           f"🔎 链上心跳超时: {nid} 已 {STALE_AFTER}+ tick 未上报, 前往核查",
                           narration=f"🔎 机器人的账本发现 {self.eng._zh(nid)} 很久没有上链心跳了"
                                     f"——可能已失联。它正循着最后一次上报的位置前去看个究竟。",
                           node=nid)
