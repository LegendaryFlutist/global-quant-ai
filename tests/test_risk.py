"""评分、波动仓位、减仓与执行接入的合成边界回归。"""

import unittest
import numpy as np
import pandas as pd
from quant_lab.risk.policy import RiskPolicy, load_risk_policy
from quant_lab.risk.sizing import position_budget
from quant_lab.risk.reductions import reduction_decision
from quant_lab.risk.scoring import daily_volatility, score_entries
from test_pattern_execution import candles, simulate


class RiskTests(unittest.TestCase):
    def test_budget_bounds_and_volatility(self):
        """更高波动压低杠杆及敞口，估计止损预算不超过1%。"""
        low = position_budget(95, 0.01, 0.02)
        high = position_budget(95, 0.04, 0.02)
        self.assertLess(high["leverage"], low["leverage"])
        self.assertLessEqual(high["nominal_exposure"], low["nominal_exposure"])
        for budget in (low, high):
            self.assertLessEqual(budget["estimated_stop_loss_fraction"], 0.01)
            self.assertLessEqual(budget["margin_fraction"], 0.2)
            self.assertLessEqual(budget["leverage"], 10)

    def test_score_drawdown_and_no_direction_block(self):
        """低质量、回撤触发和观望方向不允许分配仓位。"""
        for kwargs in ({"score": 40}, {"drawdown": 0.08}, {"direction": 0}):
            args = dict(score=90, daily_volatility=0.02, stop_fraction=0.03)
            args.update(kwargs)
            result = position_budget(**args)
            self.assertFalse(result["allowed"])
            self.assertEqual(result["nominal_exposure"], 0)

    def test_policy_and_invalid_inputs(self):
        """策略版本加载稳定，拒绝非有限数据和超限配置。"""
        self.assertEqual(load_risk_policy().to_dict(), RiskPolicy().to_dict())
        for kwargs in (
            {"max_leverage": 21},
            {"max_risk_fraction": 0.03},
            {"max_margin_fraction": float("nan")},
        ):
            with self.assertRaises(ValueError):
                RiskPolicy(**kwargs)
        with self.assertRaises(ValueError):
            position_budget(90, float("nan"), 0.03)

    def test_reductions_once_and_no_additions(self):
        """风险恶化优先，跳至2R只减一次且每次数量为正。"""
        args = dict(
            current_r=2.5,
            peak_r=2.5,
            score=90,
            entry_score=90,
            volatility=0.02,
            entry_volatility=0.02,
        )
        action = reduction_decision(**args)
        self.assertEqual(action["key"], "PROFIT_2R")
        self.assertEqual(action["fraction"], 0.5)
        self.assertIsNone(
            reduction_decision(**args, completed=["PROFIT_1R", "PROFIT_2R"])
        )
        self.assertEqual(
            reduction_decision(**{**args, "volatility": 0.04})["key"],
            "VOLATILITY_SPIKE",
        )
        self.assertEqual(
            reduction_decision(**{**args, "score": 60})["key"], "SCORE_DETERIORATION"
        )
        with self.assertRaises(ValueError):
            reduction_decision(**{**args, "score": float("nan")})

    def test_volatility_prefix_and_missing_bar(self):
        """未来追加不改变既有波动率；缺失K线不作为正常日窗口。"""
        bars = candles(count=150)
        bars["close"] = 100 + np.sin(np.arange(150))
        np.testing.assert_allclose(
            daily_volatility(bars).iloc[:120],
            daily_volatility(bars.iloc[:120]),
            equal_nan=True,
        )
        with self.assertRaises(ValueError):
            daily_volatility(bars.drop(index=40))

    def test_score_components_and_direction_gate(self):
        """模型支持不冒充胜率，大周期不支持时拦截方向。"""
        frame = pd.DataFrame(
            [
                dict(
                    higher_trend=1,
                    analysis_trend=1,
                    analysis_dow_event=1,
                    analysis_dow_state=1,
                    analysis_wave_bias=1,
                    bullish_count=2,
                    bearish_count=0,
                    swing_range_pct=0.1,
                )
            ]
        )
        result = score_entries(frame, [[0.1, 0.1, 0.8]]).iloc[0]
        self.assertEqual(result.score, 90)
        self.assertEqual(result.direction, 1)
        self.assertFalse(result.probabilities_calibrated)
        frame.higher_trend = -1
        self.assertEqual(score_entries(frame, [[0.1, 0.1, 0.8]]).iloc[0].direction, 0)
        with self.assertRaises(ValueError):
            score_entries(frame, [[0.1, 0.1, 0.9]])

    def test_execution_sizing_and_ledger(self):
        """动态预算实际改变成交数量，费用后账本与权益一致。"""
        data = candles(count=8)
        plan = pd.DataFrame(
            {"close_time": data.close_time, "score": 90.0, "daily_volatility": 0.02}
        )
        result = simulate(
            data,
            [1, 0, 0, 0, 0, 0, 0, 0],
            risk_policy=RiskPolicy(),
            risk_plan=plan,
            risk_reductions=True,
        )
        trade = result["trades"].iloc[0]
        self.assertGreater(trade.leverage_at_entry, 0)
        self.assertLessEqual(trade.risk_budget_fraction, 0.01)
        self.assertAlmostEqual(trade.net_pnl, result["metrics"]["net_return"])
        self.assertEqual(result["metrics"]["leverage_mode"], "SCORE_VOLATILITY")

    def test_execution_low_score_warmup_and_alignment(self):
        """拒绝低分/零波动和错位决策，不偷用下一行评分。"""
        data = candles(count=5)
        plan = pd.DataFrame(
            {
                "close_time": data.close_time,
                "score": [30, 100, 100, 100, 100],
                "daily_volatility": 0.02,
            }
        )
        self.assertEqual(
            simulate(data, [1, 0, 0, 0, 0], risk_policy=RiskPolicy(), risk_plan=plan)[
                "metrics"
            ]["entries"],
            0,
        )
        plan.score = 90
        plan.daily_volatility = 0
        self.assertEqual(
            simulate(data, [1, 0, 0, 0, 0], risk_policy=RiskPolicy(), risk_plan=plan)[
                "metrics"
            ]["entries"],
            0,
        )
        plan.close_time += 1
        with self.assertRaises(ValueError):
            simulate(data, [1, 0, 0, 0, 0], risk_policy=RiskPolicy(), risk_plan=plan)

    def test_reductions_accounting_and_past_only(self):
        """减仓发生在后续开盘，未来评分不改变更早成交，权益包含全部部分退出。"""
        data = candles(
            [
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (102.5, 103, 102, 102.5),
                (104, 105, 103, 104),
                (103, 104, 102, 103),
                (102, 103, 101, 102),
            ]
        )
        plan = pd.DataFrame(
            {"close_time": data.close_time, "score": 90.0, "daily_volatility": 0.02}
        )
        kwargs = dict(
            risk_policy=RiskPolicy(),
            risk_plan=plan,
            risk_reductions=True,
            exit_mode="time_exit",
        )
        result = simulate(data, [1, 0, 0, 0, 0, 0], **kwargs)
        self.assertGreater(result["metrics"]["partial_exit_count"], 0)
        self.assertAlmostEqual(
            result["metrics"]["net_return"], result["trades"].net_pnl.sum()
        )
        trade = result["trades"].iloc[0]
        self.assertLessEqual(
            trade.partial_exit_quantity + trade.final_exit_quantity,
            abs(trade.initial_quantity) + 1e-10,
        )
        changed = plan.copy()
        changed.loc[4:, "score"] = 10
        later = simulate(data, [1, 0, 0, 0, 0, 0], **{**kwargs, "risk_plan": changed})
        np.testing.assert_allclose(
            result["equity"].equity.iloc[:4], later["equity"].equity.iloc[:4]
        )

    def test_maintenance_assumption_must_match(self):
        """仓位近似维持保证金与执行引擎不同必须拒绝，避免虚假安全余量。"""
        data = candles()
        plan = pd.DataFrame({"score": 90.0, "daily_volatility": 0.02}, index=data.index)
        with self.assertRaises(ValueError):
            simulate(
                data,
                [1, 0, 0, 0, 0],
                risk_policy=RiskPolicy(),
                risk_plan=plan,
                maintenance_rate=0.02,
            )
