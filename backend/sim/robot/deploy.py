# -*- coding: utf-8 -*-
"""
道钉投放与快照导出 (DeployMixin)
===================================
职责: 巡检机器人的"工程动作" —— 落点合法性判定 (管内/不压石/留间隔)、
道钉投放 (永久中继节点 + 链上哑节点注册)、机器人快照导出。
"""
import logging   # 标准库: 模块日志 (道钉投放)
import math   # 标准库: 距离判定 (压石/间隔)

from .constants import DEPLOY_GAP   # 距既有道钉的最小投放间隔

log = logging.getLogger(__name__)   # 本模块日志器


class DeployMixin:
    """职责: PatrolRobot 的工程动作混入。

    属性要求 (由 PatrolRobot.__init__ 提供): self.eng (引擎引用),
    self.node (机器人伪节点), self.stock (道钉库存), self._deployed/
    _deployed_at (投放计数与最近落钉 tick), self.state/target/sos_active/
    trail (快照数据源)。

    调用链: robot._advance/_rescue_step -> _deploy_ok -> _deploy_beacon
    (节点入网 + 链上注册); engine.snapshot -> export。
    """

    # ---- 道钉投放 ----
    def _deploy_ok(self) -> bool:
        """落点合法性: 管内 / 不压巨石 / 距既有道钉留间隔"""
        eng = self.eng
        if not eng._in_tube((self.node.x, 0.0, self.node.z)):
            return False
        for o in eng.obstacles:            # 不砸在石头上
            if math.hypot(self.node.x - o["x"], self.node.z - o["z"]) < o["r"] + 30:
                return False
        for n in eng.nodes.values():       # 距既有道钉留间隔
            if (n.role == "beacon"
                    and math.hypot(self.node.x - n.x, self.node.z - n.z) < DEPLOY_GAP):
                return False
        return True

    def _deploy_beacon(self):
        """投放道钉: 永久中继入网 (普通节点 + 链上哑节点)"""
        eng = self.eng
        self._deployed += 1
        self._deployed_at = eng.tick
        bid = f"BEACON-{self._deployed:02d}"
        from ..node import Node   # 局部导入: 避免包初始化期环 (node 无环, 习惯保持)
        b = Node(id=bid, x=round(self.node.x, 1), y=0.0, z=round(self.node.z, 1),
                 role="beacon", temp_c=-35.0, tilt_deg=0.0,
                 ant_gain_dbi=5.0, radiation_rad=0.0)
        eng.nodes[bid] = b
        eng.chain_net.register_node(bid)   # 全同步哑节点: 转发链包, 不出块不遥测
        self.stock -= 1
        log.info("投放道钉 %s @(%s,%s) 库存余 %d",
                 bid, b.x, b.z, self.stock)
        eng._emit("beacon_deploy", "ok",
                  f"📍 投放道钉 {bid} (库存余 {self.stock} 枚), 永久中继入网",
                  narration=f"📍 巡检机器人在此投放了一根备用通信桩作中转!"
                            f"道钉将常驻此地,失联区域的数据将从它身上重新接回主网。",
                  node=bid)

    # ---- 快照 ----
    def export(self) -> dict:
        """机器人快照 (引擎 snapshot.robot 字段)。

        Args: None。
        Returns: dict —— x/y/z(位置)/state(四态)/target(当前目标 id)/
        stock(道钉余量)/sos(呼救节点列表)/trail(隔点抽样的面包屑)。
        Globals Used: None。Calls: None (纯读取)。
        """
        return {"x": round(self.node.x, 1), "y": 0.0, "z": round(self.node.z, 1),
                "state": self.state,
                "target": self.target[0] if self.target else None,
                "stock": self.stock, "sos": sorted(self.sos_active),
                "trail": self.trail[::2]}
