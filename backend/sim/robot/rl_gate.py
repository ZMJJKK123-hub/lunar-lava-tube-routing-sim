# -*- coding: utf-8 -*-
"""
道钉学习问询门 (rl_gate): 机器人 <-> DeployQLearner 的适配层
================================================================
职责:
- want_deploy: 规则版落钉口 (桥接检测/加固落钉) 在落钉瞬间的问询入口 ——
  开关关 -> 恒 True (规则零回归, 但仍登记审计窗口);
  开关开 -> 采特征组状态键, ε-贪婪返回投/忍; 忍 = 目标冷却后重评;
- observe_learner: 每 tick 结算窗口扫描 (落钉: 经钉/白扔/无效;
  忍: 自愈/放弃) -> 判定奖励回填学习器;
- wait_giveup: 任务放弃终局 (忍到放弃) 的显式喂给。
分层: 读引擎/机器人公开状态, 不反向被依赖; 奖励判定集中于此,
q_deploy 保持零 engine 依赖可独立单测。
依赖: sim.rl.DeployQLearner (惰性导入防环), sim.config (RLD_*), robot 常量。
"""
import logging   # 标准库: 模块日志 (问询/结算事件)

from ..config import (RL_DEPLOY_ENABLED, RLD_ISO_MID_TICKS,   # 开关/失联分桶中界
                      RLD_RW_GOOD, RLD_RW_HESITATE,           # 奖励: 经钉/忍到放弃
                      RLD_RW_LATE, RLD_RW_PATIENT,            # 奖励: 无效/忍对
                      RLD_RW_WASTE, RLD_WINDOW_TICKS)         # 奖励: 白扔/结算窗口
from ..types import DeployState   # 契约: 状态元组
from .constants import (RESCUE_PATIENCE, RLD_GATE_RECONSIDER_TICKS,   # 忍超时/冷却
                        SOS_ARM_TICKS)   # 失联分桶下界

log = logging.getLogger(__name__)   # 本模块日志器 (学习行为事件)


def _learner(eng):
    """取(或惰性建)引擎上的道钉学习器。

    Args: eng: SimulationEngine。Returns: DeployQLearner 实例。
    """
    if eng.rl_deploy_learner is None:
        from ..rl import DeployQLearner   # 惰性: 首次落钉才建表 (默认零痕迹)
        eng.rl_deploy_learner = DeployQLearner()
    return eng.rl_deploy_learner


def _featurize(rb, nid: str) -> DeployState | None:
    """采集目标节点的离散化状态键 (全部本地可观测, 非上帝视角)。

    Args: rb: PatrolRobot; nid: 目标节点 id。Returns: DeployState 或 None
          (节点不存在)。Calls: rb.real_degree / eng.jam_lift_at。
    """
    eng = rb.eng
    n = eng.nodes.get(nid)
    if n is None:
        return None
    iso = rb._iso.get(nid, 0)                     # 连续失联 tick (assist 口常 0)
    iso_b = 0 if iso <= SOS_ARM_TICKS else (1 if iso <= RLD_ISO_MID_TICKS else 2)
    deg = rb.real_degree(nid)                     # 剔除机器人自身边的真实度数
    stock = rb.stock
    return DeployState(iso_b=iso_b, pboost=bool(n.power_boosted),
                       deg_b=0 if deg == 0 else (1 if deg == 1 else 2),
                       jam=eng.jam_lift_at(n.x, n.z) > 1.0,
                       stock_b=0 if stock <= 2 else (1 if stock <= 4 else 2))


def want_deploy(rb, tid: str | None, via: str) -> bool:
    """落钉问询门: 规则版即将落钉的瞬间问学习器投不投。

    Args: rb: PatrolRobot; tid: 任务目标 id (None=巡逻偶遇借道, 语义模糊
          保持规则版直投); via: "sos"(桥接口)/"assist"(加固口)。
    Returns: bool —— True=投 (调用方照常 _deploy_beacon), False=忍 (登记冷却)。
    Globals Used: RL_DEPLOY_ENABLED (经 eng.rl_deploy 读取)。
    Calls: _learner/_featurize/decide/note_deploy/note_wait。
    """
    eng = rb.eng
    if tid is None:
        return True                  # 巡逻偶遇借道: 语义模糊, 规则版直投 (零痕迹)
    learner = _learner(eng)          # 审计器: 无论开关, 任务落钉都登记结算窗口
    if not getattr(eng, "rl_deploy", RL_DEPLOY_ENABLED):
        learner.note_deploy(eng.tick, _next_beacon_id(rb), tid, via,
                            _featurize(rb, tid) or DeployState(0, False, 2, False, 2))
        return True                  # 开关关: 规则版恒投 (仅审计)
    if eng.tick < rb._deploy_wait_until.get(tid, 0):
        return False                 # 忍的冷却期内: 不问询, 直接不投
    state = _featurize(rb, tid)
    if state is None:
        return True
    if learner.decide(via, state):
        learner.note_deploy(eng.tick, _next_beacon_id(rb), tid, via, state)
        return True
    learner.note_wait(eng.tick, tid, via, state)
    rb._deploy_wait_until[tid] = eng.tick + RLD_GATE_RECONSIDER_TICKS
    log.info("道钉学习[忍] %s 目标=%s (ε=%.3f)", via, tid, learner.epsilon)
    eng._emit("deploy_rl_wait", "info",
              f"🤖 学习器选择观望: {tid} 可能自行恢复, 道钉留作他用 (库存 {rb.stock})",
              node=tid)
    return False


