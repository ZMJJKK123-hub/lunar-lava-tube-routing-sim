# -*- coding: utf-8 -*-
"""
弱链加固任务 (AssistMixin)
============================
职责: 巡检机器人的"弱链加固"任务分支 —— 赶到自举(高功率自救)节点身旁,
先环形踩点采样, 再择点投放道钉补链路冗余。
v2 修复 (落点受限无限循环的治本处): 落点合法性 (管内/不压巨石/距既有
道钉留间隔) 提前进入踩点采样与择点 —— 旧版只按"可见数"挑点, 站过去才
发现放不了钉, "落点受限"放弃 -> 冷却后重来, 永远放不出钉; 且投完钉后
下一拍站位必犯钉距, 靠误打误撞的放弃收尾。新版: 采样只踩合法点, 站位
非法先挪到最近合法点, 落钉成功即撤离冷却 (一钉一任务, 防连发)。
依赖: 引擎公开状态 / MotionMixin (移动/踩点/面包屑) / DeployMixin
(落点判定与投放) / rl_gate.want_deploy (投/忍问询)。
"""
import logging   # 标准库: 模块日志 (加固任务分支)
import math   # 标准库: 择点距离判定

from ..config import MIN_DEGREE, ROBOT_ID                # 度数安全线/自身标识
from .constants import (HISTORIC_SPOT_DECAY, HISTORIC_SPOT_GAIN,   # 择点收益/半衰
                        INVESTIGATE_COOLDOWN, RANGE,      # 撤离冷却/通信半径
                        RESCUE_PATIENCE, SCOUT_BUDGET_TICKS,        # 超时/侦察预算
                        STUCK_GIVEUP_TICKS)               # 撞墙放弃阈值
from .rl_gate import want_deploy   # 道钉学习问询门 (落钉瞬间投/忍决策)

log = logging.getLogger(__name__)   # 本模块日志器


