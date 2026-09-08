# -*- coding: utf-8 -*-
"""
网络层: 链路聚合 + 路由 + PAMAS + 模式机 (NetworkMixin)
========================================================
职责: 引擎的"网络大脑" —— 每 tick 全量重算链路预算表 (LOS 过滤 + 物理
定价)、Dijkstra 路由与信息素负载、节点网络状态 (队列/降级)、PAMAS 电台
调度、被遮挡邻居清单、STABLE/HEALING/CONVERGED 自愈模式机。
依赖: physics (链路预算/代价), routing (Dijkstra), config, robot 挂点。
"""
import logging   # 标准库: 模块日志 (链路生死/拥塞)
import random  # 标准库: 拥塞事件的采样播报 (防刷屏)

from ..config import (BOOST_DROP_GUARD_DB, BOOST_STEP_DB,   # 回落安全余量/功率步长
                      CHAIN_QUEUE_CAP)   # 控制平面配额 (链上待发字节封顶)
from .. import physics            # 物理层: link_budget/link_cost/sim_distance
from ..routing import routing_step   # 路由: Dijkstra 波前 + 跳数分层
from .state_machine import StateMachineMixin   # 自愈模式机 (见独立模块)

log = logging.getLogger(__name__)   # 本模块日志器
from .geometry import _seg2d_intersect, _seg_blocked_by_sphere  # 遮挡成因判定


