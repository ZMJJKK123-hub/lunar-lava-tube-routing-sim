# -*- coding: utf-8 -*-
"""FastAPI 入口: WebSocket 实时通道 + 静态前端托管(可选)"""
import asyncio
import contextlib
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from sim.engine import ENGINE

app = FastAPI(title="月面熔岩管多智能体网络仿真引擎")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

CLIENTS: set[WebSocket] = set()


async def broadcast(message: dict):
    dead = []
    data = json.dumps(message, ensure_ascii=False)
    for ws in CLIENTS:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        CLIENTS.discard(ws)


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
                resp = ENGINE.apply_override(msg["node"], msg.get("params", {}))
                await broadcast(ENGINE.snapshot())
                await ws.send_text(json.dumps({"cmd": "ack", "req_id": msg.get("req_id"), **resp}))
            elif cmd == "disaster":
                ENGINE.inject_disaster(msg.get("kind"))
                await broadcast(ENGINE.snapshot())
            elif cmd == "add_wall":
                # 2D 俯视图画墙: {"cmd":"add_wall","x1":..,"z1":..,"x2":..,"z2":..}
                ENGINE.add_wall(msg["x1"], msg["z1"], msg["x2"], msg["z2"])
                await broadcast(ENGINE.snapshot())
            elif cmd == "move_obstacle":
                resp = ENGINE.move_obstacle(msg["index"], msg["x"], msg["z"])
                await broadcast(ENGINE.snapshot())
                await ws.send_text(json.dumps({"cmd": "ack", **resp}))
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
