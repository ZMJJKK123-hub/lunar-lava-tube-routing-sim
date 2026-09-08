# -*- coding: utf-8 -*-
"""
自愈能力实验总控 (A 组: 现行 RCSPA 版本)
=========================================
实验 1 自愈时间: 六类故障各重复注入 -> 故障时刻到覆盖率恢复(>=95%故障前且
              脱离 HEALING)的秒表分布
实验 2 鲁棒性曲线: 同时击毁 k∈{3,6,12,18,24} 节点 -> 崩塌深度/恢复后覆盖率/
              恢复期送达率 -> 冗余度 = 恢复到>=90% 的最大 k
实验 3 链路恢复率: 干扰源压制 60s -> 召回后 60s 内熔断链路的恢复比例
用法: python -u report/run_healing.py   (产物: report/healing_*.json/.md/.csv)
"""
import json          # 标准库: 快照解析与结果落盘
import random        # 标准库: 击毁目标抽样 (种子化可复现)
import re            # 标准库: 日志窗口的模式抽取
import subprocess    # 标准库: 记录 git 版本
import sys           # 标准库: 中止码
import time          # 标准库: 实验秒表
from datetime import datetime
from pathlib import Path

import websocket     # 第三方: WS 客户端

WS_URL = "ws://127.0.0.1:5000/ws"
OUT = Path(__file__).parent
LOG_PATH = OUT.parent / "backend" / "sim.log"
random.seed(20260908)          # 场景抽样可复现

STEADY_COV = 99.9             # 稳态判据: 覆盖率 (%)
RECOVER_RATIO = 0.95          # 自愈判据: 覆盖率恢复到故障前的 95%
EXP2_RECOVER = 0.90           # 冗余度判据: 恢复到故障前的 90%
EXP2_KS = [3, 6, 12, 18, 24]  # 击毁节点数梯度 (5%~40%)


