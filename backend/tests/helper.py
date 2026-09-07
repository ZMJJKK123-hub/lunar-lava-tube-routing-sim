# -*- coding: utf-8 -*-
"""
测试共享助手: 引擎逐 tick 步进 (与 run_forever 同序, 不依赖事件循环)。
"""
from sim.engine import SimulationEngine   # 仿真引擎 (各测试自建实例)


def step_ticks(eng: SimulationEngine, n: int):
    """推进 n 个 tick: 物理演化 -> 网络 -> 传输 -> 账本 (主循环同序)"""
    for _ in range(n):
        eng.tick += 1
        for nd in eng.nodes.values():
            nd.step(dt_hours=0.004)
        eng.compute_network()
        eng.transport.step()
        eng.chain_net.step(eng.tick)
