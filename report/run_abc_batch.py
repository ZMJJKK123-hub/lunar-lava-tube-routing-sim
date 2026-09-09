# -*- coding: utf-8 -*-
"""
三臂配对批量实验 A/B/C (信道决策器: RCSPA / Q-learning / 随机)
================================================================
方法学 (对应审计漏洞 1/2/3 的修复):
- 阴性对照 C 组 (随机信道): 检验"该负载下信道选择是否构成区分度";
- 轮换执行次序 (拉丁方旋转): 消除 A 先 B 后的时间趋势混杂;
- 压力负载 (--load): 制造可区分的失败空间 (轻载天花板效应的对策);
- 配对 t 检验 (同流量种子): 报告 t 值与 df 临界值判定, 不做等价断言;
- B 组学习证据: ε 终值 / 首尾时窗时延对比 / 奖励曲线 (采集器入库)。
用法: python -u report/run_abc_batch.py [轮数=5] [每组秒数=300]
产物: report/abc_batch_<时间戳>.json + comparison_ABC.md
"""
import json          # 标准库: 结果聚合与落盘
import os            # 标准库: 自身 PID (僵尸清扫豁免)
import re            # 标准库: 解析采集器 stdout 的 DONE 行
import statistics as st   # 标准库: mean/std
import subprocess    # 标准库: 采集器子进程与服务器管理
import time          # 标准库: 服务器重启节拍
from datetime import datetime
from pathlib import Path
import urllib.request   # 标准库: /health 探活

ROOT = Path(__file__).parent.parent
OUT = Path(__file__).parent

ARMS = ("A", "B", "C")
ARM_NAME = {"A": "RCSPA (规则)", "B": "Q-learning", "C": "随机信道 (阴性对照)"}
LOAD = 0.5                     # 压力负载: 每 0.5s 一条 (轻载的 4 倍)
T_CRIT_05 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
             6: 2.447, 7: 2.365, 8: 2.306}   # 双尾 0.05 的 t 临界值

# 汇总指标 (客户端口径) —— 逐报文原始见 raw_settlements
KEYS = ["ack_admitted", "client_settled_DELIVERED", "client_failed_total",
        "reject_total", "client_latency_mean", "client_latency_p95"]


