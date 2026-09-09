# -*- coding: utf-8 -*-
"""
道钉投/忍学习器与问询门单测
================================
覆盖: Q 表贪心决策 / 六分支结算矩阵 / 忍取代中性结算 / 门控规则模式
审计 / 忍冷却 / 引擎跨 reset 保留学习器 / 开关关闭弃表。
运行: cd backend && python -m unittest tests.test_q_deploy
"""
import unittest   # 标准库: 测试框架

from sim.config import RLD_RW_GOOD, RLD_RW_HESITATE, RLD_RW_LATE   # 奖励常量
from sim.config import RLD_RW_PATIENT, RLD_RW_WASTE                # 奖励常量
from sim.rl import DeployQLearner          # 被测: 道钉学习器
from sim.robot import rl_gate              # 被测: 问询门/结算扫描
from sim.types import DeployState          # 契约: 状态元组

S = DeployState(iso_b=1, pboost=True, deg_b=0, jam=False, stock_b=1)


class _Node:   # 测试桩: 仅携带特征采集所需字段
    def __init__(self, x=0.0, z=0.0, boosted=False):
        self.x, self.z = x, z
        self.power_boosted = boosted


class _Eng:    # 测试桩: 引擎公开状态的最小面 (routes/nodes/tick/jam/emit)
    def __init__(self, routes=None, nodes=None):
        self.routes = routes or {}
        self.nodes = nodes or {}
        self.tick = 0
        self.rl_deploy = False
        self.rl_deploy_learner = None

    def jam_lift_at(self, x, z):   # 无干扰源场景
        return 0.0

    def _emit(self, *a, **k):      # 事件总线桩: 丢弃
        pass


class _Robot:   # 测试桩: PatrolRobot 的公开面
    def __init__(self, eng, stock=4, degrees=None, iso=None):
        self.eng = eng
        self.stock = stock
        self._degrees = degrees or {}
        self._iso = iso or {}
        self._deploy_wait_until = {}
        self._deployed = 0

    def real_degree(self, nid):
        return self._degrees.get(nid, 2)


class TestLearner(unittest.TestCase):
    """纯学习器: 贪心决策与结算矩阵。"""

    def test_greedy_follows_q(self):
        """ε=0 时贪心跟随 Q 值: wait 占优 -> 忍; invest 占优 -> 投。"""
        ln = DeployQLearner()
        ln.epsilon = 0.0
        ln.q["sos"][S] = [0.0, 1.0]
        self.assertFalse(ln.decide("sos", S))
        ln.q["sos"][S] = [1.0, 0.0]
        self.assertTrue(ln.decide("sos", S))
        self.assertEqual(ln.picks, {"invest": 1, "wait": 1})

    def test_settle_matrix(self):
        """六类结算的计数与 TD 方向 (正奖励抬/负奖励压对应动作 Q)。"""
        ln = DeployQLearner()
        for i, (kind, rw) in enumerate((("good", RLD_RW_GOOD),
                                        ("waste", RLD_RW_WASTE),
                                        ("late", RLD_RW_LATE))):
            st = DeployState(iso_b=i, pboost=False, deg_b=i, jam=False, stock_b=i)
            ln.note_deploy(0, f"BEACON-0{i}", "N", "sos", st)
            ln.settle(ln.pending_deploys[0], rw, kind, S)
            expect_up = rw > 0
            self.assertTrue(ln.q["sos"][st][0] > 0 if expect_up
                            else ln.q["sos"][st][0] < 0, kind)
        for kind, rw in (("patient", RLD_RW_PATIENT),
                         ("hesitated", RLD_RW_HESITATE)):
            ln.note_wait(0, "N", "sos", S)
            ln.settle(ln.pending_waits[0], rw, kind, S)
        self.assertEqual(ln.settled, {"good": 1, "waste": 1, "late": 1,
                                      "patient": 1, "hesitated": 1})
        self.assertFalse(ln.pending_deploys or ln.pending_waits)

    def test_wait_supersede_neutral(self):
        """同目标再忍: 旧忍被中性结算 (不计数, r=0 仅价值传播)。"""
        ln = DeployQLearner()
        ln.note_wait(0, "N", "sos", S)
        ln.note_wait(10, "N", "sos", S)
        self.assertEqual(len(ln.pending_waits), 1)
        self.assertEqual(sum(ln.settled.values()), 0)   # 无胜负计数
        self.assertEqual(len(ln.history), 1)            # 但有一条 r=0 历史

    def test_drop_unrealized(self):
        """预测钉 id 未兑现的窗口可直接丢弃。"""
        ln = DeployQLearner()
        ln.note_deploy(0, "BEACON-99", "N", "sos", S)
        ln.drop(ln.pending_deploys[0])
        self.assertFalse(ln.pending_deploys)


