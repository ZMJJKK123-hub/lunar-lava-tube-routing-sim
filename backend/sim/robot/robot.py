# -*- coding: utf-8 -*-
"""
巡检机器人核心: 状态机 + 道钉投放 (PatrolRobot)
====================================================
职责边界 (与引擎低耦合):
  - 本模块持有机器人全部决策逻辑: 移动状态机 / SOS 判定与信标 / 道钉投放;
  - 引擎每 tick 只调两个挂点: inject_links(links) [建边后, Dijkstra 前]
    与 tick(tick) [路由算完后]; ROBOT_ENABLED=False 时零痕迹;
  - 机器人是"全同步观察者": 持有完整链但不出块不遥测 (chain_net 注册为
    非共识节点, 不进轮值名单); 孤岛数据经它/道钉回流主网。

SOS 语义: 无线电层广播信标 —— hop<0 连续 SOS_ARM_TICKS 的节点每
SOS_BEACON_EVERY tick 发一帧 (按节点编号错峰), 300m+LOS 内可闻;
机器人只信耳朵 (覆盖内听测), 不感知全局分裂 (全局态势由账本侧边栏承担)。

状态机: PATROL(随机巡逻) -> 听到覆盖内 SOS -> RESCUE(赶往) ->
"SOS 可闻 且 自身连通主网"同时成立 -> 原地投放道钉(永久中继) -> 孤岛自愈。
"""
import logging   # 标准库: 模块日志 (状态机决策)

from ..config import ROBOT_ID               # 协议标识: 自身节点 ID
from ..node import Node                     # 节点数据类: 构造机器人伪节点/道钉
from .. import physics                      # 物理层: link_budget/link_cost/distance
from .constants import (BEACON_STOCK, INVESTIGATE_COOLDOWN,   # 道钉库存/核查冷却
                        RANGE, RESCUE_DEAF, RESCUE_PATIENCE,  # 救援节拍
                        ROBOT_LINK_PENALTY, WAYPOINT_PATIENCE)  # 边代价罚/路点耐心
from .deploy import DeployMixin             # 工程动作: 道钉投放/快照导出
from .motion import MotionMixin             # 运动学能力 (移动/视线/路点)
from .rescue import RescueMixin             # 救援状态机分支 (三态推进)
from .senses import SenseMixin              # 感知能力 (听测/情报/任务挑选)

log = logging.getLogger(__name__)   # 本模块日志器


