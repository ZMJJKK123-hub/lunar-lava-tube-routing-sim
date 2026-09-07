# -*- coding: utf-8 -*-
"""
事件与解说中心: EventHub
============================
职责: 全引擎统一的事件总线 —— 算法事件 (链路熔断/重路由/自愈收敛等)
与通俗解说 (narration) 的产生、滚动缓冲、关键解说保鲜。
分层: 纯领域组件, 被引擎/传输层/账本/机器人共同调用, 不依赖其他模块。
"""
from collections import deque   # 标准库: 滚动事件队列 (定长, 自动挤旧)

# 关键解说类型: 单独保鲜, 不受事件滚动队列挤出 -> 前端解说员始终可播
_NARRATION_KEEP = ("disaster", "node_dead", "healing_start",
                   "converged", "isolated", "rejoin")


def zh(nid: str) -> str:
    """节点 ID -> 中文口语名 (N34 -> "34号"), 解说词用"""
    return f"{nid.split('-')[1]}号"


class EventHub:
    """职责: 事件总线: append-only 滚动日志 + 最新关键解说快照。

    核心属性:
    - events: deque(maxlen=120) 事件流 (快照只取尾部 40 条下发);
    - last_narration: 最近一条关键解说 (供前端解说员轮播);
    - _seq: 事件自增序号 (前端稳定 key)。

    调用链: 各层 -> engine._emit -> hub.emit -> (events.append
    [+ last_narration 更新]) -> engine.snapshot -> events 尾部下发。
    """

    def __init__(self):
        self.events = deque(maxlen=120)
        self._seq = 0
        self.last_narration: dict | None = None

    def emit(self, tick: int, type_: str, severity: str, msg: str,
             narration: str | None = None, **payload):
        """记录一条事件; 命中关键类型时同步保鲜解说词。

        Args: tick: 仿真时刻; type_: 事件类型 (前端按类型着色);
              severity: info/ok/warn/error; msg: 事件日志文本;
              narration: 通俗解说词 (可选); **payload: 结构化附加字段。
        Returns: None。Globals Used: _NARRATION_KEEP (关键解说保鲜名单)。
        Calls: None。
        """
        self._seq += 1
        self.events.append({
            "id": self._seq, "tick": tick, "type": type_,
            "severity": severity, "msg": msg, "narration": narration, **payload,
        })
        if narration and type_ in _NARRATION_KEEP:
            self.last_narration = {"id": self._seq, "text": narration}
