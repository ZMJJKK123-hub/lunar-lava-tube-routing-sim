# -*- coding: utf-8 -*-
"""
道钉投/忍时机学习器 (DeployQLearner)
====================================================
职责: RL 试点② —— 机器人「什么时候该扔道钉」的决策与结算核心。
两张 Q 表 (sos 桥接口 / assist 加固口), 状态键为 DeployState 元组,
动作 = {invest 投, wait 忍}; 奖励由 robot/rl_gate 依世界观测判定后
回填 (经钉恢复 +1 / 假孤岛白扔 -1 / 窗口尽仍失联 -0.5 /
忍后自愈 +0.5 / 忍到放弃 -0.5), 本模块只管表/探索/窗口簿记/TD 更新。
纯逻辑层: 零 engine 依赖 (观测判定在 rl_gate), 可独立单测。
决策开关关闭时 decide 不被调用 (规则版恒投), 但落钉仍经 note_deploy
登记 —— 学习器兼任「落钉审计器」, A/B 两组共享同一浪费率口径。
"""
import logging   # 标准库: 模块日志 (结算摘要)
import random    # 标准库: ε-贪婪探索掷骰与平手破平

from collections import deque   # 标准库: 近期奖励滚动窗口 (定长)

from ..config import (RLD_ALPHA, RLD_EPS_DECAY, RLD_EPS0,   # 超参: 学习率/探索衰减/初值
                      RLD_EPS_MIN, RLD_GAMMA)               # 探索下限/折扣
from ..types import DeployState, DeployLearningStats   # 契约: 状态元组/快照观测

log = logging.getLogger(__name__)   # 本模块日志器 (结算事件可见, 不打断仿真)

# 动作下标: Q 值二元组 [invest, wait] 的取位 (状态表内部约定)
_INVEST, _WAIT = 0, 1


