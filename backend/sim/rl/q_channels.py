# -*- coding: utf-8 -*-
"""
逐边信道 Q-learning 学习器 (ChannelQLearner)
====================================================
职责: B 组实验的核心 —— 每条边一张 "状态->信道" 打分表; 状态取该边
当前被占信道元组 (本地可观), 动作为 3 个信道之一; 奖励取自传输层
结果信号 (DELIVERED +1 / TIMEOUT -1 / 拒绝 -0.5 / 每次重传 -0.05)。
信用分配: 整条路径各跳共享同一标量奖励 (v1 简化, 已知粗糙)。
Globals Used: RL_ALPHA / RL_GAMMA / RL_EPS0 / RL_EPS_MIN / RL_EPS_DECAY。
"""
import logging   # 标准库: 模块日志 (周期摘要)
from collections import deque, defaultdict   # 标准库: 归因队列/嵌套表

from ..config import (RL_ALPHA, RL_EPS_DECAY, RL_EPS0,   # 超参: 学习率/探索衰减/初值
                      RL_EPS_MIN, RL_GAMMA)              # 探索下限/折扣

log = logging.getLogger(__name__)   # 本模块日志器

# 结果信号 -> 标量奖励 (与 A 组共用的物理事实, 只是数值化)
_REWARD = {"DELIVERED": 1.0, "TIMEOUT": -1.0,
           "BUFFER_FULL": -0.5, "MAX_RETRIES": -0.5, "NO_PATH": -0.5}
_RETRY_COST = -0.05          # 每次重传的额外惩罚


class ChannelQLearner:
    """职责: 逐边 Q 表 + ε-贪婪选道 + 结果驱动的 TD 更新。

    核心属性:
    - q: {边键: {状态: [q0, q1, q2]}}  (信道即动作下标);
    - pending: {(src, dst): deque[[(边键,状态,信道), ...]]} 规划期归因队列;
    - epsilon: 探索率 (随选择次数指数衰减到下限);
    - history: 最近 100 条奖励 (收敛观测)。

    调用链: rl_plan -> choose/note_path ; transport.step -> drain
    -> (结果信号 -> reward -> TD 更新) ; snapshot -> stats。
    """

    def __init__(self):
        self.q: dict = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
        self.pending: dict = defaultdict(deque)
        self.seen: deque = deque(maxlen=400)     # 已结算 msg_id (防重复)
        self.epsilon = RL_EPS0
        self.n_choices = 0
        self.history: deque = deque(maxlen=100)
        self.settled = {"DELIVERED": 0, "FAILED": 0}

    # ---------- 决策 ----------
    def choose(self, edge, state) -> int:
        """ε-贪婪选信道: 以 epsilon 概率随机探索, 否则取 Q 最优
        (平手随机破平 —— 避免恒选 0 号信道的系统性偏置)。

        Args: edge: 边键 (排序二元组); state: 该边被占信道元组。
        Returns: int ∈ {0,1,2}。Globals Used: RL_EPS_MIN/RL_EPS_DECAY。
        """
        import random
        self.n_choices += 1
        self.epsilon = max(RL_EPS_MIN, self.epsilon * RL_EPS_DECAY)
        if random.random() < self.epsilon:
            return random.randrange(3)
        qs = self.q[edge][state]
        best = max(qs)
        return random.choice([i for i, v in enumerate(qs) if v == best])

    def note_path(self, src, dst, hops):
        """规划期登记: (src,dst) 的逐跳 (边,状态,信道), 结算时 FIFO 配对。
        同源同目的并发报文可能错位配对 —— v1 已知简化。"""
        self.pending[(src, dst)].append(hops)

    # ---------- 学习 ----------
    def drain(self, transport) -> None:
        """扫传输层结果信号, 未结算的按 (src,dst) FIFO 配对路径并更新 Q。

        Args: transport: TransportLayer (读 results / _busy_channels)。
        Returns: None。Globals Used: RL_ALPHA / RL_GAMMA。Calls: _update。
        """
        for r in list(transport.results):
            mid = r.get("msg_id")
            if mid is None or mid in self.seen:
                continue
            self.seen.append(mid)
            hops = self._pop_pending(r.get("src"), r.get("dst"))
            if not hops:
                continue                     # 非本学习器规划的报文
            status = r.get("status", "")
            base = _REWARD.get(status, -0.2 if status else 0.0)
            reward = base + _RETRY_COST * (r.get("retries") or 0)
            self.history.append(reward)
            if status == "DELIVERED":
                self.settled["DELIVERED"] += 1
            elif status:
                self.settled["FAILED"] += 1
            busy = transport._busy_channels()
            for edge, state, ch in hops:
                nxt = tuple(sorted(busy.get(frozenset(edge), ())))
                self._update(edge, state, ch, reward, nxt)

    def _pop_pending(self, src, dst):
        q = self.pending.get((src, dst))
        return q.popleft() if q else None

    def _update(self, edge, state, action, reward, next_state) -> None:
        """TD 更新: Q[s][a] += α (r + γ·maxQ(s') − Q[s][a])。"""
        qs = self.q[edge][state]
        nqs = self.q[edge][next_state]
        qs[action] += RL_ALPHA * (reward + RL_GAMMA * max(nqs) - qs[action])

    # ---------- 观测 ----------
    def stats(self, enabled: bool) -> dict:
        """快照 rl 字段: 开关/Q 表规模/信道选择分布/近期奖励/探索率。"""
        picks = [0, 0, 0]
        for states in self.q.values():
            for qs in states.values():
                picks[max(range(3), key=lambda i: qs[i])] += 1
        avg = (round(sum(self.history) / len(self.history), 3)
               if self.history else None)
        return {"enabled": enabled, "q_edges": len(self.q),
                "q_entries": sum(len(v) for v in self.q.values()),
                "greedy_picks": picks, "epsilon": round(self.epsilon, 3),
                "avg_reward_100": avg, "settled": dict(self.settled),
                "n_choices": self.n_choices}
