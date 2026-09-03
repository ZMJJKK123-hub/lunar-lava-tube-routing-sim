# -*- coding: utf-8 -*-
"""
物理层计算: 路径损耗 / 热噪声 / SNR / BER / 链路熔断判定
"""
import math

# 频段参数表: UWB 高速短距, LoRa 低速远距 (深空工程常用双模)
BAND_PROFILE = {
    "UWB":  {"freq_ghz": 3.5, "data_rate": 6.8e6,  "bandwidth_hz": 500e6,
             "modulation": "BPSK", "ebn0_ref_db": 9.6,  "max_range": 30.0},
    "LoRa": {"freq_ghz": 0.433, "data_rate": 5.0e3, "bandwidth_hz": 125e3,
             "modulation": "CSS", "ebn0_ref_db": -20.0, "max_range": 150.0},
}

# 月球熔岩管内: 无大气, 视距 + 洞壁散射, 路径损耗指数取 2.6
PATH_LOSS_EXPONENT = 2.6
REFERENCE_DIST_M = 1.0
K_BOLTZ = 1.38e-23
T0_KELVIN = 290.0

# 解调门限 SNR (工程值): UWB-BPSK 高速需 8dB; LoRa-CSS 扩频容错 -15dB
SNR_REQ_DB = {"UWB": 8.0, "LoRa": -15.0}


WORLD_SCALE = 10.0


def distance(a, b) -> float:
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def sim_distance(a, b) -> float:
    """归一化仿真距离: 供物理公式使用, 与世界尺度解耦"""
    return distance(a, b) / WORLD_SCALE


def free_space_path_loss_db(d_m: float, freq_ghz: float) -> float:
    """通用路径损耗模型: PL = FSPL(d0) + 10*gamma*log10(d/d0)   (文献式13)
    月球熔岩管内无大气、以视距为主; 洞壁散射/多径已并入 PATH_LOSS_EXPONENT(=2.6),
    故不再单独叠加额外散射项, 避免双重计损。"""
    if d_m < REFERENCE_DIST_M:
        d_m = REFERENCE_DIST_M
    lam = 3e8 / (freq_ghz * 1e9)
    d0 = REFERENCE_DIST_M
    # 常数项: d0 处的自由空间损耗, 系数固定 20 (与路径损耗指数无关)
    fspl0 = 20 * math.log10(4 * math.pi * d0 / lam)
    # 距离项: 随距离按 10*gamma 增长, 由 PATH_LOSS_EXPONENT 控制
    dist = 10 * PATH_LOSS_EXPONENT * math.log10(d_m / d0)
    return fspl0 + dist


def thermal_noise_floor_dbm(node, bandwidth_hz: float) -> float:
    """
    热噪声功率 = kTB。月球无大气吸热, 节点温度直接决定本征噪声底。
    node.temp_c 越高噪声底越高 (10log(T/290) 项)。
    """
    t_k = node.temp_c + 273.15
    noise_w = K_BOLTZ * t_k * bandwidth_hz
    return 10 * math.log10(noise_w / 1e-3) + 6.0   # +6dB 接收机噪声系数


def link_budget(tx, rx) -> dict | None:
    """
    计算 tx -> rx 单向链路。返回 SNR/BER/余量; 若链路物理不通返回 None。
    融合: 发射功率 + 双端天线增益 - 倾角失配惩罚 - 路径损耗 vs 有效灵敏度。
    """
    if tx.state == "DEAD" or rx.state == "DEAD":
        return None
    prof = BAND_PROFILE[tx.band]
    d = sim_distance(tx, rx)
    # 硬上限: 超过名义通信半径(UWB 30 sim = 300m)一律不通,
    # 与前端悬停时绘制的通信范围虚线圈严格一致
    if d > prof["max_range"]:
        return None

    # 天线倾角失配: cos 损失近似 -> dB 惩罚 (地基沉降导致指向偏离)
    tilt_penalty = 20 * math.log10(
        1.0 / max(0.05, math.cos(math.radians(tx.tilt_deg + rx.tilt_deg) / 2))
    ) if (tx.tilt_deg + rx.tilt_deg) > 3 else 0.0

    pl = free_space_path_loss_db(d, prof["freq_ghz"])
    prx_dbm = (tx.tx_power_dbm + tx.ant_gain_dbi + rx.ant_gain_dbi
               - pl - tilt_penalty)

    noise_dbm = thermal_noise_floor_dbm(rx, prof["bandwidth_hz"])
    snr_db = prx_dbm - noise_dbm

    # BER: 由 (Eb/N0) 决定, BPSK 用 Q 函数近似; CSS(LoRa) 容错极强
    snr_lin = 10 ** (snr_db / 10.0)
    proc_gain = prof["data_rate"] and (prof["bandwidth_hz"] / prof["data_rate"])
    ebn0 = snr_lin * min(proc_gain, 1e6) / 10
    if prof["modulation"] == "BPSK":
        ber = 0.5 * math.erfc(math.sqrt(max(ebn0, 0.0)))
    else:  # LoRa CSS: 指数衰减容错曲线
        ber = 0.5 * math.exp(-max(ebn0, 0.0) / 4)
    ber = min(1.0, max(ber, 1e-12))

    margin = prx_dbm - rx.effective_rx_sensitivity(rx.band)  # 链路余量 dB
    # 熔断判定放宽到实用阈值: 有余量且 BER 可接受
    up = margin > 0 and ber < 1e-3
    return {
        "distance": round(d, 1),
        "prx_dbm": round(prx_dbm, 1),
        "snr_db": round(snr_db, 1),
        "ber": ber,
        "margin_db": round(margin, 1),
        "band": tx.band,
        "up": up,   # 链路熔断判定
    }


def link_cost(tx, rx, link: dict, load: float = 0.0) -> float:
    """
    多变量融合路由代价 —— 算法核心。
    Cost = 能量项 + 链路质量项 + 拥塞项 + 可靠性项 + 信息素负载项
    load: 该链路的指数平滑历史承载量 (ACO 信息素), 让多智能体自动分流。
    """
    # 能量项: 剩余电量越少代价越高 (均衡能耗, 延长网络寿命)
    energy = 2.0 * (1.0 - min(tx.battery_soc, 100) / 100.0)
    if tx.thermal_derating < 0.8:          # 极端温度下惩罚
        energy += (0.8 - tx.thermal_derating) * 5.0

    # 链路质量项: 低 SNR / 高 BER 惩罚
    quality = 0.0
    if link["snr_db"] < 15:
        quality += (15 - link["snr_db"]) * 0.25
    if link["ber"] > 1e-9:
        quality += min(3.0, link["ber"] * 3000.0)  # ber=1e-3(熔断边缘)时惩罚≈3
    quality += 1.0  # 基础跳代价

    # 拥塞项: 队列积压 -> 时延增大
    congestion = tx.queue_pct / 100.0 * 2.5 + rx.queue_pct / 100.0 * 1.5

    # 可靠性项: 辐射剂量 + SEU 历史 + 状态降级惩罚
    reliability = tx.seu_flips * 0.05 + (2.0 if tx.state != "ACTIVE" else 0.0)
    reliability += tx.radiation_rad / 20000.0

    # 低速模式惩罚 (LoRa 降速换距离, 但吞吐低)
    speed_penalty = 1.5 if tx.band == "LoRa" else 0.0

    # 信息素负载项 (ACO): 历史承载越多代价越高 -> 流量自动向空闲链路分流
    pheromone = min(4.0, load * 0.8)

    return round(energy + quality + congestion + reliability + speed_penalty + pheromone, 3)
