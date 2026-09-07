# -*- coding: utf-8 -*-
"""
传输层数据模型: 常量 + 报文/分段实体 + 损坏概率
====================================================
报文生命周期:
  1) 连接接纳 (零时间开销的"握手"): send_message 瞬间跑 rscspa 选路,
     有路 = 连接建立, 报文立即出发; 无路 = NO_PATH, 报文拒绝。
  2) 数据传输: 报文**整包不分段**, 沿路径逐跳 store-and-forward;
     每 hop 1 tick, 按当前 BER 对整包字节掷骰判损坏(含 14B ACK 开销),
     坏则重传; 队列/缓冲/逐跳计数全部按报文完整字节数计 —— 负载语义
     与分段时代完全等价 (2KB 报文仍占 2048B 缓冲), 只是画面一包一点。
  3) 传完直接收场 (无 FIN 挥手): DELIVERED 事件 + 结果信号。
- 半双工: 每 tick 每节点只推进一个报文, 排队即真实拥塞。
- 链路中断: 报文从当前位置重新 rscspa 绕行 (传输级自愈)。
- 结果信号: DELIVERED / TIMEOUT / NO_PATH / BUFFER_FULL / MAX_RETRIES,
  全部走 events + results 双通道; 超时必然带 TIMEOUT 信号。
"""
from typing import Deque  # 标准库: 类型注解用 (发送缓冲队列)


# ---------------- 传输层可调常量 ----------------
RETRIES_MAX = 3             # 每包每跳最大重传次数
QUEUE_LIMIT_BYTES = 8192    # 节点发送缓冲上限 (queue_pct = 积压/8192*100)
MAX_CONCURRENT = 6          # 在途报文上限 (防洪)
DEFAULT_TIMEOUT = 90        # 报文超时 (tick, 1 tick = 0.25s)
ACK_BYTES = 14              # 每跳数据 ACK 开销
# 自动遥测开关: 开启时每 6 tick 自动从 sensor 发遥测给 sink。
# 默认关闭 —— 流量完全由用户手动发起, 便于观察单条报文的传输过程
AUTO_TELEMETRY = False


def _damage_prob(ber: float, nbytes: int) -> float:
    """nbytes 字节经 BER 信道至少错 1 比特的概率: 1-(1-ber)^(8n)"""
    p = min(max(ber, 0.0), 0.5)
    return 1.0 - (1.0 - p) ** (nbytes * 8)


class Segment:
    """一个完整报文 (整包), 沿路径逐跳搬运。

    属性: mid=报文id; seq=段序; nbytes=字节数; cur/nxt=当前与下一跳节点;
    wire=本 tick 是否在线 (前端画移动方块); retries=本跳重传计数;
    guard=最近起飞 tick (防同 tick 双重推进); hops=已飞完跳数。
    """
    __slots__ = ("mid", "seq", "nbytes", "cur", "nxt", "wire", "retries",
                 "guard", "hops")

    def __init__(self, mid, seq, nbytes, cur, nxt):
        self.mid = mid
        self.seq = seq
        self.nbytes = nbytes
        self.cur = cur
        self.nxt = nxt
        self.wire = False      # True = 本 tick 正在线上 (前端画移动方块)
        self.retries = 0
        self.guard = -1        # 最近一次起飞的 tick (防同 tick 双重推进)
        self.hops = 0          # 已飞完的跳数 (前端全程进度 = (hops+t)/总跳数)


class Message:
    """一条端到端报文的元数据与计账。

    属性: id/src/dst/total/created/deadline=生命周期; status=INFLIGHT 等;
    path=当前路径; chan=边->信道分配; total_segs=整包恒 1;
    tx/rx_bytes=字节计账; reroutes=绕行次数; path_history=路径变迁。
    """
    def __init__(self, mid, src, dst, total, path, channels, created, deadline):
        self.id = mid
        self.src, self.dst = src, dst
        self.total = total
        self.created = created
        self.deadline = deadline
        self.status = "INFLIGHT"
        self.stage = "DATA"     # 连接接纳在发送瞬间完成, 直接进入数据阶段
        self.path = list(path)
        self.chan = {}                       # edge(排序元组) -> 信道
        for k in range(len(path) - 1):
            self.chan[tuple(sorted((path[k], path[k + 1])))] = channels[k]
        self.total_segs = 1                  # 整包单段: 不再按 MSS 分段
        self.done = 0                        # 已送达终点的分段数
        self.retries = 0
        self.hops_done = 0
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.reroutes = 0
        self.path_history = [list(path)]
