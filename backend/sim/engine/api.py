# -*- coding: utf-8 -*-
"""
引擎对外 API 层: 上帝模式 / 灾害注入 / 墙与巨石操作 (ApiMixin)
================================================================
职责: WS 指令的领域入口 —— 节点参数覆写 (含临界死亡判定)、四类灾害
(塌方/热浪/耀斑/陨石/摧毁主干道)、墙体增删、巨石拖拽、任意两节点发报文。
依赖: 引擎世界层 (LOS 重算) 与网络层 (compute_network), transport 发送。
"""
import logging   # 标准库: 模块日志 (上帝操作/灾害)
import math    # 标准库: 巨石拖拽的节点重叠判定
import random  # 标准库: 热浪/耀斑的随机强度
import time    # 标准库: 暂停锚定时刻 (monotonic)

from ..config import (JAM_LIFT_MAX_DB, JAM_RADIUS,   # 干扰源抬升/半径 (开关灾害)
                       ROBOT_ID)   # 协议标识: 机器人不作打击候选

log = logging.getLogger(__name__)   # 本模块日志器


class ApiMixin:
    """职责: SimulationEngine 的对外指令混入。

    属性要求 (由 SimulationEngine.__init__ 提供): self.nodes/routes/links/
    walls/obstacles/transport/tick; 世界层与网络层方法 (add_wall 等会触发
    _recompute_los + compute_network 全量重算)。

    调用链: main.py WS 指令 -> (apply_override | inject_disaster |
    add_wall/... | send_user_message) -> _recompute_los/compute_network。
    """

    def send_user_message(self, src: str, dst: str, nbytes: int = 1024):
        """对外: 任意两节点间发送真实报文 (WS send_msg 指令入口)。

        Args: src/dst: 源/目的节点 id; nbytes: 报文字节数。
        Returns: dict —— transport.send_message 的受理结果 (含 signal)。
        Globals Used: None。Calls: transport.send_message (最终送达/超时
        信号走 events 与 transport.results 双通道)。
        """
        return self.transport.send_message(src, dst, int(nbytes), kind="user")

    def toggle_pause(self):
        """暂停/恢复仿真 (WS toggle_pause 指令入口): 翻转 paused 标志。
        暂停 = 物理拍冻结在当前帧 (节点/网络/传输/链/机器人/tick 全停,
        广播与上帝操作保留); 恢复 = 从冻结帧原速续走, 无快进堆积。

        Args: None。
        Returns: dict {ok, paused} —— paused 为切换后的状态。
        Globals Used: None。Calls: _emit (暂停/恢复事件与解说词)。
        """
        self.paused = not self.paused
        if self.paused:
            self._paused_at = time.monotonic()   # 动画进度分数的冻结锚点
        log.info("仿真%s @tick=%d", "暂停" if self.paused else "恢复", self.tick)
        if self.paused:
            self._emit("sim_paused", "info",
                       f"⏸ 仿真已暂停 (tick={self.tick}), 所有计算冻结在这一帧",
                       narration="⏸ 沙盘按下暂停键——所有计算与报文飞行定格在"
                                 "此刻,你可以慢慢观察这张网络;再按一次继续。")
        else:
            self._emit("sim_resumed", "ok",
                       f"▶ 仿真恢复 (tick={self.tick}), 从冻结帧继续",
                       narration="▶ 沙盘恢复运行,所有计算从刚才定格的那一帧"
                                 "原速继续。")
        return {"ok": True, "paused": self.paused}

    def toggle_rl(self):
        """B 组实验开关 (WS toggle_rl 指令入口): 信道决策器
        RCSPA(A组规则) <-> Q-learning(B组学习) 互换; 关闭即弃 Q 表
        (再开从头学, 保证每轮实验独立)。

        Args: None。
        Returns: dict {ok, rl_channels} —— 切换后的开关状态。
        Globals Used: None。Calls: _emit (实验事件与解说词)。
        """
        self.rl_channels = not self.rl_channels
        if not self.rl_channels:
            self.rl_learner = None
        log.info("RL信道开关 -> %s", "Q-learning(B组)" if self.rl_channels
                 else "RCSPA(A组)")
        self._emit("rl_toggle", "info",
                   "🧪 信道决策器切换为 "
                   + ("Q-learning (B组: 逐边Q表, 边跑边学)" if self.rl_channels
                      else "RCSPA (A组: 规则)"),
                   narration=("🧪 实验模式开启——每根通信桩现在用自己的打分表"
                              "选信道, 靠送达/超时的奖惩自学避让干扰;"
                              "观察它的送达率能否追上手写规则。"
                              if self.rl_channels else
                              "🧪 切回规则模式 (RCSPA)。"))
        return {"ok": True, "rl_channels": self.rl_channels}

    def apply_override(self, node_id: str, params: dict):
        """上帝模式: 覆写节点可变参数; 温度/电量越界 -> 当场死亡播报。
        不做即时 compute_network: 引擎每 0.25s 全量重算, 滑块拖动风暴下
        每条消息重算是把事件循环打满的元凶 (参数最迟下一拍生效)。

        Args: node_id: 节点 id; params: {参数名: 新值} (白名单见 Node.MUTABLE)。
        Returns: dict {ok} 或 {ok:False, error}。Raises: 无 (KeyError 内捕)。
        Globals Used: None。Calls: node.apply_override / _emit。
        死亡单向门: temp>=100 或 soc<=3 -> state=DEAD (无自动复活)。
        """
        node = self.nodes.get(node_id)
        if node is None:
            return {"ok": False, "error": "no such node"}
        for k, v in params.items():
            try:
                node.apply_override(k, v)
            except KeyError as e:
                return {"ok": False, "error": str(e)}
        # 手动接管判定: 上帝改功率/发射电流 -> 退出自动调功 (人机不抢方向盘)
        if node.power_auto and ({"tx_power_dbm", "i_tx"} & params.keys()):
            node.power_auto = False
            log.info("自动调功让位 %s: 功率参数已由上帝接管", node_id)
            self._emit("power_manual", "info",
                       f"⚑ {node_id} 发射功率转为手动控制 (链路自举停用)",
                       node=node_id)
        narration = None
        if node.state != "DEAD" and (node.temp_c >= 100 or node.battery_soc <= 3):
            cause = "温度突破 100°C 临界值,芯片烧毁" if node.temp_c >= 100 else "电量耗尽"
            node.state = "DEAD"
            log.warning("节点烧毁 %s (%s, temp=%.1f soc=%.1f)",
                        node_id, cause, node.temp_c, node.battery_soc)
            narration = (f"☠ 惨剧发生:{self._zh(node_id)} {cause},节点当场报废(现场已冒烟)。"
                         f"多智能体算法将立即绕开它重建路由。")
            self._emit("node_dead", "error",
                       f"☠ {node_id} 临界报废 ({cause})", narration=narration, node=node_id)
        elif params:
            log.info("上帝覆写 %s %s", node_id, params)
            self._emit("override", "info",
                       f"⚑ 上帝模式: {node_id} 参数覆写 {params}")
        return {"ok": True}

    def add_wall(self, x1: float, z1: float, x2: float, z2: float):
        """2D 俯视图添加墙体 -> 重算视距拓扑, 被切断的边从图中消失, 路由绕行。

        Args: x1/z1/x2/z2: 墙体两端世界坐标。Returns: None。
        Globals Used: None。Calls: _recompute_los / _emit / compute_network。
        """
        self.walls.append({"x1": round(x1, 1), "z1": round(z1, 1),
                           "x2": round(x2, 1), "z2": round(z2, 1)})
        before = set(self.blocked_pairs)
        self._recompute_los()
        newly = len(set(self.blocked_pairs) - before)
        log.info("画墙 (%.0f,%.0f)-(%.0f,%.0f): 切断 %d 对视线",
                 x1, z1, x2, z2, newly)
        self._emit("wall_added", "warn",
                   f"🧱 新增墙体, 视线切断 {newly} 对节点直连",
                   narration=f"🧱 一堵岩壁插入网络!{newly} 对彼此可见的节点被墙体隔断,"
                             f"它们之间的边已从图中移除——算法正在重算路由,数据将绕行中继节点…")
        self.compute_network()

    def move_obstacle(self, idx: int, x: float, z: float):
        """2D 沙盘巨石拖拽: 移动障碍 -> LOS 重算 -> 被切断的链路从图中消失。

        Args: idx: 巨石下标; x/z: 新位置世界坐标。Returns: dict ——
        {ok, cut} 或 {ok:False, error: bad index/outside cave/node overlap}。
        Globals Used: None。Calls: _in_tube / _recompute_los / compute_network。
        """
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
        log.info("巨石#%d 拖至 (%.0f,%.0f): 切断 %d 对视线", idx, x, z, newly)
        if newly:
            self._emit("obstacle_moved", "warn",
                       f"🪨 巨石移位, 视线切断 {newly} 对节点直连",
                       narration=f"🪨 巨石被移动!它切断了 {newly} 对节点之间的视线——"
                                 f"相关链路已从图中移除,算法正在重算路由,数据将绕行中继…")
        self.compute_network()
        return {"ok": True, "cut": newly}

    def remove_wall(self, index: int):
        """撤销第 index 堵墙 (按加入顺序 0..n-1) -> LOS 重算。

        Args: index: 墙体下标。Returns: dict {ok} 或 {ok:False, error}。
        Globals Used: None。Calls: _recompute_los / compute_network。
        """
        if not (0 <= index < len(self.walls)):
            return {"ok": False, "error": "bad index"}
        self.walls.pop(index)
        self._recompute_los()
        log.info("拆墙 #%d (余 %d 堵)", index, len(self.walls))
        self._emit("wall_removed", "ok", f"🧱 墙体 #{index} 已拆除",
                   narration="🧱 一堵岩壁被拆除,被它隔断的视线恢复,算法正在回归更优的直连路径。")
        self.compute_network()
        return {"ok": True}

    def clear_walls(self):
        """清空全部墙体 -> 视距拓扑还原。

        Args: None。Returns: None。Globals Used: None。
        Calls: _recompute_los / compute_network。
        """
        self.walls = []
        self._recompute_los()
        log.info("清空全部墙体")
        self._emit("wall_cleared", "ok", "🧱 墙体已清除, 视距拓扑还原",
                   narration="🧱 岩壁已移除,节点视线恢复,算法正在回归最优直连路径。")
        self.compute_network()

    def inject_disaster(self, kind: str | None):
        """灾害注入分发: 塌方/摧毁主干道单独处理, 其余按全表应力/单点打击。

        Args: kind: collapse/kill_backbone/thermal_surge/solar_flare/
              random_kill 之一; None = 清除灾害标记。
        Returns: None。Globals Used: None。Calls: _collapse/_kill_backbone/
        compute_network (灾害后全量重算)。
        """
        self.disaster = kind
        if kind is None:
            return
        log.warning("灾害注入: %s", kind)
        if kind == "collapse":
            self._collapse()
            return
        if kind == "kill_backbone":
            self._kill_backbone()
            return
        if kind == "jammer":
            # 开关式灾害: 已开机 -> 召回; 未开机 -> 腔室内随机落点启动
            if self.jammer is None:
                c = self.chambers[0]
                self.jammer = {"x": c["x"] + self._rng.uniform(-0.4, 0.4) * c["r"],
                               "z": c["z"] + self._rng.uniform(-0.4, 0.4) * c["rz"],
                               "wx": c["x"], "wz": c["z"]}
                log.warning("干扰源开机 @(%s,%s) 半径 %.0fm 抬升 %.0fdB",
                            self.jammer["x"], self.jammer["z"],
                            JAM_RADIUS, JAM_LIFT_MAX_DB)
                self._emit("disaster", "error",
                           "📵 强力移动干扰源开机, 正在全管游走",
                           narration="📵 一台强干扰源开始在熔岩管内游走——"
                                     "它靠近哪里, 哪里的无线电噪声就飙升、链路成片熔断;"
                                     "观察它走远后网络如何自动愈合。再点一次开关可召回。")
            else:
                self.jammer = None
                self.disaster = None
                log.warning("干扰源召回")
                self._emit("disaster", "ok",
                           "📵 干扰源已召回, 被压制区域链路将自动恢复",
                           narration="📵 干扰源关机——被压制区域的信噪比回升,"
                                     "熔断的链路正在逐条复活。")
            self.compute_network()
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
            log.warning("热浪: 全网升温 35~70°C")
        elif kind == "solar_flare":
            for n in targets:
                n.radiation_rad += random.uniform(8000, 20000)
            log.warning("耀斑: 全网辐射 +8000~20000 rad")
        elif kind == "random_kill":
            victim = random.choice([n for n in targets if n.id != self.sink_id])
            victim.state = "DEAD"
            log.warning("陨石击毁 %s", victim.id)
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
        log.warning("摧毁主干道 %s (承载 %d 条流)", victim_id, load_of[victim_id])
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
        log.warning("塌方: 巨石@(%s,%s) 砸断 %s<->%s", mid[0], mid[2], key[0], key[1])
        self._recompute_los()
        self._emit("disaster", "error", f"☄ 洞顶塌方: 巨石阻断 {key[0]} ↔ {key[1]}",
                   narration=f"⚠️ 警报:熔岩管洞顶发生塌方!一块巨石砸落,正好阻断了 "
                             f"{self._zh(key[0])} 与 {self._zh(key[1])} 节点之间的主干信道"
                             f"(视线被完全遮挡,信噪比归零)。多智能体算法正在感知断裂…",
                   a=key[0], b=key[1])
        self.compute_network()
