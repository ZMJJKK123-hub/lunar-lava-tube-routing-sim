# -*- coding: utf-8 -*-
"""
SOS 呼救判定层 (SosMixin)
==========================
职责: 巡检机器人的呼救判定 —— 失联布防 (连续 SOS_ARM_TICKS 拍)/恢复解除
(连续 SOS_DISARM_TICKS 拍)/报废摘除/SOS 信标帧呈现; 并向救援层提供
"任务目标是否真恢复"判定 (_target_recovered)。
布防与解除均带消抖的原因: 边缘抖动的弱链若恢复 1 拍即解除呼救, 机器人
会被"派去-召回"反复拉扯 (假孤岛拉锯); 对称消抖后单拍闪断不再改变任务态势,
只有持续 1 秒以上的真恢复才让机器人收队。
依赖: 引擎公开状态 (routes/nodes/sink_id/vis_packet/_zh) 与
MotionMixin._los_clear (信标可达圈判定); 不反向被引擎依赖。
"""
import logging   # 标准库: 模块日志 (布防/解除/摘除)

from .. import physics                      # 物理层: distance (信标可达圈)
from ..config import ROBOT_ID               # 协议标识: 自身节点 ID
from .constants import (RANGE, SOS_ARM_TICKS, SOS_BEACON_EVERY,   # 布防/信标节拍
                        SOS_DISARM_TICKS)   # 解除消抖拍数

log = logging.getLogger(__name__)   # 本模块日志器


class SosMixin:
    """职责: PatrolRobot 的 SOS 呼救判定混入。

    属性要求 (由 PatrolRobot.__init__ 提供): self.eng (引擎引用),
    self.node (机器人伪节点), self.sos_active (呼救节点集合),
    self._iso (nid -> 连续失联拍数), self._rec (nid -> 连续恢复拍数),
    self._checked_until (目标冷却表), self._deployed_at (最近落钉拍)。
    """

    def _update_sos(self, tick: int) -> None:
        """逐节点呼救判定总入口: 报废摘除 / 可达拍消抖解除 / 失联拍计数的
        布防与信标发送 (三分支各落私有方法, 本方法只做分派)。
        Args: tick: 当前物理拍。Returns: None。Calls: _sos_drop_dead /
        _sos_recover_step / _sos_island_step。
        """
        eng = self.eng
        for n in eng.nodes.values():
            if n.role == "beacon" or n.id == eng.sink_id:
                continue
            if n.state == "DEAD":
                self._sos_drop_dead(n)
                continue
            hop = eng.routes.get(n.id, {}).get("hop_count", -1)
            if hop >= 0:
                self._sos_recover_step(n, tick)
            else:
                self._sos_island_step(n, tick)

    def _sos_drop_dead(self, n) -> None:
        """报废即摘除: sos_active 残留会让画面一直画死人的 SOS 环,
        且把机器人引向尸体。Args: n: 报废节点。Returns: None。"""
        if n.id in self.sos_active:
            self.sos_active.discard(n.id)
            log.info("SOS 终止 %s: 节点已报废", n.id)
            self.eng._emit("sos_stop", "info",
                           f"🕯 {n.id} 已报废, SOS 信标静默", node=n.id)
        self._iso.pop(n.id, None)
        self._rec.pop(n.id, None)

    def _sos_recover_step(self, n, tick: int) -> None:
        """可达拍: 解除消抖 —— 连续 SOS_DISARM_TICKS 拍可达才解除呼救;
        单拍闪回只累计不解除 (假孤岛拉锯的治本处)。
        Args: n: 存活节点; tick: 当前物理拍。Returns: None。
        """
        self._iso.pop(n.id, None)
        if n.id not in self.sos_active:
            self._rec.pop(n.id, None)
            return
        self._rec[n.id] = self._rec.get(n.id, 0) + 1
        if self._rec[n.id] < SOS_DISARM_TICKS:
            return
        self.sos_active.discard(n.id)
        self._rec.pop(n.id, None)
        log.info("SOS 解除 %s: 恢复可达已连续 %d tick", n.id, SOS_DISARM_TICKS)
        self.eng._emit("sos_stop", "ok", f"✔ {n.id} 重新可达, SOS 停发",
                       narration=f"✅ {self.eng._zh(n.id)} 重新接回网络,"
                                 f"呼救解除。", node=n.id)

    def _sos_island_step(self, n, tick: int) -> None:
        """失联拍: 布防计数 (连续 SOS_ARM_TICKS 拍起呼) + 信标帧节奏发送
        (按节点编号错峰, 300m+LOS 内可闻)。
        Args: n: 存活节点; tick: 当前物理拍。Returns: None。
        Calls: _emit_beacons。
        """
        eng = self.eng
        self._rec.pop(n.id, None)
        self._iso[n.id] = self._iso.get(n.id, 0) + 1
        if self._iso[n.id] == SOS_ARM_TICKS:
            self.sos_active.add(n.id)
            log.warning("SOS 启动 %s: 连续失联 %d tick", n.id, SOS_ARM_TICKS)
            eng._emit("sos_start", "error",
                      f"🆘 {n.id} 失联 {SOS_ARM_TICKS} tick, 开始广播 SOS",
                      narration=f"🆘 {eng._zh(n.id)} 已连续失联,开始向外广播"
                                f"SOS 求援信号——巡检机器人若巡至其通信范围内"
                                f"就能听到。", node=n.id)
        if (n.id in self.sos_active
                and (tick + int(n.id.split("-")[1])) % SOS_BEACON_EVERY == 0):
            self._emit_beacons(n)

    def _target_recovered(self, tid: str) -> bool:
        """任务目标是否算"真恢复": 路由可达, 且呼救已消抖解除; 刚落钉完事
        也算 (钉已固化链路, 不必等消抖窗口走完才撤离)。
        Args: tid: 目标节点 id。Returns: bool (True = 救援层可撤离收队)。
        """
        eng = self.eng
        return (eng.routes.get(tid, {}).get("hop_count", -1) >= 0
                and (tid not in self.sos_active
                     or eng.tick - self._deployed_at <= SOS_DISARM_TICKS))

    def _emit_beacons(self, n) -> None:
        """SOS 信标的物理呈现: 覆盖内最多 3 个邻居 + (若在圈内) 机器人。
        Args: n: 呼救节点。Returns: None。Calls: eng.vis_packet。"""
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
