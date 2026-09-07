# 🌒 月球熔岩管多智能体网络沙盘

前后端分离的实时数字孪生:60 根通信桩在一张扁椭圆熔岩管画布上自组织 mesh 组网,后端逐 tick 运行全部算法,前端纯 Canvas 2D 渲染。

> 📖 **函数级技术手册见 [backend/README.md](backend/README.md)**(物理层/路由/传输层/区块链/机器人逐模块逐函数说明),本文件只做入口导航。

## 快速开始(单端口部署)

```bash
# 后端 :5000 (同时托管前端构建产物, 一个端口搞定)
cd backend && pip install -r requirements.txt
npm --prefix ../frontend run build     # 首次或前端改动后
python main.py                         # 打开 http://127.0.0.1:5000
```

> 开发期前端热更新:`npm --prefix frontend run dev` (:5173)。
> 服务只绑定 127.0.0.1,仅供本机演示。

## 场景

- **画布**:纯深空色底,扁长椭圆腔室(约 2.2:1),15 块左右互不重叠可拖拽巨石(撒布上限 26,受互不重叠约束实际放得下 ~15);遮挡规则一句话——两节点连线被石头/墙体挡住 → 图中无边,必须中继绕行。
- **节点**:60 根桩(sink=六边形/探测器=菱形/道钉=圆),悬停看 SoC/SNR/温度/路由表/被遮挡邻居,左键 Inspector 调上帝参数。
- **报文**:右键节点"发送消息到…"发起真实报文,store-and-forward 逐跳可见,BER 掷骰重传、断链绕行、超时作废全程可观察。
- **巡检机器人**:金色菱形随机巡逻;被隔断节点发 SOS(红脉冲) → 机器人赶到投放道钉(永久中继)接回主网;链上情报主动侦查失联节点。
- **区块链**:⛓ 账本侧边栏看全网世界状态同步;⛓ 链流量开关看遥测/出块/追块报文的泛洪传播;画墙分区可见分叉,拆墙自动愈合。

## 交互速查

| 操作 | 效果 |
|---|---|
| 拖拽巨石 / 画墙(放墙模式) | LOS 重算,链路消失,路由绕行,解说播报自愈过程 |
| 空白拖拽 / Shift+拖动 / 滚轮 | 平移 / 缩放(画墙模式外) |
| 右键节点 | 破坏 / 过热测试 / 恢复 / 发送消息到… |
| HUD 灾害按钮 | 塌方 / 热浪 / 耀斑 / 摧毁主干道 |
| HUD ⛓ 两按钮 | 账本侧边栏 / 链流量可视化 |
| HUD ↺ 重置 | 两段式确认,同种子原地重建世界 |

## 代码地图

```
backend/main.py            FastAPI+WS 入口(单端口, 接入层)
backend/sim/engine.py      仿真引擎(地质/LOS/链路/模式机/快照/主循环)
backend/sim/physics.py     物理层(路径损耗/SNR/BER/六项路由代价)
backend/sim/routing.py     Dijkstra 波前 + RCSPA 资源约束选路
backend/sim/transport.py   传输层(连接接纳/整包逐跳/重传/绕行)
backend/sim/blockchain.py  账本(统一排他调度PoA/泛洪/追块/分叉愈合)
backend/sim/robot.py       巡检机器人(SOS听测/道钉/链上情报)
backend/sim/node.py        节点数据类(PAMAS/RTG/SEU 演化)
frontend/src/radar/        Canvas 2D 渲染引擎
frontend/src/components/   HUD/Inspector/日志/账本/引导等 React 组件
```