class PatrolRobot(MotionMixin, SenseMixin, RescueMixin, DeployMixin):
    """职责: 巡检机器人: 移动资产 + 物理搭桥自愈执行者。

    核心属性:
    - eng: 仿真引擎引用 (只读拓扑/路由/账本, 写仅限投放道钉);
    - node: 机器人伪节点 (入路由图的 ROBOT 端点, 不入 engine.nodes);
    - state: PATROL / RESCUE / INVESTIGATE / FALLBACK / ASSIST 五态
      (ASSIST=弱链加固: 覆盖内听到高功率自举节点, 前去落钉补冗余);
    - target: (nid, x, z) 当前任务目标; stock: 剩余道钉数;
    - trail: 面包屑 [(x, z, 是否连通主网)]; sos_active: 呼救节点集合。

    调用链: engine.compute_network -> inject_links (链路注入)
    -> engine.compute_network -> tick -> _update_sos + _advance
    -> (_pick_task -> _rescue_step/_patrol_step) -> _deploy_beacon。
    """

    def __init__(self, engine):
        self.eng = engine
        c = engine.chambers[0]
        self.node = Node(id=ROBOT_ID, x=c["x"], y=0.0, z=c["z"], role="robot",
                         battery_mah=120000.0, battery_capacity=120000.0,
                         ant_gain_dbi=5.0)
        self.node.radio = "TXRX"
        self.state = "PATROL"
        self.waypoint = None
        self.target = None                  # (nid, x, z) RESCUE 目标
        self.stock = BEACON_STOCK
        self._deployed = 0
        self._rescue_since = 0
        self._deaf_until = 0
        self._checked_until: dict = {}   # nid -> 已核查冷却截止 tick
        self._deployed_at = -1           # 最近一次落钉 tick
        self._slide = 0      # 贴墙绕行方向记忆: 0=直行, ±1=锁定的绕行方向
        self._wp_since = 0                 # 当前巡逻路点的起始 tick
        self.trail: list = []            # 面包屑: [(x,z,conn)] 核查/救援/回撤途中逐 tick 记录
        self._iso: dict[str, int] = {}      # nid -> 连续失联 tick 数
        self.sos_active: set[str] = set()   # 正在呼救的节点
        # 全同步观察者入链: 转发/追块全真, 但不在共识名单 (不出块/不遥测)
        engine.chain_net.register_node(ROBOT_ID)

    # ======== 挂点①: 链路注入 (compute_network 建边后, Dijkstra 前) ========
    def inject_links(self, links: dict):
        """ROBOT<->覆盖内 (300m+LOS) 节点的链路注入 links。
        注入点位于链路生死事件之后 -> 机器人移动引起的边翻动不产生事件。

        Args: links: 引擎链路表 dict (本方法直接向其追加机器人边)。
        Returns: None (原地修改 links)。Globals Used: ROBOT_LINK_PENALTY/RANGE。
        Calls: _los_clear[MotionMixin] / physics.distance/link_budget/link_cost。
        边代价一律加罚: 机器人是"最后手段"中继, 唯有孤岛流量会借道。
        """
        rp = self.node
        for n in self.eng.nodes.values():
            if n.state == "DEAD":
                continue
            key = tuple(sorted((n.id, ROBOT_ID)))   # 全网统一: 排序键
            if key in links:
                continue
            if physics.distance(rp, n) > RANGE:
                continue
            if not self._los_clear((rp.x, rp.z), (n.x, n.z)):
                continue
            lab = physics.link_budget(n, rp)
            lba = physics.link_budget(rp, n)
            if lab is None or lba is None:
                continue
            # 代价罚: 机器人是"最后手段"中继 —— 正常流量绕行都更便宜,
            # 唯有无路可走的孤岛才会经由它 (也使桥接检测不会误判)
            if key[0] == n.id:
                c_ab = physics.link_cost(n, rp, lab, 0.0) + ROBOT_LINK_PENALTY
                c_ba = physics.link_cost(rp, n, lba, 0.0) + ROBOT_LINK_PENALTY
            else:
                c_ab = physics.link_cost(rp, n, lba, 0.0) + ROBOT_LINK_PENALTY
                c_ba = physics.link_cost(n, rp, lab, 0.0) + ROBOT_LINK_PENALTY
            links[key] = {**lab, "load": 0.0, "cost_ab": c_ab, "cost_ba": c_ba}

    # ======== 挂点②: 每 tick 推进 (路由算完后) ========
    def tick(self, tick: int):
        """机器人主推进: 呼救判定 -> 状态机与移动。

        Args: tick: 当前仿真 tick。Returns: None。
        Globals Used: None。Calls: _update_sos[SenseMixin] / _advance。
        """
        self._update_sos(tick)
        self._advance(tick)

    # ---- 状态机与移动 ----
    def _advance(self, tick: int):
        eng = self.eng
        # 桥接检测: 有真实节点的路径正经过机器人 (代价罚保证只有孤岛会这样)
        # -> 此地此刻已被机器人物理验证可搭桥, 落道钉固化, 机器人继续巡逻
        if (tick >= self._deaf_until and self.stock > 0 and self._deploy_ok()
                and any(ROBOT_ID in (r.get("path") or [])
                        for nid, r in eng.routes.items() if nid != ROBOT_ID)):
            log.info("桥接检测命中: 有路径正经过机器人, 落钉")
            self._deploy_beacon()
        self._pick_task(tick)
        if self.state in ("RESCUE", "INVESTIGATE", "FALLBACK", "ASSIST"):
            self._crumb()
        if self.state in ("RESCUE", "INVESTIGATE", "FALLBACK", "ASSIST") and self.target:
            self._rescue_step(tick)
        else:
            self._patrol_step(tick)

    def _pick_task(self, tick: int):
        """任务挑选: 实时 SOS -> 链上失联核查 -> 本地弱链听测 -> 链上弱链情报
        -> 无事恢复巡逻 (同级由近及远; ASSIST 任务由 _assist_step 自行收尾)"""
        eng = self.eng
        heard = (self._hear()
                 if tick >= self._deaf_until and self.state != "FALLBACK"
                 else None)
        if heard:
            # 实时 SOS 优先 (零滞时); 同一目标进行中不重复开任务
            if not self._on_mission_for(heard[0]):
                self._start_mission("RESCUE", heard[0], tick)
            self.target = (heard[0], heard[1].x, heard[1].z)
            return
        # 链上失联核查: 朝最近失联嫌疑的最后已知坐标侦查
        susp = self._chain_intel(tick) if tick >= self._deaf_until else None
        # 本地弱链听测 (300m 零滞后) / 链上弱链情报 (全局, 滞后一个遥测周期)
        frag = (self._hear_fragile()
                if tick >= self._deaf_until and self.state != "FALLBACK"
                else None)
        cfrag = self._chain_fragile(tick) if tick >= self._deaf_until else None
        if susp:
            _, nid, sx, sz = susp
            if not self._on_mission_for(nid):
                self._start_mission("INVESTIGATE", nid, tick)
            if self.state != "FALLBACK":       # 回撤/重定位中不覆盖目标点位
                self.target = (nid, sx, sz)
        elif frag:
            if not self._on_mission_for(frag[0]):
                self._start_mission("ASSIST", frag[0], tick, via="ear")
            if self.state != "FALLBACK":
                self.target = (frag[0], frag[1].x, frag[1].z)
        elif cfrag:
            _, nid, sx, sz = cfrag
            if not self._on_mission_for(nid):
                self._start_mission("ASSIST", nid, tick, via="chain")
            if self.state != "FALLBACK":
                self.target = (nid, sx, sz)
        elif self.state in ("RESCUE", "INVESTIGATE"):
            self.state = "PATROL"
            self.target = None
            log.info("无呼救与嫌疑 -> 恢复巡逻")
            eng._emit("robot_patrol", "info", "🤖 无呼救与嫌疑, 恢复巡逻")

    def _patrol_step(self, tick: int):
        """巡逻移动: 路点到达/超时即换新, 朝当前路点移动"""
        if self.waypoint is None or self._near(self.waypoint):
            self.waypoint = self._rand_waypoint()
            self._wp_since = tick
        elif tick - self._wp_since > WAYPOINT_PATIENCE:
            self.waypoint = None            # 路点超时 (死角): 下拍换新
        if self.waypoint:
            self._move_toward(self.waypoint)

    def _giveup(self, eng, tid, tick, why):
        """放弃救援: 撤离 + 耳聋期 + 目标冷却 (防无限重试)。

        Args: eng: 引擎; tid: 目标 id; tick: 当前拍; why: 原因 (入事件文本)。
        Returns: None。Globals Used: RESCUE_DEAF/INVESTIGATE_COOLDOWN。
        Calls: eng._emit。
        """
        self.state = "PATROL"
        self.target = None
        self._deaf_until = tick + RESCUE_DEAF
        log.info("放弃救援 %s (%s): 耳聋至 t%d 冷却至 t%d",
                 tid, why, self._deaf_until,
                 tick + INVESTIGATE_COOLDOWN)
        self._checked_until[tid] = tick + INVESTIGATE_COOLDOWN
        eng._emit("robot_giveup", "warn",
                  f"🤖 放弃救援 ({why}): 超出单枚道钉可桥范围, 机器人撤离",
                  narration="🤖 机器人无法同时连通两侧网络,"
                            "本次救援放弃——它将驶离该区域继续巡逻。")
