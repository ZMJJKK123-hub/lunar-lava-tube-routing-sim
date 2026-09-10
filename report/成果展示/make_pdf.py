# -*- coding: utf-8 -*-
"""
成果展示 PDF 生成器 v3 (3 页, 精简通俗版)
=====================================
数据源 (只读): 自愈实验/healing_*.json + 信道实验/abc_batch_*.json
             + 道钉实验/deploy_ab_*.json (RL 试点②)
产物: report/成果展示/成果展示.pdf (matplotlib 出图 + fpdf2 组版, 中文 msyh)
v3 改版要点 (按讲稿反馈): 总览文字分段层层递进, 三套自救机制逐条讲前提
(功率自举的电量红线/300 米硬半径等); 删 KPI 卡/干扰源专场/学习证据表/道钉
账本 (讲解时口头补充); 信道页先讲清"比的是什么"再上图; 压缩为 3 页。
"""
import json
import glob
import statistics as st          # 标准库: 均值/标准差/中位数
from datetime import datetime    # 标准库: 生成时间戳
from pathlib import Path         # 标准库: 数据文件定位

import matplotlib                # 出图后端 (无窗)
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # 图表绘制
from fpdf import FPDF            # PDF 组版 (fpdf2)

BASE = Path(__file__).parent.parent          # report/
OUT = Path(__file__).parent                  # 成果展示/
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "DengXian", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

C_TITLE = (13, 33, 66)      # 深蓝 (标题/结构)
C_ACC = (0, 165, 150)       # 青 (好结果)
C_WARN = (255, 130, 60)     # 橙 (亮点/学习组)
C_BAD = (220, 60, 60)       # 红 (故障/失败)
C_GRAY = (110, 125, 145)    # 灰 (注解)

# ================= 数据加载 =================
healing = json.load(open(sorted(glob.glob(str(BASE / "自愈实验/healing_*.json")))[-1],
                          encoding="utf-8"))
abc = json.load(open(sorted(glob.glob(str(BASE / "信道实验/abc_batch_*.json")))[-1],
                         encoding="utf-8"))
dep = json.load(open(sorted(glob.glob(str(BASE / "道钉实验/deploy_ab_*.json")))[-1],
                        encoding="utf-8"))

# ---- 自愈实验 ----
scen_times = {}
for r in healing["exp1_heal_time"]:
    scen_times.setdefault(r["scenario"], []).append(
        r["heal_secs"] if r["heal_secs"] else 160)   # 未愈记为 ">150"
e2 = healing["exp2_robustness"]
ks = [r["k"] for r in e2]
min_cov = [r["min_cov"] for r in e2]
fin_cov = [r["final_cov"] for r in e2]

# ---- 信道三臂 ----
runs = abc["runs"]
arms = ("A", "B", "C")
lat_mean = {a: st.mean([r["client_latency_mean"] for r in runs[a]]) for a in arms}
lat_std = {a: st.stdev([r["client_latency_mean"] for r in runs[a]]) for a in arms}
t_bc = next(t for t in abc["paired_tests"] if "B−C on client_latency" in t["pair"])

# ---- 道钉 A/B ----
dA = dep["runs_A"]
dB = dep["runs_B"]
spkA = [r["beacons_deployed"] for r in dA]
spkB = [r["beacons_deployed"] for r in dB]
covA = [r["coverage_integral_pct_s"] for r in dA]
covB = [r["coverage_integral_pct_s"] for r in dB]
save_pct = round(100 * (st.mean(spkA) - st.mean(spkB)) / st.mean(spkA))


