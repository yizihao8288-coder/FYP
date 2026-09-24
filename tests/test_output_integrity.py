from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from csi300_events.methodology import load_methodology_config


class RealOutputIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.config = load_methodology_config(cls.root / "config" / "methodology_config.json")
        cls.output = cls.root / "outputs" / "hfq_final"
        cls.events = pd.read_csv(
            cls.output / "sample_hfq_final.csv", dtype={"stock_code": "string"}
        )
        for column in (
            "event_date",
            "base_period_start",
            "base_period_end",
            "peak_date",
            "resistance_date",
            "R3_target_date",
            "R5_target_date",
            "R20_target_date",
            "R60_target_date",
        ):
            cls.events[column] = pd.to_datetime(cls.events[column])

    def test_event_boundaries_and_frozen_thresholds(self) -> None:
        events = self.events
        normalized_columns = events.columns.str.casefold()
        self.assertFalse(normalized_columns.duplicated().any())
        self.assertTrue(events["event_date"].between("2016-01-01", "2026-06-08").all())
        self.assertTrue((events["base_period_end"] < events["event_date"]).all())
        self.assertTrue((events["peak_date"] < events["base_period_start"]).all())
        self.assertTrue(events["Duration"].between(120, 252).all())
        self.assertTrue((events["bandwidth"] <= 0.25 + 1e-12).all())
        self.assertTrue((events["trend_slope"].abs() <= 0.0004 + 1e-12).all())
        self.assertTrue((events["Drawdown"] <= -0.30 + 1e-12).all())
        self.assertTrue((events["BreakoutStrength"] >= 0.005 - 1e-12).all())
        self.assertTrue(
            np.allclose(
                events["resistance"],
                events["breakout_price"] / (1 + events["BreakoutStrength"]),
            )
        )

    def test_membership_st_and_data_quality(self) -> None:
        events = self.events
        self.assertTrue(events["CSI300_member_t_minus_1"].eq(1).all())
        self.assertTrue(events["final_ST_flag"].eq(0).all())
        self.assertTrue(events["consolidation_ST_days"].eq(0).all())
        self.assertTrue((events["valid_price_ratio"] >= 0.95).all())
        self.assertTrue((events["max_consecutive_suspension"] <= 5).all())
        self.assertTrue((events["peak_window_valid_ratio"] >= 0.95).all())
        self.assertTrue(events["sample_price_source"].eq("eastmoney_hfq").all())

    def test_cooldown_uses_unified_market_calendar(self) -> None:
        calendar = pd.read_csv(self.root / "data" / "processed" / "trading_calendar.csv")
        calendar["trade_date"] = pd.to_datetime(calendar["trade_date"])
        position = {date: index for index, date in enumerate(calendar["trade_date"])}
        for _, group in self.events.groupby("stock_code"):
            positions = sorted(position[date] for date in group["event_date"])
            self.assertTrue(
                all(right - left > 60 for left, right in zip(positions, positions[1:]))
            )

    def test_return_columns_and_statuses_reconcile(self) -> None:
        events = self.events
        for horizon in (3, 5, 20, 60):
            value = f"R{horizon}"
            status = f"R{horizon}_status"
            target = f"R{horizon}_target_date"
            self.assertTrue({value, status, target}.issubset(events.columns))
            self.assertTrue(
                events[status]
                .isin(
                    {
                        "available",
                        "calendar_not_yet_available",
                        "target_price_or_trade_missing",
                    }
                )
                .all()
            )
            self.assertTrue(events.loc[events[status].eq("available"), value].notna().all())
            self.assertTrue(events.loc[~events[status].eq("available"), value].isna().all())
            self.assertFalse(events[status].eq("calendar_not_yet_available").any())
        self.assertTrue(
            np.allclose(
                events["RelativeVolume"],
                events["relative_volume60"],
                equal_nan=True,
            )
        )
        self.assertFalse(events.duplicated(["stock_code", "event_date"]).any())

    def test_summary_and_model_counts_reconcile(self) -> None:
        summary = json.loads(
            (self.output / "hfq_final_summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["event_start"], "2016-01-01")
        self.assertEqual(summary["event_end"], "2026-06-08")
        self.assertEqual(summary["price_target_end"], "2026-09-01")
        self.assertEqual(
            summary["latest_event_date_with_full_calendar_R60"], "2026-06-08"
        )
        self.assertTrue(summary["all_allowed_event_dates_have_full_calendar_R60"])
        self.assertEqual(summary["primary_return_horizon"], 20)
        self.assertEqual(summary["final_main_event_count"], len(self.events))
        counts = pd.read_csv(self.output / "model_sample_counts.csv")
        r20 = counts.loc[counts["dependent_variable"].eq("R20")].iloc[0]
        regression = pd.read_csv(self.output / "regression_sample_R20.csv")
        self.assertEqual(int(r20["complete_case_count"]), len(regression))


if __name__ == "__main__":
    unittest.main()
