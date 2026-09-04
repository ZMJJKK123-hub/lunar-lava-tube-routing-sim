# -*- coding: utf-8 -*-
"""FastAPI 入口: WebSocket 实时通道 + 前端静态页面托管 (单端口 5000)"""
import asyncio
import contextlib
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sim.engine import ENGINE

# 前端构建产物 (npm run build 后的 dist)
DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

app = FastAPI(title="月面熔岩管多智能体网络仿真引擎")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

CLIENTS: set[WebSocket] = set()
INFLIGHT: dict[WebSocket, asyncio.Task] = {}   # 客户端 -> 正在进行的发送任务


async def _send_to(ws: WebSocket, data: str):
    """单个客户端的限时发送; 失败/超时由 done 回调踢出"""
    await asyncio.wait_for(ws.send_text(data), timeout=10.0)


async def broadcast(message: dict):
    """
    发后即忘广播: 引擎循环绝不同步 await 任何客户端发送。
    uvicorn 的 ws.send 在对端停止读取时会无限期挂起 (transport 缓冲满 ->
    writable 事件被清除), 若在引擎循环内直接 await 会冻结整个仿真。
    这里把每条发送丢进独立任务; 上一帧还没发完(卡住)的客户端直接踢出。
    """
    data = json.dumps(message, ensure_ascii=False)
    for ws in list(CLIENTS):
        if ws in INFLIGHT:          # 上一帧仍卡着 -> 判定僵死连接, 踢出
            CLIENTS.discard(ws)
            INFLIGHT.pop(ws, None)
            continue
        task = asyncio.create_task(_send_to(ws, data))

        def _done(t, ws=ws):
            INFLIGHT.pop(ws, None)
            if t.cancelled() or t.exception() is not None:
                CLIENTS.discard(ws)

        INFLIGHT[ws] = task
        task.add_done_callback(_done)


@app.get("/health")
async def health():
    """仿真健康度: tick 应随时间持续增长"""
    return {"tick": ENGINE.tick, "clients": len(CLIENTS),
            "mode": ENGINE.mode, "nodes": len(ENGINE.nodes)}


# ---------- 前端静态托管: 单端口部署, 页面与接口同源 ----------
if DIST_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/")
    async def serve_index():
        return FileResponse(DIST_DIR / "index.html",
                            headers={"Cache-Control": "no-cache"})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
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
                raise NotImplementedError
    except WebSocketDisconnect:
        CLIENTS.discard(ws)
    except Exception as e:
        await ws.send_text(json.dumps({"cmd": "error", "detail": str(e)}))
        CLIENTS.discard(ws)


@app.on_event("startup")
async def startup():
    ENGINE.compute_network()
    # 关键: 持有 task 强引用, 防止被 GC 静默回收导致引擎停摆
    app.state.engine_task = asyncio.create_task(ENGINE.run_forever(broadcast))


if __name__ == "__main__":
    import uvicorn
    with contextlib.suppress(KeyboardInterrupt):
        # ws="wsproto": 规避 websockets 17.x legacy 协议在客户端断开时的
        # AssertionError (该异常曾逃逸并杀死整个进程)
        uvicorn.run(app, host="0.0.0.0", port=5000, ws="wsproto")
