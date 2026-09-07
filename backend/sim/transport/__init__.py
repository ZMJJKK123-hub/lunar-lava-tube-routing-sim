# -*- coding: utf-8 -*-
"""
传输层包: 连接接纳 + store-and-forward 数据传输
====================================================
对外只暴露 TransportLayer; 子模块: model(常量+实体) /
core(发送入口+查询+编排) / relay(逐跳推进+绕行+超时)。
"""
from .core import TransportLayer   # 传输层主体 (混入 RelayMixin)

__all__ = ["TransportLayer"]
