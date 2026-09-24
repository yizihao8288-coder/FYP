from __future__ import annotations

import unittest

import pandas as pd

from csi300_events.sample_quality_audit import (
    _duplicate_outputs,
    _gap_bucket,
    _near_straddle,
    _primary_difference_reason,
)


class SampleQualityAuditUnitTests(unittest.TestCase):
    def test_threshold_near_requires_a_cross_source_straddle(self) -> None:
        self.assertTrue(_near_straddle(0.24, 0.251, 0.25, 0.02, "le"))
        self.assertFalse(_near_straddle(0.20, 0.24, 0.25, 0.02, "le"))
        self.assertTrue(_near_straddle(0.004, 0.006, 0.005, 0.005, "ge"))

    def test_primary_reason_uses_the_failed_frozen_rule(self) -> None:
        self.assertEqual(
            _primary_difference_reason("distress_drawdown_failed", True),
            "Drawdown 阈值判定差异",
        )
        self.assertEqual(
            _primary_difference_reason("invalid_peak_window_data", False),
            "BaoStock 数据缺失",
        )

    def test_gap_buckets_preserve_boundaries(self) -> None:
        self.assertEqual(_gap_bucket(20), "≤20 trading days")
        self.assertEqual(_gap_bucket(21), "21–60")
        self.assertEqual(_gap_bucket(60), "21–60")
        self.assertEqual(_gap_bucket(61), "61–120")
        self.assertEqual(_gap_bucket(252), "121–252")
        self.assertEqual(_gap_bucket(253), ">252")

    def test_duplicate_overlap_is_flagged_without_deletion(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=400)
        events = pd.DataFrame(
            {
                "stock_code": ["sh.600000", "sh.600000"],
                "stock_name": ["测试", "测试"],
                "event_date": [dates[200], dates[280]],
                "ConsolidationStart": [dates[80], dates[150]],
                "ConsolidationEnd": [dates[199], dates[279]],
                "PeakDate": [dates[30], dates[30]],
            }
        )
        detail, summary = _duplicate_outputs(events, dates)
        self.assertEqual(len(detail), 1)
        self.assertEqual(int(detail.iloc[0]["trading_day_gap"]), 80)
        self.assertEqual(int(detail.iloc[0]["consolidation_overlap"]), 1)
        self.assertEqual(int(detail.iloc[0]["same_episode_suspected"]), 1)
        self.assertEqual(int(summary["adjacent_event_pair_count"].sum()), 1)


if __name__ == "__main__":
    unittest.main()