# ================= 图表 (每个都带"怎么看"导读, 由组版层渲染) =================
def chart_robustness():
    """图1: 一次杀掉 k 个节点, 网络崩到哪/能恢复到哪。红虚线=崩塌谷底, 青实线=恢复后。"""
    fig, ax = plt.subplots(figsize=(6.4, 3.3), dpi=200)
    ax.plot(ks, min_cov, "o--", color="#E05040", lw=2, ms=8, label="刚被砸时 (崩塌谷底)")
    ax.plot(ks, fin_cov, "s-", color="#00A596", lw=2.2, ms=8, label="120 秒后 (自愈恢复)")
    for k, m, f in zip(ks, min_cov, fin_cov):
        ax.annotate("", xy=(k, f), xytext=(k, m),
                    arrowprops=dict(arrowstyle="->", color="#7A8AA0", lw=1))
    ax.set_xlabel("一次性击毁的节点数 (总共 59 个工作节点)", fontsize=10)
    ax.set_ylabel("全网覆盖率 %", fontsize=10)
    ax.set_xticks(ks)
    ax.set_ylim(0, 105)
    ax.legend(fontsize=9, loc="lower left")
    ax.set_title("压力测试: 一次砸掉多少节点, 网络还救得回来?", fontsize=11, color="#0D2142")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    p = OUT / "_chart1.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def chart_healtime():
    """图2: 五类故障砸下后, 网络自救要几秒 (横向条=秒表)。"""
    fig, ax = plt.subplots(figsize=(6.2, 3.1), dpi=200)
    order = ["random_kill", "kill_backbone", "collapse", "jammer_60s", "wall_cut"]
    names = ["随机砸掉 1 个节点", "砸掉主干节点", "塌方 (巨石断链)", "干扰源压制 60 秒", "整堵墙切穿 (硬隔离)"]
    med = [st.median(scen_times[s]) for s in order]
    cols = ["#00A596" if m < 150 else "#E05040" for m in med]
    bars = ax.barh(names[::-1], med[::-1], color=cols[::-1], height=0.55)
    for b, m in zip(bars, med[::-1]):
        ax.text(b.get_width() + 2, b.get_y() + b.get_height() / 2,
                (f"{m:.1f} 秒" if m < 150 else ">150 秒 未愈"), va="center",
                fontsize=9.5, color="#0D2142")
    ax.set_xlim(0, 185)
    ax.set_xlabel("从出事到自动恢复 (秒, 多次重复取中位数)", fontsize=10)
    ax.set_title("故障自愈秒表: 出事后网络多久自己缓过来", fontsize=11, color="#0D2142")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    p = OUT / "_chart2.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def chart_abc():
    """图3: 三种'选信道脑子'的速度对比 + 每轮学习组 vs 随机组差值。"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.8, 3.6), dpi=200,
                                   gridspec_kw={"width_ratios": [1.7, 1.1]})
    labels = ["A 手写规则", "B 机器学习", "C 纯随机\n(对照)"]
    vals = [lat_mean[a] * 0.25 for a in arms]          # 拍→秒
    errs = [lat_std[a] * 0.25 for a in arms]
    cols = ["#7A8AA0", "#FF823C", "#C8CDD6"]
    ax1.bar(labels, vals, yerr=errs, capsize=5, color=cols, width=0.55)
    for i, v in enumerate(vals):
        ax1.text(i, v + 0.02, f"{v:.2f} 秒", ha="center", fontsize=10,
                 color="#0D2142", fontweight="bold")
    ax1.set_ylabel("消息平均送达耗时 (秒)", fontsize=9.5)
    ax1.set_ylim(0, 2.1)
    ax1.set_title("三种脑子谁送得快 (5 轮平均)", fontsize=10, color="#0D2142")
    ax1.grid(axis="y", alpha=0.25)
    diffs = [(b["client_latency_mean"] - c["client_latency_mean"]) * 0.25
             for b, c in zip(runs["B"], runs["C"])]
    ax2.bar(range(1, 6), diffs, color="#FF823C", width=0.6)
    ax2.axhline(0, color="#333", lw=0.8)
    ax2.set_xticks(range(1, 6))
    ax2.set_xlabel("第几轮", fontsize=9)
    ax2.set_title("每轮: 学习组比随机组\n快多少秒 (5/5 轮全胜)", fontsize=9, color="#0D2142")
    ax2.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    p = OUT / "_chart3.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def chart_deploy():
    """图4: 道钉 A/B——左: 每轮用钉数; 右: 平均用钉数 vs 网络健康总分。"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.8, 3.9), dpi=200,
                                   gridspec_kw={"width_ratios": [1.15, 1]})
    x = [1, 2, 3]
    w = 0.36
    ax1.bar([i - w / 2 for i in x], spkA, w, color="#7A8AA0", label="A 组 老脑子(必投)")
    ax1.bar([i + w / 2 for i in x], spkB, w, color="#FF823C", label="B 组 学过(会忍)")
    for i, (a, b) in enumerate(zip(spkA, spkB), 1):
        ax1.text(i - w / 2, a + 0.1, str(a), ha="center", fontsize=9.5, color="#4A5A70")
        ax1.text(i + w / 2, b + 0.1, str(b), ha="center", fontsize=9.5,
                 color="#E06020", fontweight="bold")
    ax1.set_xticks(x)
    ax1.set_xticklabels(["第 1 轮", "第 2 轮", "第 3 轮"])
    ax1.set_ylabel("这一轮插了几根钉", fontsize=9.5)
    ax1.set_ylim(0, 7)
    ax1.legend(fontsize=8.5, loc="upper left")
    ax1.set_title("每轮钉子消耗 (共 6 根)", fontsize=10, color="#0D2142")
    ax1.grid(axis="y", alpha=0.25)
    # 右: 平均钉数 与 网络健康总分 并排
    ax2.bar([0, 1], [st.mean(spkA), st.mean(spkB)], 0.5,
            color=["#7A8AA0", "#FF823C"])
    ax2.text(0, st.mean(spkA) + 0.08, f"{st.mean(spkA):.2f}", ha="center",
             fontsize=10.5, color="#4A5A70")
    ax2.text(1, st.mean(spkB) + 0.08, f"{st.mean(spkB):.2f}", ha="center",
             fontsize=10.5, color="#E06020", fontweight="bold")
    ax2.annotate(f"省 {save_pct}%", xy=(1, st.mean(spkB) + 0.55),
                 ha="center", fontsize=11, color="#00A596", fontweight="bold")
    ax2.set_xticks([0, 1])
    ax2.set_xticklabels(["A 必投", "B 会忍"], fontsize=9.5)
    ax2.set_ylabel("平均每轮用钉 (根)", fontsize=9.5)
    ax2.set_ylim(0, 5)
    ax2.set_title("钉子少花 1/3", fontsize=10, color="#0D2142")
    ax2.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    p = OUT / "_chart4.png"
    fig.savefig(p)
    plt.close(fig)
    return p


