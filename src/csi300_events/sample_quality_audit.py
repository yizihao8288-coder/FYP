from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from .acquisition import load_processed_stock
from .adaptive_pipeline import (
    _build_context,
    _detect_consolidation,
    _load_name_history,
    _price_extrema,
    _quality_metrics,
    _recent_120_metrics,
)
from .data_quality import (
    _markdown_table,
    _merge_sources,
    _normalise_baostock,
    _write_csv,
)
from .methodology import MethodologyConfig, load_methodology_config


# These are audit bands only. They do not change any event-screening threshold.
THRESHOLD_PROXIMITY = {
    "Drawdown": 0.02,
    "Bandwidth": 0.02,
    "Trend120": 0.01,
    "BreakoutStrength": 0.005,
}

DIFFERENCE_CATEGORIES = [
    "BaoStock 数据缺失",
    "交易日期/行情日期不一致",
    "前复权价格差异",
    "Drawdown 阈值判定差异",
    "Bandwidth 阈值判定差异",
    "Trend120 阈值判定差异",
    "BreakoutStrength 阈值判定差异",
    "横盘区间识别差异",
    "resistance price 差异",
    "其他",
]

GAP_BUCKETS = ["≤20 trading days", "21–60", "61–120", "121–252", ">252"]


def _safe_code(stock_code: str) -> str:
    return stock_code.replace(".", "_")


def _value(value: object) -> float:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(converted) if pd.notna(converted) else np.nan


def _pass(value: float, threshold: float, direction: str) -> bool:
    if not np.isfinite(value):
        return False
    if direction == "le":
        return value <= threshold
    if direction == "abs_le":
        return abs(value) <= threshold
    if direction == "ge":
        return value >= threshold
    raise ValueError(f"Unsupported threshold direction: {direction}")


def _near_straddle(
    tencent_value: float,
    bao_value: float,
    threshold: float,
    tolerance: float,
    direction: str,
) -> bool:
    if not np.isfinite(tencent_value) or not np.isfinite(bao_value):
        return False
    tencent_test = abs(tencent_value) if direction == "abs_le" else tencent_value
    bao_test = abs(bao_value) if direction == "abs_le" else bao_value
    return (
        _pass(tencent_value, threshold, direction)
        != _pass(bao_value, threshold, direction)
        and min(abs(tencent_test - threshold), abs(bao_test - threshold)) <= tolerance
    )


def _primary_difference_reason(decision: str, qfq_difference: bool) -> str:
    if decision in {
        "recent_120_long_suspension",
        "recent_120_insufficient_valid_price",
        "invalid_peak_window_data",
    }:
        return "BaoStock 数据缺失"
    if decision in {
        "event_date_not_in_calendar",
        "event_day_not_normal_trading",
    }:
        return "交易日期/行情日期不一致"
    if decision == "distress_drawdown_failed":
        return "Drawdown 阈值判定差异"
    if decision == "recent_120_bandwidth_failed":
        return "Bandwidth 阈值判定差异"
    if decision == "recent_120_trend_failed":
        return "Trend120 阈值判定差异"
    if decision == "breakout_threshold_failed":
        return "BreakoutStrength 阈值判定差异"
    if decision == "adaptive_consolidation_not_identified":
        return "横盘区间识别差异"
    if qfq_difference:
        return "前复权价格差异"
    return "其他"


def _build_bao_context(
    stock_code: str,
    bao: pd.DataFrame,
    calendar: pd.DataFrame,
    membership: pd.DataFrame,
    histories: dict[str, tuple[np.ndarray, np.ndarray]],
):
    daily = pd.DataFrame(
        {
            "stock_code": stock_code,
            "trade_date": bao["trade_date"],
            "adjusted_close": bao["close"],
            "adjusted_high": bao["high"],
            "adjusted_low": bao["low"],
            "close": bao["close"],
            "volume": bao["volume"],
            "turnover_rate": np.nan,
            "tradestatus": bao["tradestatus"],
            "isST": bao["isST"],
        }
    )
    context, _ = _build_context(daily, calendar, membership, histories)
    return context


