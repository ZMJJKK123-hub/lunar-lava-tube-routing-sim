# -*- coding: utf-8 -*-
"""
救援状态机分支 (RescueMixin)
===============================
职责: PatrolRobot 的"救援多分支推进" —— 弱链加固(ASSIST) / 目标恢复撤离 /
到场无果冷却 / 到场无桥重定位 / 超时放弃 / 回撤点判定。决策入口 _rescue_step
由 robot.py 的 _advance 调用; 移动能力来自 MotionMixin。
"""
import logging   # 标准库: 模块日志 (救援分支结局)
import math   # 标准库: 可桥性预判的距离计算

from ..config import MIN_DEGREE, ROBOT_ID                   # 度数安全线/自身节点标识
from .constants import (HISTORIC_SPOT_GAIN, INVESTIGATE_COOLDOWN,   # 择点收益门槛/核查冷却
                        RANGE, RESCUE_PATIENCE,                    # 通信半径/救援超时
                        SCOUT_BUDGET_TICKS,                        # 侦察预算 (拍)
                        STUCK_GIVEUP_TICKS)                        # 撞墙放弃阈值 (拍)
from .rl_gate import want_deploy   # 道钉学习问询门 (落钉瞬间投/忍决策)

log = logging.getLogger(__name__)   # 本模块日志器


