# -*- coding: utf-8 -*-
"""
快照与渲染总线层: SnapshotMixin
==================================
职责:
- 渲染总线 vis_packet: 任意层在收发点调它即可上屏, 前端按 kind 自动配色
  (零注册; 样式表只是美化覆盖, 未登记类型按名称哈希取色);
- 快照导出 snapshot: 全量 JSON (链路/节点/路由/流量/账本/统计) 供 WS 广播。
依赖: physics (距离过滤), config (VIS_* 上限与优先级), robot 挂点。
"""
import logging   # 标准库: 模块日志 (快照周期摘要)
import time   # 标准库: monotonic 时钟 (总线导出的 tick 内飞行进度)

from ..config import (ROBOT_ID, TICK_PHYS_S,          # 机器人标识/物理拍
                      VIS_MAX, VIS_PRIORITY, VIS_RESERVE)   # 总线截断策略
from .. import physics   # 物理层: distance (链路下发距离过滤)

log = logging.getLogger(__name__)   # 本模块日志器


class SnapshotMixin:
    """职责: SimulationEngine 的快照与渲染总线混入。

    属性要求 (由 SimulationEngine.__init__ 提供): self.packets_vis/_vis_at
    (总线缓冲), self.nodes/links/routes/traffic/wave/events/robot/transport/
    chain_net/history (快照数据源)。

    调用链: 各层收发点 -> vis_packet (登记) ; run_forever -> snapshot
    -> _vis_export (按优先级截断) -> main.broadcast -> 前端。
    """

    # ==================================================================
    def vis_packet(self, a: str, b: str, kind: str, relayed: bool = True):
        """渲染总线固定注册函数: 一个报文从 a 飞到 b 的单跳。
        任何层在任何收发点调用它即可上屏; 前端按 kind 自动配色绘制
        (样式表只是美化覆盖, 未登记的类型按名称哈希取色) —— 零注册。
        (列表每 tick 清空, 控量靠 _vis_export 截断; 此处仅留病态保险丝)

        Args: a/b: 收发节点 id; kind: 报文类型; relayed: 是否中继转发跳。
        Returns: None。Globals Used: None。Calls: None。
        """
        if len(self.packets_vis) >= 5000:       # 保险丝: 正常 tick 量级 <1k
            return
        self.packets_vis.append({"a": a, "b": b, "kind": kind, "r": relayed})

    def _vis_export(self) -> list:
        """总线快照导出: 标注 tick 内进度 t (0..1), 按类型优先级截断;
        未登记类型走保留名额, 保证零注册上报在风暴中也不丢。"""
        if not self.packets_vis:
            return []
        frac = (min(1.0, max(0.0, (time.monotonic() - self._vis_at) / TICK_PHYS_S))
                if self._vis_at else 0.0)
        order = {k: i for i, k in enumerate(VIS_PRIORITY)}
        known = sorted((p for p in self.packets_vis if p["kind"] in order),
                       key=lambda p: order[p["kind"]])
        others = [p for p in self.packets_vis if p["kind"] not in order]
        items = known[:VIS_MAX - VIS_RESERVE] + others[:VIS_RESERVE]
        return [{**p, "t": round(frac, 3)} for p in items]

    def _npos(self, nid):
        """快照用坐标: 普通节点/道钉在 nodes 表, ROBOT 用机器人伪节点"""
        n = self.nodes.get(nid)
        if n is not None:
            return n
        if self.robot is not None and nid == ROBOT_ID:
            return self.robot.node
        return self.robot.node if self.robot else n

    def snapshot(self) -> dict:
        """全量快照 (WS 每 0.2s 广播一帧; 前端唯一数据源)。

        Args: None。Returns: dict —— tick/mode/wave/events(尾部40)/links/
        nodes/routes/traffic/robot/transport/packets(在途+总线)/chain/stats。
        Globals Used: None。Calls: _snap_links/_snap_stats/transport.* /
        chain_net.export_info/_vis_export; 追加 history 曲线点。
        """
        alive = [n for n in self.nodes.values() if n.state != "DEAD"]
        snap = {
            "tick": self.tick,
            "disaster": self.disaster,
            "mode": self.mode,
            "wave": self.wave,
            "events": list(self.hub.events)[-40:],
            "last_narration": self.hub.last_narration,
            "obstacles": self.obstacles,
            "walls": self.walls,
            "links": self._snap_links(),
            "nodes": {nid: {**n.to_dict(),
                            "blocked_nbrs": self.blocked_info.get(nid, []),
                            "sos": bool(self.robot and nid in self.robot.sos_active)}
                      for nid, n in self.nodes.items()},
            "routes": self.routes,
            "traffic": self.traffic,
            "robot": (self.robot.export() if self.robot else None),
            "transport": self.transport.summary(),
            "packets": self.transport.active_packets() + self._vis_export(),
            "chain": self.chain_net.export_info(),
            "stats": self._snap_stats(alive),
        }
        if self.tick % 200 == 0:
            log.debug("快照 tick=%s nodes=%d links=%d packets=%d chain_h=%s",
                      self.tick, len(snap["nodes"]), len(snap["links"]),
                      len(snap["packets"]), snap["chain"]["h_max"])
        self.history.append({"t": self.tick, **snap["stats"]})
        return snap

    def _snap_links(self) -> list:
        """链路下发列表 (仅近距边, 含传输层计账)"""
        return [
            {
                "a": a, "b": b,
                "snr_db": l["snr_db"], "up": l["up"],
                "margin_db": l["margin_db"], "ber": l["ber"],
                "cost": l["cost_ab"], "band": l["band"], "load": l["load"],
                "tr": self.transport.link_summary((a, b)),
            }
            for (a, b), l in self.links.items()
            if physics.distance(self._npos(a), self._npos(b)) < 70 * physics.WORLD_SCALE
        ]

    def _snap_stats(self, alive) -> dict:
        """全局统计 (HUD 数字与 history 曲线)"""
        avg_snr = (sum(n.snr_db for n in alive) / len(alive)) if alive else 0
        avg_soc = (sum(n.battery_soc for n in alive) / len(alive)) if alive else 0
        return {
            "alive": len(alive), "total": len(self.nodes),
            "reachable": sum(1 for r in self.routes.values() if r.get("hop_count", -1) >= 0),
            "coverage_pct": self._coverage(),
            "avg_snr_db": round(avg_snr, 1), "avg_soc_pct": round(avg_soc, 1),
            "max_hop": self.wave.get("max_hop", 0),
            "total_flow": len(self.traffic),
            "obstacles": len(self.obstacles),
            "blocked_pairs": len(self.blocked_pairs),
            "mean_degree": round(2 * len(self.links) / max(1, len(self.nodes)), 2),
        }