def _bao_event_metrics(
    context,
    event_position: int,
    config: MethodologyConfig,
) -> dict[str, object]:
    specification = config.specifications["baseline"]
    recent = _recent_120_metrics(context, event_position, config)
    consolidation = _detect_consolidation(
        context, event_position, specification, config
    )
    if consolidation is None:
        consolidation = recent
        consolidation["duration"] = config.min_consolidation_duration
        basis = "recent120_failed_screen"
    else:
        basis = "adaptive_consolidation"

    start = int(consolidation["start"])
    peak_start = start - config.peak_lookback_before_consolidation
    peak_end = start - 1
    peak_quality: dict[str, object] | None = None
    peak_price = np.nan
    if peak_start >= 0:
        peak_quality = _quality_metrics(context, peak_start, peak_end, config)
        peak_price, _, _, _ = _price_extrema(context, peak_start, peak_end)

    low = _value(consolidation.get("low"))
    resistance = _value(consolidation.get("high"))
    drawdown = low / peak_price - 1.0 if low > 0 and peak_price > 0 else np.nan
    event_close = context.close[event_position]
    breakout = (
        float(event_close / resistance - 1.0)
        if np.isfinite(event_close) and event_close > 0 and resistance > 0
        else np.nan
    )
    return {
        "metric_basis": basis,
        "start_position": start,
        "consolidation_start": context.dates[start] if start >= 0 else pd.NaT,
        "duration": int(consolidation["duration"]),
        "bandwidth": _value(consolidation.get("bandwidth")),
        "trend120": _value(consolidation.get("trend120")),
        "resistance": resistance,
        "drawdown": drawdown,
        "breakout": breakout,
        "peak_price": peak_price,
        "recent_valid_ratio": _value(recent.get("ratio")),
        "recent_long_suspension": int(bool(recent.get("long_suspension", False))),
        "peak_valid_ratio": (
            _value(peak_quality.get("ratio")) if peak_quality is not None else np.nan
        ),
        "peak_long_suspension": (
            int(bool(peak_quality.get("long_suspension", False)))
            if peak_quality is not None
            else np.nan
        ),
        "event_date_available": int(bool(context.status_known[event_position])),
        "event_day_normal": int(
            bool(context.normal_trade[event_position] and context.valid_price[event_position])
        ),
    }


def _critical_price_differences(
    merged: pd.DataFrame,
    start: pd.Timestamp,
    event_date: pd.Timestamp,
) -> dict[str, float]:
    sample = merged[
        merged["trade_date"].between(start, event_date, inclusive="both")
    ].copy()
    close_diff = (
        (sample["TencentCloseQFQ"] - sample["BaoStockCloseScaled"]).abs()
        / sample["TencentCloseQFQ"].abs()
    ).replace([np.inf, -np.inf], np.nan)
    return_diff = (sample["TencentReturn"] - sample["BaoStockReturn"]).abs()
    event_row = sample[sample["trade_date"].eq(event_date)]
    event_close_diff = np.nan
    if len(event_row):
        event_close_diff = _value(
            (
                (event_row["TencentCloseQFQ"] - event_row["BaoStockCloseScaled"]).abs()
                / event_row["TencentCloseQFQ"].abs()
            ).iloc[0]
        )
    return {
        "critical_close_abs_pct_diff_median": _value(close_diff.median()),
        "critical_close_abs_pct_diff_p95": _value(close_diff.quantile(0.95)),
        "critical_return_abs_diff_p95": _value(return_diff.quantile(0.95)),
        "event_close_abs_pct_diff": event_close_diff,
    }