class Harness:
    """WS 实验骨架: 快照轮询 / 稳态等待 / 恢复等待 (含看门狗与心跳)。"""

    def __init__(self):
        self.ws = websocket.create_connection(WS_URL, timeout=2)
        self.ws.settimeout(1.0)
        first = json.loads(self.ws.recv())
        assert first.get("cmd") == "geology", first.get("cmd")
        c0 = first["geology"]["chambers"][0]
        xs = [c0["x"] - c0["r"], c0["x"] + c0["r"]]
        zs = [c0["z"] - (c0.get("rz") or c0["r"]),
              c0["z"] + (c0.get("rz") or c0["r"])]
        self.wall_x = sum(xs) / 2                  # 墙: 竖切全管
        self.wall_z = (zs[0] - 60, zs[1] + 60)

    def send(self, obj):
        self.ws.send(json.dumps(obj))

    def reset(self):
        self.send({"cmd": "reset"})

    def poll(self, seconds, traffic=False, label=""):
        """轮询快照若干秒, 返回 (最新快照, 期间覆盖率最小值)。"""
        t0 = time.time()
        latest, min_cov = {}, 1e9
        next_traffic = t0 + 1.0
        next_hb = t0 + 30.0
        probe = [0]
        next_probe = t0 + 30.0
        while time.time() - t0 < seconds:
            now = time.time()
            if now >= next_hb:
                print(f"  ...{label} t={now-t0:.0f}s tick={latest.get('tick')} "
                      f"cov={latest.get('stats', {}).get('coverage_pct')}",
                      flush=True)
                next_hb = now + 30.0
            if now >= next_probe:                  # 冻结看门狗 (30s 至少 +40 tick)
                if probe[0] and latest.get("tick", 0) - probe[0] < 40:
                    print("ABORT: 仿真冻结", flush=True)
                    sys.exit(3)
                probe[0] = latest.get("tick", 0)
                next_probe = now + 30.0
            if traffic and now >= next_traffic and latest:
                nodes = [k for k, v in latest.get("nodes", {}).items()
                         if v.get("state") != "DEAD" and k != "NODE-00"]
                if nodes:
                    self.send({"cmd": "send_msg", "src": random.choice(nodes),
                               "dst": "NODE-00",
                               "bytes": random.choice([512, 1024, 1536])})
                next_traffic = now + 2.0
            try:
                msg = json.loads(self.ws.recv())
                if msg.get("tick") is not None:
                    latest = msg
                    min_cov = min(min_cov, msg.get("stats", {})
                                  .get("coverage_pct", 1e9))
            except websocket.WebSocketTimeoutException:
                pass
        return latest, (min_cov if min_cov < 1e9 else None)

    def _reconnect(self):
        """断线自愈: 被服务器僵尸驱逐(TCP 缓冲塞满>10s)后重建连接。"""
        try:
            self.ws.close()
        except Exception:
            pass
        self.ws = websocket.create_connection(WS_URL, timeout=2)
        self.ws.settimeout(1.0)
        json.loads(self.ws.recv())               # 丢弃 geology 帧

    def wait_steady(self, label):
        """重置后等稳态: 连续 5 帧覆盖率达标且脱离 HEALING;
        6 秒无帧 = 疑似被驱逐 -> 重连+重发 reset (最多两次)。"""
        self.reset()
        t0 = time.time()
        last_frame = time.time()
        ok, latest, reconnects = 0, {}, 0
        while time.time() - t0 < 90:
            if time.time() - last_frame > 6 and reconnects < 2:
                reconnects += 1
                print(f"  ({label} 连接静默{reconnects}, 重连+重置)", flush=True)
                self._reconnect()
                self.reset()
                last_frame = time.time()
                ok = 0
            try:
                msg = json.loads(self.ws.recv())
                if msg.get("tick") is not None:
                    latest = msg
                    last_frame = time.time()
                    st = msg.get("stats", {})
                    if (st.get("coverage_pct", 0) >= STEADY_COV
                            and msg.get("mode") != "HEALING"):
                        ok += 1
                    else:
                        ok = 0
                    if ok >= 5:
                        return latest
            except websocket.WebSocketTimeoutException:
                pass
        print(f"  警告: {label} 稳态等待超时, 以当前状态继续 "
              f"cov={latest.get('stats', {}).get('coverage_pct') if latest else None}",
              flush=True)
        return latest if latest else self.poll(5, label=f"{label}·兜底取帧")[0]

    def wait_recovery(self, pre_cov, timeout, label):
        """故障注入后等待自愈: 覆盖率>=95%故障前 且 脱离 HEALING。"""
        t0 = time.time()
        latest, min_cov = {}, 1e9
        probe, next_probe = [0], t0 + 30.0
        next_hb = t0 + 30.0
        while time.time() - t0 < timeout:
            now = time.time()
            if now >= next_hb:
                print(f"  ...{label} 恢复等待 t={now-t0:.0f}s "
                      f"cov={latest.get('stats', {}).get('coverage_pct')}",
                      flush=True)
                next_hb = now + 30.0
            if now >= next_probe:
                if probe[0] and latest.get("tick", 0) - probe[0] < 40:
                    return None, latest, (min_cov if min_cov < 1e9 else None)
                probe[0] = latest.get("tick", 0)
                next_probe = now + 30.0
            try:
                msg = json.loads(self.ws.recv())
                if msg.get("tick") is not None:
                    latest = msg
                    min_cov = min(min_cov, msg.get("stats", {})
                                  .get("coverage_pct", 1e9))
                    if (msg.get("stats", {}).get("coverage_pct", 0)
                            >= RECOVER_RATIO * pre_cov
                            and msg.get("mode") != "HEALING"):
                        return time.time() - t0, msg, (
                            min_cov if min_cov < 1e9 else None)
            except websocket.WebSocketTimeoutException:
                pass
        return None, latest, (min_cov if min_cov < 1e9 else None)


def log_window(t0_hms, t1_hms, *patterns):
    """sim.log 时间窗内各模式行数 (累计口径的真相源)。
    读全部轮转代 (sim.log/.1/.2) 拼接 —— 实验窗口跨越轮转点时行会
    被搬进旧代文件, 只读当前代会漏计 (实验3 曾因此全零)。"""
    counts = {p: 0 for p in patterns}
    files = [LOG_PATH, LOG_PATH.with_suffix(".log.1"), LOG_PATH.with_suffix(".log.2")]
    lines = []
    for f in files:
        try:
            lines.extend(f.read_text(encoding="utf-8",
                                     errors="ignore").splitlines())
        except FileNotFoundError:
            pass
    for line in lines:
        hms = line[6:14] if line[:2].isdigit() else ""
        if not (t0_hms <= hms <= t1_hms):
            continue
        for p in patterns:
            if p in line:
                counts[p] += 1
    return counts


def hms():
    return time.strftime("%H:%M:%S")


