# -*- coding: utf-8 -*-
"""
通信桩实体 (Node): 一根桩的全部物理参数 + 每 tick 演化
=========================================================
分层: 纯数据+自演化层 —— 自身不做任何网络决策; 链路质量(snr_db/ber)与
路由结果(hop_count/neighbors)由引擎算完回填, 本类只负责"参数随时间怎么变"。
"""
import logging                                     # 标准库: 模块日志 (死亡/SEU/功率自举)
from dataclasses import dataclass, field, asdict   # 标准库: 数据类骨架/字段工厂/序列化
import random                                      # 标准库: 温度扰动与 SEU 掷骰

from .config import (BOOST_EVERY_TICKS, BOOST_MIN_SOC_PCT,   # 度数自保: 调功节拍/电量红线%
                     BOOST_STEP_DB, DEG_HYSTERESIS_TICKS,   # 功率步长/充足滞回拍数
                     MIN_DEGREE, TX_POWER_MAX_DB)           # 链路警戒线/功率上限

log = logging.getLogger(__name__)   # 本模块日志器


@dataclass
class Node:
    """通信桩: 一根桩的完整物理画像。

    职责: 承载能源/射频/环境/网络四组可变状态, 并在 step() 中完成
    "耗电 → RTG 涓流回充 → 辐射累积 → SEU 翻转 → 温度扰动"的每 tick 演化;
    不计算链路、不做路由 (那是 physics 与 engine 的分工)。

    属性: 四组参数 (逐字段含义见下方行内注释) ——
    ① 能源: battery_mah/supercap_pct/三态电流/temp_c;
    ② 射频: tx_power_dbm/rx_sensitivity_dbm/ant_gain_dbi/tilt_deg/band;
    ③ 环境: radiation_rad/seu_flips;
    ④ 网络(引擎回填): snr_db/ber/queue_pct/neighbors/hop_count/state/radio。

    调用链: engine.run_forever -> Node.step (每 tick 演化);
    physics.link_budget/link_cost 只读本类字段计价;
    engine.snapshot -> to_dict 序列化下发前端;
    WS set_param -> apply_override 上帝参数覆写。
    """
    id: str
    x: float          # 3D 坐标 (熔岩管内弧线坐标)
    y: float
    z: float

    # ============ 1. 能源参数 (Power) ============
    battery_mah: float = 12000.0        # 电池剩余容量 mAh
    battery_capacity: float = 12000.0   # 满容量
    i_tx: float = 420.0                 # 发射功耗 mA
    i_rx: float = 95.0                  # 接收功耗 mA
    i_sleep: float = 2.5                # 睡眠功耗 mA
    supercap_pct: float = 100.0         # 超级电容充电百分比
    temp_c: float = -20.0               # 实时温度 (°C), 月球熔岩管内 -60~+80

    # ============ 2. 射频与天线参数 (RF & Antenna) ============
    tx_power_dbm: float = 14.0          # 发射功率 dBm
    rx_sensitivity_dbm: float = -102.0  # 接收灵敏度 dBm (硬件老化会恶化)
    ant_gain_dbi: float = 3.0           # 天线增益 dBi
    tilt_deg: float = 0.0               # 物理倾角 (地基沉降导致天线偏转)
    band: str = "UWB"                   # UWB(高速短距) / LoRa(低速远距)
    snr_db: float = 0.0                 # 信噪比 (每帧计算)
    ber: float = 0.0                    # 误码率 (每帧计算)

    # ============ 3. 环境与机械参数 (Environmental) ============
    radiation_rad: float = 0.0          # 累积辐射剂量 (rad) -> SEU 概率
    seu_flips: int = 0                  # 单粒子翻转累计次数

    # ============ 4. 网络与缓冲参数 (Networking) ============
    queue_pct: float = 0.0              # 数据包队列积压率 %
    queue_capacity: int = 256           # 邻居表/队列容量
    neighbors: int = 0                  # 当前邻居数
    hop_count: int = 0                  # 到汇聚节点的跳数
    state: str = "ACTIVE"               # ACTIVE / DEGRADED / SEU_RESET / DEAD
    radio: str = "IDLE"                  # PAMAS 无线状态机: IDLE / TXRX / SLEEP

    # ---- 运行时缓存 ----
    role: str = "relay"                 # sink(洞口基站) / sensor / relay
    link_cost_cache: dict = field(default_factory=dict)

    # ---- 链路度数自保 (Starlink 式冗余维护的本地反馈状态) ----
    power_auto: bool = True             # 功率自动调优开关 (上帝手改功率后置 False 让位)
    _rated_tx: float = 0.0              # 额定发射功率 dBm (__post_init__ 锚定, 回落下限)
    _rated_itx: float = 0.0             # 额定发射电流 mA (__post_init__ 锚定, 换算基线)
    _boost_at_tick: int = 0             # 最近一次调功的 tick (节拍门)
    _deg_ok_since: int = 0              # 度数>=3 持续计时起点 (0 = 当前不充足)

    def __post_init__(self):
        """锚定额定射频基线 (spawn 时的功率/电流), 供自举回落数值换算。"""
        self._rated_tx = self.tx_power_dbm
        self._rated_itx = self.i_tx

    # ------------------------------------------------------------------
    @property
    def duty_tx(self) -> float:
        """发射占空比: 队列越满发射越频繁 -> 耗电越多。

        Args: None。
        Returns: float ∈ [0.1, 0.9]。Globals Used: None。
        Calls: None。公式: min(0.9, 0.1 + queue_pct/100 × 0.8)。
        """
        return min(0.9, 0.1 + self.queue_pct / 100.0 * 0.8)

    @property
    def avg_current_ma(self) -> float:
        """三态加权平均电流 (mA), 电量消耗的电流基数。

        Args: None。
        Returns: float, mA。Globals Used: None。Calls: duty_tx。
        权重: 发射按占空比 / 接收常开 0.5 / 睡眠常开 0.3。
        """
        return (self.i_tx * self.duty_tx + self.i_rx * 0.5 + self.i_sleep * 0.3)

    @property
    def battery_soc(self) -> float:
        """剩余电量百分比。

        Args: None。
        Returns: float ∈ [0, 100]。Globals Used: None。Calls: None。
        """
        return max(0.0, self.battery_mah / self.battery_capacity) * 100.0

    @property
    def thermal_derating(self) -> float:
        """极端温差下的电池放电效率衰减系数。

        锂电池在 >45°C 加速老化/自放电, 在 <-20°C 内阻骤增 (耗电被放大)。
        Returns: float ∈ [0.3, 1.0], 1.0=无衰减。Globals Used: None。Calls: None。
        """
        t = self.temp_c
        if t > 45:
            return max(0.4, 1.0 - (t - 45) * 0.012)
        if t < -20:
            return max(0.3, 1.0 - (-20 - t) * 0.015)
        return 1.0

    def effective_rx_sensitivity(self, band: str = "UWB") -> float:
        """有效接收灵敏度 (dBm) —— 本节点在该频段能解调的最低接收功率。

        = 热噪声底 + 解调门限SNR + 高温NF恶化 + 硬件老化偏置;
        温度经 kTB 噪声底与 NF 双重恶化 —— 这是"高温->SNR下降->链路熔断"
        耦合链的物理根基。
        Args: band: "UWB"/"LoRa" 频段名。
        Returns: float, dBm (越低越灵敏)。
        Globals Used: physics.BAND_PROFILE / physics.SNR_REQ_DB (局部导入)。
        Calls: physics.thermal_noise_floor_dbm。
        """
        from .physics import (BAND_PROFILE, SNR_REQ_DB,      # 频段表/解调门限
                              thermal_noise_floor_dbm)       # kTB 噪声底
        noise = thermal_noise_floor_dbm(self, BAND_PROFILE[band]["bandwidth_hz"])
        nf_penalty = max(0.0, self.temp_c - 25.0) * 0.12   # 高温 NF 恶化 dB
        aging = (-102.0) - self.rx_sensitivity_dbm          # 老化/手动恶化 dB
        return noise + SNR_REQ_DB[band] + nf_penalty + aging

    def step(self, dt_hours: float):
        """每 tick 物理演化: 耗电/RTG回充/超级电容/辐射累积/SEU/温度扰动。

        Args: dt_hours: 本 tick 折算的小时数 (引擎固定传 0.004, 即每 0.25s
              实拍折算 14.4 仿真秒 —— 电池演化加速 ~58 倍, 演示级时间尺度)。
        Returns: None (全部副作用写自身字段; 电量归零或 SEU 时改 state)。
        Globals Used: None。Calls: duty_tx/avg_current_ma/thermal_derating/battery_soc。
        死亡短路: state=DEAD 时直接返回 (不耗电不漂移 —— 死节点被冻结)。
        """
        if self.state == "DEAD":
            return
        # 电量: 电流 x 时间, 受温度衰减系数放大
        # PAMAS 节能: 休眠态(邻居正在收发、自身无数据)电流降至 15%
        sleep_factor = 0.15 if self.radio == "SLEEP" else 1.0
        drain = self.avg_current_ma * dt_hours * sleep_factor / self.thermal_derating
        # RTG 同位素温差电源持续涓流充电 (深空节点标准配置):
        # 忙节点净耗电、休眠节点净回升, SoC 长期动态平衡, 支持演示级长时运行
        rtg_charge = 240.0 * dt_hours          # 240 mAh/h
        self.battery_mah = max(0.0, min(self.battery_capacity,
                                        self.battery_mah - drain + rtg_charge))
        if self.battery_mah <= 0.01 * self.battery_capacity:
            self.state = "DEAD"
            log.warning("节点死亡 %s: 电量耗尽 (temp=%.1f°C derating=%.2f)",
                        self.id, self.temp_c, self.thermal_derating)
            return

        # 超级电容缓冲: 高发射功率时放电, 空闲时涓流充电
        if self.duty_tx > 0.5:
            self.supercap_pct = max(0.0, self.supercap_pct - 2.5)
        else:
            self.supercap_pct = min(100.0, self.supercap_pct + 0.8)

        # 辐射累积 + 单粒子翻转 (SEU): 剂量越高翻转概率越大
        self.radiation_rad += random.uniform(0.0, 0.6)
        p_seu = min(0.02, self.radiation_rad / 50000.0)
        if random.random() < p_seu:
            self.seu_flips += 1
            log.debug("SEU 翻转 %s (累计 %d, 剂量 %d rad)",
                      self.id, self.seu_flips, self.radiation_rad)
            # SEU 触发一次邻居表复位, 短暂 degraded
            self.neighbors = 0
            self.state = "SEU_RESET"

        if self.state == "SEU_RESET" and random.random() < 0.5:
            self.state = "ACTIVE"

        # 队列由传输层真实维护 (compute_network 每帧按缓冲字节数回填)
        # 温度向环境基准回归的微扰
        self.temp_c += random.uniform(-0.15, 0.15)

    def to_dict(self) -> dict:
        """序列化下发前端 (snapshot.nodes 的单节点条目)。

        Args: None。
        Returns: dict —— asdict 全字段 (剔除 link_cost_cache 缓存) +
        四个计算量 (battery_soc/thermal_derating/effective_rx_sens/
        avg_current_ma, 供 Inspector 直读)。Globals Used: None。Calls: asdict。
        """
        d = asdict(self)
        d.pop("link_cost_cache")
        d["battery_soc"] = round(self.battery_soc, 1)
        d["thermal_derating"] = round(self.thermal_derating, 3)
        d["effective_rx_sens"] = round(self.effective_rx_sensitivity(self.band), 1)
        d["avg_current_ma"] = round(self.avg_current_ma, 1)
        return d

    # 支持前端上帝模式: 允许直接覆写任意物理参数
    MUTABLE = {
        "temp_c", "tx_power_dbm", "rx_sensitivity_dbm", "ant_gain_dbi",
        "tilt_deg", "band", "battery_mah", "queue_pct", "radiation_rad",
        "i_tx", "state", "supercap_pct",
    }

    # ---------------- 链路度数自保 (Starlink 式冗余维护的本地反馈) ----------------
    @property
    def power_boosted(self) -> bool:
        """发射功率是否处于自举态 (高于额定) —— 快照琥珀环/统计消费。"""
        return self.tx_power_dbm > self._rated_tx + 1e-9

    def tune_power_for_degree(self, nbrs: int, tick: int):
        """度数自保状态机: 活跃链路不足 -> 功率阶梯自举 (抬 SNR 救弱链);
        度数充足且持续 -> 分步回落省电。纯本地反馈零通信;
        超 300m 硬半径功率救不了 —— 那是机器人/道钉的职责边界。

        Args: nbrs: 活跃链路数 (引擎回填); tick: 当前物理拍。
        Returns: None (无动作) / ("boost", 旧功率dBm) / ("fallback", 旧功率dBm)
                 —— 变更交引擎播报事件 (Node 不持有事件总线)。
        Globals Used: MIN_DEGREE/BOOST_STEP_DB/TX_POWER_MAX_DB/BOOST_MIN_SOC_PCT/
        BOOST_EVERY_TICKS/DEG_HYSTERESIS_TICKS。Calls: _apply_i_tx。
        """
        if (not self.power_auto or self.role == "beacon"
                or self.state in ("DEAD", "SEU_RESET")):
            return None
        if nbrs >= MIN_DEGREE + 1:              # 充足 (滞回上沿 >=3): 起表计时
            if not self._deg_ok_since:
                self._deg_ok_since = tick
            if (self.power_boosted
                    and tick - self._deg_ok_since >= DEG_HYSTERESIS_TICKS
                    and tick - self._boost_at_tick >= BOOST_EVERY_TICKS):
                old = self.tx_power_dbm
                self.tx_power_dbm = max(self._rated_tx,
                                        round(self.tx_power_dbm - BOOST_STEP_DB, 1))
                self._apply_i_tx()
                self._boost_at_tick = tick
                return ("fallback", old)
            return None
        self._deg_ok_since = 0                  # 不充足: 滞回计时清零
        if (nbrs < MIN_DEGREE and self.battery_soc > BOOST_MIN_SOC_PCT
                and tick - self._boost_at_tick >= BOOST_EVERY_TICKS
                and self.tx_power_dbm < TX_POWER_MAX_DB):
            old = self.tx_power_dbm
            self.tx_power_dbm = min(TX_POWER_MAX_DB,
                                    round(self.tx_power_dbm + BOOST_STEP_DB, 1))
            self._apply_i_tx()
            self._boost_at_tick = tick
            return ("boost", old)
        return None

    def _apply_i_tx(self):
        """内部: 按当前功率对额定发射电流做射频换算 (功率比 = 10^(dB/10))。"""
        self.i_tx = round(self._rated_itx
                          * 10 ** ((self.tx_power_dbm - self._rated_tx) / 10.0), 1)

    def apply_override(self, key: str, value):
        """上帝模式参数覆写入口 (白名单制)。

        Args: key: 参数名 (必须在 MUTABLE 白名单内); value: 新值 (不校验类型)。
        Returns: None。Raises: KeyError —— key 不在白名单时。
        Globals Used: Node.MUTABLE (类级白名单)。
        """
        if key not in Node.MUTABLE:
            raise KeyError(f"parameter '{key}' is not mutable")
        setattr(self, key, value)
