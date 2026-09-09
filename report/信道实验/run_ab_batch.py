# -*- coding: utf-8 -*-
"""
A/B 配对批量实验执行器 (n 轮, 每轮 A/B 同流量种子)
=====================================================
方法学: 每次运行前重启服务器(干净进程) -> 同种子重置世界 -> 第 i 轮
A/B 共享流量种子 (1000+i) -> 配对差值消除流量噪声; 汇总 mean±std。
用法: python -u report/run_ab_batch.py [轮数=3] [每组秒数=600]
产物: report/ab_batch_<时间戳>.json + comparison_AB_avg.md
"""
import json          # 标准库: 结果聚合与落盘
import os            # 标准库: 自身 PID (僵尸清扫豁免)
import re            # 标准库: 解析采集器 stdout 的 DONE 行
import statistics as st   # 标准库: mean/std
import subprocess    # 标准库: 采集器子进程
import time          # 标准库: 服务器重启节拍
from datetime import datetime
from pathlib import Path
import urllib.request   # 标准库: /health 探活

ROOT = Path(__file__).parents[1]
OUT = Path(__file__).parent
ROUNDS = 3
SECS = 600

# 汇总的关键指标 (客户端口径为主)
KEYS = ["ack_admitted", "client_delivery_rate_pct", "delivery_rate_pct",
        "avg_delivery_ticks", "p95_delivery_ticks"]
CLIENT_DELIVERED = "client_settled_DELIVERED"


