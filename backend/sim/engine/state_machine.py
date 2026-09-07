# -*- coding: utf-8 -*-
"""
自愈模式机: STABLE / HEALING / CONVERGED (StateMachineMixin)
==============================================================
职责: 检测"结构性变化"(链路生死/节点失联)并驱动自愈叙事与收敛判定。
只有结构性变化才触发或重置自愈; ACO 信息素引起的等价路径微调不算新灾害,
机器人随移动的边翻动也不算 (它是移动资产, 不是拓扑事故)。
"""
from ..config import HEALING_HOLD_TICKS, ROBOT_ID   # 收敛保持拍数/机器人标识


class StateMachineMixin:
    """SimulationEngine 的自愈模式机混入。

    属性要求 (由 SimulationEngine.__init__ 提供): self.mode/_stable_ticks/
    heal_started_tick/_pre_collapse_routes/routes/prev_links/tick。

    执行链路: NetworkMixin.compute_network -> _mode_step
    -> (_structural_change + _emit_converged)。
    """

    def _mode_step(self, quiet: bool, links):
        """模式机推进: 结构突变 -> HEALING; 保持稳定 HEALING_HOLD_TICKS
        -> CONVERGED (自愈完成叙事); CONVERGED -> STABLE (回落)"""
        structural = (not quiet and self._structural_change(links))
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
                    self._emit_converged(quiet)
        elif self.mode == "CONVERGED":
            self.mode = "STABLE"

    def _structural_change(self, links) -> bool:
        """结构变化判定: 排除机器人边后的链路生死翻转"""
        all_keys = {k for k in set(links) | set(self.prev_links)
                    if ROBOT_ID not in k}
        return any(
            links.get(k, {}).get("up", False)
            != self.prev_links.get(k, {}).get("up", False)
            for k in all_keys)

    def _emit_converged(self, quiet: bool):
        """收敛落地: 进入 CONVERGED + 自愈耗时/示例路径解说"""
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
