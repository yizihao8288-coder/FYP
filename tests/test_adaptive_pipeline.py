from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from csi300_events.adaptive_pipeline import (
    _apply_cooldown,
    _build_context,
    _evaluate_after_recent_filters,
    _membership_at_t_minus_1,
    _recent_120_metrics,
)
from csi300_events.methodology import load_methodology_config


class AdaptiveEventPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_methodology_config(
            Path(__file__).resolve().parents[1] / "config" / "methodology_config.json"
        )
        cls.baseline = cls.config.specifications["baseline"]

    def _synthetic_context(
        self,
        *,
        suspended_positions: tuple[int, ...] = (),
        st_positions: tuple[int, ...] = (),
        future_close: float = 60.0,
    ):
        dates = pd.bdate_range("2008-01-01", periods=900)
        event_position = 800
        close = np.full(len(dates), 100.0)
        # The last passing interval is [t-150, t-1].  Adding t-151 introduces
        # the 100 price and must immediately stop the backward expansion.
        close[event_position - 150 : event_position] = 60.0
        close[event_position] = 61.0
        close[event_position + 1 :] = future_close
        status = np.ones(len(dates), dtype=int)
        is_st = np.zeros(len(dates), dtype=int)
        for position in suspended_positions:
            status[position] = 0
            close[position] = np.nan
        for position in st_positions:
            is_st[position] = 1

        daily = pd.DataFrame(
            {
                "stock_code": "sh.600000",
                "trade_date": dates,
                "adjusted_close": close,
                "adjusted_high": close,
                "adjusted_low": close,
                "close": close,
                "volume": 1_000_000.0,
                "turnover_rate": 1.0,
                "tradestatus": status,
                "isST": is_st,
            }
        )
        calendar = pd.DataFrame({"trade_date": dates})
        membership = pd.DataFrame(
            {
                "stock_code": ["sh.600000"],
                "in_date": [dates[0]],
                "out_date": [pd.NaT],
            }
        )
        histories = {
            "sh.600000": (
                np.array([dates[0].to_datetime64()]),
                np.array(["浦发银行"], dtype=object),
            )
        }
        context, _ = _build_context(daily, calendar, membership, histories)
        return context, event_position

    def test_adaptive_duration_and_nonoverlapping_peak_window(self) -> None:
        context, event_position = self._synthetic_context()
        record, outcome, _ = _evaluate_after_recent_filters(
            context, event_position, self.baseline, self.config
        )
        self.assertEqual(outcome, "selected_before_cooldown")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["ConsolidationDuration"], 150)
        self.assertEqual(record["ConsolidationStart"], context.dates[event_position - 150])
        self.assertEqual(record["ConsolidationEnd"], context.dates[event_position - 1])
        self.assertLess(record["PeakDate"], record["ConsolidationStart"])
        self.assertEqual(record["Resistance"], 60.0)
        self.assertAlmostEqual(record["BreakoutStrength"], 61.0 / 60.0 - 1.0)
        self.assertAlmostEqual(record["DistressDrawdown"], -0.40)

    def test_future_prices_cannot_change_event_at_t(self) -> None:
        first, event_position = self._synthetic_context(future_close=1.0)
        second, _ = self._synthetic_context(future_close=1_000.0)
        record_a, outcome_a, _ = _evaluate_after_recent_filters(
            first, event_position, self.baseline, self.config
        )
        record_b, outcome_b, _ = _evaluate_after_recent_filters(
            second, event_position, self.baseline, self.config
        )
        self.assertEqual(outcome_a, outcome_b)
        assert record_a is not None and record_b is not None
        for column in (
            "ConsolidationStart",
            "ConsolidationDuration",
            "Resistance",
            "PeakDate",
            "PeakPrice",
            "DistressDrawdown",
            "BreakoutStrength",
        ):
            self.assertEqual(record_a[column], record_b[column])

    def test_six_market_day_suspension_is_not_skipped(self) -> None:
        positions = tuple(range(700, 706))
        context, event_position = self._synthetic_context(suspended_positions=positions)
        recent = _recent_120_metrics(context, event_position, self.config)
        self.assertAlmostEqual(recent["ratio"], 0.95)
        self.assertTrue(recent["long_suspension"])
        self.assertFalse(recent["passes"])

    def test_any_st_day_from_consolidation_start_through_t_excludes_event(self) -> None:
        context, event_position = self._synthetic_context(
            st_positions=(700,)
        )
        record, outcome, details = _evaluate_after_recent_filters(
            context, event_position, self.baseline, self.config
        )
        self.assertIsNone(record)
        self.assertEqual(outcome, "ST_during_consolidation_or_event")
        self.assertEqual(details, {"st_days": 1})

    def test_membership_uses_previous_market_day(self) -> None:
        context, event_position = self._synthetic_context()
        self.assertTrue(context.member_t_minus_1[event_position])
        dates = context.dates
        ended_on_t_minus_1 = pd.DataFrame(
            {
                "in_date": [dates[0]],
                "out_date": [dates[event_position - 1]],
            }
        )
        result = _membership_at_t_minus_1(dates, ended_on_t_minus_1)
        # Membership intervals use an exclusive out_date.  If removal already
        # took effect on t-1, the stock cannot enter the event universe at t.
        self.assertFalse(result[event_position])

    def test_cooldown_uses_market_calendar_positions(self) -> None:
        records = [
            {"_calendar_index": 800},
            {"_calendar_index": 860},
            {"_calendar_index": 861},
        ]
        kept = _apply_cooldown(records, self.config.cooldown_market_days)
        self.assertEqual([row["_calendar_index"] for row in kept], [800, 861])


if __name__ == "__main__":
    unittest.main()
