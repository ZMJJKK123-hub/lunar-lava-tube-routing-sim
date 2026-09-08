# -*- coding: utf-8 -*-
"""
B 组连接接纳规划器 (rl_plan)
====================================================
职责: 与 rscspa 同契约的替代决策器 —— 路径走现成 Dijkstra (零新算法),
逐跳信道由逐边 Q-learning ε-贪婪决定 (复用距离/干扰惩罚不写死,
由重传/超时的负奖励自学)。规划期登记归因队列供结果结算。
依赖: sim.routing (dijkstra), .q_channels (学习器, 挂在 eng.rl_learner)。
"""
import logging   # 标准库: 模块日志 (拒配诊断)

from .. import routing   # 路由层: dijkstra 复用 (A/B 同一路径基座)

log = logging.getLogger(__name__)   # 本模块日志器


def rl_plan(transport, src, dst):
    """B 组连接接纳: Dijkstra 选路 + Q-learning 选道。

    Args: transport: TransportLayer (读 _adj/_busy_channels);
          src/dst: 源/目的节点 id。
    Returns: 与 rscspa 同契约 —— 成功 {path, channels, cost};
             无路 None (拒绝依据)。
    Globals Used: None (超参在学习器内部)。Calls: routing.dijkstra /
    eng.rl_learner.choose/note_path (首用惰性创建)。
    """
    adj = transport._adj()
    if src not in adj or dst not in adj:
        return None
    dist, prev, _ = routing.dijkstra(adj, src)
    if src != dst and (prev.get(dst) is None or dist.get(dst, float("inf"))
                       == float("inf")):
        return None
    # 回溯路径 (Dijkstra 的 prev 已含最短树)
    path, cur = [], dst
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    if path[0] != src:
        return None

    eng = transport.eng
    if eng.rl_learner is None:
        from .q_channels import ChannelQLearner   # 惰性: 开关开启后首用建表
        eng.rl_learner = ChannelQLearner()
    learner = eng.rl_learner
    busy = transport._busy_channels()

    channels, hops = [], []
    cost = 0.0
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        edge = tuple(sorted((a, b)))
        state = tuple(sorted(busy.get(frozenset(edge), ())))
        ch = learner.choose(edge, state)
        channels.append(ch)
        hops.append((edge, state, ch))
        cost += dict(adj.get(a, ())).get(b, 0.0)
    learner.note_path(src, dst, hops)
    return {"path": path, "channels": channels, "cost": round(cost, 3)}
