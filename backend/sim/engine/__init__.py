# -*- coding: utf-8 -*-
"""
仿真引擎包: 纯 2D 扁椭圆熔岩管沙盘
======================================
每 tick 分层管线: node 物理演化 -> 链路预算/路由 (world+network)
-> 传输层报文推进 -> 区块链泛洪/出块; 巡检机器人两挂点接入。
地质: 单一大腔室 (~2.2:1 扁椭圆) + ~15 块互不重叠巨石 (撒布上限 26)
+ 60 根通信桩; 事件流 + 通俗解说 + ACO 信息素 + 自愈模式机
(STABLE / HEALING / CONVERGED)。

子模块: world(地质/LOS) / events(事件总线) / network(链路/路由/模式机)
/ api(上帝模式/灾害) / snapshot(快照/渲染总线); 本文件只做装配与主循环。
对外保持 `from sim.engine import ENGINE` 单入口不变。
"""
import asyncio  # 标准库: 主循环 sleep 与并发广播任务调度
import logging  # 标准库: 单 tick 异常的显式记录 (不静默吞噬)
import math    # 标准库: 干扰源游走/抬升的几何计算
import random   # 标准库: 种子化随机源 (地质生成可复现)
import time     # 标准库: monotonic 时钟 (物理拍/广播拍错峰调度)

from collections import deque   # 标准库: history 滚动曲线 (定长)

from ..config import (JAM_LIFT_MAX_DB, JAM_RADIUS, JAM_SPEED,   # 干扰源抬升/半径/速度
                      LOG_TICK_EVERY, ROBOT_ENABLED,   # 日志采样/功能开关
                      RL_CHANNEL_ENABLED,               # B组实验: 信道决策器开关
                      SEED, TICK_BROADCAST_S, TICK_PHYS_S)  # 种子/主循环节拍
from ..node import Node                     # 节点数据类 (类型注解用)
from ..transport import TransportLayer      # 传输层 (真实报文收发)
from ..blockchain import BlockchainNetwork  # 账本网络 (全网状态同步)
from ..robot import PatrolRobot             # 巡检机器人 (SOS/道钉)
from .api import ApiMixin                   # 对外指令 (上帝模式/灾害)
from .events import EventHub, zh            # 事件总线与中文口语化
from .network import NetworkMixin           # 链路/路由/模式机
from .snapshot import SnapshotMixin         # 快照/渲染总线
from .world import WorldMixin               # 地质/LOS

log = logging.getLogger(__name__)   # 本模块日志器 (tick 异常可见, 不打断仿真)