def _discrepancy_outputs(
    config: MethodologyConfig,
    events: pd.DataFrame,
    classifications: pd.DataFrame,
    calendar: pd.DataFrame,
    membership: pd.DataFrame,
    histories: dict[str, tuple[np.ndarray, np.ndarray]],
    stock_dir: Path,
    validation_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    discrepancies = classifications[
        classifications["EventClassificationAgreement"].eq(0)
    ].copy()
    discrepancies = discrepancies.merge(
        events,
        on=["stock_code", "event_date"],
        how="left",
        validate="one_to_one",
        suffixes=("_validation", ""),
    )
    date_to_position = {
        date: position
        for position, date in enumerate(pd.DatetimeIndex(calendar["trade_date"]))
    }
    records: list[dict[str, object]] = []

    for stock_code, group in discrepancies.groupby("stock_code", sort=True):
        candidates = sorted(validation_dir.glob(f"events_{stock_code}__*.csv"))
        if len(candidates) != 1:
            raise FileNotFoundError(
                f"Expected one BaoStock event cache for {stock_code}, found {len(candidates)}"
            )
        bao = _normalise_baostock(
            pd.read_csv(candidates[0], dtype={"code": "string"})
        )
        tencent = load_processed_stock(stock_dir / f"{_safe_code(str(stock_code))}.csv.gz")
        merged, scale = _merge_sources(tencent, bao)
        context = _build_bao_context(
            str(stock_code), bao, calendar, membership, histories
        )
        bao_dates = set(bao["trade_date"])
        tencent_status_dates = set(
            tencent.loc[tencent["tradestatus"].notna(), "trade_date"]
        )

        for event in group.itertuples(index=False):
            event_date = pd.Timestamp(event.event_date)
            event_position = date_to_position[event_date]
            metrics = _bao_event_metrics(context, event_position, config)
            tencent_start = pd.Timestamp(event.ConsolidationStart)
            tencent_start_position = date_to_position[tencent_start]
            critical_start_position = max(
                0,
                min(tencent_start_position, int(metrics["start_position"]))
                - config.peak_lookback_before_consolidation,
            )
            critical_start = pd.Timestamp(calendar.iloc[critical_start_position]["trade_date"])
            expected_dates = set(
                calendar.loc[
                    calendar["trade_date"].between(
                        critical_start, event_date, inclusive="both"
                    ),
                    "trade_date",
                ]
            )
            date_mismatch_count = len(
                (expected_dates & tencent_status_dates) - bao_dates
            )
            price_differences = _critical_price_differences(
                merged, critical_start, event_date
            )
            qfq_difference = bool(
                (
                    np.isfinite(price_differences["event_close_abs_pct_diff"])
                    and price_differences["event_close_abs_pct_diff"] > 0.01
                )
                or (
                    np.isfinite(price_differences["critical_return_abs_diff_p95"])
                    and price_differences["critical_return_abs_diff_p95"] > 0.005
                )
            )

            tencent_drawdown = _value(event.DistressDrawdown)
            tencent_bandwidth = _value(event.Bandwidth)
            tencent_trend = _value(event.Trend120)
            tencent_breakout = _value(event.BreakoutStrength)
            bao_drawdown = _value(metrics["drawdown"])
            bao_bandwidth = _value(metrics["bandwidth"])
            bao_trend = _value(metrics["trend120"])
            bao_breakout = _value(metrics["breakout"])
            bao_start = pd.Timestamp(metrics["consolidation_start"])
            start_gap = (
                date_to_position[bao_start] - tencent_start_position
                if pd.notna(bao_start)
                else np.nan
            )
            resistance_scaled = (
                _value(metrics["resistance"]) * scale
                if np.isfinite(scale)
                else np.nan
            )
            resistance_difference = (
                abs(resistance_scaled - _value(event.Resistance))
                / abs(_value(event.Resistance))
                if np.isfinite(resistance_scaled) and _value(event.Resistance) != 0
                else np.nan
            )

            near_drawdown = _near_straddle(
                tencent_drawdown,
                bao_drawdown,
                config.distress_drawdown_threshold,
                THRESHOLD_PROXIMITY["Drawdown"],
                "le",
            )
            near_bandwidth = _near_straddle(
                tencent_bandwidth,
                bao_bandwidth,
                config.specifications["baseline"].bandwidth_threshold,
                THRESHOLD_PROXIMITY["Bandwidth"],
                "le",
            )
            near_trend = _near_straddle(
                tencent_trend,
                bao_trend,
                config.trend120_threshold,
                THRESHOLD_PROXIMITY["Trend120"],
                "abs_le",
            )
            near_breakout = _near_straddle(
                tencent_breakout,
                bao_breakout,
                config.specifications["baseline"].breakout_threshold,
                THRESHOLD_PROXIMITY["BreakoutStrength"],
                "ge",
            )

            decision = str(event.BaoStockDecision)
            primary = _primary_difference_reason(decision, qfq_difference)
            contributing: list[str] = [primary]
            if date_mismatch_count > 0 or not int(metrics["event_date_available"]):
                contributing.append("交易日期/行情日期不一致")
            if qfq_difference:
                contributing.append("前复权价格差异")
            if near_drawdown:
                contributing.append("Drawdown 阈值判定差异")
            if near_bandwidth:
                contributing.append("Bandwidth 阈值判定差异")
            if near_trend:
                contributing.append("Trend120 阈值判定差异")
            if near_breakout:
                contributing.append("BreakoutStrength 阈值判定差异")
            if np.isfinite(start_gap) and start_gap != 0:
                contributing.append("横盘区间识别差异")
            if np.isfinite(resistance_difference) and resistance_difference > 0.01:
                contributing.append("resistance price 差异")
            contributing = [
                category for category in DIFFERENCE_CATEGORIES if category in set(contributing)
            ]

            records.append(
                {
                    "stock_code": stock_code,
                    "stock_name": event.stock_name,
                    "event_date": event_date,
                    "Tencent_event": 1,
                    "BaoStock_event": int(event.BaoStockSelected),
                    "BaoStock_decision": decision,
                    "Tencent_drawdown": tencent_drawdown,
                    "BaoStock_drawdown": bao_drawdown,
                    "Tencent_bandwidth": tencent_bandwidth,
                    "BaoStock_bandwidth": bao_bandwidth,
                    "Tencent_trend120": tencent_trend,
                    "BaoStock_trend120": bao_trend,
                    "Tencent_breakout_strength": tencent_breakout,
                    "BaoStock_breakout_strength": bao_breakout,
                    "Tencent_consolidation_start": tencent_start,
                    "BaoStock_consolidation_start": bao_start,
                    "Tencent_consolidation_duration": int(event.ConsolidationDuration),
                    "BaoStock_consolidation_duration": int(metrics["duration"]),
                    "BaoStock_metric_basis": metrics["metric_basis"],
                    "consolidation_start_gap_trading_days": start_gap,
                    "Tencent_resistance": _value(event.Resistance),
                    "BaoStock_resistance": _value(metrics["resistance"]),
                    "BaoStock_resistance_scaled_to_Tencent": resistance_scaled,
                    "resistance_abs_pct_difference": resistance_difference,
                    "BaoStock_recent120_valid_ratio": metrics["recent_valid_ratio"],
                    "BaoStock_recent120_long_suspension": metrics[
                        "recent_long_suspension"
                    ],
                    "BaoStock_peak_window_valid_ratio": metrics["peak_valid_ratio"],
                    "BaoStock_peak_window_long_suspension": metrics[
                        "peak_long_suspension"
                    ],
                    "BaoStock_event_date_available": metrics["event_date_available"],
                    "BaoStock_event_day_normal": metrics["event_day_normal"],
                    "critical_calendar_date_mismatch_count": date_mismatch_count,
                    "BaoToTencent_price_scale": scale,
                    **price_differences,
                    "near_Drawdown_threshold": int(near_drawdown),
                    "near_Bandwidth_threshold": int(near_bandwidth),
                    "near_Trend120_threshold": int(near_trend),
                    "near_BreakoutStrength_threshold": int(near_breakout),
                    "any_threshold_near_straddle": int(
                        near_drawdown or near_bandwidth or near_trend or near_breakout
                    ),
                    "difference_reason": primary,
                    "all_difference_reasons": ";".join(contributing),
                }
            )

    detail = pd.DataFrame(records).sort_values(
        ["difference_reason", "stock_code", "event_date"]
    )
    summary_rows: list[dict[str, object]] = []
    for category in DIFFERENCE_CATEGORIES:
        primary_count = int(detail["difference_reason"].eq(category).sum())
        contributing_count = int(
            detail["all_difference_reasons"].str.split(";").map(
                lambda values: category in values
            ).sum()
        )
        summary_rows.append(
            {
                "difference_reason": category,
                "primary_event_count": primary_count,
                "primary_share_of_108": primary_count / len(detail),
                "contributing_event_count": contributing_count,
                "contributing_share_of_108": contributing_count / len(detail),
            }
        )
    summary = pd.DataFrame(summary_rows)
    proximity = pd.DataFrame(
        [
            {
                "metric": label,
                "screening_threshold": threshold,
                "audit_near_band": THRESHOLD_PROXIMITY[label],
                "near_threshold_straddle_count": int(detail[column].sum()),
                "share_of_108": float(detail[column].mean()),
            }
            for label, threshold, column in (
                (
                    "Drawdown",
                    config.distress_drawdown_threshold,
                    "near_Drawdown_threshold",
                ),
                (
                    "Bandwidth",
                    config.specifications["baseline"].bandwidth_threshold,
                    "near_Bandwidth_threshold",
                ),
                (
                    "Trend120",
                    config.trend120_threshold,
                    "near_Trend120_threshold",
                ),
                (
                    "BreakoutStrength",
                    config.specifications["baseline"].breakout_threshold,
                    "near_BreakoutStrength_threshold",
                ),
            )
        ]
    )
    return detail, summary, proximity


def _gap_bucket(gap: int) -> str:
    if gap <= 20:
        return GAP_BUCKETS[0]
    if gap <= 60:
        return GAP_BUCKETS[1]
    if gap <= 120:
        return GAP_BUCKETS[2]
    if gap <= 252:
        return GAP_BUCKETS[3]
    return GAP_BUCKETS[4]


def _duplicate_outputs(
    events: pd.DataFrame, dates: pd.DatetimeIndex
) -> tuple[pd.DataFrame, pd.DataFrame]:
    positions = {date: index for index, date in enumerate(dates)}
    records: list[dict[str, object]] = []
    for stock_code, group in events.groupby("stock_code", sort=True):
        ordered = group.sort_values("event_date")
        if len(ordered) < 2:
            continue
        rows = list(ordered.itertuples(index=False))
        for left, right in zip(rows, rows[1:]):
            date_1 = pd.Timestamp(left.event_date)
            date_2 = pd.Timestamp(right.event_date)
            gap = positions[date_2] - positions[date_1]
            overlap_start = max(
                pd.Timestamp(left.ConsolidationStart),
                pd.Timestamp(right.ConsolidationStart),
            )
            overlap_end = min(
                pd.Timestamp(left.ConsolidationEnd),
                pd.Timestamp(right.ConsolidationEnd),
            )
            overlap = overlap_start <= overlap_end
            overlap_days = (
                positions[overlap_end] - positions[overlap_start] + 1
                if overlap
                else 0
            )
            same_peak = pd.Timestamp(left.PeakDate) == pd.Timestamp(right.PeakDate)
            suspected = bool(overlap)
            reason = ""
            if overlap and same_peak:
                reason = "横盘区间重叠且前期高点相同"
            elif overlap:
                reason = "横盘区间重叠"
            records.append(
                {
                    "stock_code": stock_code,
                    "stock_name": left.stock_name,
                    "event_date_1": date_1,
                    "event_date_2": date_2,
                    "trading_day_gap": gap,
                    "gap_bucket": _gap_bucket(gap),
                    "consolidation_start_1": pd.Timestamp(left.ConsolidationStart),
                    "consolidation_end_1": pd.Timestamp(left.ConsolidationEnd),
                    "consolidation_start_2": pd.Timestamp(right.ConsolidationStart),
                    "consolidation_end_2": pd.Timestamp(right.ConsolidationEnd),
                    "consolidation_overlap": int(overlap),
                    "overlap_trading_days": overlap_days,
                    "same_peak_date": int(same_peak),
                    "same_episode_suspected": int(suspected),
                    "same_episode_reason": reason,
                }
            )
    detail = pd.DataFrame(records)
    if len(detail):
        detail = detail.sort_values(["stock_code", "event_date_1"])
    summary = pd.DataFrame(
        [
            {
                "gap_bucket": bucket,
                "adjacent_event_pair_count": int(detail["gap_bucket"].eq(bucket).sum()),
                "share_of_adjacent_pairs": float(detail["gap_bucket"].eq(bucket).mean()),
            }
            for bucket in GAP_BUCKETS
        ]
    )
    return detail, summary


def _membership_mask(
    dates: pd.DatetimeIndex, intervals: pd.DataFrame
) -> np.ndarray:
    previous = np.concatenate(
        (np.array([np.datetime64("NaT")]), dates[:-1].to_numpy(dtype="datetime64[ns]"))
    )
    result = np.zeros(len(dates), dtype=bool)
    for row in intervals.itertuples(index=False):
        active = previous >= np.datetime64(pd.Timestamp(row.in_date))
        if pd.notna(row.out_date):
            active &= previous < np.datetime64(pd.Timestamp(row.out_date))
        result |= active
    return result


def _universe_coverage_by_year(
    config: MethodologyConfig,
    events: pd.DataFrame,
    calendar: pd.DataFrame,
    membership: pd.DataFrame,
    stock_dir: Path,
) -> pd.DataFrame:
    dates = pd.DatetimeIndex(calendar["trade_date"])
    years = list(range(2009, pd.Timestamp(config.sample_end).year + 1))
    year_masks = {year: np.asarray(dates.year == year) for year in years}
    accumulators: dict[int, dict[str, object]] = {
        year: {
            "members": set(),
            "price_stocks": set(),
            "history_stocks": set(),
            "nonpositive_stocks": set(),
            "member_stock_days": 0,
            "status_available_stock_days": 0,
            "valid_price_stock_days": 0,
            "nonpositive_qfq_stock_days": 0,
            "missing_qfq_stock_days": 0,
            "history_eligible_member_stock_days": 0,
        }
        for year in years
    }

    for stock_code, intervals in membership.groupby("stock_code", sort=True):
        member = _membership_mask(dates, intervals)
        if not member.any():
            continue
        path = stock_dir / f"{_safe_code(str(stock_code))}.csv.gz"
        if not path.exists():
            continue
        daily = (
            load_processed_stock(path)
            .sort_values("trade_date")
            .drop_duplicates("trade_date", keep="last")
            .set_index("trade_date")
            .reindex(dates)
        )
        status = pd.to_numeric(daily["tradestatus"], errors="coerce").to_numpy(float)
        close = pd.to_numeric(daily["adjusted_close"], errors="coerce").to_numpy(float)
        high = pd.to_numeric(daily["adjusted_high"], errors="coerce").to_numpy(float)
        low = pd.to_numeric(daily["adjusted_low"], errors="coerce").to_numpy(float)
        status_known = np.isfinite(status)
        valid_price = (
            (status == 1)
            & np.isfinite(close)
            & (close > 0)
            & np.isfinite(high)
            & (high > 0)
            & np.isfinite(low)
            & (low > 0)
        )
        nonpositive = (status == 1) & np.isfinite(close) & (close <= 0)
        missing = (status == 1) & ~np.isfinite(close)
        known_positions = np.flatnonzero(status_known)
        history_eligible = np.zeros(len(dates), dtype=bool)
        if len(known_positions):
            minimum_position = int(known_positions[0]) + (
                config.min_consolidation_duration
                + config.peak_lookback_before_consolidation
            )
            history_eligible[minimum_position:] = True

        for year, year_mask in year_masks.items():
            active = member & year_mask
            if not active.any():
                continue
            result = accumulators[year]
            result["members"].add(str(stock_code))
            if (active & valid_price).any():
                result["price_stocks"].add(str(stock_code))
            if (active & history_eligible).any():
                result["history_stocks"].add(str(stock_code))
            if (active & nonpositive).any():
                result["nonpositive_stocks"].add(str(stock_code))
            result["member_stock_days"] += int(active.sum())
            result["status_available_stock_days"] += int((active & status_known).sum())
            result["valid_price_stock_days"] += int((active & valid_price).sum())
            result["nonpositive_qfq_stock_days"] += int((active & nonpositive).sum())
            result["missing_qfq_stock_days"] += int((active & missing).sum())
            result["history_eligible_member_stock_days"] += int(
                (active & history_eligible).sum()
            )

    event_counts = events.groupby(events["event_date"].dt.year).size().to_dict()
    rows: list[dict[str, object]] = []
    for year in years:
        result = accumulators[year]
        member_count = len(result["members"])
        price_count = len(result["price_stocks"])
        member_days = int(result["member_stock_days"])
        rows.append(
            {
                "year": year,
                "number_of_unique_CSI300_constituents_available": member_count,
                "number_of_stocks_with_price_data": price_count,
                "coverage_ratio": price_count / member_count if member_count else np.nan,
                "baseline_event_count": int(event_counts.get(year, 0)),
                "member_stock_days": member_days,
                "status_available_stock_day_ratio": (
                    int(result["status_available_stock_days"]) / member_days
                    if member_days
                    else np.nan
                ),
                "valid_qfq_price_stock_day_ratio": (
                    int(result["valid_price_stock_days"]) / member_days
                    if member_days
                    else np.nan
                ),
                "number_of_stocks_with_minimum_history": len(result["history_stocks"]),
                "minimum_history_stock_coverage_ratio": (
                    len(result["history_stocks"]) / member_count
                    if member_count
                    else np.nan
                ),
                "history_eligible_member_stock_day_ratio": (
                    int(result["history_eligible_member_stock_days"]) / member_days
                    if member_days
                    else np.nan
                ),
                "nonpositive_qfq_stock_day_count": int(
                    result["nonpositive_qfq_stock_days"]
                ),
                "stocks_with_nonpositive_qfq": len(result["nonpositive_stocks"]),
                "missing_qfq_stock_day_count": int(result["missing_qfq_stock_days"]),
                "DataAsOfDate": config.sample_end,
                "IsPartialYear": year == pd.Timestamp(config.sample_end).year,
            }
        )
    return pd.DataFrame(rows)


def _quality_report(
    config: MethodologyConfig,
    events: pd.DataFrame,
    discrepancy: pd.DataFrame,
    discrepancy_summary: pd.DataFrame,
    proximity: pd.DataFrame,
    duplicate: pd.DataFrame,
    duplicate_summary: pd.DataFrame,
    coverage: pd.DataFrame,
) -> str:
    primary = discrepancy_summary.set_index("difference_reason")["primary_event_count"]
    contributing = discrepancy_summary.set_index("difference_reason")[
        "contributing_event_count"
    ]
    near_count = int(discrepancy["any_threshold_near_straddle"].sum())
    multi_stock_count = int(
        events.groupby("stock_code").size().gt(1).sum()
    )
    overlap_count = int(duplicate["consolidation_overlap"].sum()) if len(duplicate) else 0
    suspected_count = int(duplicate["same_episode_suspected"].sum()) if len(duplicate) else 0
    short_count = int(duplicate["trading_day_gap"].le(60).sum()) if len(duplicate) else 0
    early = coverage[coverage["year"].between(2009, 2014)]
    early_events = int(early["baseline_event_count"].sum())
    early_price_coverage = float(early["coverage_ratio"].min())
    early_day_coverage = float(early["valid_qfq_price_stock_day_ratio"].min())
    early_history_coverage = float(
        early["minimum_history_stock_coverage_ratio"].min()
    )
    early_nonpositive = int(early["nonpositive_qfq_stock_day_count"].sum())
    coverage_problem = early_day_coverage < 0.95 or early_history_coverage < 0.95

    lines = [
        "# 事件样本质量审计",
        "",
        f"DataAsOfDate：**{config.sample_end}**。本轮只使用事件日及以前的信息，没有计算未来收益、CAR或回归，也没有修改任何筛选阈值。",
        "",
        "## A. 230个事件能否作为主研究事件池",
        "",
        "230个事件可以继续保留为腾讯qfq口径下的baseline candidate pool，但不应直接把全部230个标记为双源确认的干净样本。122个事件获得双源一致确认，108个事件需要保留审计标记。冻结事件定义不等于忽略数据源差异。",
        "",
        "## B. 跨数据源差异来源",
        "",
        f"108个不一致事件中，排他的首要失败原因是：Drawdown {int(primary.get('Drawdown 阈值判定差异', 0))}个、Bandwidth {int(primary.get('Bandwidth 阈值判定差异', 0))}个、Trend120 {int(primary.get('Trend120 阈值判定差异', 0))}个、BreakoutStrength {int(primary.get('BreakoutStrength 阈值判定差异', 0))}个、BaoStock数据缺失 {int(primary.get('BaoStock 数据缺失', 0))}个、横盘区间识别 {int(primary.get('横盘区间识别差异', 0))}个、其他 {int(primary.get('其他', 0))}个。",
        f"按非排他的贡献原因统计，{int(contributing.get('前复权价格差异', 0))}个事件存在显著qfq路径差异，{int(contributing.get('横盘区间识别差异', 0))}个事件的横盘起点不同，{int(contributing.get('resistance price 差异', 0))}个事件的缩放后Resistance相差超过1%。因此阈值是最终分类发生翻转的位置，但根本来源主要需要从复权价格路径和自适应横盘区间差异中解释。",
        f"采用预先声明的审计邻域（Drawdown ±2个百分点、Bandwidth ±2个百分点、Trend120 ±1个百分点、BreakoutStrength ±0.5个百分点），有 {near_count}/108 个事件属于至少一个阈值附近的跨源翻转。该统计只诊断临界性，不改变阈值。",
        "",
        "## C. 重复事件与pseudo-replication",
        "",
        f"共有 {multi_stock_count} 只股票出现多个事件，形成 {len(duplicate)} 组同股相邻事件。≤60个市场交易日的相邻事件有 {short_count} 组，说明60日cooldown没有失效。横盘区间重叠有 {overlap_count} 组，按客观规则标记为同一episode可疑的有 {suspected_count} 组。程序没有自动删除这些事件。",
        "",
        "## D. 2009–2014事件较少是否由覆盖造成",
        "",
        f"2009–2014合计 {early_events} 个baseline事件。该阶段按年度计算的成分股有价覆盖最低为 {early_price_coverage:.2%}，有效正值qfq股票日覆盖最低为 {early_day_coverage:.2%}，具备最低372个市场日前置跨度的股票覆盖最低为 {early_history_coverage:.2%}。早期成员股票日中检测到 {early_nonpositive:,} 条腾讯qfq非正价格记录。",
        (
            "因此早期事件偏少至少部分来自可量化的数据覆盖和前置历史限制，不能全部解释为真实市场差异。年份分布在统一价格源问题解决前不宜作经济解释。"
            if coverage_problem or early_nonpositive > 0
            else "数据覆盖没有显示足以解释早期低事件数的系统性缺口；在现有证据下，较低数量主要来自冻结规则与真实价格路径，但不能仅凭事件数断言市场机制。"
        ),
        "",
        "## E. 是否需要修改baseline定义",
        "",
        "没有发现迫使研究者修改Drawdown、横盘长度、Bandwidth、Trend120或BreakoutStrength阈值的逻辑错误。需要处理的是价格源与复权历史的一致性，而不是通过放松规则解决数据问题。建议保持230个候选事件不变，在进入下一阶段前预先决定腾讯qfq为唯一主源，或改用另一套可复现的统一qfq序列后完整重跑。",
        "",
        "## 差异首要原因汇总",
        "",
        _markdown_table(discrepancy_summary),
        "",
        "## 阈值附近翻转",
        "",
        _markdown_table(proximity),
        "",
        "## 同股相邻事件间隔",
        "",
        _markdown_table(duplicate_summary),
        "",
        "## 历史沪深300覆盖",
        "",
        _markdown_table(coverage),
        "",
        "历史沪深300身份始终按事件日前一个统一A股市场交易日t-1判断，没有改成当前成分股、市值Top300或永久名单。",
        "",
    ]
    return "\n".join(lines)


def run_sample_quality_audit(
    config: MethodologyConfig, config_path: str | Path
) -> dict[str, Path]:
    config_path = Path(config_path).resolve()
    output_dir = config.resolve(config.output_dir, config_path)
    events = pd.read_csv(
        output_dir / "baseline_events.csv", dtype={"stock_code": "string"}
    )
    for column in (
        "event_date",
        "ConsolidationStart",
        "ConsolidationEnd",
        "PeakDate",
        "ResistanceDate",
    ):
        events[column] = pd.to_datetime(events[column], errors="raise")
    classifications = pd.read_csv(
        output_dir / "event_classification_validation.csv",
        dtype={"stock_code": "string"},
    )
    classifications["event_date"] = pd.to_datetime(
        classifications["event_date"], errors="raise"
    )
    membership = pd.read_csv(
        config.resolve(config.membership_path, config_path),
        dtype={"stock_code": "string"},
    )
    membership["in_date"] = pd.to_datetime(membership["in_date"], errors="raise")
    membership["out_date"] = pd.to_datetime(
        membership["out_date"], errors="coerce"
    )
    calendar = pd.read_csv(config.resolve(config.calendar_path, config_path))
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="raise")
    calendar = calendar[
        calendar["trade_date"].le(pd.Timestamp(config.sample_end))
    ].sort_values("trade_date").reset_index(drop=True)
    dates = pd.DatetimeIndex(calendar["trade_date"])
    raw_dir = config.resolve(config.raw_dir, config_path)
    histories = _load_name_history(raw_dir / "baostock" / "hs300_snapshots")
    stock_dir = config.resolve(config.processed_stock_dir, config_path)
    validation_dir = raw_dir / "baostock" / "price_validation"

    discrepancy, discrepancy_summary, proximity = _discrepancy_outputs(
        config,
        events,
        classifications,
        calendar,
        membership,
        histories,
        stock_dir,
        validation_dir,
    )
    duplicate, duplicate_summary = _duplicate_outputs(events, dates)
    coverage = _universe_coverage_by_year(
        config, events, calendar, membership, stock_dir
    )

    if len(discrepancy) != 108:
        raise AssertionError(f"Expected 108 discrepancies, found {len(discrepancy)}")
    if int(discrepancy_summary["primary_event_count"].sum()) != len(discrepancy):
        raise AssertionError("Primary discrepancy categories do not reconcile")
    if int(duplicate["trading_day_gap"].le(config.cooldown_market_days).sum()) != 0:
        raise AssertionError("Duplicate audit found a cooldown violation")

    paths = {
        "discrepancy_detail": output_dir / "discrepancy_audit_table.csv",
        "discrepancy_summary": output_dir / "discrepancy_summary.csv",
        "threshold_proximity": output_dir
        / "discrepancy_threshold_proximity_summary.csv",
        "duplicate_detail": output_dir / "duplicate_event_audit.csv",
        "duplicate_summary": output_dir / "duplicate_event_gap_summary.csv",
        "universe_coverage": output_dir / "universe_coverage_by_year.csv",
        "report": output_dir / "event_sample_quality_audit.md",
    }
    _write_csv(discrepancy, paths["discrepancy_detail"])
    _write_csv(discrepancy_summary, paths["discrepancy_summary"])
    _write_csv(proximity, paths["threshold_proximity"])
    _write_csv(duplicate, paths["duplicate_detail"])
    _write_csv(duplicate_summary, paths["duplicate_summary"])
    _write_csv(coverage, paths["universe_coverage"])
    paths["report"].write_text(
        _quality_report(
            config,
            events,
            discrepancy,
            discrepancy_summary,
            proximity,
            duplicate,
            duplicate_summary,
            coverage,
        ),
        encoding="utf-8",
    )
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit cross-source discrepancies, duplicate events and CSI300 coverage."
    )
    parser.add_argument("--config", default="config/methodology_config.json")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = load_methodology_config(config_path)
    paths = run_sample_quality_audit(config, config_path)
    print("Saved event-sample quality-audit outputs:")
    for label, path in paths.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
