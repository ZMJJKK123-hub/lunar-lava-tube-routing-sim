# -*- coding: utf-8 -*-
"""
基线数据采集器 (A 组: 现行 RCSPA 版本)
========================================
方法: 重置世界(同种子) -> 每 2s 注入一条遥测型报文(随机存活节点 -> NODE-00)
      -> 每 5s 采样一帧全局指标, 持续 10 分钟 -> 落盘 JSON + 可读摘要。
用法: python report/collect_baseline.py [秒数=600]
产物: report/baseline_rcspa_<时间戳>.json / .md
"""
import json          # 标准库: 快照解析与结果落盘
import random        # 标准库: 流量大小随机化 (512~1536B, 同 AUTO_TELEMETRY 档位)
import subprocess    # 标准库: 记录采集时的 git 版本 (实验可复现性)
import sys           # 标准库: 命令行时长参数
import time          # 标准库: 采样节拍
from datetime import datetime
from pathlib import Path

import websocket     # 第三方: WS 客户端 (websocket-client)

WS_URL = "ws://127.0.0.1:5000/ws"
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 600
TRAFFIC_EVERY_S = 2.0
SAMPLE_EVERY_S = 5.0
OUT = Path(__file__).parent

ws = websocket.create_connection(WS_URL, timeout=2)
ws.settimeout(1.0)
print("STEP1 已连接", flush=True)
first = json.loads(ws.recv())          # 首帧 geology
assert first.get("cmd") == "geology", first.get("cmd")
print("STEP2 geology ok", flush=True)

# 干净起点: 同种子重置 (RL/干扰源均为关机默认)
ws.send(json.dumps({"cmd": "reset"}))
print("STEP3 reset 已发", flush=True)

try:
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, timeout=5).stdout.strip()
except Exception:
    rev = "?"

samples, delivered_log, traffic_sent = [], [], 0
latest = {}
stale_since = None          # 僵尸帧看门狗: tick 停走 15s 即中止报错 (防采死帧)
recv_n = 0
freeze_probe = [0]             # 墙钟冻结检测: 上一探测点的 tick 水位
next_freeze = 0.0              # 下次冻结检测时刻 (循环前锚定 t0)
acks = {"admitted": 0, "rejected": 0, "reject_signals": {}}
t0 = time.time()
next_freeze = t0 + 30.0        # 冻结检测时刻 (t0 就绪后锚定)
t0_hms = time.strftime("%H:%M:%S")
next_traffic = t0 + 1.0
next_sample = t0 + 2.0
next_hb = t0 + 5.0
while time.time() - t0 < DURATION:
    now = time.time()
    if now >= next_hb:      # 心跳: 定位循环内卡点
        print(f"HB t={now-t0:.0f}s recv={recv_n} sent={traffic_sent} "
              f"latest_tick={latest.get('tick')} samples={len(samples)}",
              flush=True)
        next_hb = now + 5.0
    # 墙钟冻结检测: 每 30s 检查一次 (时间戳触发, 防同秒多迭代重复比较误判)
    if now >= next_freeze:
        next_freeze = now + 30.0
        if freeze_probe[0] and latest.get("tick", 0) - freeze_probe[0] < 40:
            print(f"COLLECTOR ABORT: 仿真减速/tick冻结 (30s 仅前进 "
                  f"{latest.get('tick', 0) - freeze_probe[0]} tick), 数据不可用",
                  flush=True)
            sys.exit(3)
        freeze_probe[0] = latest.get("tick", 0)
    if latest and stale_since is not None and now - stale_since > 15:
        print("COLLECTOR ABORT: tick 停走 >15s (服务异常?), 已采集",
              len(samples), "帧")
        sys.exit(2)
    if now >= next_traffic:            # 注入遥测型流量
        try:
            nodes = {k: v for k, v in latest.get("nodes", {}).items()
                     if v.get("state") != "DEAD" and k != "NODE-00"}
            if nodes:
                src = random.choice(list(nodes))
                ws.send(json.dumps({"cmd": "send_msg", "src": src,
                                    "dst": "NODE-00",
                                    "bytes": random.choice([512, 1024, 1536])}))
                traffic_sent += 1
        except Exception:
            pass
        next_traffic = now + TRAFFIC_EVERY_S
    try:
        msg = json.loads(ws.recv())
        recv_n += 1
        if msg.get("cmd") == "ack":      # send_msg 的准入回执: 受理/拒绝真相
            if msg.get("ok"):
                acks["admitted"] += 1
            else:
                acks["rejected"] += 1
                sig = msg.get("signal") or msg.get("error") or "?"
                acks["reject_signals"][sig] = acks["reject_signals"].get(sig, 0) + 1
        if msg.get("tick") is not None:
            if latest and msg["tick"] > latest.get("tick", -1):
                stale_since = now
            latest = msg
    except websocket.WebSocketTimeoutException:
        pass
    except Exception:
        time.sleep(0.5)
    if time.time() >= next_sample and latest:
        st = latest.get("stats", {})
        tr = latest.get("transport", {}).get("totals", {})
        samples.append({
            "t": round(time.time() - t0, 1), "tick": latest.get("tick"),
            "mode": latest.get("mode"), "alive": st.get("alive"),
            "coverage_pct": st.get("coverage_pct"), "mean_degree": st.get("mean_degree"),
            "avg_snr_db": st.get("avg_snr_db"), "avg_soc_pct": st.get("avg_soc_pct"),
            "fragile_nodes": st.get("fragile_nodes"), "max_hop": st.get("max_hop"),
            "delivered": tr.get("delivered"), "timeout": tr.get("timeout"),
            "inflight": tr.get("inflight"),
            "retries": tr.get("retries"), "drops": tr.get("drops"),
        })
        next_sample = time.time() + SAMPLE_EVERY_S

