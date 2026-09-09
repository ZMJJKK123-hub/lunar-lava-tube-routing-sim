# -*- coding: utf-8 -*-
"""
强化学习实验包 (B 组: 信道选择 / 道钉时机 Q-learning)
=====================================================
职责: A/B 对比实验的 B 组决策器 —— ①逐边 Q-learning 替换 RCSPA 的
信道分配 (路径仍走 Dijkstra); ②道钉投/忍时机学习 (规则版恒投)。
奖励均取自真实结果观测 (传输层结果信号 / 落钉后恢复路径)。
分层: 决策层 (只读引擎/传输层/机器人公开状态, 不反向被依赖)。
依赖: sim.routing.dijkstra, sim.config (超参), sim.types (契约)。
"""
from .planner import rl_plan, random_plan   # 连接接纳入口 (B组 Q-learning / C组 随机信道, 与 rscspa 同契约)
from .q_channels import ChannelQLearner  # 逐边 Q 表学习器
from .q_deploy import DeployQLearner     # 道钉投/忍时机学习器 (RL 试点②)

__all__ = ["rl_plan", "random_plan", "ChannelQLearner", "DeployQLearner"]
