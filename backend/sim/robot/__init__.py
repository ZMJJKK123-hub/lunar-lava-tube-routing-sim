# -*- coding: utf-8 -*-
"""
巡检机器人包: SOS 听测 + 道钉投放 (物理搭桥自愈)
====================================================
对外只暴露 PatrolRobot / plan_robot_path / ROBOT_ID 三个名字,
引擎通过两个挂点 (inject_links / tick) 驱动, 其余实现细节全部包内封闭。
子模块: constants(参数) / motion(运动学) / senses(感知) / robot(状态机)。
"""
from ..config import ROBOT_ID        # 协议标识 (跨模块统一, 置于 config)
from .motion import MotionMixin, plan_robot_path   # 运动学混入 + 路径规划扩展点
from .robot import PatrolRobot                      # 机器人本体 (状态机+道钉)

__all__ = ["PatrolRobot", "plan_robot_path", "ROBOT_ID"]
