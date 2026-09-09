# -*- coding: utf-8 -*-
"""
道钉时机 A/B 采集器 (RL 试点②)
=====================================================
方法: 重置世界(同种子) -> [B组] 若开关未开则 toggle_rl_deploy ->
      每 60s 一个灾害槽 (kill_backbone/collapse/jammer开30s/random_kill
      轮换) 制造救援样本 -> 每 5s 采样 (覆盖率/道钉库存/学习器结算)。
用法: python report/collect_deploy.py [秒数=600] [--deploy-rl] [--seeded]
产物: report/deploy_<组>_<时间戳>.json + .md + stdout "DEPLOY DONE {json}"
口径: 落钉审计由服务端学习器统一判定 (A/B 同口径 —— A 组规则恒投,
      但每根钉同样按「恢复路径是否经钉」结算 good/waste/late)。
"""
import json          # 标准库: 快照解析与结果落盘
import random        # 标准库: 灾害流量随机化 (种子化, A/B 配对可比)
import subprocess    # 标准库: 记录采集时的 git 版本
import sys           # 标准库: 命令行时长参数
import time          # 标准库: 采样/灾害节拍
from datetime import datetime   # 标准库: 产物时间戳
from pathlib import Path        # 标准库: 产物路径

import websocket     # 第三方: WS 客户端 (websocket-client)

PORT = 5000          # 目标服务器端口 (--port=5001 等可覆盖, 与主实验隔离)
for _a in sys.argv[1:]:
    if _a.startswith("--port="):
        PORT = int(_a.split("=", 1)[1])
WS_URL = f"ws://127.0.0.1:{PORT}/ws"
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 600
RL_MODE = "--deploy-rl" in sys.argv       # B 组: 道钉时机 Q-learning
TRAFFIC_SEED = 2000 if "--seeded" in sys.argv else None
GROUP = "B-deploy-rl" if RL_MODE else "A-deploy-rule"
PREFIX = "deploy_b" if RL_MODE else "deploy_a"
DISASTER_EVERY_S = 60.0                   # 灾害槽间隔 (墙钟)
JAMMER_WINDOW_S = 30.0                    # 干扰源槽的开机时长
DISASTERS = ["kill_backbone", "collapse", "jammer", "random_kill"]
TRAFFIC_EVERY_S = 4.0                     # 轻流量 (维持网络活性, 非本实验变量)
SAMPLE_EVERY_S = 5.0
OUT = Path(__file__).parent / "每轮原始数据"   # 单轮产物归档子目录
OUT.mkdir(exist_ok=True)

ws = websocket.create_connection(WS_URL, timeout=2)
ws.settimeout(1.0)
first = json.loads(ws.recv())             # 首帧 geology
assert first.get("cmd") == "geology", first.get("cmd")
print("STEP1 geology ok", flush=True)

ws.send(json.dumps({"cmd": "reset"}))     # 干净起点 (B 组学习器跨 reset 保留)


def drain(seconds: float) -> dict:
    """边收帧边等待 (盲等会反压冻结服务器); 返回最新快照。"""
    latest = {}
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            m = json.loads(ws.recv())
            if m.get("tick") is not None:
                latest = m
        except Exception:
            pass
    return latest


latest = drain(8.0)                       # 统一预热
# B 组开学习器 (仅当开关未开 —— 连跑轮次间不重复 toggle, 保住 Q 表)
if RL_MODE and not latest.get("rl_deploy", {}).get("enabled"):
    ws.send(json.dumps({"cmd": "toggle_rl_deploy"}))
    latest = drain(1.0)
print(f"STEP2 reset+预热 rl_deploy={latest.get('rl_deploy')}", flush=True)

try:
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, timeout=5).stdout.strip()
except Exception:
    rev = "?"
if TRAFFIC_SEED is not None:
    random.seed(TRAFFIC_SEED)

