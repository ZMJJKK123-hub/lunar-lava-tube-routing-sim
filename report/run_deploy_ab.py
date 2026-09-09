# -*- coding: utf-8 -*-
"""
道钉时机 A/B 批量实验执行器 (RL 试点②, n 轮配对)
=====================================================
方法学: **A 组 n 轮先连跑, B 组 n 轮后连跑** (与信道实验的逐轮交替不同):
B 组 Q 表跨 reset 保留但跨进程不保留 —— 同组连跑共享一次服务器进程,
轮间仅 reset, 才能积累出学习曲线; A/B 按轮号配对同灾害/流量种子。
用法: python -u report/run_deploy_ab.py [轮数=3] [每组秒数=600]
产物: report/deploy_ab_<时间戳>.json + comparison_deploy.md
"""
import json          # 标准库: 结果聚合与落盘
import re            # 标准库: 解析采集器 stdout 的 DONE 行
import statistics as st   # 标准库: mean/std
import subprocess    # 标准库: 采集器子进程与服务器管理
import time          # 标准库: 服务器重启节拍
from datetime import datetime   # 标准库: 产物时间戳
from pathlib import Path        # 标准库: 路径定位
import urllib.request   # 标准库: /health 探活

ROOT = Path(__file__).parent.parent
OUT = Path(__file__).parent
ROUNDS = 3
SECS = 600
KEYS = ["beacons_deployed", "coverage_integral_pct_s", "coverage_min_pct",
        "waste_rate_pct"]


def restart_server():
    """协议: 重启服务器 (杀 :5000 监听进程 -> 干净进程拉起 -> 探活)。"""
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
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True,
                       timeout=15)
        time.sleep(1.5)
    subprocess.Popen(["python", "main.py"], cwd=str(ROOT / "backend"),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        try:
            with urllib.request.urlopen("http://127.0.0.1:5000/health",
                                        timeout=2) as r:
                if json.loads(r.read()).get("tick", 0) > 0:
                    return True
        except Exception:
            pass
    raise RuntimeError("服务器重启后探活失败")


def run_once(rl: bool) -> dict:
    """跑一轮道钉采集器, 返回其 DONE 行 JSON。
    --seeded: 采集器 random.seed(2000), A/B 流量序列同源; 灾害序列由
    墙钟灾害槽 (每 60s 一槽) 同节拍驱动 —— 配对可比。"""
    cmd = ["python", "-u", str(OUT / "collect_deploy.py"), str(SECS), "--seeded"]
    if rl:
        cmd.append("--deploy-rl")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                          timeout=SECS + 300)
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    m = re.search(r"DEPLOY DONE (\{.*\})", out)
    if not m:
        raise RuntimeError(f"采集失败 rl={rl}: {out[-300:]} {err[-300:]}")
    return json.loads(m.group(1))


def agg(rows):
    """mean±std 聚合关键指标。"""
    out = {}
    for k in KEYS:
        vals = [r[k] for r in rows if r.get(k) is not None]
        if vals:
            out[k] = {"mean": round(st.mean(vals), 2),
                      "std": round(st.stdev(vals), 2) if len(vals) > 1 else 0.0,
                      "n": len(vals)}
    return out


def main():
    """批量执行入口: 可选 argv[1]=轮数(默认3), argv[2]=每组秒数(默认600)。"""
    import sys   # 标准库: 命令行参数
    global ROUNDS, SECS
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        ROUNDS = int(sys.argv[1])
    if len(sys.argv) > 2 and sys.argv[2].isdigit():
        SECS = int(sys.argv[2])
    rounds, secs = ROUNDS, SECS
    runs = {"A": [], "B": []}
    retries = []
    t0 = time.time()
    for group, rl in (("A", False), ("B", True)):
        for i in range(1, rounds + 1):
            for attempt in range(1, 4):
                # A 组每轮重启 (无状态); B 组仅第 1 轮重启 (保 Q 表连跑)
                if group == "A" or i == 1:
                    print(f"[{group}组 轮{i}] 重启服务器...", flush=True)
                    restart_server()
                try:
                    r = run_once(rl, i)
                    break
                except RuntimeError as e:
                    retries.append({"group": group, "round": i,
                                    "attempt": attempt, "why": str(e)[:120],
                                    "qtable_lost": group == "B" and i > 1})
                    print(f"[{group}组 轮{i} 第{attempt}试失败重试]", flush=True)
            r["_round"] = i
            runs[group].append(r)
            print(f"[{group}组 轮{i}] 落钉 {r.get('beacons_deployed')} 根 | "
                  f"结算 {r.get('deploy_settled')} | 决策 {r.get('deploy_picks')}",
                  flush=True)
    a, b = agg(runs["A"]), agg(runs["B"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def row(name, key):
        ra, rb = a.get(key), b.get(key)
        return (f"| {name} | " + (f"{ra['mean']} ± {ra['std']}" if ra else "—")
                + " | " + (f"{rb['mean']} ± {rb['std']}" if rb else "—") + " |")

    md = [
        "# 道钉时机 A/B (A=规则恒投, B=Q-learning 学投/忍)", "",
        f"- 方法学: A 组每轮重启; B 组同进程连跑 n 轮 (Q 表跨 reset 保留, "
        f"学习曲线可见); 每 60s 灾害槽 (kill_backbone/collapse/jammer30s/"
        f"random_kill) 制造救援样本; 每组每轮 {secs}s; 世界种子 42",
        f"- 落钉审计口径 (两组同): 落钉后 40tick 窗口内恢复路径经钉=good, "
        f"恢复不经钉=waste(假孤岛白扔), 仍失联=late",
        f"- 总耗时 {round(time.time()-t0)}s | 重试 {len(retries)} 次 | "
        f"原始: deploy_ab_{stamp}.json", "",
        "| 指标 | A 组 规则恒投 | B 组 Q-learning |", "|---|---|---|",
        row("落钉总数", "beacons_deployed"),
        row("覆盖率积分 (%·s)", "coverage_integral_pct_s"),
        row("覆盖率最低点 %", "coverage_min_pct"),
        row("假孤岛浪费率 % (B 组学习器口径)", "waste_rate_pct"),
        "", "## B 组学习曲线 (跨轮累计决策/结算)", "",
        "| 轮 | 决策 invest/wait | 结算 good/waste/late/patient/hesitated | ε |",
        "|---|---|---|---|",
    ]
    for r in runs["B"]:
        p, s = r.get("deploy_picks") or {}, r.get("deploy_settled") or {}
        md.append(f"| {r['_round']} | {p.get('invest', 0)}/{p.get('wait', 0)} | "
                  f"{s.get('good', 0)}/{s.get('waste', 0)}/{s.get('late', 0)}/"
                  f"{s.get('patient', 0)}/{s.get('hesitated', 0)} | "
                  f"{r.get('deploy_epsilon')} |")
    (OUT / "comparison_deploy.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / f"deploy_ab_{stamp}.json").write_text(
        json.dumps({"meta": {"rounds": rounds, "secs": secs,
                             "wall_secs": round(time.time() - t0)},
                    "A_mean_std": a, "B_mean_std": b,
                    "runs_A": runs["A"], "runs_B": runs["B"],
                    "retries": retries}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\nDEPLOY AB DONE -> deploy_ab_{stamp}.json / comparison_deploy.md",
          flush=True)


if __name__ == "__main__":
    main()
