# -*- coding: utf-8 -*-
"""
救援状态机分支 (RescueMixin)
===============================
职责: PatrolRobot 的"救援三态推进" —— 目标恢复撤离 / 到场无果冷却 /
到场无桥重定位 / 超时放弃 / 回撤点判定。决策入口 _rescue_step 由
robot.py 的 _advance 调用; 移动能力来自 MotionMixin。
"""
import logging   # 标准库: 模块日志 (救援分支结局)
import math   # 标准库: 可桥性预判的距离计算

from ..config import ROBOT_ID                              # 协议标识: 自身节点
from .constants import (INVESTIGATE_COOLDOWN, RANGE,       # 核查冷却/通信半径
                        RESCUE_PATIENCE)                   # 救援超时节拍

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
        """救援/核查/回撤三态推进: 依目标可达性与到场情况分派。

        Args: tick: 当前仿真 tick。Returns: None。
        Globals Used: None。Calls: 分派到本模块各分支 + MotionMixin 移动。
        """
        eng = self.eng
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