class SimulationEngine(WorldMixin, NetworkMixin, ApiMixin, SnapshotMixin):
    """职责: 仿真引擎门面: 装配各层 + 主循环 + 上帝重置。

    核心属性:
    - nodes/links/routes/traffic: 网络状态 (network 每拍重算);
    - hub: EventHub 事件总线; history: 统计曲线 (deque maxlen=300);
    - transport/chain_net/robot: 三大子系统挂点;
    - packets_vis/_vis_at: 渲染总线缓冲; mode: 自愈模式机状态。

    调用链: main.startup -> ENGINE.run_forever(broadcast)
    -> [node.step -> compute_network -> transport.step -> chain_net.step]
    x每 0.25s -> snapshot x每 0.2s -> broadcast。
    """

    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.sink_id = None
        self.history = deque(maxlen=300)
        self.tick = 0
        self.links: dict = {}
        self.prev_links: dict = {}
        self.routes: dict = {}
        self.prev_routes: dict = {}
        self.traffic: list[dict] = []
        self.link_load: dict[tuple, float] = {}
        self.hub = EventHub()
        self.wave: dict = {}
        self.mode = "STABLE"
        self._stable_ticks = 0
        self.disaster: str | None = None
        self.robot = None      # 巡检机器人 (动态移动信源)
        self.walls: list = []  # 用户在 2D 俯视图上画的墙体
        self.heal_started_tick = 0
        self._pre_collapse_routes: dict = {}
        self.paused = False    # 仿真暂停标志 (True=物理拍冻结在当前帧)
        self._paused_at = 0.0  # 暂停锚定时刻 (monotonic; 动画进度分数的冻结时钟)
        # 移动干扰源 (开关式灾害): {x, z, wx, wz} 游走坐标与路点; None=关机
        self.jammer: dict | None = None
        # B 组实验: 信道决策器开关 (False=RCSPA 规则; True=Q-learning) 与
        # 惰性创建的学习器实例 (reset 重建即弃表, 每次实验从头学)
        self.rl_channels = RL_CHANNEL_ENABLED
        self.rl_learner = None
        # 传输层: 真实报文 store-and-forward (接纳/重传/超时/字节计数)
        self.transport = TransportLayer(self)
        # 渲染总线: 收发点调 vis_packet() 即自动上屏, 新报文类型零注册
        self.packets_vis: list[dict] = []
        self._vis_at = 0.0
        # 地质生成 (失败自动换种子重试, 保证覆盖率)
        for attempt in range(8):
            self._seed = SEED + attempt * 1000
            self._rng = random.Random(self._seed)
            self._build_geology()
            self._recompute_los()
            self.compute_network(quiet=True)
            if self._coverage() >= 96.0:
                break
        # 区块链网络: 全节点世界状态同步 (须在节点生成之后挂载)
        self.chain_net = BlockchainNetwork(self)
        # 巡检机器人: SOS 听测 + 道钉投放 (独立模块, 引擎只挂两个挂点)
        self.robot = PatrolRobot(self) if ROBOT_ENABLED else None
        log.info("世界构建完成: seed=%s 节点=%d 巨石=%d 链路=%d 覆盖率=%.1f%%",
                 self._seed, len(self.nodes), len(self.obstacles),
                 len(self.links), self._coverage())

    # ---------------- 事件总线便捷入口 (全引擎统一口径) ----------------
    @property
    def last_narration(self):
        """最新关键解说 (真身在 hub, 属性保持旧引用路径可用)。

        Returns: dict {id, text} 或 None。Globals Used: None。Calls: None。
        """
        return self.hub.last_narration

    def _emit(self, type_: str, severity: str, msg: str,
              narration: str | None = None, **payload):
        """事件转发: 各层经引擎实例调用 (hub 为唯一真身)。

        Args: type_: 事件类型; severity: info/ok/warn/error; msg: 日志文本;
              narration: 通俗解说词 (可选); **payload: 结构化附加字段。
        Returns: None。Globals Used: None。Calls: hub.emit。
        """
        self.hub.emit(self.tick, type_, severity, msg, narration, **payload)

    @staticmethod
    def _zh(nid: str) -> str:
        """节点 ID -> 中文口语名 (解说词用)"""
        return zh(nid)

    def _coverage(self) -> float:
        """全网覆盖率: 可达节点占比。
        道钉是基础设施资产, 不计入覆盖率分子分母 (否则投放后永远到不了 100%)。

        Args: None。Returns: float ∈ [0, 100]。Globals Used: None。Calls: None。
        """
        real = [nid for nid, n in self.nodes.items() if n.role != "beacon"]
        reach = sum(1 for nid in real
                    if self.routes.get(nid, {}).get("hop_count", -1) >= 0)
        return round(reach / max(1, len(real)) * 100, 1)

    def reset(self):
        """上帝重置: 以同一种子原地重建整个世界 (节点/巨石/链/机器人/账本
        全部回到初始, 墙体/灾害痕迹清空)。同步执行, 主循环无需重启。

        Args: None。Returns: None。Globals Used: SEED (种子基线)。
        Calls: self.__init__ (整世界重建)。
        """
        self.__init__()

    def _anim_now(self) -> float:
        """动画时钟: 暂停时钉在暂停锚点 (进度分数冻结), 运行时即 monotonic。"""
        return self._paused_at if self.paused else time.monotonic()

    # ---------------- 移动干扰源 (开关式灾害的引擎侧) ----------------
    def jam_lift_at(self, x: float, z: float) -> float:
        """坐标 (x, z) 处的干扰噪声抬升 (dB): 距干扰源凸衰减 (平方, 近处猛
        中远处缓), 出半径为 0; 干扰源关机时恒 0。链路预算与机器人边按
        "接收端坐标"调用 —— 抬升直接压 SNR, 链路熔断/恢复全由每拍重算
        自然导出, 无恢复逻辑。"""
        j = self.jammer
        if j is None:
            return 0.0
        f = max(0.0, 1.0 - math.hypot(x - j["x"], z - j["z"]) / JAM_RADIUS)
        return JAM_LIFT_MAX_DB * f * f

    def _step_jammer(self):
        """干扰源推进: 朝随机路点匀速游走 (无线电实体, 穿墙); 到点换新。
        仅移动 —— 噪声抬升由 jam_lift_at 按最新坐标即时计算, 无缓存失真。"""
        j = self.jammer
        if j is None:
            return
        c = self.chambers[0]
        d = math.hypot(j["wx"] - j["x"], j["wz"] - j["z"])
        if d < 1.0:                       # 到点: 腔室内极坐标均匀换新路点
            ang = self._rng.uniform(0, math.pi * 2)
            rr = math.sqrt(self._rng.uniform(0.05, 0.9))
            j["wx"] = c["x"] + math.cos(ang) * rr * c["r"] * 0.95
            j["wz"] = c["z"] + math.sin(ang) * rr * c["rz"] * 0.95
            return
        step = min(JAM_SPEED, d)
        j["x"] += (j["wx"] - j["x"]) / d * step
        j["z"] += (j["wz"] - j["z"]) / d * step

    async def run_forever(self, broadcaster):
        """主循环: 物理拍 (0.25s) 与广播拍 (0.2s) 错峰推进;
        暂停时物理拍整体冻结, 广播继续重发冻结帧。
        Globals Used: ENGINE 全部仿真状态。
        Calls: node.step / compute_network / transport.step /
        chain_net.step / snapshot / broadcaster (main.broadcast)。"""
        next_phys, next_bcast = 0.0, 0.0
        while True:
            now = time.monotonic()
            if now >= next_phys and not self.paused:
                self.tick += 1
                self.packets_vis.clear()        # 渲染总线: 每 tick 重建
                self._vis_at = time.monotonic()
                try:
                    for n in self.nodes.values():
                        n.step(dt_hours=0.004)
                    self._step_jammer()   # 移动干扰源游走 (暂停时随物理拍冻结)
                    self.compute_network()
                    self.transport.step()   # 报文逐跳推进 (握手/重传/超时)
                    self.chain_net.step(self.tick)  # 区块链泛洪/出块/追块
                    if self.tick % LOG_TICK_EVERY == 0:
                        log.debug("心跳 tick=%s mode=%s links=%d cov=%.1f%% chain_h=%d",
                                  self.tick, self.mode, len(self.links),
                                  self._coverage(),
                                  max(nd.height for nd in self.chain_net.nodes.values()))
                except Exception as e:      # 单 tick 异常不杀死引擎 (显式记录)
                    log.exception("tick %s error (ignored)", self.tick)
                next_phys = now + TICK_PHYS_S
            if now >= next_bcast:
                try:
                    await broadcaster(self.snapshot())
                except Exception as e:
                    log.exception("broadcast error (ignored)")
                next_bcast = now + TICK_BROADCAST_S
            await asyncio.sleep(0.02)


# 模块级单例: main.py 与测试直接导入 (import 即构建世界)
ENGINE = SimulationEngine()
