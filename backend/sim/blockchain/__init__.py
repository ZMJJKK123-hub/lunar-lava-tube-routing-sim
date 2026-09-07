# -*- coding: utf-8 -*-
"""
区块链包: 全网状态同步模拟 (统一排他调度 PoA + 泛洪 + 追块/分叉愈合)
======================================================================
对外只暴露 BlockchainNetwork (引擎唯一挂点); 子模块: model(常量+实体)
/ chain_node(链上节点核心) / sync(同步协议) / network(网络编排)。
"""
from .network import BlockchainNetwork   # 账本网络编排 (引擎挂点)

__all__ = ["BlockchainNetwork"]