# ================= 实验 1: 自愈时间 =================
def exp1(h):
    print("\n== 实验 1: 自愈时间 ==", flush=True)
    scenarios = [
        ("kill_backbone", 2, lambda h: h.send({"cmd": "disaster",
                                               "kind": "kill_backbone"})),
        ("collapse", 2, lambda h: h.send({"cmd": "disaster",
                                          "kind": "collapse"})),
        ("random_kill", 3, lambda h: h.send({"cmd": "disaster",
                                             "kind": "random_kill"})),
        ("jammer_60s", 2, lambda h: h.send({"cmd": "disaster",
                                            "kind": "jammer"})),
        ("wall_cut", 2, lambda h: h.send({"cmd": "add_wall",
                                          "x1": h.wall_x, "z1": h.wall_z[0],
                                          "x2": h.wall_x, "z2": h.wall_z[1]})),
    ]
    rows = []
    for name, reps, inject in scenarios:
        for i in range(reps):
            tag = f"{name}#{i + 1}"
            steady = h.wait_steady(tag)
            pre = steady["stats"]["coverage_pct"]
            t0h = hms()
            inject(h)
            secs, final, min_cov = h.wait_recovery(pre, 150, tag)
            t1h = hms()
            lw = log_window(t0h, t1h, "熔断", "链路恢复", "投放道钉",
                            "SOS 启动", "自愈完成")
            rows.append({"scenario": name, "rep": i + 1,
                         "pre_cov": pre, "min_cov": min_cov,
                         "heal_secs": round(secs, 1) if secs else None,
                         "final_cov": final["stats"]["coverage_pct"],
                         "final_alive": final["stats"]["alive"],
                         "log": lw})
            print(f"[{tag}] 故障前{pre}% 谷底{min_cov}% "
                  f"自愈={rows[-1]['heal_secs']}s "
                  f"终态{rows[-1]['final_cov']}% 道钉{lw['投放道钉']}",
                  flush=True)
            if name == "jammer_60s":            # 收尾: 召回干扰源
                h.send({"cmd": "disaster", "kind": "jammer"})
                h.poll(5, label="jammer召回")
            if name == "wall_cut":              # 收尾: 拆墙
                h.send({"cmd": "clear_walls"})
                h.poll(5, label="拆墙")
    return rows


# ================= 实验 2: 鲁棒性曲线 =================
def exp2(h):
    print("\n== 实验 2: 鲁棒性-失效比例曲线 ==", flush=True)
    rows = []
    for k in EXP2_KS:
        tag = f"kill{k}"
        steady = h.wait_steady(tag)
        pre = steady["stats"]["coverage_pct"]
        alive = [nid for nid, n in steady["nodes"].items()
                 if n["state"] != "DEAD" and nid != "NODE-00"]
        victims = random.sample(alive, k)
        t0h = hms()
        for v in victims:
            h.send({"cmd": "set_param", "node": v,
                    "params": {"state": "DEAD"}})
        _, min_cov = h.poll(6, label=f"{tag}·崩塌采样")
        final, _ = h.poll(120, traffic=True, label=f"{tag}·恢复期")
        t1h = hms()
        lw = log_window(t0h, t1h, "受理:", "送达", "投放道钉", "SOS 启动")
        fin_cov = final["stats"]["coverage_pct"]
        rows.append({"k": k, "killed_pct": round(100 * k / 59, 1),
                     "pre_cov": pre, "min_cov": min_cov,
                     "final_cov": fin_cov,
                     "final_alive": final["stats"]["alive"],
                     "final_reachable": final["stats"]["reachable"],
                     "recovered_to_90": fin_cov >= EXP2_RECOVER * pre,
                     "delivery_in_recovery": {
                         "accepted": lw["受理:"], "delivered": lw["送达"]},
                     "beacons": lw["投放道钉"]})
        print(f"[{tag}] 崩塌至{min_cov}% -> 120s后 {fin_cov}% "
              f"存活{rows[-1]['final_alive']} "
              f"恢复期送达 {lw['送达']}/{lw['受理:']}", flush=True)
    return rows


