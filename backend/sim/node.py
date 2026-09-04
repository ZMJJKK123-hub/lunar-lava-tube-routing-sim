# -*- coding: utf-8 -*-
"""
通信桩 (Node) 真实物理参数类
每一帧仿真中, 引擎会基于这些参数计算链路质量与路由代价。
"""
from dataclasses import dataclass, field, asdict
import random


@dataclass
class Node:
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

    # ------------------------------------------------------------------
    @property
    def duty_tx(self) -> float:
        """发射占空比: 队列越满, 发射越频繁 -> 耗电越多"""
        return min(0.9, 0.1 + self.queue_pct / 100.0 * 0.8)

    @property
    def avg_current_ma(self) -> float:
        """加权平均电流, 用于电量消耗"""
        return (self.i_tx * self.duty_tx + self.i_rx * 0.5 + self.i_sleep * 0.3)

    @property
    def battery_soc(self) -> float:
        return max(0.0, self.battery_mah / self.battery_capacity) * 100.0

    @property
    def thermal_derating(self) -> float:
        """
        极端温差下的电池放电效率衰减系数。
        锂电池在 >45°C 加速老化/自放电, 在 <-20°C 内阻骤增。
        返回 0~1 的有效放电效率。
        """
        t = self.temp_c
        if t > 45:
            return max(0.4, 1.0 - (t - 45) * 0.012)
        if t < -20:
            return max(0.3, 1.0 - (-20 - t) * 0.015)
        return 1.0

    def effective_rx_sensitivity(self, band: str = "UWB") -> float:
        """
        有效接收灵敏度 = 热噪声底 + 解调门限SNR + 高温NF恶化 + 硬件老化。
        温度通过 kTB 噪声底与器件噪声系数(NF)双重恶化灵敏度 —— 这是
        "高温 -> SNR下降 -> 链路熔断" 耦合链的物理根基。
        rx_sensitivity_dbm 滑块作为硬件老化偏置 (默认 -102 时为 0 dB)。
        """
        from .physics import (BAND_PROFILE, SNR_REQ_DB,
                              thermal_noise_floor_dbm)
        noise = thermal_noise_floor_dbm(self, BAND_PROFILE[band]["bandwidth_hz"])
        nf_penalty = max(0.0, self.temp_c - 25.0) * 0.12   # 高温 NF 恶化 dB
        aging = (-102.0) - self.rx_sensitivity_dbm          # 老化/手动恶化 dB
        return noise + SNR_REQ_DB[band] + nf_penalty + aging

    def step(self, dt_hours: float):
        """每 Tick 的物理演化: 耗电 / 辐射累积 / SEU / 温度缓变"""
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
            # SEU 触发一次邻居表复位, 短暂 degraded
            self.neighbors = 0
            self.state = "SEU_RESET"

        if self.state == "SEU_RESET" and random.random() < 0.5:
            self.state = "ACTIVE"

        # 队列由传输层真实维护 (compute_network 每帧按缓冲字节数回填)
        # 温度向环境基准回归的微扰
        self.temp_c += random.uniform(-0.15, 0.15)

    def to_dict(self) -> dict:
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

    def apply_override(self, key: str, value):
        if key not in Node.MUTABLE:
            raise KeyError(f"parameter '{key}' is not mutable")
        setattr(self, key, value)
