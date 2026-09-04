# 后端完全手册(backend/)

> 月球熔岩管多智能体网络沙盘 —— 仿真引擎
> 一句话:维护 60 根通信桩 + 26 块巨石的地下网络世界,每 0.25 秒重算一次
> "谁能连谁、数据怎么走、哪条链路熔断",通过 WebSocket 以 5Hz 推送给前端。
>
> 本手册按"**每个文件 → 每个类 → 每个函数**"组织:每个函数只讲**它干什么、
> 输入什么、返回什么**,不需要读源码。最后两章统一讲**函数之间怎么组装**、
> **路由代价的权重公式怎么算**。函数名可直接在编辑器里搜索定位。

---

## 目录

- [1. 文件与类总览](#1-文件与类总览)
- [2. 函数级 API 手册](#2-函数级-api-手册)
  - [2.1 sim/node.py —— Node 类(通信桩)](#21-simnodepy--node-类通信桩)
  - [2.2 sim/physics.py —— 物理层(6 个函数)](#22-simphysicspy--物理层6-个函数)
  - [2.3 sim/routing.py —— 路由算法(4 个函数)](#23-simroutingpy--路由算法4-个函数)
  - [2.4 sim/engine.py —— SimulationEngine 类(仿真引擎)](#24-simenginepy--simulationengine-类仿真引擎)
  - [2.5 sim/transport.py —— 传输层(真实报文)](#25-simtransportpy--传输层真实报文)
  - [2.6 sim/blockchain.py —— 区块链全网状态同步](#26-simblockchainpy--区块链全网状态同步)
  - [2.7 main.py —— FastAPI 入口](#27-mainpy--fastapi-入口)
- [3. 对外接口(HTTP + WebSocket)](#3-对外接口http--websocket)
- [4. 函数之间怎么组装(调用链)](#4-函数之间怎么组装调用链)
- [5. 权重怎么算(link_cost 六项公式详解)](#5-权重怎么算link_cost-六项公式详解)

---

## 1. 文件与类总览

| 文件 | 类/模块 | 一句话职责 |
|---|---|---|
| `sim/node.py` | `class Node` | 一根通信桩的全部物理参数 + 每 tick 的演化(耗电/辐射/温度) |
| `sim/physics.py` | 模块(6 个函数) | 纯计算:距离/路径损耗/噪声/SNR/BER/链路熔断/路由代价 |
| `sim/routing.py` | 模块(4 个函数) | 纯算法:Dijkstra 全网路由 + 波前记录 + RCSPA 信道分配 |
| `sim/transport.py` | `class TransportLayer` | 真实报文传输:握手/重传/超时信号/逐跳字节计数 |
| `sim/blockchain.py` | `class BlockchainNetwork` | 区块链全网状态同步:轮询PoA/泛洪/追块/分叉愈合 |
| `sim/engine.py` | `class SimulationEngine` | 总指挥:世界生成、LOS 遮挡、每 tick 流水线、灾害、快照输出 |
| `main.py` | `app = FastAPI()` | 网络入口:WebSocket 广播、HTTP 健康检查、托管前端页面 |
| `README.md` | — | 本手册 |

依赖方向(单向,无循环):`main → engine → routing/physics → node`。
分层原则:**node 存状态,physics 只算数(纯函数),routing 只跑图,engine 负责编排**。

---

## 2. 函数级 API 手册

### 2.1 sim/node.py —— Node 类(通信桩)

一个 `Node` 实例 = 一根通信桩。字段分四组:**能源**(battery_mah=12000mAh、
i_tx=420mA、i_rx=95mA、i_sleep=2.5mA、supercap_pct、temp_c)、
**射频**(tx_power_dbm=14、rx_sensitivity_dbm=-102、ant_gain_dbi、tilt_deg、
band="UWB"、snr_db、ber)、**环境**(radiation_rad、seu_flips)、
**网络**(queue_pct、neighbors、hop_count、state、radio)。

#### 属性(property,读取时实时计算)

| 属性 | 返回 | 功能 |
|---|---|---|
| `duty_tx` | 0.1~0.9 | 发射占空比。队列越满发射越勤:`0.1 + queue_pct/100×0.8`,封顶 0.9 |
| `avg_current_ma` | mA | 加权平均电流 = `i_tx×duty_tx + i_rx×0.5 + i_sleep×0.3`,用于算耗电 |
| `battery_soc` | 0~100 | 剩余电量百分比 `battery_mah/battery_capacity×100` |
| `thermal_derating` | 0.3~1.0 | 温度放电效率。>45°C 每度衰减 1.2%(下限 0.4);<-20°C 每度衰减 1.5%(下限 0.3) |

#### 方法(4 个)

| 方法 | 输入 → 输出 | 功能 |
|---|---|---|
| `effective_rx_sensitivity(band)` | 频段名 → dBm 数值 | **有效接收灵敏度** = kTB 热噪声底 + 解调门限(UWB 8dB / LoRa -15dB)+ 高温噪声系数恶化(>25°C 每度 +0.12dB)+ 老化偏置。"温度→噪声→灵敏度→熔断"这条耦合链的根基 |
| `step(dt_hours)` | 时间步长 → 无(原地演化) | **每 tick 的物理演化**:①耗电 = 平均电流×时间×休眠系数(SLEEP 时 0.15)÷温度效率,同时 RTG 同位素电源涓流充电 240mAh/h,净结果一行写入电量;②电量≤1% → 判 DEAD;③超级电容:高负载放电、空闲充电;④辐射剂量累积 + 掷骰触发 SEU 单粒子翻转(翻转后进 SEU_RESET,50% 概率下 tick 恢复);⑤温度随机游走 ±0.15°C(队列不再在此合成演化,由传输层每帧按真实缓冲回填) |
| `to_dict()` | 无 → dict | 序列化为前端可用的 JSON(附算好的 soc/derating/灵敏度/电流,剔除缓存字段) |
| `apply_override(key, value)` | 参数名+值 → 无(非法抛 KeyError) | 上帝模式改参数,只允许 `MUTABLE` 白名单里的键(temp_c / tx_power_dbm / band / state 等 12 个) |

---

### 2.2 sim/physics.py —— 物理层(6 个函数)

模块级常量:`WORLD_SCALE=10.0`(世界坐标÷10 = 仿真米数)、`BAND_PROFILE`
(UWB:3.5GHz / 6.8Mbps / 上限 30 仿真米 = 300m;LoRa:433MHz / 5kbps / 上限 150 仿真米)、
路径损耗指数 2.6(熔岩管洞壁散射)、解调门限 `SNR_REQ_DB`。

| 函数 | 输入 → 输出 | 功能 |
|---|---|---|
| `distance(a, b)` | 两节点 → 米 | 三维欧氏距离(世界坐标)。显式勾股展开,无任何花活 |
| `sim_distance(a, b)` | 两节点 → 仿真米 | `distance / WORLD_SCALE`。物理公式统一用它,与世界显示尺度解耦 |
| `free_space_path_loss_db(d_m, freq_ghz)` | 距离+频率 → dB | 通用路径损耗模型 `PL = FSPL(d₀) + 10·γ·log10(d/d₀)`,γ=`PATH_LOSS_EXPONENT`=2.6(洞壁散射已并入指数,不再单独叠加,避免双重计损) |
| `thermal_noise_floor_dbm(node, bandwidth_hz)` | 节点+带宽 → dBm | 热噪声 = kTB,用**节点实时温度**(月球无大气,温度直接抬噪声底),再 +6dB 接收机噪声系数 |
| `link_budget(tx, rx)` | 发节点+收节点 → dict 或 **None** | **链路预算:判定这条边通不通、质量如何**。返回 `{distance, prx_dbm, snr_db, ber, margin_db, band, up}` 或 None。内部五步:①任一端 DEAD → None;②超频段硬上限 → None(与前端悬停虚线圈严格一致);③双端倾角和 >3° 时按 cos 损失罚 dB;④接收功率 = 发射功率+双端天线增益−路损−倾角罚,SNR = 接收功率−噪声底;⑤BER:BPSK 用 erfc(Q 函数近似),LoRa CSS 用指数容错曲线。**熔断判定 up = 链路余量>0 且 BER<1e-3** |
| `link_cost(tx, rx, link, load)` | 两节点+链路 dict+历史负载 → float | **多变量融合路由代价,即 Dijkstra 的边权**。六项相加:能量+质量+拥塞+可靠性+LoRa 低速罚+信息素。**公式与权重值详见第 5 章** |

---

### 2.3 sim/routing.py —— 路由算法(4 个函数)

| 函数 | 输入 → 输出 | 功能 |
|---|---|---|
| `build_graph(nodes, links)` | 节点+链路表 → 邻接表 | 把 `links` 字典压成 `{节点: [(邻居, 边权), ...]}`,只收 `up=True` 的链路;边权双向可以不等(cost_ab ≠ cost_ba,因为两端电量/队列不同) |
| `dijkstra(graph, source)` | 邻接表+源 → (dist, prev, settle_order) | 标准 Dijkstra。**额外产出 `settle_order`**:节点被"敲定最短距离"的先后顺序,即波前扩散序列,前端按它逐个点亮节点播放算法运行过程 |
| `routing_step(nodes, links, sink_id)` | 节点+链路+汇 → (routes, wave) | 全网站到 sink 的一次计算:建图 → Dijkstra(以 sink 为源)→ 回溯每个节点的路径。输出 `routes[nid] = {hop_count, next_hop, path, total_cost}`,不可达节点 hop_count=-1。`wave = {settle_order, hop_of, max_hop}` 供波前动画与跳数分层 |
| `rscspa(adj, source, sink, n_channels=3, K=3, busy_edge)` | 邻接表+起讫+参数 → {path, channels, cost} 或 None | **RCSPA,按论文 "Routing and channel assignment for low power transmission in PCS" 实现**。Dijkstra 变体:状态从"节点"升级为"(节点, 最近 K−1 跳信道元组)"。①`if r in tail: continue` —— 同一信道连续 K=3 条边内不得复用(复用距离约束);②`penalty = w×1.2` —— 该信道被附近活跃呼叫占用时施加 120% 干扰惩罚,新呼叫被现有流量"排斥"自动绕行到总功率更低的路径(论文核心效果);③终止后回溯得路径 + 逐跳信道分配。当前由巡检机器人调用,机器人处于关闭待命状态(见 2.4 的 `ROBOT_ENABLED`) |

---

### 2.4 sim/engine.py —— SimulationEngine 类(仿真引擎)

模块级几何工具(4 个,类外):

| 函数 | 功能 |
|---|---|
| `_cross(ox, oz, ax, az, bx, bz)` | 2D 叉积,供线段相交判定 |
| `_seg2d_intersect(p1, p2, w1, w2)` | 两线段是否相交(严格判定)——用户墙体切视线用 |
| `_seg_blocked_by_sphere(p1, p2, c, R)` | 线段是否穿过球体(点到线段距离 ≤ R)——巨石/巨柱遮挡判定 |
| `_bez(p0, p1, p2, t)` | 二次贝塞尔插值,隧道曲线取点用 |

类常量:`_CHAMBERS=[(650,-500,1300,600)]` 唯一大腔室(纯 2D 沙盘,扁椭圆熔岩管平面示意,长半轴 1300 / 短半轴 600,约 2.2:1)、
`_TUNNELS=[]`、`_PILLARS=[]`(旧多腔室模板已清空保留)、`UWB_RANGE=30.0`、
`SEED=42`、`HEALING_HOLD_TICKS=4`、`ROBOT_ENABLED=False`。

#### A. 世界生成(启动时跑一次)

| 方法 | 功能 |
|---|---|
| `__init__()` | 初始化全部容器(nodes/links/routes/traffic/events…),然后**最多换 8 个种子重建世界**,直到节点覆盖率 ≥96%(保证随机撒点不产生孤岛)。模块底部创建全局单例 `ENGINE = SimulationEngine()` |
| `_build_geology()` | 生成地质:腔室(圆心 ±30 随机抖动)→ 隧道(贝塞尔曲线,当前为空)→ 巨柱(当前为空)→ 依次调 `_spawn_nodes / _spawn_obstacles / _recompute_los` |
| `_tunnel_point(ti, t, off_r, theta)` | 隧道曲线上取点 + 法向偏移(2D 遗留接口) |
| `_spawn_nodes()` | **摆世界里的东西**:先放 26 块互不重叠巨石(半径 55~130,间距 ≥ r1+r2+110,让出 sink 区),再随机撒 59 根桩(NODE-01~59,间距 ≥105,避石 70);NODE-00 是 sink,固定在左上开阔处。每根桩按离圆心深度初始化温度/电量/辐射(越深越冷、辐射越高),role 按 1/3 概率给 sensor |
| `_spawn_obstacles()` | 空函数(巨石已在 `_spawn_nodes` 里放完,保留接口) |

#### B. 视距与合法性

| 方法 | 功能 |
|---|---|
| `_in_tube(p)` | 点是否在大腔室圆内(圆外 = 岩壁)。巨石拖拽落点合法性校验用 |
| `_seg_in_tube(p1, p2)` | 线段 4 个采样点全在腔室内才合法(出圆即穿岩) |
| `_recompute_los()` | **重算全部节点对的视线,产出 `blocked_pairs` 集合——被挡的节点对永不建边**。四重遮挡:①巨石(0.85r 球体与线段相交)②巨柱多球③线段穿出腔室圆(岩壁)④用户墙体(线段相交)。粗筛 480 仿真米外的节点对省算力 |
| `export_geology()` | 把腔室/隧道/巨柱/巨石/墙体打包成 dict,前端连接时一次性下发 |

#### C. 事件与核心流水线

| 方法 | 功能 |
|---|---|
| `_emit(type, severity, msg, narration, **payload)` | 发事件:滚进 120 条的事件队列;关键类型(disaster/node_dead/healing_start/converged/isolated/rejoin)的解说另存 `last_narration`,防止被滚动队列挤出 |
| `_zh(nid)` | "NODE-05" → "05号"(解说用语) |
| `compute_network(quiet=False)` | **引擎心脏,一个函数串起十步**(详见第 4 章调用链):建边→链路生死事件→Dijkstra 路由→ACO 信息素→重路由事件→节点状态/队列→PAMAS 电台判定→遮挡清单→收敛状态机。队列不再合成:积压率直接取传输层各节点缓冲的真实字节数。`quiet=True` 时跳过全部事件(仅启动重建世界时用) |
| `_coverage()` | 覆盖率 = 可达节点数 / 总节点数 ×100 |
| `snapshot()` | **打包全网快照**(约 91KB):tick/mode/wave/events[-40]/巨石/墙体/links/nodes(含 blocked_nbrs)/routes/traffic/robot/stats,前端每 0.2s 收到的就是它 |
| `run_forever(broadcaster)` | **引擎主循环**(async):每 0.25s 一个 tick(全部 Node.step + compute_network + transport.step 报文推进),每 0.2s 广播一次 snapshot;单 tick 异常只记日志不杀循环 |
| `send_user_message(src, dst, nbytes)` | 任意两节点发送真实报文(WS `send_msg` 指令入口),返回受理结果,送达/超时信号走事件流 |

#### D. 上帝模式 / 用户交互

| 方法 | 功能 |
|---|---|
| `apply_override(node_id, params)` | 上帝模式:批量改节点参数(走 Node 白名单);改出临界值(≥100°C 或电量≤3%)直接判 DEAD 并发解说;改完重算网络 |
| `add_wall(x1,z1,x2,z2)` | 用户画墙:入 walls 列表 → LOS 重算 → 报告新增切断对数 → 重算网络 |
| `remove_wall(index)` | 撤销第 index 堵墙(按加入顺序)→ LOS 重算 → 重算网络 |
| `clear_walls()` | 清空全部墙 → 同上 |
| `move_obstacle(idx, x, z)` | 拖巨石:校验索引与落点在腔室内 → 移动 → LOS 重算 → 返回 `{ok, cut:新增切断对数}` → 重算网络 |

#### E. 灾害系统(`inject_disaster(kind)` 总入口)

| kind | 执行 | 效果 |
|---|---|---|
| `kill_backbone` → `_kill_backbone()` | 统计每条路径的中间节点承载几条流,**电死承载最多的中继** | 精确打击主干道,强制全网岔路绕行 |
| `collapse` → `_collapse()` | 在**信息素负载最重的链路中点**砸一块 r=38 巨石(若 70m 内有节点则挪开 50m) | LOS 重算切断该链路,几何意义上的真实塌方 |
| `thermal_surge` | 全网温度 +35~70°C | 噪声底抬升 → SNR 下降 → 链路熔断(热学耦合链) |
| `solar_flare` | 全网辐射 +8000~20000 rad | SEU 翻转概率上升 → 节点间歇降级 |
| `random_kill` | 随机击毁一个非 sink 节点 | 考验自愈的最朴素手段 |

#### F. 巡检机器人(五件套,当前 `ROBOT_ENABLED=False` 关闭待命)

| 方法 | 功能 |
|---|---|
| `_init_robot()` | 从某条可行路径的起点出发,初始化机器人状态(u/v 边、t 进度、pos 坐标、visited 集) |
| `_robot_adj()` | 用 `up=True` 的链路建邻接表(边权取 link_cost) |
| `_busy_channels()` | 统计当前主干流量占用的信道(每条边按节点编号和模 3 分配),供 RCSPA 干扰排斥 |
| `_robot_plan()` | 每到一节点,以机器人前方节点为源调 `rscspa()` 重规划回洞口的资源约束最短路径 |
| `_robot_step()` | 沿边插值游走,抵达节点时记录+发事件+优先选未访问的邻居(探索岔路);经过的路径节点也算 PAMAS 活跃 |

---

### 2.5 sim/transport.py —— 传输层(真实报文,整包 store-and-forward)

报文生命周期:
```
① 连接接纳 (端到端, 零时间开销): rscspa 瞬间选路,
     有路 = 连接建立, 报文整包即刻出发; 无路 = NO_PATH 拒绝
② 数据传输: 报文整包不分段, 逐跳 store-and-forward, 每跳 1 tick,
   按当前 BER 对整包字节掷骰判损坏(含 14B ACK 开销), 坏则重传,
   连续 3 次作废; 中继队列空闲时 cut-through 直通续飞
     前端: 一个发光方块(按信道配色)沿边飞行 = 一条报文
③ 传完直接收场(无 FIN 挥手): DELIVERED 事件 + 结果信号
④ 失败(超时/无路/重传耗尽): 在出事节点显示红叉停留 2s 淡出
```
负载语义:队列/缓冲/逐跳字节计数全部按报文**完整字节数**计
(2KB 报文占 2048B 缓冲、queue_pct 与逐跳 tx 与分段时代完全等价),
只是不再拆段 —— 画面上一条报文一个方块。
常量:`RETRIES_MAX=3`、`QUEUE_LIMIT_BYTES=8192`、`MAX_CONCURRENT=6`、
`DEFAULT_TIMEOUT=90` tick、`AUTO_TELEMETRY=False`(自动遥测默认关,流量由用户手动发起)。

#### 对外方法

| 方法 | 输入 → 输出 | 功能 |
|---|---|---|
| `send_message(src, dst, bytes, timeout_ticks, kind)` | 起讫+字节数 → 受理结果 dict | **发送入口**:rscspa 选路+逐跳信道(在途报文占用的信道排斥新报文),先派 SYN 探路。立即返回 `{ok, msg_id, path, channels, segments}`;拒绝返回 `{ok:False, signal:...}`(NO_SUCH_NODE/SRC_DEAD/BUSY/NO_PATH) |
| `step()` | 无 → 无 | **每 tick 推进**:①自动遥测(默认关);②半双工推进——每 tick 每节点一个发送名额(控制帧优先于数据段),排队即真实拥塞;③超时检查,**必然发出 TIMEOUT 信号**(注明卡在握手哪一步/数据阶段) |
| `queue_pct(nid)` / `node_bytes(nid)` | 节点 → 积压率/字节数 | 真实队列积压率 = 缓冲中在途字节 ÷ 8192 × 100,每帧回填 node.queue_pct |
| `active_packets()` | 无 → packets 数组 | 在途 DATA 分段 → 前端动画数据 `{a, b, t, kind, bytes, chan, msg, seg}`;t 为本跳进度(tick 内墙钟插值),t=-1 表示停驻节点排队。只下发 DATA——握手控制帧在底层真实运行但不下发 |
| `active_nodes_edges()` / `active_traffic()` | 无 → 活跃集/流量表 | PAMAS 活跃集 = 真正有帧/分段要收发的节点;engine.traffic = 在途报文路径 |
| `link_summary(edge)` / `summary()` | 边/无 → 计数 | 每条边的 tx/rx/pkts/retries/drops;全网汇总与最近 12 条结果信号 |

#### 结果信号(results 双通道)

报文完结必然落一条结果进 `results`(快照透传)并发事件:
`DELIVERED`(送达,含耗时/重传/绕行)、`TIMEOUT`(超时,含滞留位置与阶段)、
`NO_PATH`(无可达路径)、`BUFFER_FULL`(中继缓冲溢出)、`MAX_RETRIES`(数据重传耗尽)、
`HANDSHAKE_FAIL`(握手帧重传耗尽)。
事件类型:`msg_sent / msg_handshake(SYN 抵达·SYN-ACK 折返) / msg_connected(三次握手完成) /
msg_delivered / msg_timeout / msg_reroute / msg_fail / msg_no_path`。
链路中断时在途帧/分段从当前位置重新 rscspa 绕行(SYN/ACK 朝目的、SYN-ACK 朝源),即传输级自愈。

---

### 2.6 sim/blockchain.py —— 区块链全网状态同步

每个节点运行一条链(`ChainNode`),各自维护**全网所有节点参数**的世界状态。
关键设计:**全部规则只依赖链内共识数据,各节点视角恒一致**(不用本地时钟):
- **数据模型**:`Transaction`(robot_id/seq/tick/payload/tx_id=SHA-256)、
  `Block`(index/prev_hash/tick/creator/txs/block_hash)、泛洪信封(4 种类型:
  TX / BLOCK / SYNC_REQ / SYNC_RESP,含 msg_id/ttl/from_node)。
- **调度(轮询 PoA)**:`leader(H) = 全体ID排序[H % N]`,纯高度轮询;
  出块条件 = 轮到自己 ∧ mempool 非空 ∧ 块龄 ≥ 3 tick,单块 ≤ 10 笔。
- **Leader 阵亡兜底**:块龄 ≥ SKIP_AFTER(12) 时由"时间窗轮值"节点出
  空块推进轮询(出块人 = sorted[(H + tick//12) % N],窗口内唯一,链内可验),
  杀死 Leader 不会让链停摆。
- **泛洪**:seen LRU(4096) + TTL=12 抑制风暴,一跳一 tick;
  请求与响应都全网转发,远端节点也能响应。
- **世界状态**:链 = 有序日志;world_state 只在上链时按序重放,
  `tx.seq > latest_seq[robot]` 才应用(防乱序/防重放);
  遥测 payload = 节点真实物理参数(坐标/SoC/温度/状态/队列/电台)。
- **追块**:收到 index > 本地高度的块,或每 20 tick 错峰心跳 SYNC_REQ,
  邻居按请求高度回批(≤12 块)逐块验证补链。
- **分叉愈合**:同高度竞争块(画墙分区两边各自出块所致)→ 请求对方
  完整链 → 从创世整链重验 → 按"更高者胜/同高尾部哈希小者胜"全序裁决,
  双方对称执行必然收敛。
- **快照导出** `chain` 字段:最高高度/活跃节点数/已对齐数/链前缀一致数
  (节点链尾落在基准链最近 3 块内 = 一致; 正常传播波不闪, 真分叉/掉队即转"同步中")/
  最新块/每节点高度与哈希/基准世界状态/分歧节点状态(上限12),供前端 ⛓ 账本侧边栏。
- 事件:`chain_block`(每6块)/`chain_heal`(分叉愈合)进入 EventLog。
- **渲染总线**:投递循环中每个真实转发的跳调 `engine.vis_packet(a,b,类型)`
  上报,随快照 `packets` 字段与传输层 DATA 混流下发;前端按类型自动配色
  (样式表可选覆盖,未登记类型按名称哈希取色)——**新报文类型零注册即上屏**。
- **字节计账**:每 tick 各节点待发的链上报文按真实体积(payload JSON 长度,
  按 msg_id 缓存)计入该节点 `queue_pct` —— 上限 4KB(=50%,控制平面配额:
  追块批量包单条可达数十 KB,足额会计会令全网常态饱和),并联动 PAMAS 电台
  (链待发节点翻 TXRX)与耗电(duty_tx 随积压上升)。

离线验证:正常运行 60/60 节点距链顶 ≤2 块;画墙割裂→拆墙后 60/60 追平
(分叉自动愈合);杀死轮值 Leader 链继续推进;单 tick 增量 43-65ms(预算 250ms)。

---

### 2.7 main.py —— FastAPI 入口

| 函数/对象 | 功能 |
|---|---|
| `CLIENTS` / `INFLIGHT` | 当前 WebSocket 客户端集合 / 每客户端正在进行的发送任务 |
| `_send_to(ws, data)` | 单客户端限时发送(10s 超时),超时/失败由回调踢出 |
| `broadcast(message)` | **发后即忘广播**:每条 snapshot 丢进独立任务发送,引擎循环绝不 await 任何客户端——防止一个僵死连接(TCP 缓冲满不再读取)冻结整个仿真;上一帧还没发完的客户端直接判定僵死踢出(前端会自动重连) |
| `ws_endpoint("/ws")` | WebSocket 入口:连接即下发 geology + 首帧 snapshot,然后循环收指令(set_param/disaster/add_wall/remove_wall/clear_walls/move_obstacle),执行后广播新状态 |
| `health("/health")` | HTTP 健康检查:返回 `{tick, clients, mode, nodes}`,tick 应随时间持续增长 |
| `serve_index("/")` + `/assets` 挂载 | 托管 `frontend/dist` 构建产物——**页面与接口同源,单端口 5000** |
| `startup()` | 启动钩子:先手动 `compute_network()` 一次,再起 `run_forever` 任务并**持有强引用**(防被 GC 静默回收导致引擎停摆) |

---

## 3. 对外接口(HTTP + WebSocket)

### HTTP(端口 5000)

| 路由 | 功能 |
|---|---|
| `GET /` | 前端页面(frontend/dist 构建产物) |
| `GET /health` | `{tick, clients, mode, nodes}`,监控引擎是否存活(tick 在涨 = 活着) |

### WebSocket `ws://127.0.0.1:5000/ws`

**服务端推送**(两种帧):

| 帧 | 内容 |
|---|---|
| `{cmd:"geology", geology}` | 连接时一次:腔室/隧道/巨柱/巨石/墙体 |
| snapshot(无 cmd) | 每 0.2s:tick / mode / wave / events[-40] / obstacles / walls / links / nodes / routes / traffic / **packets(在途报文方块)** / robot / stats |

**客户端指令**(JSON,`cmd` 字段区分):

| 指令 | 载荷 | 效果 |
|---|---|---|
| `set_param` | `{node:"NODE-05", params:{temp_c:80}, req_id}` | 上帝模式改参数,回 `ack` |
| `disaster` | `{kind:"collapse" / "kill_backbone" / "thermal_surge" / "solar_flare" / "random_kill" / null}` | 注入灾害 |
| `add_wall` | `{x1,z1,x2,z2}` | 画墙切视线 |
| `remove_wall` | `{index}` | 撤销第 index 堵墙(Ctrl+Z / 点墙) |
| `clear_walls` | — | 清空全部墙 |
| `move_obstacle` | `{index, x, z}` | 拖巨石,回 `{ack, cut:切断对数}` |
| `send_msg` | `{src:"NODE-38", dst:"NODE-07", bytes:2048}` | 任意两节点发送真实报文;回受理 ack,送达/超时信号走事件流 |

---

## 4. 函数之间怎么组装(调用链)

### 主链:每个仿真 tick(0.25s)

```
main.startup() 起任务(持强引用)
  └─ engine.run_forever()                        ← while True 主循环
       ├─ for n in nodes: n.step(0.004h)         ← ①物理演化:耗电/辐射/SEU/队列/温度
       │     (读 duty_tx / avg_current_ma / thermal_derating 三个属性;
       │      radio=="SLEEP" 时电流×0.15 —— 上游来自 compute_network 的 PAMAS 判定)
       └─ engine.compute_network()               ← ②网络重算,内部十步:
            1. physics.link_budget(a,b) 双向      ← 边通不通(物理),SNR/BER 写回节点
            2. 链路生死事件(对比 prev_links)
            3. routing.routing_step(...)          ← 全网 Dijkstra + 波前
            4. ACO 信息素平滑 link_load            ← 0.82×旧 + 0.18×新
            5. 重路由/孤岛/重新入网事件
            6. 节点状态回填 + 队列真化(积压 = 传输层缓冲里的真实字节)+ DEGRADED 判定
            7. traffic = 传输层在途报文(真实路径与字节)
            8. PAMAS 判定 → 每桩 radio(TXRX/SLEEP/IDLE,活跃集 = 真有报文的节点)
            9. blocked_info 遮挡清单(喂前端红虚线)
            10. 收敛状态机(mode: STABLE/HEALING/CONVERGED)
       └─ transport.step()                       ← ③报文逐跳推进(握手/重传/超时/自动遥测)
       └─ 每 0.2s: broadcast(engine.snapshot())  ← ④发后即忘推给前端
```

**三个跨 tick 反馈回路**(系统的自组织就来自这里):

1. **物理→网络**:n.step 改变温度/电量 → 下 tick link_budget 的噪声底/灵敏度变 → 链路可能熔断;
2. **网络→物理**:PAMAS 判出的 SLEEP → 下 tick n.step 电流×0.15 → 休眠真省电;
3. **网络→网络**:ACO 信息素 → 下 tick link_cost 的 pheromone 项 → 流量自动摊匀。

### 交互链:用户操作(全部走同一条短路)

```
前端指令 → main.ws_endpoint 收到 → engine 对应方法
   add_wall / remove_wall / move_obstacle / apply_override / inject_disaster
     └─ 改世界状态(墙 / 巨石 / 节点参数 / 节点生死)
     └─ (涉及几何时)_recompute_los() → blocked_pairs 更新
     └─ compute_network()                ← 与主链共用同一个心脏
     └─ _emit 事件 + broadcast(snapshot) ← 前端立即看到后果
```

一句话总结:**一切变化的出口都是 compute_network,一切呈现的出口都是 snapshot。**

---

## 5. 权重怎么算(link_cost 六项公式详解)

`physics.link_cost()` 就是 Dijkstra 的边权,六项直接相加:
`cost = energy + quality + congestion + reliability + speed_penalty + pheromone`。
下面逐项讲每一项是什么、由什么决定、代码怎么算。

**先明确两个量**:SNR(dB)就是这一段传输信号的信噪比;BER 就是误码率。
一跳就是路径上每经过一条链路,数据被转发一次。

### energy(能量项)

由发送节点的剩余电量决定:`energy = 2.0 × (1 − 电量百分比/100)`——电量满就是 0 分,
电空就是 2 分;温度极端、放电效率掉到 0.8 以下时,再按 `(0.8 − 效率) × 5.0` 加罚。
作用:让路径自动避开低电量的节点,别把个别桩累死。

### quality(链路质量项)

- 当这条边的信噪比小于 15 的时候,quality 加上 `(15 − SNR) × 0.25`;
- 当 BER(误码率)大于 10⁻⁹ 的时候,quality 加上 `min(BER × 3000, 3.0)`;
- 每一跳(每经过一条边)都会给 quality 加上 1.0——这是基础跳代价,
  让算法在其他条件相同时默认偏爱跳数少的路径。

### congestion(拥塞项)

这个参数非常简单,只由节点队列的积压程度给出一个参数。每个节点都有这个积压参数
`queue_pct`(0~100 的百分比),它的决定过程:

1. 每个节点有一个真实的发送缓冲,里面排着在途报文的分段(每段 256 字节),
   缓冲上限 8192 字节;
2. queue_pct = 缓冲里排队字节数 ÷ 8192 × 100。报文每跳到站就入队排队,
   发出一段就出队。节点是半双工的,每一个回合(0.25 秒,也就是代码重新计算的
   一次轮回/一个 tick)只能推进队首一个分段,所以多条流共用同一个节点时,
   队列就会真实地排队、真实地积压;
3. queue_pct 在这一轮已经决定之后,congestion = 出发点 queue_pct ÷ 100 × 2.5
   + 接收点 queue_pct ÷ 100 × 1.5,最后就得到这个参数。

### reliability(可靠性项)

由翻转次数、状态、辐射剂量三者决定:

- 翻转次数 × 0.05:这是 SEU(单粒子翻转)的惩罚,翻转次数越大,惩罚越大;
- 状态惩罚:如果 state 是 ACTIVE 就没有这个惩罚,不是 ACTIVE 就加 2.0;
- 在前两项相加的基础上,再加辐射值 ÷ 20000。

### speed_penalty(低速模式惩罚)

这个比较特殊:特殊情况下通信换了一个方式,用 LoRa 去通信的话,就有一个 1.5 的惩罚;
但一般来说我们目前用不到 LoRa,用的都是最基本的 UWB 通信,所以不会有这个惩罚。

### pheromone(信息素负载项)

历史承载越多代价越高,流量会自动向空闲链路分发。load 参数每一帧都计算一次:
这一帧的所有路径中,每有一条经过这条边就 +1(没有就不加),得到本帧的 usage。
但确定权重时并没有只采取某一时刻的 usage,而是采取了新旧混合的方式:
`load = 0.82 × 上一帧 load 的值 + 0.18 × 本帧 usage 的值`,
这才是这一次 load 参数的具体的值。最后进 link_cost 时,
取 `min(load × 0.8, 4.0)` 去当这一项的惩罚值。

(六项权重全部写在 `physics.py → link_cost()` 里,想调整直接改那一处。)
