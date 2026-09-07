# -*- coding: utf-8 -*-
"""
引擎对外 API 层: 上帝模式 / 灾害注入 / 墙与巨石操作 (ApiMixin)
================================================================
职责: WS 指令的领域入口 —— 节点参数覆写 (含临界死亡判定)、四类灾害
(塌方/热浪/耀斑/陨石/摧毁主干道)、墙体增删、巨石拖拽、任意两节点发报文。
依赖: 引擎世界层 (LOS 重算) 与网络层 (compute_network), transport 发送。
"""
import math    # 标准库: 巨石拖拽的节点重叠判定
import random  # 标准库: 热浪/耀斑的随机强度

from ..config import ROBOT_ID   # 协议标识: 机器人不作打击候选


class ApiMixin:
    """SimulationEngine 的对外指令混入。

    属性要求 (由 SimulationEngine.__init__ 提供): self.nodes/routes/links/
    walls/obstacles/transport/tick; 世界层与网络层方法 (add_wall 等会触发
    _recompute_los + compute_network 全量重算)。

    执行链路: main.py WS 指令 -> (apply_override | inject_disaster |
    add_wall/... | send_user_message) -> _recompute_los/compute_network。
    """

    def send_user_message(self, src: str, dst: str, nbytes: int = 1024):
        """对外: 任意两节点间发送真实报文 (WS send_msg 指令入口)
        返回受理结果; 最终送达/超时信号走 events 与 transport.results"""
        return self.transport.send_message(src, dst, int(nbytes), kind="user")

    def apply_override(self, node_id: str, params: dict):
        """上帝模式: 覆写节点可变参数; 温度/电量越界 -> 当场死亡播报。
        不做即时 compute_network: 引擎每 0.25s 全量重算, 滑块拖动风暴下
        每条消息重算是把事件循环打满的元凶 (参数最迟下一拍生效)。"""
        node = self.nodes.get(node_id)
        if node is None:
            return {"ok": False, "error": "no such node"}
        for k, v in params.items():
            try:
                node.apply_override(k, v)
            except KeyError as e:
                return {"ok": False, "error": str(e)}
        narration = None
        if node.state != "DEAD" and (node.temp_c >= 100 or node.battery_soc <= 3):
            cause = "温度突破 100°C 临界值,芯片烧毁" if node.temp_c >= 100 else "电量耗尽"
            node.state = "DEAD"
            narration = (f"☠ 惨剧发生:{self._zh(node_id)} {cause},节点当场报废(现场已冒烟)。"
                         f"多智能体算法将立即绕开它重建路由。")
            self._emit("node_dead", "error",
                       f"☠ {node_id} 临界报废 ({cause})", narration=narration, node=node_id)
        elif params:
            self._emit("override", "info",
                       f"⚑ 上帝模式: {node_id} 参数覆写 {params}")
        return {"ok": True}

    def add_wall(self, x1: float, z1: float, x2: float, z2: float):
        """2D 俯视图添加墙体 -> 重算视距拓扑, 被切断的边从图中消失, 路由绕行"""
        self.walls.append({"x1": round(x1, 1), "z1": round(z1, 1),
                           "x2": round(x2, 1), "z2": round(z2, 1)})
        before = set(self.blocked_pairs)
        self._recompute_los()
        newly = len(set(self.blocked_pairs) - before)
        self._emit("wall_added", "warn",
                   f"🧱 新增墙体, 视线切断 {newly} 对节点直连",
                   narration=f"🧱 一堵岩壁插入网络!{newly} 对彼此可见的节点被墙体隔断,"
                             f"它们之间的边已从图中移除——算法正在重算路由,数据将绕行中继节点…")
        self.compute_network()

    def move_obstacle(self, idx: int, x: float, z: float):
        """2D 沙盘巨石拖拽: 移动障碍 -> LOS 重算 -> 被切断的链路从图中消失"""
        if not (0 <= idx < len(self.obstacles)):
            return {"ok": False, "error": "bad index"}
        if not self._in_tube((x, 0.0, z)):
            return {"ok": False, "error": "outside cave"}
        o = self.obstacles[idx]
        if any(math.dist((x, 0.0, z), (n.x, n.y, n.z)) < o["r"] + 20
               for n in self.nodes.values() if n.state != "DEAD"):
            return {"ok": False, "error": "node overlap"}   # 不许把石头压在节点上
        before = set(self.blocked_pairs)
        o["x"], o["y"], o["z"] = round(x, 1), 0.0, round(z, 1)
        self._recompute_los()
        newly = len(set(self.blocked_pairs) - before)
        if newly:
            self._emit("obstacle_moved", "warn",
                       f"🪨 巨石移位, 视线切断 {newly} 对节点直连",
                       narration=f"🪨 巨石被移动!它切断了 {newly} 对节点之间的视线——"
                                 f"相关链路已从图中移除,算法正在重算路由,数据将绕行中继…")
        self.compute_network()
        return {"ok": True, "cut": newly}

    def remove_wall(self, index: int):
        """撤销第 index 堵墙 (按加入顺序 0..n-1) -> LOS 重算"""
        if not (0 <= index < len(self.walls)):
            return {"ok": False, "error": "bad index"}
        self.walls.pop(index)
        self._recompute_los()
        self._emit("wall_removed", "ok", f"🧱 墙体 #{index} 已拆除",
                   narration="🧱 一堵岩壁被拆除,被它隔断的视线恢复,算法正在回归更优的直连路径。")
        self.compute_network()
        return {"ok": True}

    def clear_walls(self):
        """清空全部墙体 -> 视距拓扑还原"""
        self.walls = []
        self._recompute_los()
        self._emit("wall_cleared", "ok", "🧱 墙体已清除, 视距拓扑还原",
                   narration="🧱 岩壁已移除,节点视线恢复,算法正在回归最优直连路径。")
        self.compute_network()

    def inject_disaster(self, kind: str | None):
        """灾害注入分发: 塌方/摧毁主干道单独处理, 其余按全表应力/单点打击"""
        self.disaster = kind
        if kind is None:
            return
        if kind == "collapse":
            self._collapse()
            return
        if kind == "kill_backbone":
            self._kill_backbone()
            return
        name = {"thermal_surge": "热浪", "solar_flare": "太阳耀斑",
                "random_kill": "陨石撞击"}[kind]
        self._emit("disaster", "error", f"☄ 灾害注入: {name}",
                   narration=f"☄ {name}来袭!全网络节点同时承受极端应力,请观察各节点的"
                             f"实时指标变化与算法的应对。")
        targets = list(self.nodes.values())
        if kind == "thermal_surge":
            for n in targets:
                n.temp_c += random.uniform(35, 70)
        elif kind == "solar_flare":
            for n in targets:
                n.radiation_rad += random.uniform(8000, 20000)
        elif kind == "random_kill":
            victim = random.choice([n for n in targets if n.id != self.sink_id])
            victim.state = "DEAD"
            self._emit("node_dead", "error", f"☠ {victim.id} 被击毁",
                       narration=f"☄ 一颗陨石击中 {self._zh(victim.id)},该节点当场损毁。"
                                 f"算法正在评估损失并重建路由…", node=victim.id)
        self.compute_network()

    def _kill_backbone(self):
        """摧毁主干道中继: 精确打击当前流量最集中的节点, 强制岔路绕行"""
        self._pre_collapse_routes = {k: dict(v) for k, v in self.routes.items()}
        # 找承载流量最大的中继 (非 sink)
        load_of = {}
        for r in self.routes.values():
            for nid in (r.get("path") or [])[1:-1]:
                if nid == ROBOT_ID:
                    continue             # 机器人是移动资产, 不作打击候选
                load_of[nid] = load_of.get(nid, 0) + 1
        if not load_of:
            return
        victim_id = max(load_of, key=load_of.get)
        victim = self.nodes[victim_id]
        victim.state = "DEAD"
        self._emit("node_dead", "error",
                   f"☠ 主干道中继 {victim_id} 被摧毁 (承载 {load_of[victim_id]} 条流)",
                   narration=f"☠ 警报:主干道上的关键中继节点 {self._zh(victim_id)} 被摧毁!"
                             f"它原本承载着 {load_of[victim_id]} 条数据流。请观察 3D 画面——"
                             f"数据流将在岔路口拐弯,沿曲折的备用洞穴通道继续向洞口传输…",
                   node=victim_id)
        self.compute_network()

    def _collapse(self):
        """洞顶塌方: 在最繁忙主干链路中点砸落巨石"""
        self._pre_collapse_routes = {k: dict(v) for k, v in self.routes.items()}
        live = [(k, l) for k, l in self.links.items()
                if l["up"] and ROBOT_ID not in k]   # 机器人边不作为塌方目标
        if not live:
            return
        key, _lk = max(live, key=lambda kv: self.link_load.get(kv[0], 0.0))
        a, b = self.nodes[key[0]], self.nodes[key[1]]
        mid = ((a.x + b.x) / 2, 0.0, (a.z + b.z) / 2)
        for n in self.nodes.values():
            if n.id in key or n.state == "DEAD":
                continue
            if math.dist(mid, (n.x, n.y, n.z)) < 70.0:
                mid = (mid[0] + 50.0, mid[1], mid[2] + 50.0)
                break
        self.obstacles.append({
            "x": round(mid[0], 2), "y": round(mid[1], 2), "z": round(mid[2], 2),
            "r": 38.0, "h": 0.0, "shape": "boulder", "rot": random.uniform(0, 360),
        })
        self._recompute_los()
        self._emit("disaster", "error", f"☄ 洞顶塌方: 巨石阻断 {key[0]} ↔ {key[1]}",
                   narration=f"⚠️ 警报:熔岩管洞顶发生塌方!一块巨石砸落,正好阻断了 "
                             f"{self._zh(key[0])} 与 {self._zh(key[1])} 节点之间的主干信道"
                             f"(视线被完全遮挡,信噪比归零)。多智能体算法正在感知断裂…",
                   a=key[0], b=key[1])
        self.compute_network()