class TestGate(unittest.TestCase):
    """问询门: 规则模式审计 / 忍冷却 / 结算扫描六分支。"""

    def _mk(self, hop=-1, path=None, rl=False):
        routes = {"NODE-07": {"hop_count": hop, "path": path or []}}
        nodes = {"NODE-07": _Node(boosted=True), "BEACON-01": _Node()}
        eng = _Eng(routes, nodes)
        eng.rl_deploy = rl
        return eng, _Robot(eng, degrees={"NODE-07": 1}, iso={"NODE-07": 30})

    def test_rule_mode_always_invest_with_audit(self):
        """开关关: 恒投 (规则零回归), 但落钉仍登记审计窗口。"""
        eng, rb = self._mk()
        self.assertTrue(rl_gate.want_deploy(rb, "NODE-07", "sos"))
        self.assertEqual(len(eng.rl_deploy_learner.pending_deploys), 1)

    def test_none_tid_direct_invest_no_audit(self):
        """巡逻偶遇借道 (无目标): 直投且不登记 (审计只对任务落钉)。"""
        eng, rb = self._mk()
        self.assertTrue(rl_gate.want_deploy(rb, None, "sos"))
        self.assertIsNone(eng.rl_deploy_learner)

    def test_wait_decision_and_cooldown(self):
        """开关开+学习器恒忍: 首问登记冷却, 冷却期内不再消耗决策。"""
        eng, rb = self._mk(rl=True)
        eng.rl_deploy_learner = DeployQLearner()
        eng.rl_deploy_learner.decide = lambda *a: False   # 桩: 恒忍
        self.assertFalse(rl_gate.want_deploy(rb, "NODE-07", "sos"))
        n = eng.rl_deploy_learner.n_choices
        eng.tick += 5   # 冷却 12 拍内
        self.assertFalse(rl_gate.want_deploy(rb, "NODE-07", "sos"))
        self.assertEqual(eng.rl_deploy_learner.n_choices, n)   # 未再决策

    def test_observe_good_waste_late(self):
        """落钉窗口三分支: 路径经钉=good / 恢复不经钉=waste / 窗口尽=late。"""
        eng, rb = self._mk(hop=1, path=["NODE-07", "BEACON-01", "NODE-00"])
        rl_gate.want_deploy(rb, "NODE-07", "sos")           # 规则模式登记
        rl_gate.observe_learner(rb, 1)
        self.assertEqual(eng.rl_deploy_learner.settled["good"], 1)
        eng2, rb2 = self._mk(hop=1, path=["NODE-07", "NODE-00"])
        rl_gate.want_deploy(rb2, "NODE-07", "sos")
        rl_gate.observe_learner(rb2, 1)
        self.assertEqual(eng2.rl_deploy_learner.settled["waste"], 1)
        eng3, rb3 = self._mk(hop=-1)
        rl_gate.want_deploy(rb3, "NODE-07", "sos")
        rl_gate.observe_learner(rb3, 41)                     # 窗口 40 拍尽
        self.assertEqual(eng3.rl_deploy_learner.settled["late"], 1)

    def test_observe_wait_patient_and_giveup(self):
        """忍挂起两分支: 真自行恢复=patient / wait_giveup=hesitated;
        仅经机器人本体的可达是暂态, 不得误判 patient。"""
        eng, rb = self._mk(hop=-1, rl=True)
        eng.rl_deploy_learner = DeployQLearner()
        eng.rl_deploy_learner.note_wait(0, "NODE-07", "sos", S)
        eng.routes["NODE-07"].update(hop_count=1,
                                     path=["NODE-07", "ROBOT", "NODE-00"])
        rl_gate.observe_learner(rb, 5)               # 机器人路过: 不结算
        self.assertEqual(sum(eng.rl_deploy_learner.settled.values()), 0)
        eng.routes["NODE-07"].update(hop_count=1,
                                     path=["NODE-07", "NODE-00"])
        rl_gate.observe_learner(rb, 6)               # 真恢复: patient
        self.assertEqual(eng.rl_deploy_learner.settled["patient"], 1)
        eng2, rb2 = self._mk(hop=-1, rl=True)
        eng2.rl_deploy_learner = DeployQLearner()
        eng2.rl_deploy_learner.note_wait(0, "NODE-07", "sos", S)
        rl_gate.wait_giveup(rb2, "NODE-07")          # 任务放弃终局
        self.assertEqual(eng2.rl_deploy_learner.settled["hesitated"], 1)

    def test_observe_tolerates_missing_node(self):
        """结算时刻目标节点消失: 用原状态保守传播, 不抛异常。"""
        eng, rb = self._mk(hop=-1)
        rl_gate.want_deploy(rb, "NODE-07", "sos")
        del eng.nodes["NODE-07"]
        rl_gate.observe_learner(rb, 41)
        self.assertEqual(eng.rl_deploy_learner.settled["late"], 1)


class TestEngineRetention(unittest.TestCase):
    """引擎级: 学习器跨 reset 保留 / 开关关闭弃表。"""

    def test_reset_keeps_learner_toggle_drops(self):
        """reset 不弃表/不关开关 (跨世界迁移); toggle 关闭显式弃表。"""
        from sim.engine import SimulationEngine   # 局部导入: 构建完整世界 (重)
        eng = SimulationEngine()
        eng.rl_deploy_learner = object()
        eng.rl_deploy = True
        eng.reset()
        self.assertIsNotNone(eng.rl_deploy_learner)   # reset 保留学习器
        self.assertTrue(eng.rl_deploy)                # reset 保留开关
        eng.toggle_rl_deploy()                        # True -> False: 弃表
        self.assertFalse(eng.rl_deploy)
        self.assertIsNone(eng.rl_deploy_learner)
        eng2 = SimulationEngine()                     # 全新实例不应继承他进程态
        self.assertIsNone(eng2.rl_deploy_learner)


if __name__ == "__main__":
    unittest.main()
