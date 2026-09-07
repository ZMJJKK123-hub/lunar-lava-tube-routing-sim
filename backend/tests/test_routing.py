# -*- coding: utf-8 -*-
"""路由算法回归: Dijkstra 最短路 / RCSPA 信道复用约束与绕行。"""
import unittest   # 标准库: 测试框架

from sim.routing import build_graph, dijkstra, rscspa, routing_step


def _diamond():
    """对称菱形图: A-B-C-D 最短 (1+2+1=4), A-C-D 次之 (4+1=5)"""
    graph = {
        "A": [("B", 1), ("C", 4)],
        "B": [("A", 1), ("C", 2), ("D", 5)],
        "C": [("A", 4), ("B", 2), ("D", 1)],
        "D": [("B", 5), ("C", 1)],
    }
    return graph


class TestDijkstra(unittest.TestCase):
    """最短路径与波前记录"""

    def test_shortest_path(self):
        """A->D 应走 A-B-C-D (代价 4) 而非直达边 (5)"""
        dist, prev, order = dijkstra(_diamond(), "A")
        self.assertEqual(dist["D"], 4)
        self.assertEqual(prev["D"], "C")
        self.assertEqual(order[0], "A")       # 源点最先 settle

    def test_unreachable(self):
        """断开节点 dist=inf"""
        g = _diamond()
        g["E"] = []
        dist, _prev, _o = dijkstra(g, "A")
        self.assertEqual(dist["E"], float("inf"))


class TestRcspa(unittest.TestCase):
    """资源约束最短路径: 信道复用距离 + 干扰惩罚 + 无路拒绝"""

    def test_channel_reuse_distance(self):
        """6 跳链路: 任意 K=3 窗口内信道不得重复"""
        adj = {f"N{i}": [(f"N{i+1}", 1.0)] for i in range(6)}
        adj[f"N{6}"] = []
        for i in range(6):
            adj[f"N{i+1}"].append((f"N{i}", 1.0))   # 补对称边
        res = rscspa(adj, "N0", "N6", n_channels=3, K=3)
        self.assertIsNotNone(res)
        self.assertEqual(res["path"], [f"N{i}" for i in range(7)])
        ch = res["channels"]
        for i in range(len(ch) - 1):
            self.assertNotEqual(ch[i], ch[i + 1])        # 相邻不同
        for i in range(len(ch) - 2):
            self.assertNotEqual(ch[i], ch[i + 2])        # 隔一跳也不同 (K=3)

    def test_no_path(self):
        """断图返回 None (连接拒绝的依据)"""
        adj = {"A": [("B", 1.0)], "B": [("A", 1.0)], "C": []}
        self.assertIsNone(rscspa(adj, "A", "C"))

    def test_busy_penalty_detours(self):
        """共享端点的忙碌信道被惩罚: 等价双路中避开被占信道的那条"""
        # 两条等代价 2 路径 A->B->D 与 A->C->D; AB 边信道 0 已被占用
        adj = {"A": [("B", 1.0), ("C", 1.0)],
               "B": [("A", 1.0), ("D", 1.0)],
               "C": [("A", 1.0), ("D", 1.0)],
               "D": [("B", 1.0), ("C", 1.0)]}
        busy = {frozenset({"A", "B"}): {0}}
        res = rscspa(adj, "A", "D", n_channels=3, K=3, busy_edge=busy)
        # 经 B 的第一跳若用信道 0 会吃 1.2x 惩罚 -> 最优解应避开 (走 C 或换信道)
        self.assertIsNotNone(res)
        self.assertEqual(res["cost"], 2.0)


class TestRoutingStep(unittest.TestCase):
    """routing_step 全网路由 + 波前结构契约"""

    def test_wave_contract(self):
        """wave 含 settle_order/hop_of/max_hop; sink hop=0。
        注: path 为 sink-first 约定 [sink, ..., 节点] (历史行为, 未改动);
        next_hop=path[1] 仅对 1 跳节点是真下一跳 —— 遗留怪癖, 此处按现状断言。"""
        class N:   # 最小节点桩: 只需 id 属性
            def __init__(self, i):
                self.id = i
        nodes = [N("S"), N("M"), N("T")]
        links = {("S", "M"): {"up": True, "cost_ab": 1, "cost_ba": 1},
                 ("M", "T"): {"up": True, "cost_ab": 1, "cost_ba": 1}}
        routes, wave = routing_step(nodes, links, "S")
        self.assertEqual(routes["S"]["hop_count"], 0)
        self.assertEqual(routes["T"]["hop_count"], 2)
        self.assertEqual(routes["T"]["path"], ["S", "M", "T"])
        self.assertEqual(wave["max_hop"], 2)
        self.assertIn("settle_order", wave)


if __name__ == "__main__":
    unittest.main()