class NetworkMixin(StateMachineMixin):
    """职责: SimulationEngine 的网络计算混入 (自愈模式机在 StateMachineMixin)。

    属性要求 (由 SimulationEngine.__init__ 提供): self.nodes/links/routes/
    prev_links/prev_routes/link_load/traffic/blocked_pairs/blocked_info/
    mode/_stable_ticks/heal_started_tick/_pre_collapse_routes/robot/
    transport/chain_net/tick。

    调用链: run_forever -> compute_network (编排) -> _build_links ->
    _emit_link_events -> routing_step -> _update_link_load ->
    _emit_route_events -> _apply_node_net_state -> _apply_pamas ->
    _blocked_neighbors_info -> robot.tick -> _mode_step。
    """

    def compute_network(self, quiet: bool = False):
        """每 tick 全量重算: 链路 -> 路由 -> 节点状态 -> 电台 -> 模式机。

        Args: quiet: True=世界构建期 (不产事件、不触发自愈叙事)。
        Returns: None (副作用: links/routes/traffic/node.* /mode 等)。
        Globals Used: CHAIN_QUEUE_CAP (链上待发配额)。
        Calls: _build_links/_emit_link_events/routing_step/_update_link_load/
        _emit_route_events/_apply_node_net_state/_apply_pamas/
        _blocked_neighbors_info/robot.tick/_mode_step。
        """
        nodes = list(self.nodes.values())
        links = self._build_links(nodes)
        if not quiet:
            self._emit_link_events(links)
        if self.robot:
            self.robot.inject_links(links)   # 挂点①: 机器人链路 (事件比对后: 边翻动不产事件)
        self.links = links
        self.routes, self.wave = routing_step(nodes, links, self.sink_id)
        self._update_link_load()
        if not quiet:
            self._emit_route_events()
        chain_load = self._chain_load()
        self._apply_node_net_state(quiet, chain_load)
        # 活跃流量 = 传输层在途报文 (真实路径与字节)
        self.traffic = self.transport.active_traffic()
        self._apply_pamas(chain_load)
        self._blocked_neighbors_info()
        if self.robot:
            self.robot.tick(self.tick)   # 挂点②: 状态机/SOS/道钉投放 (路由算完后)
        self._mode_step(quiet, links)
        self.prev_links = {k: v for k, v in links.items()}
        self.prev_routes = {k: dict(v) for k, v in self.routes.items()}

    # ---------------- 链路 ----------------
    def _build_links(self, nodes):
        """链路预算聚合: LOS 过滤 -> link_budget 双向定价 -> 链路表。
        键一律排序 (id 字母序): 道钉 BEACON-xx < NODE-xx < ROBOT,
        传输层/统计全部按 sorted 元组查键, 两边必须同一约定。"""
        links = {}
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i], nodes[j]
                key = tuple(sorted((a.id, b.id)))
                if (a.id, b.id) in self.blocked_pairs or \
                        (b.id, a.id) in self.blocked_pairs:
                    continue                  # LOS 遮挡 (巨石/石柱)
                lab = physics.link_budget(a, b)
                lba = physics.link_budget(b, a)
                if lab is None or lba is None:
                    continue
                load = self.link_load.get(key, 0.0)
                if key[0] == a.id:            # 键方向与循环方向一致
                    c_ab, c_ba = (physics.link_cost(a, b, lab, load),
                                  physics.link_cost(b, a, lba, load))
                else:                         # 反序 (如 BEACON 在前): 按键方向定价
                    c_ab, c_ba = (physics.link_cost(b, a, lba, load),
                                  physics.link_cost(a, b, lab, load))
                links[key] = {
                    **lab,
                    "cost_ab": c_ab, "cost_ba": c_ba,
                    "load": round(load, 2),
                }
                a.snr_db, b.snr_db = lab["snr_db"], lba["snr_db"]
                a.ber, b.ber = lab["ber"], lba["ber"]
        return links

    def _emit_link_events(self, links):
        """链路生死事件比对 (对上一拍表): 熔断带成因, 恢复带 SNR"""
        for key, l in links.items():
            pl = self.prev_links.get(key)
            if pl and pl["up"] and not l["up"]:
                reason = ("SNR=%.1fdB" % l["snr_db"] if l["snr_db"] < 5
                          else "BER=%.1e" % l["ber"] if l["ber"] >= 1e-3
                          else "margin=%.1fdB" % l["margin_db"])
                log.info("链路熔断 %s<->%s (%s)", key[0], key[1], reason)
                self._emit("link_down", "error",
                           f"✖ 链路熔断 {key[0]} ↔ {key[1]} ({reason})",
                           narration=f"⚠️ {self._zh(key[0])} 与 {self._zh(key[1])} 之间的信道质量恶化"
                                     f"(信噪比跌至 {l['snr_db']}dB,低于解调门限),链路熔断。",
                           a=key[0], b=key[1])
            elif pl and not pl["up"] and l["up"]:
                log.debug("链路恢复 %s<->%s (SNR=%s)", key[0], key[1], l["snr_db"])
                self._emit("link_up", "ok",
                           f"✔ 链路恢复 {key[0]} ↔ {key[1]} (SNR={l['snr_db']}dB)",
                           a=key[0], b=key[1])

    def _update_link_load(self):
        """信息素负载: 路径占用统计 -> 指数平滑 (ACO, 流量自动分流)"""
        usage: dict[tuple, int] = {}
        for r in self.routes.values():
            path = r.get("path") or []
            for k in range(len(path) - 1):
                key = tuple(sorted((path[k], path[k + 1])))
                usage[key] = usage.get(key, 0) + 1
        for key in set(list(usage) + list(self.link_load)):
            self.link_load[key] = 0.82 * self.link_load.get(key, 0.0) + 0.18 * usage.get(key, 0)

    def _emit_route_events(self):
        """路由事件比对: 重路由只入 sim.log 不上前端 (ACO 代价每拍微动导致
        路径高频抖动, 进事件日志会刷屏挤掉有效信息); 失联/重新入网才播报。"""
        for nid, r in self.routes.items():
            pr = self.prev_routes.get(nid)
            if pr is None:
                continue
            if pr["hop_count"] > 0 and r["hop_count"] > 0 and pr["path"] != r["path"]:
                log.info("重路由 %s: %d跳 -> %d跳",
                         nid, len(pr["path"]) - 1, len(r["path"]) - 1)
            if pr["hop_count"] > 0 and r["hop_count"] < 0:
                self._emit("isolated", "error",
                           f"☠ {nid} 失联, 成为孤岛节点",
                           narration=f"⚠️ {self._zh(nid)} 与所有邻居失去联系,成为信息孤岛——"
                                     f"它周围的所有通路都被切断了。", node=nid)
            if pr["hop_count"] < 0 and r["hop_count"] > 0:
                self._emit("rejoin", "ok",
                           f"✦ {nid} 重新入网 (hop={r['hop_count']})",
                           narration=f"✅ {self._zh(nid)} 通过新路径重新回到网络。",
                           node=nid)

    # ---------------- 节点网络状态 ----------------
    def _chain_load(self) -> dict:
        """链上待发字节 (控制平面配额封顶):
        队列真化: 积压率 = 传输层缓冲字节 + 链上待发字节 (区块链报文真实计账,
        上限 CHAIN_QUEUE_CAP 作为"控制平面配额" —— 足额会计会令全网常态饱和:
        链的追块流量(心跳×多持有者响应×逐跳转发 8~15KB 批)实测均值 91% 积压)"""
        chain_net = getattr(self, "chain_net", None)
        return ({nid: min(b, CHAIN_QUEUE_CAP)
                 for nid, b in chain_net.tx_load.items()}
                if chain_net else {})

    def _apply_node_net_state(self, quiet: bool, chain_load: dict):
        """回填每节点邻居数/跳数/队列积压率; 拥塞播报与状态降级"""
        for n in self.nodes.values():
            n.neighbors = sum(1 for (a, b), l in self.links.items()
                              if n.id in (a, b) and l["up"])
            # 链路度数自保挂点 (neighbors 刚回填最鲜活):
            # drop_safe = 自举节点的每条活跃链路以本端发射降一档后余量仍足
            # (按本端方向实时预算; False 时保持功率 —— 那条链靠自举维系)
            drop_safe = True
            if n.power_boosted:
                others = [b if a == n.id else a for (a, b), l in self.links.items()
                          if n.id in (a, b) and l["up"]]
                drop_safe = bool(others) and all(
                    (physics.link_budget(n, self.nodes[o]) or {}).get("margin_db", -99)
                    > BOOST_STEP_DB + BOOST_DROP_GUARD_DB for o in others)
            act = n.tune_power_for_degree(n.neighbors, self.tick, drop_safe)
            if act and not quiet:
                kind, old_db = act
                if kind == "boost":
                    log.info("功率自举 %s: %d 条链路, %.0f->%.0f dBm",
                             n.id, n.neighbors, old_db, n.tx_power_dbm)
                    self._emit("power_boost", "warn",
                               f"⚡ {n.id} 仅剩 {n.neighbors} 条活跃链路, 发射功率自举 "
                               f"{old_db:.0f}→{n.tx_power_dbm:.0f} dBm (发射电流同步上调)",
                               narration=f"⚡ {self._zh(n.id)} 发现自己的链路不足两条,"
                                         f"正在调大发射功率努力够到更远的邻居——"
                                         f"能自救的先自救, 不等救援。", node=n.id)
                elif kind == "survival":
                    log.info("保命回落 %s: SoC=%.0f%%, %.0f->%.0f dBm",
                             n.id, n.battery_soc, old_db, n.tx_power_dbm)
                    self._emit("power_survival", "warn",
                               f"🪫 {n.id} 电量触红线, 发射功率回落 "
                               f"{old_db:.0f}→{n.tx_power_dbm:.0f} dBm 保命", node=n.id)
                else:
                    log.info("功率回落 %s: %d 条链路, %.0f->%.0f dBm",
                             n.id, n.neighbors, old_db, n.tx_power_dbm)
                    self._emit("power_back", "ok",
                               f"⚡ {n.id} 链路充足 ({n.neighbors} 条), 功率回落 "
                               f"{old_db:.0f}→{n.tx_power_dbm:.0f} dBm 省电", node=n.id)
            n.hop_count = self.routes.get(n.id, {}).get("hop_count", -1)
            n.queue_pct = self.transport.queue_pct(n.id, chain_load.get(n.id, 0))
            if n.queue_pct > 85 and not quiet and random.random() < 0.3:
                log.warning("拥塞 %s 积压%.0f%%", n.id, n.queue_pct)
                total_b = self.transport.node_bytes(n.id) + chain_load.get(n.id, 0)
                self._emit("congestion", "warn",
                           f"⚠ {n.id} 队列积压 {n.queue_pct:.0f}% ({total_b}B 待发)",
                           narration=f"⚠️ {self._zh(n.id)} 的数据包排队越来越长(积压 "
                                     f"{n.queue_pct:.0f}%),算法正在考虑分流。", node=n.id)
            if n.state not in ("DEAD", "SEU_RESET"):
                n.state = "DEGRADED" if (n.queue_pct > 70 or n.snr_db < 8) else "ACTIVE"

    def _apply_pamas(self, chain_load: dict):
        """PAMAS 独立关机判定: 激活路径外的节点若邻居正在收发 -> 休眠省电。
        活跃集 = 传输层缓冲里真正有报文要收发的节点 + 链上有待发报文的节点"""
        active_nodes, _active_edges = self.transport.active_nodes_edges()
        active_nodes.add(self.sink_id)
        for nid, b in chain_load.items():
            if b > 0:
                active_nodes.add(nid)      # 链上待发 = 电台真实收发 (TXRX)
        # 物理邻接表 (载波监听用)
        phys_adj = {}
        for (a, b) in self.links:
            phys_adj.setdefault(a, set()).add(b)
            phys_adj.setdefault(b, set()).add(a)
        for n in self.nodes.values():
            if n.state == "DEAD":
                n.radio = "IDLE"
            elif n.id in active_nodes:
                n.radio = "TXRX"
            else:
                # PAMAS 独立关机判定: 监听到邻居正在收发且自身无数据 -> 关闭电台
                nbr_busy = any(m in active_nodes for m in phys_adj.get(n.id, ()))
                n.radio = "SLEEP" if nbr_busy else "IDLE"

    def _blocked_neighbors_info(self):
        """视距架构数据: 每节点最近 3 个"近在咫尺却被岩壁/巨石挡住"的邻居
        (图中无边, 必须经中继绕行) —— 前端用红色断裂虚线呈现"""
        block_adj = {}
        for (x, y) in self.blocked_pairs:
            block_adj.setdefault(x, []).append(y)
            block_adj.setdefault(y, []).append(x)
        self.blocked_info = {}
        for n in self.nodes.values():
            lst = []
            for oid in block_adj.get(n.id, ()):
                o = self.nodes.get(oid)
                if not o:
                    continue
                d = physics.sim_distance(n, o)
                if d <= 31.0:
                    lst.append((d, oid))
            lst.sort()
            self.blocked_info[n.id] = self._blocked_causes(n, lst[:3])

    def _blocked_causes(self, n, lst):
        """逐个判定遮挡成因 (巨石/巨柱/墙体/岩壁/信道), 生成前端信息条目"""
        nxt = self.routes.get(n.id, {}).get("next_hop")
        info = []
        for d, oid in lst:
            o = self.nodes.get(oid)
            if o is None:
                continue
            pa, pb = (n.x, n.y, n.z), (o.x, o.y, o.z)
            if any(_seg_blocked_by_sphere(pa, pb, (ob["x"], ob["y"], ob["z"]), ob["r"] * 0.85)
                   for ob in self.obstacles):
                cause = "巨石遮挡"
            elif any(_seg_blocked_by_sphere(pa, pb, (s[0], s[1], s[2]), s[3])
                     for s in self.pillar_spheres):
                cause = "巨柱遮挡"
            elif any(_seg2d_intersect((pa[0], pa[2]), (pb[0], pb[2]),
                                      (w["x1"], w["z1"]), (w["x2"], w["z2"]))
                     for w in self.walls):
                cause = "墙体遮挡"
            elif not self._seg_in_tube(pa, pb):
                cause = "岩壁阻隔"
            else:
                cause = "信道质量"
            info.append({"id": oid, "d": round(d, 1), "via": nxt, "cause": cause})
        return info