samples = []
t0 = time.time()
next_traffic = t0 + 1.0
next_sample = t0 + 2.0
next_hb = t0 + 5.0
next_freeze = t0 + 30.0
disaster_slot = -1                        # 已执行的灾害槽序号
jammer_on_at = None                       # 干扰源开机时刻 (墙钟)
stale_since = None                        # 僵尸帧看门狗
freeze_probe = 0
recv_n = traffic_sent = disasters_sent = 0
while time.time() - t0 < DURATION:
    now = time.time()
    if now >= next_hb:
        print(f"HB t={now-t0:.0f}s tick={latest.get('tick')} "
              f"cov={latest.get('stats', {}).get('coverage_pct')}% "
              f"钉={sum(1 for n in latest.get('nodes', {}).values() if n.get('role') == 'beacon')}",
              flush=True)
        next_hb = now + 5.0
    if now >= next_freeze:                # 墙钟冻结检测 (30s 探一次水位)
        # 阈值 12 tick/30s (0.4 tick/s): 共用机器上与另一实验共存时的
        # 阵发降速是可接受的 (快照是无状态采样), 真死锁才判冻结
        next_freeze = now + 30.0
        if freeze_probe and latest.get("tick", 0) - freeze_probe < 12:
            print("COLLECTOR ABORT: tick 冻结", flush=True)
            sys.exit(3)
        freeze_probe = latest.get("tick", 0)
    if latest and stale_since and now - stale_since > 15:
        print("COLLECTOR ABORT: tick 停走 >35s", flush=True)
        sys.exit(2)
    # 灾害槽: 制造孤岛/弱链 -> 救援样本 (A/B 同节拍同种类, 配对可比)
    # (发送全部装甲: 瞬时网络抖动只丢一拍, 不让采集器整轮报废 —— 同基线采集器)
    slot = int((now - t0 - 20.0) / DISASTER_EVERY_S)
    if slot >= 0 and slot != disaster_slot:
        disaster_slot = slot
        kind = DISASTERS[slot % len(DISASTERS)]
        try:
            if kind == "jammer":
                if latest.get("jammer"):      # 上一槽未关 (节拍漂移) -> 先召回
                    ws.send(json.dumps({"cmd": "disaster", "kind": "jammer"}))
                    jammer_on_at = None
                ws.send(json.dumps({"cmd": "disaster", "kind": "jammer"}))
                jammer_on_at = now
            else:
                ws.send(json.dumps({"cmd": "disaster", "kind": kind}))
            disasters_sent += 1
        except Exception:
            pass
    if jammer_on_at and now - jammer_on_at > JAMMER_WINDOW_S:
        try:
            ws.send(json.dumps({"cmd": "disaster", "kind": "jammer"}))   # 召回
        except Exception:
            pass
        jammer_on_at = None
    if now >= next_traffic:               # 轻流量: 随机存活节点 -> sink
        try:
            nodes = [k for k, v in latest.get("nodes", {}).items()
                     if v.get("state") != "DEAD" and k != "NODE-00"]
            if nodes:
                ws.send(json.dumps({"cmd": "send_msg", "src": random.choice(nodes),
                                    "dst": "NODE-00", "bytes": random.choice([512, 1024])}))
                traffic_sent += 1
        except Exception:
            pass
        next_traffic = now + TRAFFIC_EVERY_S
    try:
        # 单条接收 (与基线采集器逐字同构 —— 该模式已在本机同服务器实战
        # 300s 零中断验证; 排空式接收反而周期性触发服务端僵死踢出)
        msg = json.loads(ws.recv())
        recv_n += 1
        if msg.get("tick") is not None:
            if latest and msg["tick"] > latest.get("tick", -1):
                stale_since = now
            latest = msg
    except websocket.WebSocketTimeoutException:
        pass
    except Exception:
        time.sleep(0.5)
    if now >= next_sample and latest:
        st = latest.get("stats", {})
        rld = latest.get("rl_deploy", {})
        samples.append({
            "t": round(now - t0, 1), "tick": latest.get("tick"),
            "coverage_pct": st.get("coverage_pct"), "mode": latest.get("mode"),
            "alive": st.get("alive"), "fragile": st.get("fragile_nodes"),
            "beacons": sum(1 for n in latest.get("nodes", {}).values()
                           if n.get("role") == "beacon"),
            "stock": (latest.get("robot") or {}).get("stock"),
            "sos": len((latest.get("robot") or {}).get("sos") or []),
            "rl_picks": (rld.get("picks") or {}),
            "rl_settled": (rld.get("settled") or {}),
            "rl_epsilon": rld.get("epsilon"),
        })
        next_sample = now + SAMPLE_EVERY_S

final = latest
ws.close()
beacons = sum(1 for n in final.get("nodes", {}).values() if n.get("role") == "beacon")
rld = final.get("rl_deploy", {})
settled = rld.get("settled") or {}
integral = round(sum(s.get("coverage_pct") or 0 for s in samples) * SAMPLE_EVERY_S, 1)
cov_min = min((s.get("coverage_pct") or 100 for s in samples), default=None)
gw = settled.get("good", 0) + settled.get("waste", 0)
summary = {
    "group": GROUP, "git_rev": rev, "duration_s": DURATION,
    "beacons_deployed": beacons,
    "final_stock": (final.get("robot") or {}).get("stock"),
    "coverage_integral_pct_s": integral,
    "coverage_min_pct": cov_min,
    "deploy_settled": settled, "deploy_picks": rld.get("picks") or {},
    "deploy_epsilon": rld.get("epsilon"),
    "waste_rate_pct": round(100 * settled.get("waste", 0) / gw, 1) if gw else None,
    "disasters_sent": disasters_sent, "traffic_sent": traffic_sent,
}
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
(OUT / f"{PREFIX}_{stamp}.json").write_text(
    json.dumps({"summary": summary, "samples": samples}, ensure_ascii=False,
               indent=1), encoding="utf-8")
(OUT / f"{PREFIX}_{stamp}.md").write_text(
    f"# {GROUP}\n\n- git `{rev}` | {DURATION}s | 灾害 {disasters_sent} 次 | "
    f"流量 {traffic_sent} 条\n- 落钉 {beacons} 根 | 覆盖率积分 {integral} | "
    f"最低 {cov_min}%\n- 结算 {settled} | 决策 {rld.get('picks')}\n", encoding="utf-8")
print("DEPLOY DONE", json.dumps(summary, ensure_ascii=False))