class DeployQLearner:
    """职责: 道钉投/忍决策的 Q 表 + ε-贪婪 + 结算窗口簿记 + TD 更新。

    核心属性:
    - q: {表名: {DeployState: [q_invest, q_wait]}} 两张表 (sos/assist);
    - pending_deploys: [落钉结算窗口] {tick, beacon_id, nid, table, state}
      —— 落钉后 RLD_WINDOW_TICKS 内由 rl_gate 判定经钉/白扔/无效;
    - pending_waits: [忍挂起] 同构记录 —— 目标自行恢复(+)/任务放弃(-)终局;
    - epsilon/n_choices: 探索率与决策计数; history: 近 100 次结算奖励;
    - picks/settled: 决策与六类结算的累计计数 (快照观测/实验指标)。

    调用链: rl_gate.want_deploy -> decide/note_deploy/note_wait ;
    rl_gate.observe_learner -> settle (TD 更新) ; engine.snapshot -> stats。
    """

    def __init__(self):
        self.q: dict[str, dict[DeployState, list[float]]] = {"sos": {}, "assist": {}}
        self.pending_deploys: list[dict] = []
        self.pending_waits: list[dict] = []
        self.epsilon = RLD_EPS0
        self.n_choices = 0
        self.history: deque = deque(maxlen=100)
        self.picks = {"invest": 0, "wait": 0}
        self.settled = {"good": 0, "waste": 0, "late": 0,
                        "patient": 0, "hesitated": 0}

    # ---------- 决策 ----------
    def decide(self, table: str, state: DeployState) -> bool:
        """ε-贪婪选动作: 以 epsilon 概率随机探索, 否则取 Q 最优
        (平手随机破平, 避免恒选「投」的系统性偏置)。

        Args: table: "sos"/"assist"; state: DeployState 状态键。
        Returns: bool —— True=invest(投), False=wait(忍)。
        Globals Used: RLD_EPS_MIN/RLD_EPS_DECAY。Calls: None。
        """
        self.n_choices += 1
        self.epsilon = max(RLD_EPS_MIN, self.epsilon * RLD_EPS_DECAY)
        if random.random() < self.epsilon:
            invest = random.random() < 0.5
        else:
            qs = self._qs(table, state)
            invest = (qs[_INVEST] > qs[_WAIT]
                      or (qs[_INVEST] == qs[_WAIT] and random.random() < 0.5))
        self.picks["invest" if invest else "wait"] += 1
        return invest

    # ---------- 窗口簿记 (rl_gate 调用) ----------
    def note_deploy(self, tick: int, beacon_id: str, nid: str,
                    table: str, state: DeployState) -> None:
        """登记一次落钉: 开启 RLD_WINDOW_TICKS 结算窗口 (审计与决策共用)。

        Args: tick: 落钉拍; beacon_id: 预测的新钉 id; nid: 任务目标节点;
              table: 决策表名; state: 决策时的状态键。
        Returns: None (追加到 pending_deploys)。
        """
        self.pending_deploys.append({"tick": tick, "beacon_id": beacon_id,
                                     "nid": nid, "table": table, "state": state})

    def note_wait(self, tick: int, nid: str, table: str,
                  state: DeployState) -> None:
        """登记一次「忍」: 同目标旧挂起被新决策取代 (旧记录中性结算 r=0,
        仅做价值传播不产生胜负 —— 每个忍决策终局唯一, 防重复计奖)。

        Args: tick: 决策拍; nid: 目标节点; table: 决策表名; state: 状态键。
        Returns: None。Calls: settle。
        """
        for rec in [r for r in self.pending_waits if r["nid"] == nid]:
            self.settle(rec, 0.0, None, rec["state"])   # 被取代: 中性, 不计数
        self.pending_waits.append({"tick": tick, "nid": nid,
                                   "table": table, "state": state})

    def drop(self, rec: dict) -> None:
        """丢弃一条落钉窗口 (预测钉 id 未兑现 / 目标节点已消失等无意义样本)。

        Args: rec: pending_deploys 中的记录 dict。Returns: None。
        """
        if rec in self.pending_deploys:
            self.pending_deploys.remove(rec)

    # ---------- 结算 ----------
    def settle(self, rec: dict, reward: float, kind: str | None,
               next_state: DeployState) -> None:
        """结算一条决策记录: TD 更新 + 奖励历史 + 计数, 并移出挂起表。

        Args: rec: 挂起记录; reward: 终局奖励; kind: 结算类别
              (good/waste/late/patient/hesitated; None=中性取代不计数);
              next_state: 结算时刻的状态键 (价值传播终点)。
        Returns: None。Globals Used: RLD_ALPHA/RLD_GAMMA。Calls: _qs。
        """
        action = _INVEST if "beacon_id" in rec else _WAIT
        qs = self._qs(rec["table"], rec["state"])
        nqs = self._qs(rec["table"], next_state)
        qs[action] += RLD_ALPHA * (reward + RLD_GAMMA * max(nqs) - qs[action])
        self.history.append(reward)
        if kind:
            self.settled[kind] = self.settled.get(kind, 0) + 1
            log.info("道钉结算[%s] 表=%s 奖励%.2f Q(invest)=%.3f Q(wait)=%.3f",
                     kind, rec["table"], reward, qs[_INVEST], qs[_WAIT])
        for pool in (self.pending_deploys, self.pending_waits):
            if rec in pool:
                pool.remove(rec)

    # ---------- 观测 ----------
    def stats(self, enabled: bool) -> DeployLearningStats:
        """快照导出: 开关/表规模/决策与结算计数/探索率/近期奖励。

        Args: enabled: 决策开关当前值 (False=审计模式)。Returns: 契约 dict。
        Globals Used: None。Calls: None。
        """
        avg = (round(sum(self.history) / len(self.history), 3)
               if self.history else None)
        return {"enabled": enabled,
                "q_size": len(self.q["sos"]) + len(self.q["assist"]),
                "picks": dict(self.picks), "settled": dict(self.settled),
                "epsilon": round(self.epsilon, 3),
                "avg_reward_100": avg, "n_choices": self.n_choices}

    # ---------- 内部 ----------
    def _qs(self, table: str, state: DeployState) -> list[float]:
        """取(或惰性建)某表某状态的 Q 二元组 [invest, wait]。"""
        return self.q[table].setdefault(state, [0.0, 0.0])