def restart_server():
    """协议: 清扫孤儿引擎 + 重启服务器 (干净进程, 无跨轮状态)。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe",
                              "/FO", "CSV"], capture_output=True,
                             timeout=15).stdout.decode("gbk", errors="ignore")
        for line in out.splitlines()[1:]:
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) < 5:
                continue
            pid, mem = parts[1], parts[4].replace(",", "").replace(" K", "")
            try:
                if int(pid) != os.getpid() and int(mem) > 30_000:
                    subprocess.run(["taskkill", "/PID", pid, "/F"],
                                   capture_output=True, timeout=15)
                    print(f"  清扫孤儿引擎 PID={pid}", flush=True)
            except (ValueError, OSError):
                pass
    except Exception:
        pass
    pid = None
    try:
        net = subprocess.run(["netstat", "-ano"], capture_output=True,
                             timeout=10).stdout.decode("gbk", errors="ignore")
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
                    return
        except Exception:
            pass
    raise RuntimeError("服务器重启后探活失败")


def run_once(arm, seed, secs):
    """跑一臂: 压力负载 + 配对流量种子; 返回增广汇总 + 学习证据。"""
    cmd = ["python", "-u", str(OUT / "collect_baseline.py"), str(secs),
           f"--arm={arm}", f"--traffic-seed={seed}", f"--load={LOAD}"]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                          timeout=secs + 300)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    m = re.search(r"BASELINE DONE (\{.*\})", stdout)
    if not m:
        raise RuntimeError(f"采集失败 arm={arm} seed={seed}: "
                           f"{stdout[-300:]}")
    d = json.loads(m.group(1))
    cs = d.get("client_settled", {})
    d["client_settled_DELIVERED"] = cs.get("DELIVERED", 0)
    d["client_failed_total"] = sum(v for k, v in cs.items()
                                   if k != "DELIVERED")
    d["reject_total"] = d.get("ack_rejected", 0)
    lat = d.get("client_latency") or {}
    d["client_latency_mean"] = lat.get("mean")
    d["client_latency_p95"] = lat.get("p95")
    return d


def paired_stats(runs, ka, kb, key):
    """配对 t 检验 (逐轮差值): 返回 mean/std/t/显著性 (α=0.05)。"""
    diffs = [b[key] - a[key] for a, b in zip(runs[ka], runs[kb])
             if a.get(key) is not None and b.get(key) is not None]
    n = len(diffs)
    if n < 2:
        return None
    mean = st.mean(diffs)
    sd = st.stdev(diffs)
    t_val = mean / (sd / (n ** 0.5)) if sd else float("inf")
    crit = T_CRIT_05.get(n - 1)
    return {"diff_mean": round(mean, 2), "diff_std": round(sd, 2),
            "n": n, "t": round(t_val, 2), "t_crit": crit,
            "significant": crit is not None and abs(t_val) > crit}


def agg(runs, key):
    vals = [r[key] for r in runs if r.get(key) is not None]
    if not vals:
        return None
    return (round(st.mean(vals), 2),
            round(st.stdev(vals), 2) if len(vals) > 1 else 0.0)


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5
    secs = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 300
    runs = {a: [] for a in ARMS}
    retries = []
    state_path = OUT / "abc_state.json"
    if "--fresh" in sys.argv and state_path.exists():
        state_path.unlink()
    try:
        STATE = json.loads(state_path.read_text(encoding="utf-8"))
        print(f"断点续跑: 已完成 {len(STATE['done'])} 臂次", flush=True)
    except Exception:
        STATE = {"done": []}
    t0 = time.time()
    for i in range(1, rounds + 1):
        seed = 2000 + i
        order = ARMS[(i - 1) % 3:] + ARMS[:(i - 1) % 3]   # 拉丁旋转次序
        for arm in order:
            done = next((x["r"] for x in STATE["done"]
                         if x["round"] == i and x["arm"] == arm), None)
            if done is not None:
                print(f"[轮{i} {arm}臂] 已有断点数据, 跳过", flush=True)
                runs[arm].append(done)
                continue
            for attempt in range(1, 5):
                print(f"[轮{i} {arm}臂 第{attempt}试] 重启服务器...", flush=True)
                try:
                    restart_server()
                    r = run_once(arm, seed, secs)
                    break
                except Exception as e:            # 重启/采集失败均按试次重试
                    retries.append(f"r{i}{arm}#{attempt}: {str(e)[:90]}")
                    print(f"[轮{i} {arm}臂 第{attempt}试失败: {str(e)[:80]}]",
                          flush=True)
                    time.sleep(3)
            else:
                raise RuntimeError(f"轮{i} {arm}臂 4 试皆失败")
            r["_round"], r["_seed"], r["_order"] = i, seed, order.index(arm)
            runs[arm].append(r)
            STATE["done"].append({"round": i, "arm": arm, "r": r})
            (OUT / "abc_state.json").write_text(
                json.dumps(STATE, ensure_ascii=False), encoding="utf-8")
            print(f"[轮{i} {arm}臂 seed={seed}] "
                  f"送达{r['client_settled_DELIVERED']}/{r['ack_admitted']} "
                  f"拒{r['reject_total']} "
                  f"均延{r.get('client_latency_mean')}", flush=True)

    # 汇总: 各臂 mean±std + 三对配对检验
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"meta": {"rounds": rounds, "secs_per_run": secs, "load_every_s": LOAD,
                    "order_rotation": "拉丁方(轮起臂轮换)",
                    "traffic_seeds": [2000 + i for i in range(1, rounds + 1)],
                    "retries": retries,
                    "wall_secs": round(time.time() - t0)},
           "runs": {a: runs[a] for a in ARMS},
           "paired_tests": [{"pair": f"{x}−{y} on {k}", **p}
                            for (x, y), p in [((x, y), paired_stats(
                                runs, x, y, k))
                                for x, y in (("B", "A"), ("C", "A"), ("B", "C"))
                                for k in ("client_settled_DELIVERED",
                                          "client_latency_mean",
                                          "reject_total")] if p]}
    (OUT / f"abc_batch_{stamp}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    md = [f"# 三臂配对实验 A/B/C (n={rounds}, 压力负载 {LOAD}s/条, 轮换次序)", "",
          f"- A=RCSPA规则 / B=Q-learning / C=随机信道(阴性对照) | "
          f"世界种子42, 流量种子配对, 每轮重启服务器",
          f"- 措辞纪律: n={rounds} 的配对t检验仅判断'能否检出差异', "
          f"不构成等价性证明", "",
          "| 指标 | A RCSPA | B Q-learning | C 随机 |", "|---|---|---|---|"]
    for label, key in (("受理", "ack_admitted"),
                       ("累计送达", "client_settled_DELIVERED"),
                       ("失败/超时合计", "client_failed_total"),
                       ("拒绝", "reject_total"),
                       ("平均时延 tick", "client_latency_mean"),
                       ("P95 时延 tick", "client_latency_p95")):
        cells = []
        for a in ARMS:
            m = agg(runs[a], key)
            cells.append(f"{m[0]} ± {m[1]}" if m else "—")
        md.append(f"| {label} | " + " | ".join(cells) + " |")
    md += ["", "## 配对 t 检验 (α=0.05, df=n−1)", "",
           "| 对比 | 指标 | 差值均值±std | t | 临界 | 显著 |",
           "|---|---|---|---|---|---|"]
    for item in out["paired_tests"]:
        md.append(f"| {item['pair']} | | {item['diff_mean']} ± {item['diff_std']} "
                  f"| {item['t']} | {item['t_crit']} | "
                  f"{'是' if item['significant'] else '否'} |")
    # B 学习证据
    b0, b1 = runs["B"][0], runs["B"][-1]
    md += ["", "## B 组学习证据 (首末对照)", ""]
    md.append(f"- ε 探索率终值: {[r.get('rl_final', {}).get('epsilon') for r in runs['B']]}")
    _lt = [[(r.get("latency_first_third") or {}).get("mean"),
            (r.get("latency_last_third") or {}).get("mean")]
           for r in runs["B"]]
    md.append(f"- 首时窗 vs 末时窗平均时延 (逐轮): {_lt}")
    md += ["", f"- 原始数据: abc_batch_{stamp}.json (含逐报文 raw_settlements)"]
    (OUT / "comparison_ABC.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nABC BATCH DONE -> abc_batch_{stamp}.json / comparison_ABC.md",
          flush=True)


if __name__ == "__main__":
    import sys
    main()
