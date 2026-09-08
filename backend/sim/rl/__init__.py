# -*- coding: utf-8 -*-
"""
强化学习实验包 (B 组: 信道选择 Q-learning)
====================================================
职责: A/B 对比实验的 B 组决策器 —— 用逐边 Q-learning 替换 RCSPA 的
信道分配 (路径仍走 Dijkstra); 奖励取自传输层真实结果信号。
分层: 决策层 (只读引擎/传输层公开状态, 不反向被依赖)。
依赖: sim.routing.dijkstra (路径), sim.config (超参)。
"""
from .planner import rl_plan            # 连接接纳入口 (与 rscspa 同契约)
from .q_channels import ChannelQLearner  # 逐边 Q 表学习器

__all__ = ["rl_plan", "ChannelQLearner"]
