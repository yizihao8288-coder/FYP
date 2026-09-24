from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .acquisition import load_processed_stock
from .methodology import EventSpecification, MethodologyConfig, load_methodology_config


EVENT_COLUMNS = [
    "stock_code",
    "stock_name",
    "event_date",
    "year",
    "CSI300_member_t_minus_1",
    "ConsolidationStart",
    "ConsolidationEnd",
    "ConsolidationDuration",
    "DurationCensored",
    "ExpectedTradingDays",
    "ValidPriceDays",
    "MissingPriceDays",
    "SuspensionDays",
    "ValidPriceRatio",
    "MaxConsecutiveSuspension",
    "ConsolidationHigh",
    "ConsolidationLow",
    "Bandwidth",
    "TrendBeta",
    "Trend120",
    "TrendFullPeriod",
    "Resistance",
    "ResistanceDate",
    "BreakoutStrength",
    "PeakDate",
    "PeakPrice",
    "PeakWindowValidRatio",
    "PeakWindowMissingDays",
    "PeakWindowSuspensionDays",
    "DistressDrawdown",
    "PreBreakoutDrawdown",
    "isST",
    "name_ST_flag",
    "final_ST_flag",
    "ConsolidationSTDays",
    "turnover_t",
    "median_turnover20",
    "relative_turnover20",
    "price_source",
    "adjustment_type",
    "specification",
    "event_exclusion_reason",
    "_calendar_index",
]


FUNNEL_STEPS = [
    ("enough_minimum_history", "insufficient_minimum_history"),
    ("historical_CSI300_member_at_t_minus_1", "not_CSI300_member_at_t_minus_1"),
    ("recent_120_data_quality", "insufficient_recent_120_data"),
    ("recent_120_bandwidth", "recent_120_bandwidth_failed"),
    ("recent_120_trend120", "recent_120_trend_failed"),
    ("adaptive_consolidation_identified", "adaptive_consolidation_not_identified"),
    ("consolidation_and_event_non_ST", "ST_during_consolidation_or_event"),
    ("full_consolidation_data_quality", "consolidation_data_quality_failed"),
    ("pre_consolidation_peak_window_valid", "invalid_peak_window_data"),
    ("distress_drawdown_at_most_minus_30pct", "distress_drawdown_failed"),
    ("breakout_threshold", "breakout_threshold_failed"),
    ("event_day_normal_trading", "event_day_not_normal_trading"),
    ("after_60_market_day_cooldown", "cooldown_removed"),
    ("final_events", "selected"),
]

EXTRA_EXCLUSION_COUNTERS = [
    "recent_120_low_valid_price_ratio",
    "recent_120_long_suspension",
    "peak_window_low_valid_price_ratio",
    "peak_window_long_suspension",
    "peak_window_before_calendar_or_no_price",
]


@dataclass
class StockContext:
    stock_code: str
    dates: pd.DatetimeIndex
    close: np.ndarray
    raw_close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray
    status_known: np.ndarray
    is_suspended: np.ndarray
    is_st: np.ndarray
    names: np.ndarray
    name_st: np.ndarray
    final_st: np.ndarray
    member_t_minus_1: np.ndarray
    valid_price: np.ndarray
    normal_trade: np.ndarray
    prefix_valid: np.ndarray
    prefix_suspension: np.ndarray
    prefix_st: np.ndarray
    prefix_run6: np.ndarray
    prefix_n: np.ndarray
    prefix_x: np.ndarray
    prefix_y: np.ndarray
    prefix_xx: np.ndarray
    prefix_xy: np.ndarray


def _prefix(values: np.ndarray) -> np.ndarray:
    return np.concatenate(([0], np.cumsum(values)))


def _range_sum(prefix: np.ndarray, start: int, end: int) -> float:
    return float(prefix[end + 1] - prefix[start])


def _name_indicates_st(value: object) -> bool:
    normalized = str(value).strip().upper()
    # The frozen methodology deliberately uses the conservative double rule:
    # BaoStock isST OR an ST marker anywhere in the contemporaneous name.
    return "ST" in normalized