def sweep_orphan_engines():
    """清扫孤儿引擎: 历史上被杀的批量/实验留下的完整引擎 (无端口但满速
    空转, 与服务器抢 CPU —— 世界冻结事故的反复元凶)。按内存体型区分:
    引擎 >30MB, 采集器 <10MB, 本进程豁免。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe",
                              "/FO", "CSV"], capture_output=True,
                             timeout=15).stdout.decode("gbk", errors="ignore")
    except Exception:
        return
    for line in out.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 5:
            continue
        pid, mem = parts[1], parts[4].replace(",", "").replace(" K", "")
        try:
            if int(pid) != os.getpid() and int(mem) > 30_000:
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               capture_output=True, timeout=15)
                print(f"  清扫孤儿引擎 PID={pid} ({int(mem)//1024}MB)", flush=True)
        except (ValueError, OSError):
            pass


def restart_server():
    """协议: 每次运行前重启服务器 + 清扫孤儿引擎 (干净进程, 无跨轮状态)。"""
    sweep_orphan_engines()
    pid = None
    try:
        net = subprocess.run(["netstat", "-ano"], capture_output=True,
                             timeout=10).stdout.decode("gbk",
                                                       errors="ignore")
        for line in net.splitlines():
            if ":5000" in line and "LISTENING" in line:
                pid = line.split()[-1]
                break
    except Exception:
        pass
    if pid:
        subprocess.run(["taskkill", "/PID", pid, "/F"],
                       capture_output=True, timeout=15)
        time.sleep(1.5)
    subprocess.Popen(["python", "main.py"], cwd=str(ROOT / "backend"),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:5000/health", timeout=2) as r:
                if json.loads(r.read()).get("tick", 0) > 0:
                    return True
        except Exception:
            pass
    raise RuntimeError("服务器重启后探活失败")


def run_once(rl, seed):
    """跑一轮采集器, 返回其 DONE 行 JSON (客户端/日志双口径汇总)。"""
    cmd = ["python", "-u", str(OUT / "collect_baseline.py"), str(SECS)]
    if rl:
        cmd.append("--rl")
    cmd.append(f"--traffic-seed={seed}")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                          timeout=SECS + 300)
    proc.stdout = proc.stdout.decode("utf-8", errors="replace")
    proc.stderr = proc.stderr.decode("utf-8", errors="replace")
    m = re.search(r"BASELINE DONE (\{.*\})", proc.stdout)
    if not m:
        raise RuntimeError(f"采集失败 rl={rl} seed={seed}: "
                           f"{proc.stdout[-400:]} {proc.stderr[-400:]}")
    d = json.loads(m.group(1))
    d["client_settled_DELIVERED"] = d.get("client_settled", {}).get("DELIVERED")
    return d


def agg(rows):
    """mean±std 聚合关键指标。"""
    out = {}
    for k in KEYS + [CLIENT_DELIVERED]:
        vals = [r[k] for r in rows if r.get(k) is not None]
        if vals:
            out[k] = {"mean": round(st.mean(vals), 2),
                      "std": round(st.stdev(vals), 2) if len(vals) > 1 else 0.0,
                      "n": len(vals)}
    return out


def main():
    rounds = ROUNDS
    secs = SECS
    runs = {"A": [], "B": []}
    t0 = time.time()
    retries_log = []
    for i in range(1, rounds + 1):
        seed = 1000 + i
        for group, rl in (("A", False), ("B", True)):
            for attempt in range(1, 4):        # 重试协议: 冻结为概率事件,
                print(f"[轮{i} {group}组 第{attempt}试] 重启服务器...", flush=True)
                restart_server()
                try:
                    r = run_once(rl, seed)
                    break
                except RuntimeError as e:
                    retries_log.append({"round": i, "group": group,
                                        "attempt": attempt,
                                        "why": str(e)[:120]})
                    print(f"[轮{i} {group}组 第{attempt}试失败, 重试]",
                          flush=True)
            r["_round"], r["_seed"] = i, seed
            r["_attempts"] = attempt
            runs[group].append(r)
            print(f"[轮{i} {group}组 seed={seed}] "
                  f"送达 {r.get(CLIENT_DELIVERED)}/{r.get('ack_admitted')} "
                  f"率 {r.get('client_delivery_rate_pct')}% "
                  f"均延 {r.get('avg_delivery_ticks')} tick", flush=True)
    agg_a, agg_b = agg(runs["A"]), agg(runs["B"])
    paired = []
    for a, b in zip(runs["A"], runs["B"]):
        paired.append({"round": a["_round"], "seed": a["_seed"],
                       "A_delivered": a.get(CLIENT_DELIVERED),
                       "B_delivered": b.get(CLIENT_DELIVERED),
                       "A_latency": a.get("avg_delivery_ticks"),
                       "B_latency": b.get("avg_delivery_ticks")})
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"meta": {"rounds": rounds, "secs_per_run": secs,
                    "paired_traffic_seeds": [1000 + i for i in range(1, rounds + 1)],
                    "wall_secs": round(time.time() - t0),
                    "started": datetime.now().isoformat(timespec="seconds")},
           "A_mean_std": agg_a, "B_mean_std": agg_b, "paired": paired,
           "runs_A": runs["A"], "runs_B": runs["B"],
           "retries": retries_log}
    (OUT / f"ab_batch_{stamp}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    def row(name, key, fmt="{:.2f}"):
        a, b = agg_a.get(key), agg_b.get(key)
        return (f"| {name} | " +
                (f"{fmt.format(a['mean'])} ± {fmt.format(a['std'])}" if a else "—") +
                " | " +
                (f"{fmt.format(b['mean'])} ± {fmt.format(b['std'])}" if b else "—") +
                " |")

    md = [
        "# A/B 对比 (n=%d 配对双跑, 均值±标准差)" % rounds, "",
        f"- 方法学: 每轮重启服务器 / 世界种子 42 / 第 i 轮 A/B 共享流量种子 "
        f"(配对设计) / 每组 {secs}s / 客户端口径结算 (msg_id 去重累计)",
        f"- 总耗时 {out['meta']['wall_secs']}s | 冻结重试 {len(retries_log)} 次 | "
        f"原始: ab_batch_{stamp}.json", "",
        "| 指标 | A 组 RCSPA | B 组 Q-learning |",
        "|---|---|---|",
        row("受理条数", "ack_admitted", "{:.0f}"),
        row("累计送达条数", CLIENT_DELIVERED, "{:.0f}"),
        row("送达率 % (客户端)", "client_delivery_rate_pct"),
        row("平均时延 (tick)", "avg_delivery_ticks"),
        row("P95 时延 (tick)", "p95_delivery_ticks", "{:.0f}"),
        "", "## 配对明细 (同流量种子)", "",
        "| 轮 | 种子 | A 送达 | B 送达 | A 时延 | B 时延 |",
        "|---|---|---|---|---|---|",
    ]
    for p in paired:
        md.append(f"| {p['round']} | {p['seed']} | {p['A_delivered']} | "
                  f"{p['B_delivered']} | {p['A_latency']} | {p['B_latency']} |")
    (OUT / "comparison_AB_avg.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nAB BATCH DONE -> ab_batch_{stamp}.json / comparison_AB_avg.md",
          flush=True)


if __name__ == "__main__":
    main()