# 末帧: 直接用主循环的最新快照 (广播 5Hz, latest 距今 ≤5s, 无需再排空 ——
# 曾经的 while-True 排空在永续广播流里永不超时, 是采集器挂死的真凶)
final = latest
ws.close()

results = final.get("transport", {}).get("results", [])
chain = final.get("chain", {})

# 累计真相从 sim.log 按时间窗提取 (transport.totals 是 maxlen=50 的窗口值,
# 不是累计; 日志行含 受理/送达 N tick/超时/重传 —— 全量可grep)
import re
t_end_hms = time.strftime("%H:%M:%S")
log_path = Path(__file__).parent.parent / "backend" / "sim.log"
cum = {"accepted": 0, "delivered": 0, "timeout": 0}
latencies = []
try:
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        hms = line[6:14] if line[:2].isdigit() else ""
        if not (t0_hms <= hms <= t_end_hms):
            continue
        if "受理:" in line:
            cum["accepted"] += 1
        if "送达" in line and "tick" in line:
            cum["delivered"] += 1
            m = re.search(r": (\d+)tick", line)
            if m:
                latencies.append(int(m.group(1)))
        if "超时" in line:
            cum["timeout"] += 1
except FileNotFoundError:
    pass

done = [r for r in results if r.get("signal") == "DELIVERED"]
lat = [r["ticks"] for r in done if isinstance(r.get("ticks"), (int, float))]
meta = {
    "group": "A-baseline-rcspa", "git_rev": rev,
    "started_at": datetime.now().isoformat(timespec="seconds"),
    "duration_s": DURATION, "seed": 42,
    "traffic": {"every_s": TRAFFIC_EVERY_S, "sent": traffic_sent,
                "acks": acks,
                "profile": "random alive node -> NODE-00, 512/1024/1536B"},
    "switches": {"rl_channels": False, "jammer": None, "paused": False},
}
summary = {
    "cumulative_accepted": cum["accepted"],
    "cumulative_delivered": cum["delivered"],
    "cumulative_timeout": cum["timeout"],
    "ack_admitted": acks["admitted"], "ack_rejected": acks["rejected"],
    "delivery_rate_pct": round(100 * cum["delivered"] / max(1, cum["accepted"]), 1),
    "avg_delivery_ticks": round(sum(latencies) / len(latencies), 2) if latencies else None,
    "p95_delivery_ticks": sorted(latencies)[int(len(latencies) * .95)] if latencies else None,
    "window_delivery_rate_pct": round(100 * len(done) / len(results), 1) if results else None,
    "retries_window": final.get("transport", {}).get("totals", {}).get("retries"),
    "chain_h_max": chain.get("h_max"), "chain_agree": chain.get("agree"),
    "alive": final.get("stats", {}).get("alive"),
}
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
(OUT / f"baseline_rcspa_{stamp}.json").write_text(
    json.dumps({"meta": meta, "summary": summary, "samples": samples,
                "recent_results": results}, ensure_ascii=False, indent=1),
    encoding="utf-8")

lines = [
    "# A 组基线数据 (现行 RCSPA 版本)", "",
    f"- 版本: git `{rev}` | 世界种子: 42 | 时长: {DURATION}s",
    f"- 流量: 每 {TRAFFIC_EVERY_S}s 一条随机节点→NODE-00 遥测, 注入 {traffic_sent} 条 "
    f"(ack 受理 {acks['admitted']} / 拒绝 {acks['rejected']})",
    f"- **累计受理 {cum['accepted']} | 累计送达 {cum['delivered']} | 超时 {cum['timeout']}"
    f"| 送达率 {summary['delivery_rate_pct']}%**",
    f"- 时延: 平均 {summary['avg_delivery_ticks']} tick | P95 {summary['p95_delivery_ticks']} tick",
    f"- 期末: 存活 {summary['alive']}/60 | 链高 {summary['chain_h_max']} | 对齐 {summary['chain_agree']}",
    "",
    "> 指标口径: 累计值取自 sim.log 时间窗 (transport.totals 为 50 条窗口值非累计);",
    "> 窗口送达率 {window}% 仅供参考。B 组 (Q-learning) 实验请用同采集器同口径对比。".format(
        window=summary["window_delivery_rate_pct"]),
]
(OUT / f"baseline_rcspa_{stamp}.md").write_text("\n".join(lines), encoding="utf-8")
print("BASELINE DONE", json.dumps(summary, ensure_ascii=False))