c1, c2, c3, c4 = (chart_robustness(), chart_healtime(),
                  chart_abc(), chart_deploy())

# ================= PDF 组版 =================
FONT = "C:/Windows/Fonts/msyh.ttc"
pdf = FPDF(format="A4")
pdf.add_font("yh", "", FONT)
pdf.set_auto_page_break(False)
W = 210


def head(title, sub):
    """页眉: 深蓝横幅标题 + 副题。"""
    pdf.set_fill_color(*C_TITLE)
    pdf.rect(0, 0, W, 26, "F")
    pdf.set_xy(14, 5)
    pdf.set_font("yh", "", 15)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(14, 15.5)
    pdf.set_font("yh", "", 8.5)
    pdf.set_text_color(190, 215, 240)
    pdf.cell(0, 6, sub)


def howto(x, y, w, text):
    """灰字导读: "怎么看这张图" (左对齐——两端对齐会把中英混排拉出大空隙)。"""
    pdf.set_xy(x, y)
    pdf.set_font("yh", "", 8.5)
    pdf.set_text_color(*C_GRAY)
    pdf.multi_cell(w, 5, text, align="L")


def section(y, txt):
    """小节标题条。"""
    pdf.set_xy(14, y)
    pdf.set_font("yh", "", 12)
    pdf.set_text_color(*C_TITLE)
    pdf.cell(0, 8, txt)


def banner(y, text):
    """页脚结论横幅。"""
    pdf.set_fill_color(*C_TITLE)
    pdf.rect(14, y, 182, 15, "F")
    pdf.set_xy(18, y + 3.5)
    pdf.set_font("yh", "", 10)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 8, text)