def _load_name_history(snapshot_dir: Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    frames: list[pd.DataFrame] = []
    for path in sorted(snapshot_dir.glob("*.csv")):
        frame = pd.read_csv(path, dtype={"code": "string"})
        if {"code", "code_name"}.issubset(frame.columns):
            block = frame[["code", "code_name"]].copy()
            block["snapshot_date"] = pd.Timestamp(path.stem)
            frames.append(block)
    if not frames:
        return {}
    combined = pd.concat(frames, ignore_index=True).dropna(subset=["code"])
    result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for code, group in combined.groupby("code", sort=False):
        ordered = group.sort_values("snapshot_date").drop_duplicates(
            "snapshot_date", keep="last"
        )
        result[str(code)] = (
            ordered["snapshot_date"].to_numpy(dtype="datetime64[ns]"),
            ordered["code_name"].fillna("").astype(str).to_numpy(dtype=object),
        )
    return result


def _names_for_dates(
    stock_code: str,
    dates: pd.DatetimeIndex,
    histories: dict[str, tuple[np.ndarray, np.ndarray]],
    membership_intervals: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray]:
    result = np.full(len(dates), "", dtype=object)
    known = np.zeros(len(dates), dtype=bool)
    history = histories.get(stock_code)
    if history is None:
        return result, known
    snapshot_dates, names = history
    locations = np.searchsorted(
        snapshot_dates, dates.to_numpy(dtype="datetime64[ns]"), side="right"
    ) - 1
    known = locations >= 0
    # A BaoStock CSI300 snapshot is a point-in-time name observation, not a
    # general security master.  Carry it only while the stock remains in that
    # same index-membership spell (plus its removal day, whose universe test is
    # still based on t-1).  Carrying it through years outside the index would,
    # for example, incorrectly keep a delisted "*ST" label after the company
    # had already changed its name.
    covered_by_membership_spell = np.zeros(len(dates), dtype=bool)
    date_values = dates.to_numpy(dtype="datetime64[ns]")
    for row in membership_intervals.itertuples(index=False):
        active = date_values >= np.datetime64(pd.Timestamp(row.in_date))
        if pd.notna(row.out_date):
            active &= date_values <= np.datetime64(pd.Timestamp(row.out_date))
        covered_by_membership_spell |= active
    known &= covered_by_membership_spell
    result[known] = names[locations[known]]
    return result, known


def _membership_at_t_minus_1(
    dates: pd.DatetimeIndex,
    intervals: pd.DataFrame,
) -> np.ndarray:
    result = np.zeros(len(dates), dtype=bool)
    previous_dates = dates.to_numpy(dtype="datetime64[ns]")
    previous_dates = np.concatenate(
        (np.array([np.datetime64("NaT")]), previous_dates[:-1])
    )
    for row in intervals.itertuples(index=False):
        active = previous_dates >= np.datetime64(pd.Timestamp(row.in_date))
        if pd.notna(row.out_date):
            active &= previous_dates < np.datetime64(pd.Timestamp(row.out_date))
        result |= active
    return result


def _build_context(
    daily: pd.DataFrame,
    calendar: pd.DataFrame,
    membership: pd.DataFrame,
    histories: dict[str, tuple[np.ndarray, np.ndarray]],
) -> tuple[StockContext, pd.DataFrame]:
    stock_code = str(daily["stock_code"].iloc[0])
    dates = pd.DatetimeIndex(calendar["trade_date"].sort_values().unique())
    aligned = (
        daily.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .set_index("trade_date")
        .reindex(dates)
    )
    numeric_columns = [
        "adjusted_close",
        "adjusted_high",
        "adjusted_low",
        "close",
        "volume",
        "turnover_rate",
        "tradestatus",
        "isST",
    ]
    for column in numeric_columns:
        aligned[column] = pd.to_numeric(aligned[column], errors="coerce")

    close = aligned["adjusted_close"].to_numpy(dtype=float)
    high = aligned["adjusted_high"].to_numpy(dtype=float)
    low = aligned["adjusted_low"].to_numpy(dtype=float)
    raw_close = aligned["close"].to_numpy(dtype=float)
    volume = aligned["volume"].to_numpy(dtype=float)
    turnover = aligned["turnover_rate"].to_numpy(dtype=float)
    status_values = aligned["tradestatus"].to_numpy(dtype=float)
    st_values = aligned["isST"].to_numpy(dtype=float)
    status_known = np.isfinite(status_values)
    is_suspended = status_values == 0
    is_st = st_values == 1
    stock_membership = membership[membership["stock_code"].eq(stock_code)]
    names, name_known = _names_for_dates(
        stock_code, dates, histories, stock_membership
    )
    name_st = np.array([_name_indicates_st(value) for value in names], dtype=bool)
    final_st = is_st | name_st
    valid_price = (
        np.isfinite(close)
        & (close > 0)
        & np.isfinite(high)
        & (high > 0)
        & np.isfinite(low)
        & (low > 0)
        & ~is_suspended
    )
    normal_trade = status_values == 1
    member_t_minus_1 = _membership_at_t_minus_1(
        dates, stock_membership
    )

    x = np.arange(len(dates), dtype=float)
    log_close = np.zeros(len(dates), dtype=float)
    log_close[valid_price] = np.log(close[valid_price])
    valid_float = valid_price.astype(float)
    run6 = np.zeros(len(dates), dtype=np.int64)
    if len(dates) >= 6:
        run6[: len(dates) - 5] = (
            np.convolve(is_suspended.astype(np.int8), np.ones(6, dtype=np.int8), mode="valid")
            == 6
        )

    conflict = pd.DataFrame(
        {
            "stock_code": stock_code,
            "date": dates,
            "stock_name": names,
            "isST": st_values,
            "name_ST_flag": name_st.astype(int),
            "final_ST_flag": final_st.astype(int),
        }
    )
    conflict = conflict.loc[
        name_known
        & np.isfinite(st_values)
        & (is_st != name_st)
    ].copy()
    conflict["conflict_type"] = np.where(
        conflict["isST"].eq(0) & conflict["name_ST_flag"].eq(1),
        "isST_0_but_name_indicates_ST",
        "isST_1_but_name_does_not_indicate_ST",
    )

    return (
        StockContext(
            stock_code=stock_code,
            dates=dates,
            close=close,
            raw_close=raw_close,
            high=high,
            low=low,
            volume=volume,
            turnover=turnover,
            status_known=status_known,
            is_suspended=is_suspended,
            is_st=is_st,
            names=names,
            name_st=name_st,
            final_st=final_st,
            member_t_minus_1=member_t_minus_1,
            valid_price=valid_price,
            normal_trade=normal_trade,
            prefix_valid=_prefix(valid_price.astype(np.int64)),
            prefix_suspension=_prefix(is_suspended.astype(np.int64)),
            prefix_st=_prefix(final_st.astype(np.int64)),
            prefix_run6=_prefix(run6),
            prefix_n=_prefix(valid_float),
            prefix_x=_prefix(x * valid_float),
            prefix_y=_prefix(log_close),
            prefix_xx=_prefix(x * x * valid_float),
            prefix_xy=_prefix(x * log_close),
        ),
        conflict,
    )


def _trend_beta(context: StockContext, start: int, end: int) -> float:
    count = _range_sum(context.prefix_n, start, end)
    if count < 2:
        return np.nan
    sum_x = _range_sum(context.prefix_x, start, end)
    sum_y = _range_sum(context.prefix_y, start, end)
    sum_xx = _range_sum(context.prefix_xx, start, end)
    sum_xy = _range_sum(context.prefix_xy, start, end)
    denominator = sum_xx - sum_x * sum_x / count
    if denominator <= 0:
        return np.nan
    return (sum_xy - sum_x * sum_y / count) / denominator


def _has_long_suspension(
    context: StockContext, start: int, end: int, maximum: int
) -> bool:
    run_length = maximum + 1
    if end - start + 1 < run_length:
        return False
    last_start = end - run_length + 1
    return _range_sum(context.prefix_run6, start, last_start) > 0


def _max_consecutive_true(values: np.ndarray) -> int:
    best = 0
    current = 0
    for value in values:
        current = current + 1 if bool(value) else 0
        best = max(best, current)
    return best


def _quality_metrics(
    context: StockContext,
    start: int,
    end: int,
    config: MethodologyConfig,
) -> dict[str, float | int | bool]:
    expected = end - start + 1
    valid = int(_range_sum(context.prefix_valid, start, end))
    suspended = int(_range_sum(context.prefix_suspension, start, end))
    ratio = valid / expected
    long_suspension = _has_long_suspension(
        context, start, end, config.max_consecutive_suspension
    )
    return {
        "expected": expected,
        "valid": valid,
        "missing": expected - valid,
        "suspended": suspended,
        "ratio": ratio,
        "long_suspension": long_suspension,
        "passes": ratio >= config.min_valid_price_ratio and not long_suspension,
    }


def _price_extrema(
    context: StockContext, start: int, end: int
) -> tuple[float, float, int, int]:
    block = context.close[start : end + 1]
    valid = context.valid_price[start : end + 1]
    if not valid.any():
        return np.nan, np.nan, -1, -1
    safe_high = np.where(valid, block, -np.inf)
    safe_low = np.where(valid, block, np.inf)
    high_offset = int(np.argmax(safe_high))
    low_offset = int(np.argmin(safe_low))
    return (
        float(safe_high[high_offset]),
        float(safe_low[low_offset]),
        start + high_offset,
        start + low_offset,
    )


def _recent_120_metrics(
    context: StockContext,
    event_position: int,
    config: MethodologyConfig,
) -> dict[str, float | int | bool]:
    start = event_position - config.min_consolidation_duration
    end = event_position - 1
    quality = _quality_metrics(context, start, end, config)
    high, low, high_position, _ = _price_extrema(context, start, end)
    bandwidth = high / low - 1.0 if high > 0 and low > 0 else np.nan
    beta = _trend_beta(context, start, end)
    trend120 = (
        float(np.expm1(beta * (config.trend_equivalent_days - 1)))
        if np.isfinite(beta)
        else np.nan
    )
    return {
        **quality,
        "start": start,
        "end": end,
        "high": high,
        "low": low,
        "high_position": high_position,
        "bandwidth": bandwidth,
        "beta": beta,
        "trend120": trend120,
    }


def _detect_consolidation(
    context: StockContext,
    event_position: int,
    specification: EventSpecification,
    config: MethodologyConfig,
) -> dict[str, float | int | bool] | None:
    minimum = config.min_consolidation_duration
    first = _recent_120_metrics(context, event_position, config)
    if (
        not first["passes"]
        or not np.isfinite(first["bandwidth"])
        or first["bandwidth"] > specification.bandwidth_threshold
        or not np.isfinite(first["trend120"])
        or abs(first["trend120"]) > config.trend120_threshold
    ):
        return None

    last_good = first
    for duration in range(minimum + 1, config.max_consolidation_duration + 1):
        start = event_position - duration
        if start < 0:
            break
        quality = _quality_metrics(context, start, event_position - 1, config)
        high, low, high_position, _ = _price_extrema(
            context, start, event_position - 1
        )
        bandwidth = high / low - 1.0 if high > 0 and low > 0 else np.nan
        beta = _trend_beta(context, start, event_position - 1)
        trend120 = (
            float(np.expm1(beta * (config.trend_equivalent_days - 1)))
            if np.isfinite(beta)
            else np.nan
        )
        if (
            not quality["passes"]
            or not np.isfinite(bandwidth)
            or bandwidth > specification.bandwidth_threshold
            or not np.isfinite(trend120)
            or abs(trend120) > config.trend120_threshold
        ):
            break
        last_good = {
            **quality,
            "start": start,
            "end": event_position - 1,
            "high": high,
            "low": low,
            "high_position": high_position,
            "bandwidth": bandwidth,
            "beta": beta,
            "trend120": trend120,
        }

    duration = int(last_good["expected"])
    last_good["duration"] = duration
    last_good["duration_censored"] = int(
        duration == config.max_consolidation_duration
    )
    last_good["trend_full"] = float(
        np.expm1(float(last_good["beta"]) * (duration - 1))
    )
    return last_good


def _turnover_metrics(context: StockContext, event_position: int) -> tuple[float, float, float]:
    current = context.turnover[event_position]
    prior_positions = np.flatnonzero(
        context.normal_trade[:event_position]
        & np.isfinite(context.turnover[:event_position])
        & (context.turnover[:event_position] > 0)
    )
    if len(prior_positions) < 20:
        return current, np.nan, np.nan
    median = float(np.median(context.turnover[prior_positions[-20:]]))
    relative = float(current / median) if np.isfinite(current) and median > 0 else np.nan
    return current, median, relative


def _evaluate_after_recent_filters(
    context: StockContext,
    event_position: int,
    specification: EventSpecification,
    config: MethodologyConfig,
) -> tuple[dict[str, object] | None, str, dict[str, object] | None]:
    consolidation = _detect_consolidation(
        context, event_position, specification, config
    )
    if consolidation is None:
        return None, "adaptive_consolidation_not_identified", None
    start = int(consolidation["start"])
    end = event_position - 1

    st_days = int(_range_sum(context.prefix_st, start, event_position))
    if st_days > 0:
        return None, "ST_during_consolidation_or_event", {"st_days": st_days}
    if not bool(consolidation["passes"]):
        return None, "consolidation_data_quality_failed", None

    peak_start = start - config.peak_lookback_before_consolidation
    peak_end = start - 1
    if peak_start < 0:
        return None, "invalid_peak_window_data", {"peak_before_calendar": True}
    peak_quality = _quality_metrics(context, peak_start, peak_end, config)
    peak_passes = (
        peak_quality["ratio"] >= config.peak_window_min_valid_ratio
        and not peak_quality["long_suspension"]
    )
    peak_price, _, peak_position, _ = _price_extrema(
        context, peak_start, peak_end
    )
    if not peak_passes or not np.isfinite(peak_price) or peak_price <= 0:
        return None, "invalid_peak_window_data", {
            "peak_quality": peak_quality,
            "invalid_peak_price": not np.isfinite(peak_price) or peak_price <= 0,
        }

    distress = float(consolidation["low"]) / peak_price - 1.0
    if distress > config.distress_drawdown_threshold:
        return None, "distress_drawdown_failed", None

    event_close = context.close[event_position]
    breakout = (
        event_close / float(consolidation["high"]) - 1.0
        if np.isfinite(event_close) and event_close > 0
        else np.nan
    )
    breakout_passes = (
        breakout >= specification.breakout_threshold
        if specification.breakout_operator == "ge"
        else breakout > specification.breakout_threshold
    )
    if not breakout_passes:
        return None, "breakout_threshold_failed", None
    if not context.normal_trade[event_position] or not context.valid_price[event_position]:
        return None, "event_day_not_normal_trading", None

    previous_close = context.close[event_position - 1]
    prebreakout_drawdown = (
        previous_close / peak_price - 1.0
        if np.isfinite(previous_close) and previous_close > 0
        else np.nan
    )
    turnover_t, median_turnover20, relative_turnover20 = _turnover_metrics(
        context, event_position
    )
    record: dict[str, object] = {
        "stock_code": context.stock_code,
        "stock_name": str(context.names[event_position]),
        "event_date": context.dates[event_position],
        "year": int(context.dates[event_position].year),
        "CSI300_member_t_minus_1": 1,
        "ConsolidationStart": context.dates[start],
        "ConsolidationEnd": context.dates[end],
        "ConsolidationDuration": int(consolidation["duration"]),
        "DurationCensored": int(consolidation["duration_censored"]),
        "ExpectedTradingDays": int(consolidation["expected"]),
        "ValidPriceDays": int(consolidation["valid"]),
        "MissingPriceDays": int(consolidation["missing"]),
        "SuspensionDays": int(consolidation["suspended"]),
        "ValidPriceRatio": float(consolidation["ratio"]),
        "MaxConsecutiveSuspension": _max_consecutive_true(
            context.is_suspended[start : event_position]
        ),
        "ConsolidationHigh": float(consolidation["high"]),
        "ConsolidationLow": float(consolidation["low"]),
        "Bandwidth": float(consolidation["bandwidth"]),
        "TrendBeta": float(consolidation["beta"]),
        "Trend120": float(consolidation["trend120"]),
        "TrendFullPeriod": float(consolidation["trend_full"]),
        "Resistance": float(consolidation["high"]),
        "ResistanceDate": context.dates[int(consolidation["high_position"])],
        "BreakoutStrength": float(breakout),
        "PeakDate": context.dates[peak_position],
        "PeakPrice": float(peak_price),
        "PeakWindowValidRatio": float(peak_quality["ratio"]),
        "PeakWindowMissingDays": int(peak_quality["missing"]),
        "PeakWindowSuspensionDays": int(peak_quality["suspended"]),
        "DistressDrawdown": float(distress),
        "PreBreakoutDrawdown": float(prebreakout_drawdown),
        "isST": int(context.is_st[event_position]),
        "name_ST_flag": int(context.name_st[event_position]),
        "final_ST_flag": int(context.final_st[event_position]),
        "ConsolidationSTDays": st_days,
        "turnover_t": float(turnover_t) if np.isfinite(turnover_t) else np.nan,
        "median_turnover20": median_turnover20,
        "relative_turnover20": relative_turnover20,
        "price_source": config.price_source,
        "adjustment_type": config.target_adjustment_type,
        "specification": specification.name,
        "event_exclusion_reason": "",
        "_calendar_index": event_position,
    }
    audit = {
        "stock_code": context.stock_code,
        "event_date": context.dates[event_position],
        "ConsolidationStart": context.dates[start],
        "PeakDate": context.dates[peak_position],
        "ResistanceDate": context.dates[int(consolidation["high_position"])],
    }
    return record, "selected_before_cooldown", audit


def _apply_cooldown(records: list[dict[str, object]], cooldown: int) -> list[dict[str, object]]:
    kept: list[dict[str, object]] = []
    last_position: int | None = None
    for record in sorted(records, key=lambda value: int(value["_calendar_index"])):
        position = int(record["_calendar_index"])
        if last_position is None or position - last_position > cooldown:
            kept.append(record)
            last_position = position
    return kept


def _empty_counts() -> dict[str, int]:
    return {step: 0 for step, _ in FUNNEL_STEPS}


def _evaluate_stock_specification(
    context: StockContext,
    specification: EventSpecification,
    config: MethodologyConfig,
    *,
    collect_funnel: bool,
) -> tuple[list[dict[str, object]], dict[str, int], dict[str, int], list[dict[str, object]]]:
    counts = _empty_counts()
    exclusions = {
        **{reason: 0 for _, reason in FUNNEL_STEPS},
        **{reason: 0 for reason in EXTRA_EXCLUSION_COUNTERS},
    }
    before_cooldown: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    sample_start = pd.Timestamp(config.sample_start_fallback)
    sample_end = pd.Timestamp(config.sample_end)
    sample_positions = np.flatnonzero(
        (context.dates >= sample_start)
        & (context.dates <= sample_end)
    )
    known_positions = np.flatnonzero(context.status_known)
    if len(known_positions) == 0:
        return [], counts, exclusions, audits
    minimum_position = int(known_positions[0]) + (
        config.min_consolidation_duration
        + config.peak_lookback_before_consolidation
    )
    positions = sample_positions[sample_positions >= minimum_position]
    counts["enough_minimum_history"] += len(positions)

    member_positions = positions[context.member_t_minus_1[positions]]
    counts["historical_CSI300_member_at_t_minus_1"] += len(member_positions)
    exclusions["not_CSI300_member_at_t_minus_1"] += len(positions) - len(member_positions)

    for event_position in member_positions:
        recent = _recent_120_metrics(context, int(event_position), config)
        if not recent["passes"]:
            exclusions["insufficient_recent_120_data"] += 1
            if float(recent["ratio"]) < config.min_valid_price_ratio:
                exclusions["recent_120_low_valid_price_ratio"] += 1
            if bool(recent["long_suspension"]):
                exclusions["recent_120_long_suspension"] += 1
            continue
        counts["recent_120_data_quality"] += 1
        if (
            not np.isfinite(recent["bandwidth"])
            or recent["bandwidth"] > specification.bandwidth_threshold
        ):
            exclusions["recent_120_bandwidth_failed"] += 1
            continue
        counts["recent_120_bandwidth"] += 1
        if (
            not np.isfinite(recent["trend120"])
            or abs(recent["trend120"]) > config.trend120_threshold
        ):
            exclusions["recent_120_trend_failed"] += 1
            continue
        counts["recent_120_trend120"] += 1

        record, outcome, audit = _evaluate_after_recent_filters(
            context, int(event_position), specification, config
        )
        if outcome == "adaptive_consolidation_not_identified":
            exclusions[outcome] += 1
            continue
        counts["adaptive_consolidation_identified"] += 1
        if outcome == "ST_during_consolidation_or_event":
            exclusions[outcome] += 1
            continue
        counts["consolidation_and_event_non_ST"] += 1
        if outcome == "consolidation_data_quality_failed":
            exclusions[outcome] += 1
            continue
        counts["full_consolidation_data_quality"] += 1
        if outcome == "invalid_peak_window_data":
            exclusions[outcome] += 1
            if audit and "peak_quality" in audit:
                peak_quality = audit["peak_quality"]
                if float(peak_quality["ratio"]) < config.peak_window_min_valid_ratio:
                    exclusions["peak_window_low_valid_price_ratio"] += 1
                if bool(peak_quality["long_suspension"]):
                    exclusions["peak_window_long_suspension"] += 1
            if audit and (
                audit.get("peak_before_calendar") or audit.get("invalid_peak_price")
            ):
                exclusions["peak_window_before_calendar_or_no_price"] += 1
            continue
        counts["pre_consolidation_peak_window_valid"] += 1
        if outcome == "distress_drawdown_failed":
            exclusions[outcome] += 1
            continue
        counts["distress_drawdown_at_most_minus_30pct"] += 1
        if outcome == "breakout_threshold_failed":
            exclusions[outcome] += 1
            continue
        counts["breakout_threshold"] += 1
        if outcome == "event_day_not_normal_trading":
            exclusions[outcome] += 1
            continue
        counts["event_day_normal_trading"] += 1
        if record is None:
            raise AssertionError("Selected event is missing its output record")
        before_cooldown.append(record)
        if audit is not None:
            audits.append(audit)

    kept = _apply_cooldown(before_cooldown, config.cooldown_market_days)
    counts["after_60_market_day_cooldown"] += len(kept)
    counts["final_events"] += len(kept)
    exclusions["cooldown_removed"] += len(before_cooldown) - len(kept)
    exclusions["selected"] += len(kept)
    return kept, counts, exclusions, audits


def _funnel_frame(
    counts: dict[str, int], exclusions: dict[str, int]
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    previous = 0
    for index, (step, reason) in enumerate(FUNNEL_STEPS):
        remaining = int(counts[step])
        if index == 0:
            input_count = remaining
            excluded = 0
        elif step == "final_events":
            input_count = previous
            excluded = 0
        else:
            input_count = previous
            excluded = max(0, input_count - remaining)
        rows.append(
            {
                "StepOrder": index + 1,
                "FilterStep": step,
                "InputCount": input_count,
                "ExcludedCount": excluded,
                "RemainingCount": remaining,
                "ExclusionReason": reason,
            }
        )
        previous = remaining
    return pd.DataFrame(rows)


def _duration_outputs(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    duration = pd.to_numeric(events["ConsolidationDuration"], errors="coerce")
    summary = pd.DataFrame(
        {
            "Statistic": [
                "minimum",
                "mean",
                "median",
                "std",
                "25% quantile",
                "75% quantile",
                "maximum",
                "DurationCensored=1",
            ],
            "Value": [
                duration.min(),
                duration.mean(),
                duration.median(),
                duration.std(ddof=1),
                duration.quantile(0.25),
                duration.quantile(0.75),
                duration.max(),
                int(events["DurationCensored"].sum()),
            ],
        }
    )
    bins = pd.cut(
        duration,
        bins=[119, 149, 179, 209, 251, np.inf],
        labels=["120-149", "150-179", "180-209", "210-251", "252+"],
    )
    distribution = (
        bins.value_counts(sort=False)
        .rename_axis("DurationBin")
        .rename("EventCount")
        .reset_index()
    )
    return summary, distribution


def _repetition_summary(events: pd.DataFrame) -> pd.DataFrame:
    counts = events.groupby("stock_code").size() if not events.empty else pd.Series(dtype=int)
    distances: list[int] = []
    for _, group in events.groupby("stock_code"):
        positions = np.sort(group["_calendar_index"].to_numpy(dtype=int))
        if len(positions) > 1:
            distances.extend(np.diff(positions).tolist())
    return pd.DataFrame(
        {
            "Metric": [
                "FinalEventCount",
                "UniqueStockCount",
                "MeanEventsPerStock",
                "MedianEventsPerStock",
                "MaxEventsForOneStock",
                "ShortestSameStockEventDistanceMarketDays",
            ],
            "Value": [
                len(events),
                events["stock_code"].nunique(),
                counts.mean() if len(counts) else np.nan,
                counts.median() if len(counts) else np.nan,
                counts.max() if len(counts) else 0,
                min(distances) if distances else np.nan,
            ],
        }
    )


def run_adaptive_screening(
    config: MethodologyConfig,
    config_path: str | Path,
) -> dict[str, Path]:
    stock_dir = config.resolve(config.processed_stock_dir, config_path)
    membership = pd.read_csv(
        config.resolve(config.membership_path, config_path),
        dtype={"stock_code": "string"},
    )
    membership["in_date"] = pd.to_datetime(membership["in_date"], errors="raise")
    membership["out_date"] = pd.to_datetime(membership["out_date"], errors="coerce")
    calendar = pd.read_csv(config.resolve(config.calendar_path, config_path))
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="raise")
    calendar = calendar.loc[
        calendar["trade_date"].le(config.sample_end)
    ].sort_values("trade_date").drop_duplicates("trade_date")
    histories = _load_name_history(
        config.resolve(config.raw_dir, config_path)
        / "baostock"
        / "hs300_snapshots"
    )
    output_dir = config.resolve(config.output_dir, config_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_events: dict[str, list[dict[str, object]]] = {
        name: [] for name in config.specifications
    }
    aggregate_counts = {name: _empty_counts() for name in config.specifications}
    aggregate_exclusions = {
        name: {
            **{reason: 0 for _, reason in FUNNEL_STEPS},
            **{reason: 0 for reason in EXTRA_EXCLUSION_COUNTERS},
        }
        for name in config.specifications
    }
    st_conflicts: list[pd.DataFrame] = []
    total_records = 0
    total_missing_adjusted_close = 0
    total_suspended = 0
    suspended_with_nonmissing_price = 0

    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    for number, stock_code in enumerate(codes, start=1):
        path = stock_dir / f"{stock_code.replace('.', '_')}.csv.gz"
        if not path.exists():
            raise FileNotFoundError(f"Processed stock cache is missing: {path}")
        daily = load_processed_stock(path)
        total_records += len(daily)
        total_missing_adjusted_close += int(daily["adjusted_close"].isna().sum())
        suspended_mask = daily["suspension_status"].eq(1)
        total_suspended += int(suspended_mask.sum())
        suspended_with_nonmissing_price += int(
            (suspended_mask & daily["adjusted_close"].notna()).sum()
        )
        context, conflict = _build_context(
            daily, calendar, membership, histories
        )
        if not conflict.empty:
            st_conflicts.append(conflict)

        for name, specification in config.specifications.items():
            events, counts, exclusions, _ = _evaluate_stock_specification(
                context,
                specification,
                config,
                collect_funnel=name == "baseline",
            )
            all_events[name].extend(events)
            for key, value in counts.items():
                aggregate_counts[name][key] += value
            for key, value in exclusions.items():
                aggregate_exclusions[name][key] += value
        if number % 25 == 0 or number == len(codes):
            current = len(all_events["baseline"])
            print(
                f"  adaptive screening: {number}/{len(codes)} stocks; "
                f"baseline events so far={current}",
                flush=True,
            )

    event_frames: dict[str, pd.DataFrame] = {}
    for name, records in all_events.items():
        frame = pd.DataFrame(records, columns=EVENT_COLUMNS)
        if not frame.empty:
            frame = frame.sort_values(["event_date", "stock_code"]).reset_index(drop=True)
        event_frames[name] = frame

    baseline = event_frames["baseline"]
    strict = event_frames["strict"]
    relaxed = event_frames["relaxed"]
    funnel = _funnel_frame(
        aggregate_counts["baseline"], aggregate_exclusions["baseline"]
    )
    specification_summary = pd.DataFrame(
        [
            {
                "Specification": name,
                "BandwidthThreshold": specification.bandwidth_threshold,
                "BreakoutThreshold": (
                    f">{specification.breakout_threshold:.4f}"
                    if specification.breakout_operator == "gt"
                    else f">={specification.breakout_threshold:.4f}"
                ),
                "FinalEventCount": len(event_frames[name]),
                "UniqueStockCount": event_frames[name]["stock_code"].nunique(),
            }
            for name, specification in config.specifications.items()
        ]
    )
    duration_summary, duration_bins = _duration_outputs(baseline)
    repetition = _repetition_summary(baseline)
    years = pd.DataFrame({"year": range(2009, pd.Timestamp(config.sample_end).year + 1)})
    year_counts = (
        baseline.groupby("year").size().rename("event_count").reset_index()
        if not baseline.empty
        else pd.DataFrame(columns=["year", "event_count"])
    )
    by_year = years.merge(year_counts, on="year", how="left").fillna(
        {"event_count": 0}
    )
    by_year["event_count"] = by_year["event_count"].astype(int)
    by_year["DataAsOfDate"] = config.sample_end
    by_year["IsPartialYear"] = by_year["year"].eq(pd.Timestamp(config.sample_end).year)

    conflicts = (
        pd.concat(st_conflicts, ignore_index=True)
        if st_conflicts
        else pd.DataFrame(
            columns=[
                "stock_code",
                "date",
                "stock_name",
                "isST",
                "name_ST_flag",
                "final_ST_flag",
                "conflict_type",
            ]
        )
    )
    st_deleted = aggregate_exclusions["baseline"]["ST_during_consolidation_or_event"]
    primary_event_count = int(
        baseline["event_date"].ge(pd.Timestamp(config.sample_start_primary)).sum()
    )
    sample_period_decision = pd.DataFrame(
        [
            {
                "PrimaryStart": config.sample_start_primary,
                "PrimaryEventCount": primary_event_count,
                "FinalAnalysisStart": config.sample_start_fallback,
                "FinalAnalysisEnd": config.sample_end,
                "FallbackTo2009Used": True,
                "FinalEventCount": len(baseline),
                "DecisionNote": "2009 start retained as requested; no event threshold was changed to target a sample size.",
            }
        ]
    )
    screening_audit = {
        "DataAsOfDate": config.sample_end,
        "CurrentAdjustmentType": config.current_adjustment_type,
        "TargetAdjustmentType": config.target_adjustment_type,
        "PriceConventionUnified": True,
        "TotalProcessedStockRecords": total_records,
        "MissingAdjustedCloseRecords": total_missing_adjusted_close,
        "SuspensionRecords": total_suspended,
        "SuspensionRowsWithNonmissingAdjustedPrice": suspended_with_nonmissing_price,
        "STNameStatusConflictRecords": len(conflicts),
        "STNameStatusConflictStocks": int(conflicts["stock_code"].nunique()),
        "BaselineCandidatesDeletedBySTRule": st_deleted,
        "BaselineRecent120LowValidRatioExcluded": aggregate_exclusions["baseline"]["recent_120_low_valid_price_ratio"],
        "BaselineRecent120LongSuspensionExcluded": aggregate_exclusions["baseline"]["recent_120_long_suspension"],
        "BaselinePeakWindowLowValidRatioExcluded": aggregate_exclusions["baseline"]["peak_window_low_valid_price_ratio"],
        "BaselinePeakWindowLongSuspensionExcluded": aggregate_exclusions["baseline"]["peak_window_long_suspension"],
        "BaselinePeakWindowBeforeCalendarOrNoPriceExcluded": aggregate_exclusions["baseline"]["peak_window_before_calendar_or_no_price"],
        "BaselineEventCount": len(baseline),
        "Primary2015EventCount": primary_event_count,
        "FinalAnalysisStart": config.sample_start_fallback,
        "FallbackTo2009Used": True,
        "BaselineUniqueStockCount": int(baseline["stock_code"].nunique()),
        "StrictEventCount": len(strict),
        "RelaxedEventCount": len(relaxed),
        "IndustryUsedForSelection": False,
        "TurnoverUsedForSelection": False,
        "FutureOutcomesUsedForSelection": False,
    }

    paths = {
        "baseline": output_dir / "baseline_events.csv",
        "strict": output_dir / "strict_events.csv",
        "relaxed": output_dir / "relaxed_events.csv",
        "specification_summary": output_dir / "specification_event_counts.csv",
        "funnel": output_dir / "sample_selection_funnel.csv",
        "duration_summary": output_dir / "consolidation_duration_summary.csv",
        "duration_bins": output_dir / "consolidation_duration_bins.csv",
        "repetition": output_dir / "event_repetition_summary.csv",
        "by_year": output_dir / "baseline_events_by_year.csv",
        "st_conflicts": output_dir / "st_name_status_conflicts.csv",
        "screening_audit": output_dir / "screening_audit.json",
        "sample_period_decision": output_dir / "sample_period_decision.csv",
    }
    for name, frame in (
        ("baseline", baseline),
        ("strict", strict),
        ("relaxed", relaxed),
    ):
        export = frame.drop(columns=["_calendar_index"], errors="ignore")
        export.to_csv(paths[name], index=False, encoding="utf-8-sig")
    specification_summary.to_csv(
        paths["specification_summary"], index=False, encoding="utf-8-sig"
    )
    funnel.to_csv(paths["funnel"], index=False, encoding="utf-8-sig")
    duration_summary.to_csv(
        paths["duration_summary"], index=False, encoding="utf-8-sig"
    )
    duration_bins.to_csv(paths["duration_bins"], index=False, encoding="utf-8-sig")
    repetition.to_csv(paths["repetition"], index=False, encoding="utf-8-sig")
    by_year.to_csv(paths["by_year"], index=False, encoding="utf-8-sig")
    conflicts.to_csv(paths["st_conflicts"], index=False, encoding="utf-8-sig")
    sample_period_decision.to_csv(
        paths["sample_period_decision"], index=False, encoding="utf-8-sig"
    )
    paths["screening_audit"].write_text(
        json.dumps(screening_audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\nSpecification counts")
    print(specification_summary.to_string(index=False))
    print("\nBaseline funnel")
    print(funnel.to_string(index=False))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Adaptive >=120-market-day consolidation event screening."
    )
    parser.add_argument(
        "--config", default="config/methodology_config.json"
    )
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = load_methodology_config(config_path)
    paths = run_adaptive_screening(config, config_path)
    print("\nSaved adaptive event outputs:")
    for label, path in paths.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