class RescueMixin:
    """职责: PatrolRobot 的救援分支混入。

    属性要求 (由 PatrolRobot.__init__ 提供): self.eng (引擎引用),
    self.state/target/_rescue_since (任务状态), self.stock (道钉库存)。

    调用链: robot._advance -> _rescue_step -> (_on_target_recovered |
    _on_investigate_dry | _on_arrived_dry | _giveup | _fallback_step |
    _advance_to_target[MotionMixin])。
    """

    def _rescue_step(self, tick: int):
        """救援/核查/加固/回撤多态推进: 依目标可达性与到场情况分派。

        Args: tick: 当前仿真 tick。Returns: None。
        Globals Used: None。Calls: 分派到本模块各分支 + MotionMixin 移动。
        """
        eng = self.eng
        tid = self.target[0]
        if self.state == "ASSIST":
            self._assist_step(eng, tid, tick)   # 加固有独立成功判定, 先行分派
            return
        # 撞墙脱困: 长墙围困 (全向受阻连续超阈值) -> 放弃任务, 不再原地空撞
        if self._stuck >= STUCK_GIVEUP_TICKS:
            self._stuck = 0
            self._giveup(eng, tid, tick, "路径被墙体阻断")
            return
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
            self._on_target_recovered(eng, tid, bridging_now)
        elif (not bridging_now and self.state == "INVESTIGATE"
              and self._near((self.target[1], self.target[2]), 30)):
            self._on_investigate_dry(eng, tid, tick)
        elif arrived_dry:
            self._on_arrived_dry(eng, tid, tick)
        elif (tick - self._rescue_since > RESCUE_PATIENCE
              or not bridgeable):
            why = "目标周边无可达节点" if not bridgeable else "救援超时"
            self._giveup(eng, tid, tick, why)
        elif self.state == "FALLBACK":
            self._fallback_step(eng, tid, tick)
        else:
            self._advance_to_target()

    def _assist_step(self, eng, tid, tick):
        """弱链加固推进 (ASSIST): 到自举节点身旁落钉补冗余。
        成功判定与 SOS 救援不同: 目标本就可达 (hop>=0), 完成条件是
        "度数回到安全线"或"已停止自举", 而非恢复可达。

        Args: eng: 引擎; tid: 目标 id; tick: 当前拍。
        Returns: None。Globals Used: MIN_DEGREE/INVESTIGATE_COOLDOWN/
        RESCUE_PATIENCE/RANGE。Calls: _deploy_beacon/_deploy_ok/_giveup/
        _near/_advance_to_target[MotionMixin]。
        """
        n = eng.nodes.get(tid)
        # 撞墙脱困 (最优先, 先于目标状态判定: 带病计数若不清会毒害下一任务):
        # 侦察/择点途中被长墙围困 -> 弃点收工原地落钉; 赶路被困 -> 放弃任务
        if self._stuck >= STUCK_GIVEUP_TICKS:
            self._stuck = 0
            if tick < self._scout_until or self._assist_spot is not None:
                log.info("加固弃点 %s: 采样/择点路径被墙阻断, 原地收工", tid)
                self._assist_spot = None
                self._scout_until = tick
            else:
                self._giveup(eng, tid, tick, "路径被墙体阻断")
            return
        # 完成/失效判定: 目标消失/死亡/不再自举/真实度数回安全线 -> 撤离
        # (真实度数剔除机器人自身边, 否则"站在目标身边"会被误判为已安全;
        #  撤离登记冷却: 机器人离开会掉度数, 防同一目标反复触发)
        deg = self.real_degree(tid)
        if (n is None or n.state == "DEAD"
                or not n.power_boosted or deg >= MIN_DEGREE):
            self.state = "PATROL"
            self.target = None
            self._checked_until[tid] = tick + INVESTIGATE_COOLDOWN
            if n is not None and n.state != "DEAD" and deg >= MIN_DEGREE:
                log.info("加固完成 %s: 度数=%d, 撤离", tid, deg)
                eng._emit("robot_assist_done", "ok",
                          f"🎉 {tid} 链路冗余已补足 ({deg} 条), 机器人撤离",
                          node=tid)
            else:
                log.info("加固结束 %s: 目标恢复或失效, 撤离", tid)
            return
        if self.stock == 0:
            self._giveup(eng, tid, tick, "道钉耗尽")
            return
        if tick - self._rescue_since > RESCUE_PATIENCE:
            self._giveup(eng, tid, tick, "加固超时")
            return
        # 到场 (0.6x 半径内): 先侦察踩点 (ASSIST 不赶时间, 主动采样优于被动旧账)
        if self._near((self.target[1], self.target[2]), RANGE * 0.6):
            tgt = (self.target[1], self.target[2])
            if self._scout_until == 0:               # 一次性启动侦察
                self._scout_vis0 = self._vis_count()
                self._scout_wps = self._scout_waypoints(tgt)
                self._scout_until = tick + SCOUT_BUDGET_TICKS
                log.info("加固侦察 %s: %d 个采样点, 预算 %d 拍, 基线可见 %d",
                         tid, len(self._scout_wps), SCOUT_BUDGET_TICKS,
                         self._scout_vis0)
                eng._emit("robot_scout", "info",
                          f"🤖 先绕 {tid} 侦察踩点 ({len(self._scout_wps)} 个采样位, "
                          f"预算 {SCOUT_BUDGET_TICKS} 拍), 再选最佳落钉位",
                          narration="🤖 机器人先围着目标转一小圈——把周围各个"
                                    "位置能听见几个节点都记下来, 再挑听得最全"
                                    "的地方投放道钉。")
            if tick < self._scout_until:              # 侦察期: 达标即早退, 否则踩点
                spot = self._best_historic_spot(tgt)
                if spot is None or spot[2] < self._scout_vis0 + HISTORIC_SPOT_GAIN:
                    wp = next((p for p in self._scout_wps
                               if not self._near(p, 40)), None)
                    if wp is not None:
                        self._move_toward(wp)
                        return
                    self._scout_until = tick          # 路点走完: 提前收工
            # 落钉选点 (侦察结束/提前达标): 历史最佳严格优于当前位置、且
            # 机器人->点位不穿墙 (墙后的旧面包屑不可达) 才挪
            spot = self._best_historic_spot(tgt)
            if (spot is not None and not self._near(spot, 30)
                    and spot[2] > self._vis_count()
                    and math.hypot(spot[0] - self.node.x,
                                   spot[1] - self.node.z) <= RANGE * 0.6
                    and not self._hit_wall((self.node.x, self.node.z),
                                           (spot[0], spot[1]))):
                if self._assist_spot is None:
                    log.info("加固择点: 移往最佳点位 (%.0f,%.0f) 可见 %d 节点",
                             spot[0], spot[1], spot[2])
                    eng._emit("robot_spot", "info",
                              f"🤖 选定最佳落钉位 (此地可同时看见 "
                              f"{spot[2]} 个节点), 移过去投放",
                              narration="🤖 机器人记得自己在这条路上各个位置"
                                        "能听见几个节点——它挑了一个听得最全的"
                                        "位置去投放道钉, 一根钉照顾更多邻居。")
                self._assist_spot = spot
                self._move_toward((spot[0], spot[1]))
                return
            if self._deploy_ok() and want_deploy(self, tid, "assist"):
                log.info("加固落钉: 为 %s 补链 (原 %d 条)", tid, n.neighbors)
                self._deploy_beacon()
            elif self._deploy_ok():
                pass   # 学习器选择「忍」: 本拍不落钉, 冷却后重评 (任务继续, 超时兜底)
            else:
                self._giveup(eng, tid, tick, "落点受限 (巨石/钉距), 无法加固")
            return
        self._advance_to_target()

    def _on_target_recovered(self, eng, tid, bridging_now):
        """目标已恢复可达 (常为机器人自身路过桥接)。
        若此刻正由机器人本体桥着而道钉未落地 (落点被巨石卡住等),
        不撤离 —— 继续朝目标微调位置, 下一拍重试落钉。

        Args: eng: 引擎; tid: 目标 id; bridging_now: 本体是否正在桥接。
        Returns: None (满足条件时撤离回 PATROL)。Globals Used: None。
        """
        if (not bridging_now or self.stock == 0
                or self._deployed_at == eng.tick):
            self.state = "PATROL"
            self.target = None
            log.info("目标恢复可达 %s -> 撤离", tid)
            eng._emit("robot_patrol", "info",
                      f"🤖 {tid} 已恢复可达, 机器人撤离")

    def _on_investigate_dry(self, eng, tid, tick):
        """到场无 SOS 且仍不可达: 节点已死或深隔断 -> 冷却登记防空转。

        Args: eng: 引擎; tid: 目标 id; tick: 当前拍 (冷却起点)。
        Returns: None。Globals Used: INVESTIGATE_COOLDOWN (constants)。
        """
        self._checked_until[tid] = tick + INVESTIGATE_COOLDOWN
        self.state = "PATROL"
        self.target = None
        log.info("核查无果 %s: 现场无SOS且不可达, 冷却至 t%d",
                 tid, self._checked_until[tid])
        eng._emit("robot_checked", "info",
                  f"🔎 {tid} 现场无呼救 (已死或超出可桥范围), 登记冷却",
                  node=tid)

    def _on_arrived_dry(self, eng, tid, tick):
        """到场无桥: ①绕目标环形搜索"双向可见"点位 (治巨石挡视线);
        ②退而求面包屑 —— 来路上最近的"见过主网"点位。

        Args: eng: 引擎; tid: 目标 id; tick: 当前拍。Returns: None。
        Globals Used: RANGE (备选点位可达性校验)。
        Calls: _orbit_spot/_last_net_crumb[MotionMixin] / _giveup。
        """
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
            log.info("到场无桥 %s: %s (%.0f,%.0f)", tid, how, spot[0], spot[1])
            eng._emit("robot_fallback", "info",
                      f"🤖 {how} ({spot[0]:.0f},{spot[1]:.0f}) 尝试搭桥",
                      narration=f"🤖 机器人换个角度接近——"
                                f"绕到能同时看见 {eng._zh(tid)} 和主网络的位置搭桥。")
        else:
            log.info("到场无桥 %s: 无可用备选点位", tid)
            self._giveup(eng, tid, tick, "到场无法连通且无可用备选点位")

    def _fallback_step(self, eng, tid, tick):
        """回撤到已知有网点位; 途中桥接检测自动尝试 (两端同时在望即落钉)。

        Args: eng: 引擎; tid: 目标 id; tick: 当前拍。Returns: None。
        Globals Used: RESCUE_PATIENCE (回撤超时)。
        """
        if self._near((self.target[1], self.target[2]), 40):
            self._giveup(eng, tid, tick, "回撤点仍无法桥接")
        elif tick - self._rescue_since > RESCUE_PATIENCE:
            self._giveup(eng, tid, tick, "回撤超时")
        else:
            self._advance_to_target()