# ================= 实验 3: 链路恢复率 =================
def exp3(h):
    print("\n== 实验 3: 链路恢复率 (干扰源) ==", flush=True)
    rows = []
    for i in range(2):
        tag = f"jammer#{i + 1}"
        h.wait_steady(tag)
        t0h = hms()
        h.send({"cmd": "disaster", "kind": "jammer"})
        h.poll(60, label=f"{tag}·压制")
        t_recall = hms()
        h.send({"cmd": "disaster", "kind": "jammer"})
        h.poll(60, label=f"{tag}·召回观察")
        t1h = hms()
        broke = log_window(t0h, t_recall, "熔断")["熔断"]
        healed = log_window(t_recall, t1h, "链路恢复")["链路恢复"]
        rows.append({"rep": i + 1, "broken": broke, "recovered": healed,
                     "recovery_rate_pct": round(
                         100 * min(healed, broke) / max(1, broke), 1)})
        print(f"[{tag}] 断链{broke} 恢复{healed} "
              f"恢复率{rows[-1]['recovery_rate_pct']}%", flush=True)
    return rows


def main():
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except Exception:
        rev = "?"
    h = Harness()
    t_start = time.time()
    e1 = exp1(h)
    e2 = exp2(h)
    e3 = exp3(h)
    h.ws.close()

    # 冗余度结论: 恢复到 90% 的最大 k
    redundant = max((r["k"] for r in e2 if r["recovered_to_90"]), default=0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {
        "meta": {"git_rev": rev, "seed": 20260908,
                 "started_at": datetime.now().isoformat(timespec="seconds"),
                 "wall_secs": round(time.time() - t_start),
                 "criteria": {"recover_ratio": RECOVER_RATIO,
                              "exp2_recover": EXP2_RECOVER,
                              "steady_cov": STEADY_COV}},
        "exp1_heal_time": e1, "exp2_robustness": e2,
        "exp3_link_recovery": e3,
        "redundancy_max_k": redundant,
    }
    (OUT / f"healing_{stamp}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    import statistics as st
    md = ["# 自愈能力实验数据 (A 组: 现行 RCSPA 版本)", "",
          f"- 版本 git `{rev}` | 每场景独立重置(种子42) | 总时长 "
          f"{out['meta']['wall_secs']}s", "",
          "## 实验1 自愈时间 (故障 -> 覆盖率恢复95%+脱离HEALING)", "",
          "| 故障 | 重复 | 故障前% | 谷底% | 自愈秒 | 终态% | 道钉 |",
          "|---|---|---|---|---|---|---|"]
    for r in e1:
        md.append(f"| {r['scenario']} | {r['rep']} | {r['pre_cov']} | "
                  f"{r['min_cov']} | {r['heal_secs'] if r['heal_secs'] else '>150'} | "
                  f"{r['final_cov']} | {r['log']['投放道钉']} |")
    for name in {r["scenario"] for r in e1}:
        ts = [r["heal_secs"] for r in e1
              if r["scenario"] == name and r["heal_secs"]]
        if ts:
            md.append(f"\n- **{name}**: 自愈中位数 "
                      f"{round(st.median(ts), 1)}s (n={len(ts)})")
    md += ["", "## 实验2 鲁棒性-失效比例曲线", "",
           "| 击毁k | 失效% | 谷底% | 120s后% | 存活 | 恢复期送达 | 达标90% |",
           "|---|---|---|---|---|---|---|"]
    for r in e2:
        md.append(f"| {r['k']} | {r['killed_pct']} | {r['min_cov']} | "
                  f"{r['final_cov']} | {r['final_alive']} | "
                  f"{r['delivery_in_recovery']['delivered']}/"
                  f"{r['delivery_in_recovery']['accepted']} | "
                  f"{'✓' if r['recovered_to_90'] else '✗'} |")
    md.append(f"\n- **冗余度结论: 同时击毁 {redundant} 个节点 "
              f"({round(100*redundant/59,1)}%) 仍能恢复到故障前 90% 覆盖率**")
    md += ["", "## 实验3 链路恢复率 (干扰源开60s->召回60s)", "",
           "| 轮 | 断链 | 恢复 | 恢复率% |", "|---|---|---|---|"]
    for r in e3:
        md.append(f"| {r['rep']} | {r['broken']} | {r['recovered']} | "
                  f"{r['recovery_rate_pct']} |")
    (OUT / f"healing_{stamp}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nALL EXPERIMENTS DONE -> healing_{stamp}.json/.md "
          f"(冗余度 max_k={redundant}, 总时长 {out['meta']['wall_secs']}s)",
          flush=True)


if __name__ == "__main__":
    main()
