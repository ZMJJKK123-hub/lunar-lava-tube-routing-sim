# -*- coding: utf-8 -*-
"""
核心数据结构类型契约 (TypedDict DTO)
================================================================
职责: 为模块间流转的高频字典结构提供强类型契约 (agent.md 规则二:
严禁无契约弱类型字典)。TypedDict 保持 dict 序列化兼容 —— 快照 JSON
导出与前端协议不变, 同时让签名具备类型检查能力。
分层: 纯类型定义, 零运行时逻辑, 不依赖任何模块。
"""
from typing import TypedDict


class LinkBudget(TypedDict):
    """physics.link_budget 的输出: 单向链路物理层预算。"""
    distance: float      # 归一化仿真距离 (sim 单位)
    prx_dbm: float       # 接收功率 dBm
    snr_db: float        # 信噪比 dB
    ber: float           # 误码率
    margin_db: float     # 链路余量 dB (熔断判定: >0 且 BER 可接受)
    band: str            # 频段名 ("UWB"/"LoRa")
    up: bool             # 链路是否可用


class LinkInfo(LinkBudget):
    """engine 链路表条目: LinkBudget + 双向路由代价 + 信息素负载。"""
    cost_ab: float       # a->b 方向路由代价 (六项加权)
    cost_ba: float       # b->a 方向路由代价
    load: float          # 历史承载量 (ACO 信息素, 指数平滑)


class RouteInfo(TypedDict):
    """routing_step 的输出条目: 节点到 sink 的路由结果。"""
    hop_count: int       # 跳数 (-1 = 不可达)
    next_hop: str | None  # 下一跳节点 ID (sink 为 None)
    path: list[str]      # 完整路径 (含自身与 sink)
    total_cost: float    # 路径总代价 (None = 不可达)


class WaveInfo(TypedDict):
    """波前扩散数据: 前端按 settle 顺序逐个点亮节点。"""
    settle_order: list[str]   # Dijkstra 确定最短距离的先后顺序
    hop_of: dict[str, int]    # 节点 -> 最终跳数层
    max_hop: int              # 最大跳数


class VisPacket(TypedDict):
    """渲染总线快照条目: 一个报文从 a 飞到 b 的单跳可视化。"""
    a: str               # 发方节点 ID
    b: str               # 收方节点 ID
    kind: str            # 报文类型 (前端按类型配色)
    r: bool              # 是否中继转发
    t: float             # 本 tick 内飞行进度 0..1 (-1 = 停驻排队)


class TelemetryPayload(TypedDict):
    """区块链遥测交易负载: 节点关键状态快照。"""
    x: float             # 世界坐标 x
    z: float             # 世界坐标 z
    soc: float           # 剩余电量百分比
    temp: float          # 温度 °C
    state: str           # 节点状态机当前值
    queue: float         # 发送队列积压率 %
    radio: str           # PAMAS 电台状态 (IDLE/TXRX/SLEEP)
    hop: int             # 到 sink 跳数