class AssistMixin:
    """职责: PatrolRobot 的弱链加固分支混入。

    属性要求 (由 PatrolRobot.__init__ 提供): self.eng / self.node /
    self.state / self.target / self._rescue_since / self.stock /
    self._stuck / self.trail / self._assist_spot / self._scout_until /
    self._scout_wps / self._scout_vis0 / self._checked_until。
    调用链: rescue._rescue_step -> _assist_step -> (_assist_stuck |
    _assist_finished | _assist_arrived -> _assist_scout_go /
    _assist_spot_step -> _deploy_beacon[DeployMixin])。
    """

    def _assist_step(self, eng, tid, tick):
        """加固推进分派: 撞墙 / 完成失效 / 库存超时 / 到场踩点投放。
        Args: eng: 引擎; tid: 目标 id; tick: 当前拍。Returns: None。
        """
        n = eng.nodes.get(tid)
        # 撞墙脱困最优先 (先于目标状态判定: 带病计数若不清会毒害下一任务)
        if self._stuck >= STUCK_GIVEUP_TICKS:
            self._stuck = 0
            self._assist_stuck(eng, tid, tick)
            return
        if self._assist_finished(eng, tid, tick, n):
            return
        if self.stock == 0:
            self._giveup(eng, tid, tick, "道钉耗尽")
        elif tick - self._rescue_since > RESCUE_PATIENCE:
            self._giveup(eng, tid, tick, "加固超时")
        elif self._near((self.target[1], self.target[2]), RANGE * 0.6):
            self._assist_arrived(eng, tid, tick)    # 到场: 踩点+投放
        else:
            self._advance_to_target()

    def _assist_stuck(self, eng, tid, tick):
        """撞墙收尾: 采样/择点途中被围困 -> 弃点原地收工; 赶路被困 -> 弃任务。"""
        if tick < self._scout_until or self._assist_spot is not None:
            log.info("加固弃点 %s: 采样/择点路径被墙阻断, 原地收工", tid)
            self._assist_spot = None
            self._scout_until = tick
        else:
            self._giveup(eng, tid, tick, "路径被墙体阻断")

    def _assist_finished(self, eng, tid, tick, n) -> bool:
        """完成/失效判定: 目标消失/死亡/不再自举/真实度数回安全线 -> 撤离。
        真实度数剔除机器人自身边 (站在目标身边不构成安全), 撤离登记冷却。
        Args: n: 目标节点 (可为 None)。Returns: bool True=任务已收尾。
        """
        deg = self.real_degree(tid)
        if not (n is None or n.state == "DEAD"
                or not n.power_boosted or deg >= MIN_DEGREE):
            return False
        done = n is not None and n.state != "DEAD" and deg >= MIN_DEGREE
        self._assist_withdraw(tid, tick)
        if done:
            eng._emit("robot_assist_done", "ok",
                      f"🎉 {tid} 链路冗余已补足 ({deg} 条), 机器人撤离",
                      node=tid)
            log.info("加固完成 %s: 度数=%d, 撤离", tid, deg)
        else:
            log.info("加固结束 %s: 目标恢复或失效, 撤离", tid)
        return True

    def _assist_withdraw(self, tid, tick):
        """撤离加固任务: 回巡逻 + 目标冷却 (度数回升需时间, 防同拍重开)。"""
        self.state = "PATROL"
        self.target = None
        self._checked_until[tid] = tick + INVESTIGATE_COOLDOWN

    def _assist_arrived(self, eng, tid, tick):
        """到场 (0.6x 半径内): 一次性启动侦察 (只踩合法落点), 侦察期内
        逐点采样, 预算尽/达标即转入择点投放。"""
        tgt = (self.target[1], self.target[2])
        if self._scout_until == 0:               # 一次性启动侦察
            self._scout_vis0 = self._vis_count()
            self._scout_wps = [p for p in self._scout_waypoints(tgt)
                               if self._deploy_ok_at(*p)]
            self._scout_until = tick + SCOUT_BUDGET_TICKS
            log.info("加固侦察 %s: %d 个合法采样点, 预算 %d 拍, 基线可见 %d",
                     tid, len(self._scout_wps), SCOUT_BUDGET_TICKS,
                     self._scout_vis0)
            eng._emit("robot_scout", "info",
                      f"🤖 先绕 {tid} 侦察踩点 ({len(self._scout_wps)} 个采样位, "
                      f"预算 {SCOUT_BUDGET_TICKS} 拍), 再选最佳落钉位",
                      narration="🤖 机器人先围着目标转一小圈——把周围各个"
                                "位置能听见几个节点都记下来, 再挑听得最全"
                                "的地方投放道钉。")
        if tick < self._scout_until and self._assist_scout_go(tgt):
            return                                # 侦察期内: 本拍已处理
        self._assist_spot_step(eng, tid, tick)

    def _assist_scout_go(self, tgt) -> bool:
        """侦察踩点推进: 历史合法择点已达收益门槛即收工; 否则走访下一个
        采样点, 走完提前收工。Returns: bool True=仍在侦察期 (本拍已处理)。"""
        spot = self._legal_spot(tgt)
        if spot is not None and spot[2] >= self._scout_vis0 + HISTORIC_SPOT_GAIN:
            self._scout_until = self.eng.tick    # 达标: 提前收工
            return False
        wp = next((p for p in self._scout_wps if not self._near(p, 40)), None)
        if wp is None:
            self._scout_until = self.eng.tick    # 路点走完: 提前收工
            return False
        self._move_toward(wp)
        return True

    def _assist_spot_step(self, eng, tid, tick):
        """落钉收尾: 站位合法 -> 问询投/忍就地投放; 站位非法 -> 挪到最近
        合法择点; 周边确无合法落点才放弃。落钉成功即撤离 (一钉一任务,
        防止连续投放)。"""
        tgt = (self.target[1], self.target[2])
        if self._deploy_ok():
            if want_deploy(self, tid, "assist"):
                log.info("加固落钉: 为 %s 补链 (原 %d 条)",
                         tid, self._neighbors_of(tid))
                self._deploy_beacon()
                self._assist_withdraw(tid, tick)
            # 学习器选「忍」: 本拍不落钉, 冷却后重评 (任务继续, 超时兜底)
            return
        spot = self._legal_spot(tgt)
        if spot is None:
            self._giveup(eng, tid, tick, "落点受限 (巨石/钉距), 周边无合法落点")
            return
        if self._assist_spot is None:
            log.info("加固择点: 站位受限, 移往合法点位 (%.0f,%.0f) 可见 %d",
                     spot[0], spot[1], spot[2])
            eng._emit("robot_spot", "info",
                      f"🤖 站位放不了钉, 移往合法落钉位 (可同时看见 "
                      f"{spot[2]} 个节点)", node=tid)
        self._assist_spot = spot
        self._move_toward((spot[0], spot[1]))

    def _legal_spot(self, tgt):
        """历史面包屑中"合法可落钉"的最优点位 -> (x, z, 可见数) 或 None。
        合法 = 管内/不压石/不犯钉距 (DeployMixin._deploy_ok_at); 距目标
        限 0.6x 通信半径 (钉必须罩得住目标)。得分 = 可见数 x 新近度半衰。"""
        best, bs = None, 0.0
        now = self.eng.tick
        for x, z, _conn, vis, t in self.trail:
            if math.hypot(x - tgt[0], z - tgt[1]) > RANGE * 0.6:
                continue               # 太远: 钉罩不住目标
            if not self._deploy_ok_at(x, z):
                continue               # 禁地: 压石/钉距/出管
            score = vis * (0.5 ** ((now - t) / HISTORIC_SPOT_DECAY))
            if score > bs:
                bs, best = score, (x, z, vis)
        return best

    def _neighbors_of(self, tid) -> int:
        """目标当前活跃链路数 (落钉日志用; 含道钉不含机器人边)。"""
        return sum(1 for (a, b), l in self.eng.links.items()
                   if tid in (a, b) and l["up"] and ROBOT_ID not in (a, b))
