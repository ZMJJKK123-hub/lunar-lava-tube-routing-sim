# -*- coding: utf-8 -*-
"""
自组织自愈路由: 多智能体 Dijkstra + 波前扩散记录。
每次路由计算都会记录节点被 "settle" 的顺序 (波前),
供前端播放算法扩散过程动画; 同时输出跳数分层结构。
"""
import heapq
import math


def build_graph(nodes, links):
    """links: {(id_a,id_b): link_dict} 对称链路 -> 邻接表。
    setdefault: 允许不在 nodes 表中的伪节点端点 (如巡检机器人) 入图。"""
    graph = {n.id: [] for n in nodes}
    for (a, b), link in links.items():
        if not link["up"]:
            continue
        graph.setdefault(a, []).append((b, link["cost_ab"]))
        graph.setdefault(b, []).append((a, link["cost_ba"]))
    return graph


def dijkstra(graph, source):
    """
    返回 dist/prev/settle_order。
    settle_order: 节点按 Dijkstra 确定最短距离的先后顺序 (波前扩散序列),
    前端按此顺序逐个点亮节点, 直观展示算法运行过程。
    """
    dist = {v: math.inf for v in graph}
    prev = {v: None for v in graph}
    settle_order = []
    dist[source] = 0.0
    pq = [(0.0, source)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        settle_order.append(u)                      # ← 波前记录
        for v, w in graph[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    return dist, prev, settle_order


def routing_step(nodes, links, sink_id):
    """
    计算全网站到 sink 的路由。
    返回: routes + wave(波前扩散数据: settle 顺序与跳数分层)
    """
    graph = build_graph(nodes, links)
    dist, prev, settle_order = dijkstra(graph, sink_id)
    routes = {}
    for n in nodes:
        if n.id == sink_id:
            routes[n.id] = {"hop_count": 0, "next_hop": None, "path": [n.id], "total_cost": 0}
            continue
        if math.isinf(dist[n.id]):
            routes[n.id] = {"hop_count": -1, "next_hop": None, "path": [], "total_cost": None}
            continue
        # 回溯路径
        path, cur = [], n.id
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        routes[n.id] = {
            "hop_count": len(path) - 1,
            "next_hop": path[1] if len(path) > 1 else sink_id,
            "path": path,
            "total_cost": round(dist[n.id], 2),
        }

    # 波前分层: hop = 该节点最终所在跳数层 (不可达为 -1)
    wave = {
        "settle_order": settle_order,
        "hop_of": {nid: r["hop_count"] for nid, r in routes.items()},
        "max_hop": max((r["hop_count"] for r in routes.values()), default=0),
    }
    return routes, wave


# ======================================================================
# RCSPA: 资源约束最短路径 (The Resource Constrained Shortest Path Algorithm)
# 依据: "Routing and channel assignment for low-power transmission in PCS"
#  - Dijkstra 变体: 状态 = (节点, 最近 K-1 跳的信道元组), 即论文中的
#    "补全路径 (Completion)": NEXT / DISTANCE / FORBIDDEN_RESOURCES / STATUS
#  - 复用距离 K: 连续 K 条边内同一信道不得重复 (避免同道干扰抬升功率)
#  - 信道成本 C_ij^r = 基础链路代价 + 附近已占用信道 r 的干扰惩罚
#    -> 新呼叫被现有流量"排斥", 自动绕行到总功率更低的路径 (论文核心效果)
# ======================================================================
def rscspa(adj: dict, source: str, sink: str, n_channels: int = 3, K: int = 3,
           busy_edge: dict | None = None):
    """
    adj: {node: [(neighbor, base_cost), ...]} 无向图
    busy_edge: {frozenset({a,b}): set(已占用信道)} 当前正在通信的链路及其信道
    返回: {"path": [...], "channels": [每跳信道], "cost": 总成本} 或 None
    """
    busy_edge = busy_edge or {}
    # 端点占用表: 节点 -> 附近活跃信道集合 (共享端点的链路视为互相干扰)
    node_busy: dict = {}
    for eset, chans in busy_edge.items():
        for v in eset:
            node_busy.setdefault(v, set()).update(chans)

    INF = math.inf
    start = (source, ())
    dist = {start: 0.0}
    prev: dict = {}          # state -> (prev_state, channel_used)
    pq = [(0.0, start)]
    goal_state = None

    while pq:
        d, state = heapq.heappop(pq)
        if d > dist.get(state, INF):
            continue
        u, tail = state
        if u == sink:                     # 源节点 STATUS = FINAL, 算法终止
            goal_state = state
            break
        for v, w in adj.get(u, []):
            for r in range(n_channels):
                # 冲突排除: 违反复用距离 (r 已在禁用资源列表) -> 丢弃该补全路径
                if r in tail:
                    continue
                # 信道干扰成本: r 被本链路任一端附近的活跃通信占用 -> 惩罚
                penalty = 0.0
                if r in node_busy.get(u, ()) or r in node_busy.get(v, ()):
                    penalty = w * 1.2
                nd = d + w + penalty
                ntail = (tail + (r,))[-(K - 1):] if K > 1 else ()
                nstate = (v, ntail)
                if nd < dist.get(nstate, INF):
                    dist[nstate] = nd
                    prev[nstate] = (state, r)
                    heapq.heappush(pq, (nd, nstate))

    if goal_state is None:
        return None
    # 回溯补全路径 -> 完整路径 + 每跳信道分配
    path, channels = [], []
    st = goal_state
    while st in prev:
        pst, r = prev[st]
        path.append(st[0])
        channels.append(r)
        st = pst
    path.append(source)
    path.reverse()
    channels.reverse()
    return {"path": path, "channels": channels, "cost": round(dist[goal_state], 3)}