def _next_beacon_id(rb) -> str:
    """预测下一次落钉的道钉 id (deploy.py 的 BEACON-xx 编号规则)。"""
    return f"BEACON-{rb._deployed + 1:02d}"


def observe_learner(rb, tick: int) -> None:
    """每 tick 结算扫描 (挂 robot.tick 末尾, 无学习器时零开销):
    落钉窗口 -> 经钉(+good)/白扔(waste)/无效(late); 忍挂起 ->
    目标自行恢复(patient)/忍到超时(hesitated)。

    Args: rb: PatrolRobot; tick: 当前拍。Returns: None。
    Calls: learner.settle/_featurize。
    """
    eng = rb.eng
    learner = getattr(eng, "rl_deploy_learner", None)
    if learner is None:
        return
    for rec in list(learner.pending_deploys):
        _settle_deploy(rb, learner, rec, tick)
    for rec in list(learner.pending_waits):
        _settle_wait(rb, learner, rec, tick)


def _recovered(rb, nid: str) -> bool:
    """真·恢复判定: 目标可达 且 其路径不经机器人本体。
    机器人赶到附近时自身注入的临时边会让目标 hop>=0 —— 那是移动拐杖
    不是恢复, 据此结算会把「机器人路过」误判成「忍对了」。

    Args: rb: PatrolRobot; nid: 目标节点 id。Returns: bool。
    """
    r = rb.eng.routes.get(nid)
    return (r is not None and r.get("hop_count", -1) >= 0
            and "ROBOT" not in (r.get("path") or []))


def _settle_deploy(rb, learner, rec: dict, tick: int) -> None:
    """单条落钉窗口结算: 钉未兑现 -> 丢弃; 真恢复 -> 按路径经钉与否分
    好坏 (仅靠机器人本体维持的可达是暂态, 不结算); 窗口尽仍失联 -> 无效。
    Args: rb/learner/rec/tick。Returns: None。Calls: learner.settle/drop。
    """
    eng = rb.eng
    if rec["beacon_id"] not in eng.nodes:            # 预测 id 未兑现 (落钉失败)
        learner.drop(rec)
        return
    nid = rec["nid"]
    if _recovered(rb, nid):
        via_beacon = any(rec["beacon_id"] in (r.get("path") or [])
                         for t, r in eng.routes.items()
                         if t != "ROBOT" and r.get("hop_count", -1) >= 0
                         and "ROBOT" not in (r.get("path") or []))
        _reward_settle(rb, learner, rec, RLD_RW_GOOD if via_beacon else RLD_RW_WASTE,
                       "good" if via_beacon else "waste")
    elif tick - rec["tick"] >= RLD_WINDOW_TICKS:
        _reward_settle(rb, learner, rec, RLD_RW_LATE, "late")


def _settle_wait(rb, learner, rec: dict, tick: int) -> None:
    """单条忍挂起结算: 真自行恢复 (不经机器人) -> 忍对; 挂到救援超时 -> 忍晚。
    Args: rb/learner/rec/tick。Returns: None。Calls: _reward_settle。
    """
    if _recovered(rb, rec["nid"]):
        _reward_settle(rb, learner, rec, RLD_RW_PATIENT, "patient")
    elif tick - rec["tick"] > RESCUE_PATIENCE:
        _reward_settle(rb, learner, rec, RLD_RW_HESITATE, "hesitated")


def _reward_settle(rb, learner, rec: dict, reward: float, kind: str) -> None:
    """带结算时刻状态回填的结算包装 (状态不可得时用原状态, 保守传播)。"""
    nxt = _featurize(rb, rec["nid"]) or rec["state"]
    learner.settle(rec, reward, kind, nxt)


def wait_giveup(rb, tid: str) -> None:
    """任务放弃终局: 该目标全部忍挂起立即按「忍到放弃」结算
    (rescue._giveup 调用 —— 放弃事件对世界不可观测, 必须显式喂)。

    Args: rb: PatrolRobot; tid: 目标节点 id。Returns: None。
    """
    learner = getattr(rb.eng, "rl_deploy_learner", None)
    if learner is None:
        return
    for rec in [r for r in learner.pending_waits if r["nid"] == tid]:
        _reward_settle(rb, learner, rec, RLD_RW_HESITATE, "hesitated")
