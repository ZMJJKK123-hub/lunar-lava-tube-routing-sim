# -*- coding: utf-8 -*-
"""
巡检机器人可调参数集中地 (供 motion/senses/robot 三模块共享)
==============================================================
职责: 收编原 robot.py 头部散落的全部行为常量; 改行为只动这里。
依赖: 仅依赖 sim.config 的协议标识, 不引入仿真引擎。
"""
from ..config import ROBOT_ID   # 协议标识: 机器人节点 ID (跨模块统一口径)

RANGE = 300.0            # 通信半径 (世界米; = 30 sim x WORLD_SCALE)
SPEED = 60.0             # 移动速度 (世界单位/tick)
SOS_ARM_TICKS = 4        # 连续失联 N tick 才开始呼救 (防瞬断误报)
SOS_BEACON_EVERY = 10    # SOS 信标节奏 (tick, 按节点编号错峰)
DEPLOY_GAP = 100.0       # 距既有道钉 < 此值不重复投放
RESCUE_PATIENCE = 80     # 救援超时 (tick): 物理不可救则放弃回巡逻
RESCUE_DEAF = 120        # 放弃后的"耳聋期" (tick): 撤离期间不再听测, 防无限重试
ROBOT_CHAIN_INTEL = True  # 链上情报: 用自身世界状态的心跳超时, 主动侦查失联节点
                           # (False = 纯耳朵模式, 只听 300m 内 SOS —— 教学对比用)
STALE_AFTER = 150          # 遥测停更超过此 tick 视为失联嫌疑 (遥测周期 60)
INVESTIGATE_COOLDOWN = 300  # 查无实据(已死/深隔断)后的冷却, 防反复空趟
TRAIL_MAX = 240            # 面包屑轨迹上限 (tick): (x, z, 是否连通主网)
WAYPOINT_PATIENCE = 200     # 巡逻路点超时换点, 不在死角里磨
ROBOT_LINK_PENALTY = 50.0   # 机器人边代价罚: 健康流量永不借道 (走它不如绕路),
                            # 只有孤岛 (无路可走) 才经它回流 -> 桥接检测精确
BEACON_STOCK = 6         # 携带道钉数