def paras(y, items, size, lh):
    """多段正文: 逐段左对齐排印, 段间留白, 光标顺流而下 (层层递进)。"""
    pdf.set_xy(14, y)
    pdf.set_font("yh", "", size)
    pdf.set_text_color(60, 75, 95)
    for p in items:
        pdf.multi_cell(182, lh, p, align="L")
        pdf.ln(2.4)


# ---------- 第 1 页: 总览 + 抗砸 + 自愈秒表 ----------
pdf.add_page()
head("月球熔岩管多智能体网络沙盘 · 成果展示",
     f"60 根通信桩自组织成网 × 区块链全员记账 × 巡检机器人救援 × 2 项强化学习实验 | 生成于 {datetime.now():%Y-%m-%d}")
paras(32, [
    "先交代舞台: 月球地下的熔岩管里, 60 根通信桩 (59 根工作桩 + 1 个洞口基站) 自己织成一张网, "
    "数据一跳一跳接力送出洞口; 全网的\"账本\"用区块链同步, 全员记账, 谁也赖不了账。",
    "没人值守, 网络有三套自救本领, 但每套都有前提。"
    "① 链路变弱时, 桩子自己加大发射功率把弱链拉回来——前提是自己的电量还在红线以上, "
    "且对端没超出功率够得着的硬极限 (约 300 米); "
    "② 链路彻底断开, 路由算法几秒内重算绕路——前提是地图上还存在别的路; "
    "③ 实在无路可绕, 巡检机器人才带着 6 根\"道钉\"(中继桩)赶到断点物理搭桥——"
    "前提是断网区的呼救 (SOS) 传得出来、钉子库存还没用完。",
    "我们往这张网里砸各种故障、跑对照实验, 验证它自己活下来的能力; "
    "下面所有数字都来自可复现实验 (原始数据在 report/ 目录)。",
], size=10, lh=5.6)

y = pdf.get_y() + 3
section(y, "▎它有多抗砸: 一次挂掉一批节点试试")
CH1 = 144
pdf.image(c1, x=(W - CH1) / 2, y=y + 8, w=CH1)
y = y + 8 + CH1 * 3.3 / 6.4 + 4
howto(14, y, 182,
      "怎么看上图: 红虚线=刚被砸时的崩溃谷底, 青实线=120 秒后恢复到的高度, 箭头=自愈拉回来的幅度。"
      "一次挂 18 个 (约占三成) 以内, 存活节点最终全部重连; 一次挂 24 个 (约四成) 也能从 18% 拉回 60%。")
section(y + 13, "▎它好得多快: 五类故障逐一掐秒表")
CH2 = 144
pdf.image(c2, x=(W - CH2) / 2, y=y + 21, w=CH2)
howto(14, y + 21 + CH2 * 3.1 / 6.2 + 4, 182,
      "两级自愈: 秒级=路由算法自动绕行重算; 分钟级=机器人赶到现场插道钉物理搭桥。"
      "诚实边界: 整堵墙切穿时连呼救声都过不去, 150 秒内救不回, 如实标红。")

# ---------- 第 2 页: 熔岩管是什么 / 为什么重要 / 通信为什么难 ----------
pdf.add_page()
head("熔岩管: 我们的舞台",
     "三分钟搞懂它是什么、为什么值钱、通信难在哪 | 后附剖面示意图")

# Q1: 熔岩管是什么
section(32, "▎Q1: 熔岩管是什么?")
paras(42, [
    "简单说, 就是月球地下的一条\"天然隧道\"。远古月球火山喷发时, 涌出的岩浆表面先冷却结壳, "
    "内部岩浆流走后留下一条空心管道——跟地球上\"熔岩洞\"同款, 只是月球上的更大: 宽可达数百米, "
    "长可达数十公里, 洞顶厚度足以挡住辐射和陨石。",
    "管壁是天然岩层, 不需要挖、不需要盖, 拿来就用——是月球上现成的\"免费地下室\"。",
], size=9.5, lh=5.4)

