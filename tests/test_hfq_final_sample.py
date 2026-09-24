from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from csi300_events.adaptive_pipeline import _build_context
from csi300_events.hfq_final_sample import (
    ConsolidationVersion,
    _candidate_metrics,
    _detect_consolidation,
    _future_return_metrics,
    _make_linear_state,
    load_final_config,
)
from csi300_events.methodology import load_methodology_config


class FinalHFQTrendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.methodology = load_methodology_config(
            cls.root / "config" / "methodology_config.json"
        )
        cls.main = ConsolidationVersion("A_main", 0.25, 0.0004)

    def _context(
        self, recent_prices: np.ndarray, earlier_prices: np.ndarray | None = None
    ):
        dates = pd.bdate_range("2019-01-01", periods=700)
        event_position = 650
        close = np.full(len(dates), 100.0)
        if earlier_prices is not None:
            close[
                event_position - 120 - len(earlier_prices) : event_position - 120
            ] = earlier_prices
        close[event_position - 120 : event_position] = recent_prices
        close[event_position] = float(np.nanmax(recent_prices) * 1.01)
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
                "tradestatus": 1,
                "isST": 0,
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
        return context, _make_linear_state(context), event_position

    def test_config_freezes_hfq_and_requested_versions(self) -> None:
        raw, _ = load_final_config(self.root / "config" / "hfq_final_sample.json")
        self.assertEqual(raw["sample_price_source"], "eastmoney_hfq")
        self.assertEqual(raw["versions"]["A_main"]["trend_slope_threshold"], 0.0004)
        self.assertEqual(raw["event_start"], "2016-01-01")
        self.assertEqual(raw["event_end"], "2026-06-08")
        self.assertEqual(raw["return_horizons"], [3, 5, 20, 60])
        self.assertEqual(raw["primary_return_horizon"], 20)

    def test_future_returns_use_exact_market_day_positions(self) -> None:
        context, _, event_position = self._context(np.full(120, 100.0))
        context.close[event_position] = 100.0
        context.close[event_position + 3] = 103.0
        context.close[event_position + 5] = 105.0
        result = _future_return_metrics(context, event_position, [3, 5, 60])
        self.assertAlmostEqual(result["R3"], 0.03)
        self.assertAlmostEqual(result["R5"], 0.05)
        self.assertEqual(result["R3_status"], "available")
        self.assertEqual(result["R60_status"], "calendar_not_yet_available")

    def test_future_return_does_not_fill_a_missing_target_price(self) -> None:
        context, _, event_position = self._context(np.full(120, 100.0))
        context.valid_price[event_position + 3] = False
        result = _future_return_metrics(context, event_position, [3])
        self.assertTrue(np.isnan(result["R3"]))
        self.assertEqual(result["R3_status"], "target_price_or_trade_missing")

    def test_slow_uptrend_fails_even_when_bandwidth_passes(self) -> None:
        context, state, event_position = self._context(np.linspace(100.0, 120.0, 120))
        metrics = _candidate_metrics(
            context, state, event_position - 120, event_position - 1, self.methodology
        )
        self.assertLessEqual(metrics["bandwidth"], 0.25)
        self.assertGreater(abs(metrics["trend_slope"]), 0.0004)
        self.assertIsNone(
            _detect_consolidation(
                context, state, event_position, self.main, self.methodology
            )
        )

    def test_slow_downtrend_fails(self) -> None:
        context, state, event_position = self._context(np.linspace(100.0, 85.0, 120))
        metrics = _candidate_metrics(
            context, state, event_position - 120, event_position - 1, self.methodology
        )
        self.assertLessEqual(metrics["bandwidth"], 0.25)
        self.assertLess(metrics["trend_slope"], -0.0004)

    def test_horizontal_oscillation_passes_and_endpoint_return_is_explicit(self) -> None:
        prices = 100.0 + 4.0 * np.sin(np.linspace(0, 8 * np.pi, 120))
        context, state, event_position = self._context(prices)
        metrics = _candidate_metrics(
            context, state, event_position - 120, event_position - 1, self.methodology
        )
        expected_endpoint = prices[-1] / prices[0] - 1.0
        self.assertLessEqual(abs(metrics["trend_slope"]), 0.0004)
        self.assertAlmostEqual(metrics["endpoint_return"], expected_endpoint)
        self.assertIsNotNone(
            _detect_consolidation(
                context, state, event_position, self.main, self.methodology
            )
        )

    def test_trend_slope_is_always_the_explicit_recent_120_day_regression(self) -> None:
        context, state, event_position = self._context(
            np.full(120, 100.0),
            earlier_prices=np.linspace(80.0, 100.0, 132),
        )
        detected = _detect_consolidation(
            context, state, event_position, self.main, self.methodology
        )
        self.assertIsNotNone(detected)
        self.assertEqual(detected["duration"], 252)
        self.assertAlmostEqual(detected["trend_slope"], 0.0)


if __name__ == "__main__":
    unittest.main()
