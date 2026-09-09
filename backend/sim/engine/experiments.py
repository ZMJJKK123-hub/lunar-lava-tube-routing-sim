# -*- coding: utf-8 -*-
"""
实验开关层 (ExperimentsMixin)
================================
职责: 引擎的实验性功能开关指令集中地 —— 目前为道钉投/忍 Q-learning
(RL 试点②)。独立成文件: 存量 api.py 已超行数上限, 且未来实验开关
(如 Q-routing)可继续在此落位, 不再侵入灾害/上帝指令区。
依赖: 引擎状态 (rl_deploy/rl_deploy_learner) 与事件总线。
"""
import logging   # 标准库: 模块日志 (开关切换)

log = logging.getLogger(__name__)   # 本模块日志器


class ExperimentsMixin:
    """职责: SimulationEngine 的实验开关混入。

    属性要求 (由 SimulationEngine.__init__ 提供): self.rl_deploy (开关)、
    self.rl_deploy_learner (学习器, 跨 reset 保留; 关闭时置 None 弃表)。

    调用链: main.py WS 指令 -> toggle_rl_deploy -> (robot/rl_gate 问询)
    ; engine.snapshot -> rl_deploy 字段。
    """

    def toggle_rl_deploy(self) -> dict:
        """道钉时机学习开关 (WS toggle_rl_deploy 指令入口): 规则恒投 <->
        Q-learning 投/忍互换。关闭即弃 Q 表 (再开从头学); reset 不弃表
        (世界种子相同, 经验跨世界迁移 —— 连续多轮可见学习曲线)。

        Args: None。Returns: dict {ok, rl_deploy} —— 切换后的开关状态。
        Globals Used: None。Calls: _emit (实验事件与解说词)。
        """
        self.rl_deploy = not self.rl_deploy
        if not self.rl_deploy:
            self.rl_deploy_learner = None    # 显式关闭 = 弃表 (实验独立)
        log.info("RL道钉开关 -> %s", "Q-learning(学时机)" if self.rl_deploy
                 else "规则恒投")
        self._emit("rl_deploy_toggle", "info",
                   "🧪 道钉时机决策器切换为 "
                   + ("Q-learning (机器人自学何时投钉/何时观望)" if self.rl_deploy
                      else "规则 (检测到桥接即投)"),
                   narration=("🧪 巡检机器人开始自学「投资学」——每次想投道钉时,"
                              "它会掂量这个失联区是真孤岛还是快自愈的假孤岛,"
                              "忍一忍也许能省下一根钉。它投对投错都会被结算奖惩,"
                              "观察它能不能练出会忍的手感。"
                              if self.rl_deploy else
                              "🧪 道钉决策切回规则模式 (检测到桥接立即投钉)。"),
                   )
        return {"ok": True, "rl_deploy": self.rl_deploy}