# 剖面示意图
LAVA_IMG = 175
pdf.image(str(OUT / "_lava_tube.png"), x=(W - LAVA_IMG) / 2, y=68, w=LAVA_IMG)
y = 68 + LAVA_IMG * 3.2 / 7.0 + 6

# Q2: 为什么重要
section(y, "▎Q2: 熔岩管为什么这么重要?")
paras(y + 10, [
    "① 天然屏障: 管顶岩层挡住宇宙辐射、微陨石和 300℃ 昼夜温差——在里面建基地, "
    "比在月面\"裸奔\"安全得多, 也不需要运厚重屏蔽材料上来。",
    "② 现成空间: 数百米宽的空腔拿来当月球基地、科研站、物资仓库都不用\"盖房子\"——"
    "只需封住洞口、铺好内衬, 大头工程月球已经替我们干完了。",
    "③ 战略资源: NASA 和各国航天机构已把熔岩管列为未来月球基地的首选场址; "
    "谁能率先在其中建起通信与监测网络, 谁就占据了月球基础设施的先手。",
], size=9.5, lh=5.4)

y2 = pdf.get_y() + 4

# Q3: 通信为什么难
section(y2, "▎Q3: 在里面通信为什么这么难?")
paras(y2 + 10, [
    "管是\"串珠状\"的——一串腔室由窄喉道相连, 一个喉道被堵就整腔失联; "
    "巨石和塌方随时切断链路; 没有现成基站, 全靠电池供电的通信桩; "
    "没人值守, 坏了没人修——所以网络必须自己会\"活下来\"。",
], size=9.5, lh=5.4)

banner(276, "一句话: 熔岩管是月球上最值钱的\"免费地下室\", 我们的系统让它里面断不了网。")

# ---------- 第 3 页: 道钉 RL ----------
pdf.add_page()
head("强化学习实验② · 机器人学会\"什么时候该插钉\"",
     "A=老规则(能救就立刻插) B=Q-learning(插还是忍, 事后算账自学) | 同地图同灾害节拍, 3 轮 × 10 分钟")
paras(30, [
    "机器人兜里只有 6 根道钉, 揥一根少一根。麻烦在于: 有的\"断网区\"其实马上要自己恢复了"
    "(节点会加大功率自救、干扰源会走开), 钉子插上去就白扔。学习版机器人每次插钉前先想一想——"
    "投, 还是忍一忍? 它的事后账本: 插的钉真被新路径用到=+1 分(关键投资); 对象其实自愈了=−1 分(白扔); "
    "忍一忍真等到了自愈=+0.5 分(忍对); 忍到来不及=−0.5 分(忍晚)。每 10 分钟一轮的灾害里两边各自决策。",
], size=9.8, lh=5.6)

y = pdf.get_y() + 8
pdf.image(c4, x=14, y=y, w=182)
y = y + 182 * 3.9 / 6.8 + 5
howto(14, y, 182,
      "怎么看上图: 左图逐轮数钉子——B 组(橙)三轮 2/1/4 根 vs A 组(灰) 2/3/6 根; 右图平均下来 B 组每轮少用 1.33 根。"
      "关键前提: 两组的灾害节拍完全相同, 且网络最终健康水平相当 (覆盖率×时间积分 48005 vs 48204, 差 0.4%, 在轮间波动范围内)——"
      "即\"活干得一样好, 钱花得少三分之一\"。")
howto(14, y + 50, 182,
      "诚实边界: 3 轮是小样本, 区间宽; 探索率才从 0.35 降到 0.346, 学习仍在早期, \"越跑越会忍\"的完整曲线需更长批次。\n"
      "数据与复现: report/道钉实验/ | python -u report/道钉实验/run_deploy_ab.py 3 600 5001")
banner(276, "一句话: 道钉少用三分之一, 网络一样健康——\"会忍\"是训练出来的投资眼光。")

pdf.output(OUT / "成果展示.pdf")
for p in (c1, c2, c3, c4):
    p.unlink()
print("PDF DONE ->", OUT / "成果展示.pdf")
