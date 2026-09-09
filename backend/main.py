# -*- coding: utf-8 -*-
"""FastAPI 入口: WebSocket 实时通道 + 前端静态页面托管 (单端口 5000)。
接入层职责: 协议转换/参数校验/路由分发 —— 不含任何仿真业务逻辑。"""
import asyncio       # 标准库: 广播任务调度 (发后即忘 + 限时踢出)
import logging        # 标准库: 调试日志 (sim.log 滚动文件, 见 _setup_logging)
from logging.handlers import RotatingFileHandler   # 标准库: sim.log 滚动文件处理器
import contextlib    # 标准库: KeyboardInterrupt 静默收尾
import json          # 标准库: WS 消息 JSON 编解码
from pathlib import Path   # 标准库: 前端 dist 目录定位

from fastapi import FastAPI, WebSocket, WebSocketDisconnect   # Web 框架: 应用/WS 端点
from fastapi.middleware.cors import CORSMiddleware           # 跨域中间件 (开发期 :5173)
from fastapi.responses import FileResponse                   # index.html 文件响应
from fastapi.staticfiles import StaticFiles                  # /assets 静态托管

from sim.config import (HOST, PORT, WS_BACKEND,   # 配置层: 部署
                          LOG_FILE, LOG_LEVEL)          # 配置层: 日志文件/级别
from sim.engine import ENGINE                    # 仿真引擎单例 (业务全部委托给它)

# 前端构建产物 (npm run build 后的 dist)
DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _setup_logging() -> None:
    """仿真日志引导: "sim" 树 -> sim.log 滚动文件 (与 uvicorn 访问日志分离)。"""
    root = logging.getLogger("sim")
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=2,
                             encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%m-%d %H:%M:%S"))
    root.addHandler(fh)


_setup_logging()
log = logging.getLogger("sim.main")   # 接入层日志器 (随 sim 树写入 sim.log)

app = FastAPI(title="月面熔岩管多智能体网络仿真引擎")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

CLIENTS: set[WebSocket] = set()
INFLIGHT: dict[WebSocket, asyncio.Task] = {}   # 客户端 -> 正在进行的发送任务


async def _send_to(ws: WebSocket, data: str):
    """单个客户端的限时发送; 失败/超时由 done 回调踢出。

    Args: ws: 目标连接; data: JSON 文本帧。
    Returns: None。Raises: asyncio.TimeoutError (10s, 由回调捕获踢出)。
    Globals Used: None (超时常量内联于 wait_for)。
    """
    await asyncio.wait_for(ws.send_text(data), timeout=10.0)


async def broadcast(message: dict):
    """
    发后即忘广播: 引擎循环绝不同步 await 任何客户端发送。
    uvicorn 的 ws.send 在对端停止读取时会无限期挂起 (transport 缓冲满 ->
    writable 事件被清除), 若在引擎循环内直接 await 会冻结整个仿真。
    这里把每条发送丢进独立任务; 上一帧还没发完(卡住)的客户端直接踢出。

    Args: message: 快照 dict (序列化为 JSON 文本)。Returns: None。
    Globals Used: CLIENTS (连接表) / INFLIGHT (在途发送任务表)。
    Calls: asyncio.create_task / _send_to。
    """
    data = json.dumps(message, ensure_ascii=False)
    for ws in list(CLIENTS):
        if ws in INFLIGHT:          # 上一帧仍卡着 -> 判定僵死连接, 踢出
            log.warning("踢出僵死连接: %s (上一帧仍未发完)",
                        ws.client.host if ws.client else "?")
            CLIENTS.discard(ws)
            INFLIGHT.pop(ws, None)
            continue
        task = asyncio.create_task(_send_to(ws, data))

        def _done(t, ws=ws):
            INFLIGHT.pop(ws, None)
            if t.cancelled() or t.exception() is not None:
                log.warning("发送失败踢出: %s (%s)",
                            ws.client.host if ws.client else "?",
                            t.exception() or "cancelled")
                CLIENTS.discard(ws)

        INFLIGHT[ws] = task
        task.add_done_callback(_done)


@app.get("/health")
async def health():
    """仿真健康度: tick 应随时间持续增长。

    Args: None。Returns: dict {tick, clients, mode, nodes}。
    Globals Used: ENGINE (引擎单例)。
    """
    return {"tick": ENGINE.tick, "clients": len(CLIENTS),
            "mode": ENGINE.mode, "nodes": len(ENGINE.nodes)}


