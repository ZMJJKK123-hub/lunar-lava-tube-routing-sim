# -*- coding: utf-8 -*-
"""
全局配置对象: 跨模块共享的可调常量与协议标识 (零硬编码收敛点)
================================================================
职责:
- 收编原先散落在 engine/transport/blockchain/robot/main 中的跨模块常量;
- 任何模块需要调整节奏/端口/标识时, 只改本文件, 不再全文搜索替换。
分层: 本文件属于基础设施配置层, 不依赖 sim 内任何其他模块 (零环依赖)。
"""

# ---------------- 服务部署 (main.py 消费) ----------------
HOST = "127.0.0.1"          # uvicorn 监听地址: 仅本机 (公网暴露会被扫描器操纵沙盘)
PORT = 5000                 # 单端口: 同时承担 WS 通道与前端静态托管
WS_BACKEND = "wsproto"      # WebSocket 实现: 规避 websockets 17.x 断连断言逃逸

# ---------------- 时序节拍 (引擎主循环消费) ----------------
TICK_PHYS_S = 0.25          # 仿真物理拍: 1 tick = 0.25s 仿真时间
TICK_BROADCAST_S = 0.20     # 快照广播拍: 与物理拍错开避免节拍混叠

# ---------------- 世界生成 (engine/world 消费) ----------------
SEED = 42                   # 地质生成种子 (重置时按 SEED+attempt*1000 重试)
UWB_RANGE = 30.0            # UWB 名义通信半径 (sim 单位; 与 physics.BAND_PROFILE 一致)

# ---------------- 自愈模式机 (engine/network 消费) ----------------
HEALING_HOLD_TICKS = 4      # HEALING -> CONVERGED 需连续稳定 tick 数

# ---------------- 渲染总线上限 (engine/snapshot 消费) ----------------
VIS_MAX = 260               # 单 tick 快照最多下发的包跳数 (防爆量)
VIS_PRIORITY = ("BLOCK", "SYNC_RESP", "SYNC_REQ", "TX")   # 截断时保留优先级
VIS_RESERVE = 40            # 为未登记类型保留的名额 (零注册兜底)

# ---------------- 控制平面配额 (engine/network 消费) ----------------
CHAIN_QUEUE_CAP = 4096      # 链上报文计入 queue_pct 的字节上限 (50% 配额)

# ---------------- 节点链路度数自保 (node.tune_power_for_degree 消费) ----------------
MIN_DEGREE = 2              # 活跃链路警戒线: 低于此值 -> 发射功率自举 (Starlink 式冗余维护)
BOOST_STEP_DB = 2.0         # 每次自举的功率提升步长 (dBm)
TX_POWER_MAX_DB = 22.0      # 自举功率上限 (dBm; 额定 14dBm, 超 300m 硬半径功率也救不了)
BOOST_MIN_SOC_PCT = 15.0    # 电量红线 (%): SoC 低于此值停止自举 (生存优先于连通)
BOOST_EVERY_TICKS = 8       # 调功最小间隔 (物理拍; 8 拍 = 2s, 防功率阶梯过快)
DEG_HYSTERESIS_TICKS = 16   # 度数达标需持续此拍数才回落 (滞回防来回抖动)
BOOST_DROP_GUARD_DB = 0.5   # 回落安全余量 (dB): 链路余量降一档后仍高于此才允许回落

# ---------------- 跨模块协议标识 ----------------
ROBOT_ID = "ROBOT"          # 巡检机器人节点 ID: 全网唯一伪节点 (多模块引用, 故置顶层)
SINK_ID = "NODE-00"         # 汇聚节点 (洞口基站) ID

# ---------------- 功能开关 ----------------
ROBOT_ENABLED = True        # 巡检机器人 (SOS 听测 + 道钉投放; False = 零痕迹)

# ---------------- 日志 (main.py 消费; 各模块经 getLogger(__name__) 上报) ----------------
import logging   # 标准库: 仅为测试环境挂 NullHandler (真实配置在 main.py)
LOG_FILE = "sim.log"        # 仿真调试日志落盘文件 (RotatingFileHandler)
LOG_LEVEL = "INFO"          # 级别: DEBUG 会额外输出逐 tick 摘要/重传/追块细节
LOG_TICK_EVERY = 40         # DEBUG 级引擎心跳摘要的采样间隔 (tick)
_rot = logging.getLogger("sim")   # "sim" 日志树根: 测试/独立导入时保持静默
_rot.addHandler(logging.NullHandler())
_rot.propagate = False