# ---------- 前端静态托管: 单端口部署, 页面与接口同源 ----------
if DIST_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/")
    async def serve_index():
        """SPA 入口 (no-cache: 防止发新版后浏览器驻留旧 JS)。"""
        return FileResponse(DIST_DIR / "index.html",
                            headers={"Cache-Control": "no-cache"})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """WS 双向通道: 建连即下发地质帧+首帧快照; 循环收发上帝指令。

    支持指令: set_param / disaster / send_msg / add_wall / move_obstacle /
    remove_wall / clear_walls / reset —— 全部薄转发给引擎 API 层。
    Args: ws: 客户端连接。Returns: None (连接生命周期)。
    Globals Used: ENGINE / CLIENTS。Calls: broadcast / ENGINE.* (API 层)。
    """
    await ws.accept()
    CLIENTS.add(ws)
    try:
        # 地质数据一次性下发 (隧道曲线/腔室/巨柱/巨石)
        await ws.send_text(json.dumps(
            {"cmd": "geology", "geology": ENGINE.export_geology()}, ensure_ascii=False))
        await ws.send_text(json.dumps(ENGINE.snapshot(), ensure_ascii=False))
        while True:
            msg = json.loads(await ws.receive_text())
            cmd = msg.get("cmd")
            log.info("WS <- %s", str(msg)[:120])
            if cmd == "set_param":
                # 上帝模式: {"cmd":"set_param","node":"NODE-05","params":{"temp_c":80}}
                # 只改参数+回 ack —— 不做即时 compute/broadcast: 滑块拖动可达
                # 60+ msg/s, 每条全量重算(~10ms)+全量广播(127KB)会打满事件循环
                # 把引擎拖到近停; 引擎每 0.25s 重算/每 0.2s 广播, 下个周期自然生效
                resp = ENGINE.apply_override(msg["node"], msg.get("params", {}))
                await ws.send_text(json.dumps({"cmd": "ack", "req_id": msg.get("req_id"), **resp}))
            elif cmd == "disaster":
                ENGINE.inject_disaster(msg.get("kind"))
                await broadcast(ENGINE.snapshot())
            elif cmd == "send_msg":
                # 任意两节点间发送真实报文:
                # {"cmd":"send_msg","src":"NODE-38","dst":"NODE-07","bytes":2048}
                resp = ENGINE.send_user_message(msg.get("src"), msg.get("dst"),
                                                msg.get("bytes", 1024))
                await broadcast(ENGINE.snapshot())
                await ws.send_text(json.dumps(
                    {"cmd": "ack", "req_id": msg.get("req_id"), **resp}))
            elif cmd == "toggle_pause":
                # 暂停/恢复仿真: {"cmd":"toggle_pause"} —— 物理拍冻结在当前帧
                resp = ENGINE.toggle_pause()
                await broadcast(ENGINE.snapshot())
                await ws.send_text(json.dumps(
                    {"cmd": "ack", "req_id": msg.get("req_id"), **resp}))
            elif cmd == "toggle_rl":
                # B组实验: 信道决策器 RCSPA <-> Q-learning
                resp = ENGINE.toggle_rl()
                await broadcast(ENGINE.snapshot())
                await ws.send_text(json.dumps(
                    {"cmd": "ack", "req_id": msg.get("req_id"), **resp}))
            elif cmd == "toggle_rl_deploy":
                # RL试点②: 道钉时机决策器 规则恒投 <-> Q-learning 投/忍
                resp = ENGINE.toggle_rl_deploy()
                await broadcast(ENGINE.snapshot())
                await ws.send_text(json.dumps(
                    {"cmd": "ack", "req_id": msg.get("req_id"), **resp}))
            elif cmd == "toggle_random_ch":
                # C组实验: 均匀随机信道 (阴性对照)
                resp = ENGINE.toggle_random_ch()
                await broadcast(ENGINE.snapshot())
                await ws.send_text(json.dumps(
                    {"cmd": "ack", "req_id": msg.get("req_id"), **resp}))
            elif cmd == "add_wall":
                # 2D 俯视图画墙: {"cmd":"add_wall","x1":..,"z1":..,"x2":..,"z2":..}
                ENGINE.add_wall(msg["x1"], msg["z1"], msg["x2"], msg["z2"])
                await broadcast(ENGINE.snapshot())
            elif cmd == "move_obstacle":
                resp = ENGINE.move_obstacle(msg["index"], msg["x"], msg["z"])
                await broadcast(ENGINE.snapshot())
                await ws.send_text(json.dumps({"cmd": "ack", **resp}))
            elif cmd == "remove_wall":
                ENGINE.remove_wall(msg["index"])
                await broadcast(ENGINE.snapshot())
            elif cmd == "clear_walls":
                ENGINE.clear_walls()
                await broadcast(ENGINE.snapshot())
            elif cmd == "reset":
                ENGINE.reset()
                await broadcast(ENGINE.snapshot())
    except WebSocketDisconnect:
        CLIENTS.discard(ws)
    except Exception as e:
        await ws.send_text(json.dumps({"cmd": "error", "detail": str(e)}))
        CLIENTS.discard(ws)


@app.on_event("startup")
async def startup():
    """应用启动: 初始算一次网络 + 拉起引擎常驻任务。

    Args: None。Returns: None。Globals Used: ENGINE / app.state。
    Calls: ENGINE.compute_network / asyncio.create_task (持有强引用防 GC)。
    """
    ENGINE.compute_network()
    # 关键: 持有 task 强引用, 防止被 GC 静默回收导致引擎停摆
    app.state.engine_task = asyncio.create_task(ENGINE.run_forever(broadcast))


if __name__ == "__main__":
    import uvicorn   # ASGI 服务器: 承载 FastAPI 应用与 WebSocket 长连接
    with contextlib.suppress(KeyboardInterrupt):
        # ws="wsproto": 规避 websockets 17.x legacy 协议在客户端断开时的
        # AssertionError (该异常曾逃逸并杀死整个进程)
        uvicorn.run(app, host=HOST, port=PORT, ws=WS_BACKEND)
