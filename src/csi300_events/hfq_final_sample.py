from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .acquisition import load_processed_stock
from .adaptive_pipeline import (
    StockContext,
    _apply_cooldown,
    _build_context,
    _load_name_history,
    _max_consecutive_true,
    _price_extrema,
    _quality_metrics,
    _range_sum,
)
from .methodology import MethodologyConfig, load_methodology_config


@dataclass(frozen=True)
class ConsolidationVersion:
    name: str
    bandwidth_threshold: float
    trend_slope_threshold: float


@dataclass
class LinearTrendState:
    prefix_price: np.ndarray
    prefix_x_price: np.ndarray
    next_valid: np.ndarray
    previous_valid: np.ndarray


def _resolve(value: str, config_path: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()


def _write_csv(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _write_json(value: dict[str, object], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_final_config(path: str | Path) -> tuple[dict[str, object], Path]:
    config_path = Path(path).resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    expected = {
        "A_main": (0.25, 0.0004),
        "B_strict": (0.20, 0.0003),
        "C_loose": (0.30, 0.0006),
    }
    if raw.get("sample_price_source") != "eastmoney_hfq":
        raise ValueError("The final sample price source must be eastmoney_hfq")
    versions = raw.get("versions", {})
    if set(versions) != set(expected):
        raise ValueError("versions must be A_main, B_strict and C_loose")
    for name, (bandwidth, slope) in expected.items():
        actual = versions[name]
        if not (
            np.isclose(float(actual["bandwidth_threshold"]), bandwidth)
            and np.isclose(float(actual["trend_slope_threshold"]), slope)
        ):
            raise ValueError(f"{name} thresholds do not match the requested design")
    if not np.isclose(float(raw.get("breakout_threshold", np.nan)), 0.005):
        raise ValueError("Breakout threshold must remain 0.5% for all versions")
    frozen_dates = {
        "price_analysis_start": "2015-01-01",
        "price_target_end": "2026-09-01",
        "event_start": "2016-01-01",
        "event_end": "2026-06-08",
    }
    for field, expected_value in frozen_dates.items():
        if raw.get(field) != expected_value:
            raise ValueError(f"{field} must remain {expected_value}")
    if raw.get("return_horizons") != [3, 5, 20, 60]:
        raise ValueError("Return horizons must remain [3, 5, 20, 60]")
    if int(raw.get("primary_return_horizon", -1)) != 20:
        raise ValueError("The primary return horizon must remain 20 market days")
    if int(raw.get("relative_volume_lookback", -1)) != 60:
        raise ValueError("RelativeVolume must use the prior 60 normal trading days")
    return raw, config_path


def _make_linear_state(context: StockContext) -> LinearTrendState:
    x = np.arange(len(context.dates), dtype=float)
    valid = context.valid_price
    safe_price = np.where(valid, context.close, 0.0)
    prefix_price = np.concatenate(([0.0], np.cumsum(safe_price)))
    prefix_x_price = np.concatenate(([0.0], np.cumsum(x * safe_price)))

    next_valid = np.full(len(valid), -1, dtype=np.int64)
    next_position = -1
    for position in range(len(valid) - 1, -1, -1):
        if valid[position]:
            next_position = position
        next_valid[position] = next_position
    previous_valid = np.full(len(valid), -1, dtype=np.int64)
    previous_position = -1
    for position in range(len(valid)):
        if valid[position]:
            previous_position = position
        previous_valid[position] = previous_position
    return LinearTrendState(
        prefix_price=prefix_price,
        prefix_x_price=prefix_x_price,
        next_valid=next_valid,
        previous_valid=previous_valid,
    )


def _linear_trend(
    context: StockContext,
    state: LinearTrendState,
    start: int,
    end: int,
) -> tuple[float, float, float]:
    count = _range_sum(context.prefix_n, start, end)
    if count < 2:
        return np.nan, np.nan, np.nan
    sum_x = _range_sum(context.prefix_x, start, end)
    sum_xx = _range_sum(context.prefix_xx, start, end)
    sum_price = float(state.prefix_price[end + 1] - state.prefix_price[start])
    sum_x_price = float(
        state.prefix_x_price[end + 1] - state.prefix_x_price[start]
    )
    denominator = sum_xx - sum_x * sum_x / count
    mean_price = sum_price / count
    if denominator <= 0 or not np.isfinite(mean_price) or mean_price <= 0:
        return np.nan, np.nan, np.nan
    beta = (sum_x_price - sum_x * sum_price / count) / denominator
    trend_slope = beta / mean_price

    first_position = int(state.next_valid[start])
    last_position = int(state.previous_valid[end])
    if (
        first_position < start
        or first_position > end
        or last_position < start
        or last_position > end
        or context.close[first_position] <= 0
    ):
        endpoint_return = np.nan
    else:
        endpoint_return = float(
            context.close[last_position] / context.close[first_position] - 1.0
        )
    return float(beta), float(trend_slope), endpoint_return


def _candidate_metrics(
    context: StockContext,
    state: LinearTrendState,
    start: int,
    end: int,
    methodology: MethodologyConfig,
) -> dict[str, object]:
    quality = _quality_metrics(context, start, end, methodology)
    high, low, high_position, _ = _price_extrema(context, start, end)
    bandwidth = high / low - 1.0 if high > 0 and low > 0 else np.nan
    beta, trend_slope, endpoint_return = _linear_trend(
        context, state, start, end
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
        "trend_slope": trend_slope,
        "endpoint_return": endpoint_return,
    }


def _detect_consolidation(
    context: StockContext,
    state: LinearTrendState,
    event_position: int,
    version: ConsolidationVersion,
    methodology: MethodologyConfig,
) -> dict[str, object] | None:
    minimum = methodology.min_consolidation_duration
    first = _candidate_metrics(
        context, state, event_position - minimum, event_position - 1, methodology
    )
    if (
        not bool(first["passes"])
        or not np.isfinite(first["bandwidth"])
        or float(first["bandwidth"]) > version.bandwidth_threshold
        or not np.isfinite(first["trend_slope"])
        or abs(float(first["trend_slope"])) > version.trend_slope_threshold
    ):
        return None

    last_good = first
    for duration in range(minimum + 1, methodology.max_consolidation_duration + 1):
        start = event_position - duration
        if start < 0:
            break
        candidate = _candidate_metrics(
            context, state, start, event_position - 1, methodology
        )
        if (
            not bool(candidate["passes"])
            or not np.isfinite(candidate["bandwidth"])
            or float(candidate["bandwidth"]) > version.bandwidth_threshold
        ):
            break
        last_good = candidate

    duration = int(last_good["expected"])
    trend_full = float(last_good["endpoint_return"])
    last_good["duration"] = duration
    last_good["duration_censored"] = int(
        duration == methodology.max_consolidation_duration
    )
    last_good["beta"] = float(first["beta"])
    last_good["trend_slope"] = float(first["trend_slope"])
    last_good["trend120"] = float(first["endpoint_return"])
    last_good["trend_full"] = trend_full
    return last_good


def _detect_all_consolidations(
    context: StockContext,
    state: LinearTrendState,
    event_position: int,
    versions: dict[str, ConsolidationVersion],
    methodology: MethodologyConfig,
    breakout_threshold: float,
) -> dict[str, dict[str, object] | None]:
    """Evaluate the same candidate intervals once for all threshold versions."""
    minimum = methodology.min_consolidation_duration
    first = _candidate_metrics(
        context, state, event_position - minimum, event_position - 1, methodology
    )

    # Any longer candidate contains the same recent 120 days and therefore has
    # a resistance at least as high.  If price cannot clear the 120-day
    # resistance, it cannot clear a longer interval either.  This exact
    # pre-check avoids calculating 121--252-day candidates for non-breakout days.
    event_close = context.close[event_position]
    if (
        not np.isfinite(event_close)
        or not np.isfinite(first["high"])
        or event_close / float(first["high"]) - 1.0 < breakout_threshold
    ):
        return {name: None for name in versions}

    def passes(candidate: dict[str, object], version: ConsolidationVersion) -> bool:
        return bool(
            bool(candidate["passes"])
            and np.isfinite(candidate["bandwidth"])
            and float(candidate["bandwidth"]) <= version.bandwidth_threshold
            and np.isfinite(first["trend_slope"])
            and abs(float(first["trend_slope"])) <= version.trend_slope_threshold
        )

    last_good: dict[str, dict[str, object] | None] = {
        name: first.copy() if passes(first, version) else None
        for name, version in versions.items()
    }
    active = {name for name, value in last_good.items() if value is not None}
    for duration in range(minimum + 1, methodology.max_consolidation_duration + 1):
        if not active:
            break
        start = event_position - duration
        if start < 0:
            break
        candidate = _candidate_metrics(
            context, state, start, event_position - 1, methodology
        )
        for name in tuple(active):
            if passes(candidate, versions[name]):
                last_good[name] = candidate.copy()
            else:
                active.remove(name)

    for value in last_good.values():
        if value is None:
            continue
        duration = int(value["expected"])
        trend_full = float(value["endpoint_return"])
        value["duration"] = duration
        value["duration_censored"] = int(
            duration == methodology.max_consolidation_duration
        )
        value["beta"] = float(first["beta"])
        value["trend_slope"] = float(first["trend_slope"])
        value["trend120"] = float(first["endpoint_return"])
        value["trend_full"] = trend_full
    return last_good


def _activity_metrics(
    context: StockContext, event_position: int
) -> dict[str, float]:
    prior_turnover_positions = np.flatnonzero(
        context.normal_trade[:event_position]
        & np.isfinite(context.turnover[:event_position])
        & (context.turnover[:event_position] > 0)
    )
    prior_volume_positions = np.flatnonzero(
        context.normal_trade[:event_position]
        & np.isfinite(context.volume[:event_position])
        & (context.volume[:event_position] > 0)
    )
    turnover_t = context.turnover[event_position]
    volume_t = context.volume[event_position]
    mean_turnover60 = (
        float(np.mean(context.turnover[prior_turnover_positions[-60:]]))
        if len(prior_turnover_positions) >= 60
        else np.nan
    )
    mean_volume60 = (
        float(np.mean(context.volume[prior_volume_positions[-60:]]))
        if len(prior_volume_positions) >= 60
        else np.nan
    )
    return {
        "turnover_t": float(turnover_t) if np.isfinite(turnover_t) else np.nan,
        "mean_turnover60": mean_turnover60,
        "relative_turnover60": (
            float(turnover_t / mean_turnover60)
            if np.isfinite(turnover_t) and mean_turnover60 > 0
            else np.nan
        ),
        "volume_t": float(volume_t) if np.isfinite(volume_t) else np.nan,
        "mean_volume60": mean_volume60,
        "relative_volume60": (
            float(volume_t / mean_volume60)
            if np.isfinite(volume_t) and mean_volume60 > 0
            else np.nan
        ),
    }


def _future_return_metrics(
    context: StockContext,
    event_position: int,
    horizons: list[int],
) -> dict[str, object]:
    """Calculate close-to-close returns on exact future CSI300 market days."""
    result: dict[str, object] = {}
    event_price = context.close[event_position]
    for horizon in horizons:
        target_position = event_position + horizon
        result[f"R{horizon}_target_date"] = pd.NaT
        result[f"R{horizon}"] = np.nan
        result[f"R{horizon}_status"] = "calendar_not_yet_available"
        if target_position >= len(context.dates):
            continue
        result[f"R{horizon}_target_date"] = context.dates[target_position]
        if (
            not np.isfinite(event_price)
            or event_price <= 0
            or not context.valid_price[target_position]
            or not context.normal_trade[target_position]
        ):
            result[f"R{horizon}_status"] = "target_price_or_trade_missing"
            continue
        result[f"R{horizon}"] = float(
            context.close[target_position] / event_price - 1.0
        )
        result[f"R{horizon}_status"] = "available"
    return result


def _evaluate_event(
    context: StockContext,
    state: LinearTrendState,
    event_position: int,
    version: ConsolidationVersion,
    methodology: MethodologyConfig,
    breakout_threshold: float,
    return_horizons: list[int],
) -> tuple[dict[str, object] | None, str]:
    consolidation = _detect_consolidation(
        context, state, event_position, version, methodology
    )
    return _evaluate_detected_event(
        context,
        event_position,
        version,
        methodology,
        breakout_threshold,
        consolidation,
        return_horizons,
    )


def _evaluate_detected_event(
    context: StockContext,
    event_position: int,
    version: ConsolidationVersion,
    methodology: MethodologyConfig,
    breakout_threshold: float,
    consolidation: dict[str, object] | None,
    return_horizons: list[int],
) -> tuple[dict[str, object] | None, str]:
    if consolidation is None:
        return None, "consolidation_not_identified"
    start = int(consolidation["start"])
    end = event_position - 1
    st_days = int(_range_sum(context.prefix_st, start, event_position))
    if st_days:
        return None, "ST_during_consolidation_or_event"

    peak_start = start - methodology.peak_lookback_before_consolidation
    peak_end = start - 1
    if peak_start < 0:
        return None, "invalid_peak_window"
    peak_quality = _quality_metrics(context, peak_start, peak_end, methodology)
    peak_price, _, peak_position, _ = _price_extrema(
        context, peak_start, peak_end
    )
    if (
        float(peak_quality["ratio"]) < methodology.peak_window_min_valid_ratio
        or bool(peak_quality["long_suspension"])
        or not np.isfinite(peak_price)
        or peak_price <= 0
    ):
        return None, "invalid_peak_window"

    drawdown = float(consolidation["low"]) / peak_price - 1.0
    if drawdown > methodology.distress_drawdown_threshold:
        return None, "drawdown_threshold_failed"
    event_close = context.close[event_position]
    breakout_strength = (
        float(event_close / float(consolidation["high"]) - 1.0)
        if np.isfinite(event_close) and event_close > 0
        else np.nan
    )
    if not np.isfinite(breakout_strength) or breakout_strength < breakout_threshold:
        return None, "breakout_threshold_failed"
    if not context.normal_trade[event_position] or not context.valid_price[event_position]:
        return None, "event_day_not_normal_trading"

    activity = _activity_metrics(context, event_position)
    future_returns = _future_return_metrics(
        context, event_position, return_horizons
    )
    record: dict[str, object] = {
        "stock_code": context.stock_code,
        "stock_name": str(context.names[event_position]),
        "event_date": context.dates[event_position],
        "year": int(context.dates[event_position].year),
        "breakout_price": float(event_close),
        "base_period_start": context.dates[start],
        "base_period_end": context.dates[end],
        "Drawdown": drawdown,
        "Duration": int(consolidation["duration"]),
        "bandwidth": float(consolidation["bandwidth"]),
        "trend_beta": float(consolidation["beta"]),
        "trend_slope": float(consolidation["trend_slope"]),
        "trend_strength": float(consolidation["trend120"]),
        "trend_full_period": float(consolidation["trend_full"]),
        "resistance": float(consolidation["high"]),
        "resistance_date": context.dates[int(consolidation["high_position"])],
        "BreakoutStrength": breakout_strength,
        "peak_date": context.dates[peak_position],
        "peak_price": float(peak_price),
        "CSI300_member_t_minus_1": 1,
        "final_ST_flag": int(context.final_st[event_position]),
        "consolidation_ST_days": st_days,
        "valid_price_ratio": float(consolidation["ratio"]),
        "max_consecutive_suspension": _max_consecutive_true(
            context.is_suspended[start:event_position]
        ),
        "peak_window_valid_ratio": float(peak_quality["ratio"]),
        "sample_price_source": "eastmoney_hfq",
        "version": version.name,
        "_calendar_index": event_position,
        **activity,
        "RelativeVolume": activity["relative_volume60"],
        **future_returns,
    }
    return record, "selected_before_cooldown"


def _screen_stock(
    context: StockContext,
    state: LinearTrendState,
    version: ConsolidationVersion,
    methodology: MethodologyConfig,
    breakout_threshold: float,
    return_horizons: list[int],
) -> list[dict[str, object]]:
    sample_positions = np.flatnonzero(
        (context.dates >= pd.Timestamp(methodology.sample_start_primary))
        & (context.dates <= pd.Timestamp(methodology.sample_end))
    )
    known = np.flatnonzero(context.status_known)
    if len(known) == 0:
        return []
    minimum_position = int(known[0]) + (
        methodology.min_consolidation_duration
        + methodology.peak_lookback_before_consolidation
    )
    positions = sample_positions[
        (sample_positions >= minimum_position)
        & context.member_t_minus_1[sample_positions]
    ]
    before_cooldown: list[dict[str, object]] = []
    for event_position in positions:
        record, outcome = _evaluate_event(
            context,
            state,
            int(event_position),
            version,
            methodology,
            breakout_threshold,
            return_horizons,
        )
        if outcome == "selected_before_cooldown" and record is not None:
            before_cooldown.append(record)
    return _apply_cooldown(before_cooldown, methodology.cooldown_market_days)


def _screen_stock_versions(
    context: StockContext,
    state: LinearTrendState,
    versions: dict[str, ConsolidationVersion],
    methodology: MethodologyConfig,
    breakout_threshold: float,
    return_horizons: list[int],
) -> dict[str, list[dict[str, object]]]:
    sample_positions = np.flatnonzero(
        (context.dates >= pd.Timestamp(methodology.sample_start_primary))
        & (context.dates <= pd.Timestamp(methodology.sample_end))
    )
    known = np.flatnonzero(context.status_known)
    if len(known) == 0:
        return {name: [] for name in versions}
    minimum_position = int(known[0]) + (
        methodology.min_consolidation_duration
        + methodology.peak_lookback_before_consolidation
    )
    positions = sample_positions[
        (sample_positions >= minimum_position)
        & context.member_t_minus_1[sample_positions]
    ]
    before_cooldown: dict[str, list[dict[str, object]]] = {
        name: [] for name in versions
    }
    for event_position in positions:
        if (
            not context.normal_trade[event_position]
            or not np.isfinite(context.close[event_position])
            or context.close[event_position] <= 0
        ):
            continue
        detected = _detect_all_consolidations(
            context,
            state,
            int(event_position),
            versions,
            methodology,
            breakout_threshold,
        )
        for name, version in versions.items():
            record, outcome = _evaluate_detected_event(
                context,
                int(event_position),
                version,
                methodology,
                breakout_threshold,
                detected[name],
                return_horizons,
            )
            if outcome == "selected_before_cooldown" and record is not None:
                before_cooldown[name].append(record)
    return {
        name: _apply_cooldown(records, methodology.cooldown_market_days)
        for name, records in before_cooldown.items()
    }


def _trend_audit_for_stock(
    context: StockContext,
    state: LinearTrendState,
    old_events: pd.DataFrame,
    new_events: list[dict[str, object]],
    methodology: MethodologyConfig,
    main_version: ConsolidationVersion,
) -> list[dict[str, object]]:
    old = old_events.copy()
    if len(old):
        old["event_date"] = pd.to_datetime(old["event_date"])
        old["ConsolidationStart"] = pd.to_datetime(old["ConsolidationStart"])
    old_by_date = {pd.Timestamp(row.event_date): row for row in old.itertuples(index=False)}
    new_by_date = {
        pd.Timestamp(row["event_date"]): row for row in new_events
    }
    date_positions = {pd.Timestamp(date): i for i, date in enumerate(context.dates)}
    rows: list[dict[str, object]] = []
    for event_date in sorted(set(old_by_date) | set(new_by_date)):
        new_row = new_by_date.get(event_date)
        if new_row is not None:
            bandwidth = float(new_row["bandwidth"])
            trend_slope = float(new_row["trend_slope"])
            trend120 = float(new_row["trend_strength"])
            base_start = new_row["base_period_start"]
            base_end = new_row["base_period_end"]
        else:
            old_row = old_by_date[event_date]
            event_position = date_positions.get(event_date)
            start_position = date_positions.get(pd.Timestamp(old_row.ConsolidationStart))
            if event_position is None or start_position is None:
                bandwidth = trend_slope = trend120 = np.nan
                base_start = old_row.ConsolidationStart
                base_end = pd.NaT
            else:
                metrics = _candidate_metrics(
                    context,
                    state,
                    start_position,
                    event_position - 1,
                    methodology,
                )
                _, trend_slope, trend120 = _linear_trend(
                    context,
                    state,
                    event_position - methodology.min_consolidation_duration,
                    event_position - 1,
                )
                bandwidth = float(metrics["bandwidth"])
                base_start = context.dates[start_position]
                base_end = context.dates[event_position - 1]
        passed_bandwidth = int(
            np.isfinite(bandwidth)
            and bandwidth <= main_version.bandwidth_threshold
        )
        passed_trend = int(
            np.isfinite(trend_slope)
            and abs(trend_slope) <= main_version.trend_slope_threshold
        )
        final_selection = int(event_date in new_by_date)
        old_selection = int(event_date in old_by_date)
        rows.append(
            {
                "stock_code": context.stock_code,
                "stock_name": str(
                    new_row["stock_name"]
                    if new_row is not None
                    else getattr(old_by_date[event_date], "stock_name", "")
                ),
                "event_date": event_date,
                "base_period_start": base_start,
                "base_period_end": base_end,
                "bandwidth": bandwidth,
                "trend_slope": trend_slope,
                "trend120": trend120,
                "passed_bandwidth": passed_bandwidth,
                "passed_trend": passed_trend,
                "old_hfq_event": old_selection,
                "final_selection": final_selection,
                "difference_reason": (
                    "selected_final"
                    if final_selection
                    else "filtered_by_TrendSlope"
                    if old_selection and not passed_trend
                    else "not_selected_after_full_rerun"
                ),
            }
        )
    return rows


def _event_chart(
    event: pd.Series,
    daily: pd.DataFrame,
    output_path: Path,
    *,
    compact: bool = False,
) -> None:
    event_date = pd.Timestamp(event["event_date"])
    peak_date = pd.Timestamp(event["peak_date"])
    base_start = pd.Timestamp(event["base_period_start"])
    base_end = pd.Timestamp(event["base_period_end"])
    block = daily.loc[
        daily["trade_date"].between(peak_date, event_date),
        ["trade_date", "adjusted_close", "volume"],
    ].copy()
    block["adjusted_close"] = pd.to_numeric(block["adjusted_close"], errors="coerce")
    block["volume"] = pd.to_numeric(block["volume"], errors="coerce")

    figure, (price_axis, volume_axis) = plt.subplots(
        2,
        1,
        figsize=(8.4, 4.8) if not compact else (6.4, 3.6),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
        constrained_layout=True,
    )
    price_axis.plot(block["trade_date"], block["adjusted_close"], color="#1F4E78", linewidth=1.4)
    price_axis.axvspan(peak_date, base_start, color="#F4B183", alpha=0.24, label="下跌阶段")
    price_axis.axvspan(base_start, base_end, color="#70AD47", alpha=0.17, label="横盘区域")
    price_axis.scatter([event_date], [event["breakout_price"]], color="#C00000", s=35, zorder=4, label="突破点")
    price_axis.axhline(float(event["resistance"]), color="#7F7F7F", linestyle="--", linewidth=0.9)
    price_axis.set_ylabel("hfq 收盘价")
    price_axis.set_title(f"{event['stock_code']} {event['stock_name']}  {event_date:%Y-%m-%d}", fontsize=10)
    price_axis.legend(loc="best", fontsize=7, ncol=3)
    price_axis.grid(axis="y", color="#D9D9D9", linewidth=0.5)

    volume_axis.bar(block["trade_date"], block["volume"], color="#9DC3E6", width=1.0)
    event_volume = block.loc[block["trade_date"].eq(event_date), "volume"]
    if len(event_volume) and pd.notna(event_volume.iloc[0]):
        volume_axis.bar([event_date], [event_volume.iloc[0]], color="#C00000", width=1.2)
    volume_axis.set_ylabel("成交量")
    volume_axis.grid(axis="y", color="#E7E6E6", linewidth=0.4)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=160, facecolor="white")
    plt.close(figure)


def _build_charts(
    sample: pd.DataFrame,
    stock_dir: Path,
    output_dir: Path,
) -> tuple[list[Path], Path]:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    chart_dir = output_dir / "event_charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    for old_chart in chart_dir.glob("*.png"):
        old_chart.unlink()
    paths: list[Path] = []
    for number, (_, event) in enumerate(sample.iterrows(), start=1):
        daily = load_processed_stock(
            stock_dir / f"{str(event['stock_code']).replace('.', '_')}.csv.gz"
        )
        path = chart_dir / (
            f"{number:02d}_{str(event['stock_code']).replace('.', '_')}_{pd.Timestamp(event['event_date']):%Y%m%d}.png"
        )
        _event_chart(event, daily, path)
        paths.append(path)
    montage_path = output_dir / "final_event_charts_10.png"
    rows = max(1, int(np.ceil(len(paths) / 2)))
    figure, axes = plt.subplots(rows, 2, figsize=(20, 6.4 * rows))
    axes_array = np.atleast_1d(axes).reshape(-1)
    for axis, path in zip(axes_array, paths):
        axis.imshow(plt.imread(path))
        axis.axis("off")
    for axis in axes_array[len(paths):]:
        axis.axis("off")
    figure.suptitle("东方财富 hfq 最终事件随机样本（10 个）", fontsize=18, fontweight="bold")
    figure.tight_layout(rect=(0, 0, 1, 0.99))
    figure.savefig(montage_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return paths, montage_path


def run_final_hfq_sample(config_path: str | Path) -> dict[str, Path]:
    raw, final_config_path = load_final_config(config_path)
    methodology_path = _resolve(str(raw["base_methodology_config"]), final_config_path)
    methodology = load_methodology_config(methodology_path)
    stock_dir = _resolve(str(raw["processed_stock_dir"]), final_config_path)
    output_dir = _resolve(str(raw["output_dir"]), final_config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    membership = pd.read_csv(
        methodology.resolve(methodology.membership_path, methodology_path),
        dtype={"stock_code": "string"},
    )
    membership["in_date"] = pd.to_datetime(membership["in_date"], errors="raise")
    membership["out_date"] = pd.to_datetime(membership["out_date"], errors="coerce")
    calendar = pd.read_csv(methodology.resolve(methodology.calendar_path, methodology_path))
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="raise")
    calendar = (
        calendar.loc[
            calendar["trade_date"].le(pd.Timestamp(str(raw["price_target_end"])))
        ]
        .sort_values("trade_date")
        .drop_duplicates("trade_date")
    )
    histories = _load_name_history(
        methodology.resolve(methodology.raw_dir, methodology_path)
        / "baostock"
        / "hs300_snapshots"
    )
    versions = {
        name: ConsolidationVersion(name=name, **values)
        for name, values in raw["versions"].items()
    }
    breakout_threshold = float(raw["breakout_threshold"])
    return_horizons = [int(value) for value in raw["return_horizons"]]
    target_end = pd.Timestamp(str(raw["price_target_end"]))
    if calendar.empty or calendar["trade_date"].max() != target_end:
        observed_end = None if calendar.empty else calendar["trade_date"].max()
        raise ValueError(
            "The processed market calendar must include the frozen price target end "
            f"{target_end:%Y-%m-%d}; observed end={observed_end}"
        )
    maximum_horizon = max(return_horizons)
    if len(calendar) <= maximum_horizon:
        raise ValueError("The processed market calendar is too short for the return horizons")
    latest_event_date_with_full_calendar = calendar["trade_date"].iloc[
        -(maximum_horizon + 1)
    ]
    configured_event_end = pd.Timestamp(methodology.sample_end)
    if configured_event_end != latest_event_date_with_full_calendar:
        raise ValueError(
            "Event end must be exactly the last market day with a complete R60 calendar "
            f"window: configured={configured_event_end:%Y-%m-%d}, "
            f"required={latest_event_date_with_full_calendar:%Y-%m-%d}"
        )
    old_events_value = raw.get("original_hfq_events_path")
    old_events_path = (
        _resolve(str(old_events_value), final_config_path)
        if old_events_value
        else None
    )
    if old_events_path is not None and old_events_path.exists():
        old_events = pd.read_csv(
            old_events_path,
            dtype={"stock_code": "string"},
            low_memory=False,
        )
        old_event_dates = pd.to_datetime(old_events["event_date"], errors="coerce")
        old_events = old_events.loc[
            old_event_dates.between(
                pd.Timestamp(methodology.sample_start_primary),
                pd.Timestamp(methodology.sample_end),
            )
        ].copy()
    else:
        old_events = pd.DataFrame(
            columns=["stock_code", "stock_name", "event_date", "ConsolidationStart"]
        )

    all_events: dict[str, list[dict[str, object]]] = {name: [] for name in versions}
    trend_audit_rows: list[dict[str, object]] = []
    empty_processed_codes: list[str] = []
    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    for number, stock_code in enumerate(codes, start=1):
        daily_path = stock_dir / f"{stock_code.replace('.', '_')}.csv.gz"
        if not daily_path.exists():
            raise FileNotFoundError(f"Eastmoney hfq processed cache is missing: {daily_path}")
        daily = load_processed_stock(daily_path)
        if daily.empty:
            empty_processed_codes.append(stock_code)
            continue
        context, _ = _build_context(daily, calendar, membership, histories)
        state = _make_linear_state(context)
        stock_events = _screen_stock_versions(
            context,
            state,
            versions,
            methodology,
            breakout_threshold,
            return_horizons,
        )
        for name, selected in stock_events.items():
            all_events[name].extend(selected)
        trend_audit_rows.extend(
            _trend_audit_for_stock(
                context,
                state,
                old_events.loc[old_events["stock_code"].eq(stock_code)],
                stock_events["A_main"],
                methodology,
                versions["A_main"],
            )
        )
        if number % 25 == 0 or number == len(codes):
            print(
                f"  final hfq screening: {number}/{len(codes)} stocks; "
                f"main events so far={len(all_events['A_main'])}",
                flush=True,
            )

    frames: dict[str, pd.DataFrame] = {}
    files: dict[str, Path] = {}
    for name, records in all_events.items():
        frame = pd.DataFrame(records)
        if len(frame):
            frame = frame.sort_values(["event_date", "stock_code"]).reset_index(drop=True)
        frames[name] = frame
        files[f"events_{name}"] = _write_csv(
            frame, output_dir / f"hfq_{name.lower()}_events.csv"
        )

    final = frames["A_main"].copy()
    required_first = [
        "stock_code",
        "stock_name",
        "event_date",
        "breakout_price",
        "base_period_start",
        "base_period_end",
        "bandwidth",
        "trend_slope",
        "trend_strength",
        "RelativeVolume",
        "Drawdown",
        "Duration",
        "BreakoutStrength",
        "R3",
        "R5",
        "R20",
        "R60",
    ]
    remaining = [column for column in final.columns if column not in required_first]
    final = final[required_first + remaining]
    files["sample_hfq_final"] = _write_csv(
        final, output_dir / "sample_hfq_final.csv"
    )

    completeness_rows: list[dict[str, object]] = []
    model_count_rows: list[dict[str, object]] = []
    model_predictors = ["RelativeVolume", "Drawdown", "Duration", "BreakoutStrength"]
    for horizon in return_horizons:
        return_column = f"R{horizon}"
        status_column = f"R{horizon}_status"
        available = final[return_column].notna()
        complete_model = final[[return_column, *model_predictors]].notna().all(axis=1)
        status_counts = final[status_column].value_counts(dropna=False).to_dict()
        completeness_rows.append(
            {
                "horizon_market_days": horizon,
                "event_count": len(final),
                "return_available_count": int(available.sum()),
                "return_missing_count": int((~available).sum()),
                "return_available_ratio": float(available.mean()) if len(final) else np.nan,
                "calendar_not_yet_available": int(status_counts.get("calendar_not_yet_available", 0)),
                "target_price_or_trade_missing": int(status_counts.get("target_price_or_trade_missing", 0)),
            }
        )
        model_count_rows.append(
            {
                "dependent_variable": return_column,
                "complete_case_count": int(complete_model.sum()),
                "excluded_for_missing_count": int((~complete_model).sum()),
                "predictors": "RelativeVolume + Drawdown + Duration + BreakoutStrength",
            }
        )
        if horizon == int(raw["primary_return_horizon"]):
            files["regression_sample_R20"] = _write_csv(
                final.loc[complete_model].copy(),
                output_dir / "regression_sample_R20.csv",
            )
    completeness = pd.DataFrame(completeness_rows)
    files["return_completeness"] = _write_csv(
        completeness, output_dir / "return_window_completeness.csv"
    )
    files["model_sample_counts"] = _write_csv(
        pd.DataFrame(model_count_rows), output_dir / "model_sample_counts.csv"
    )

    descriptive_variables = [
        "RelativeVolume",
        "Drawdown",
        "Duration",
        "BreakoutStrength",
        *[f"R{value}" for value in return_horizons],
    ]
    descriptive = (
        final[descriptive_variables]
        .describe(percentiles=[0.25, 0.5, 0.75])
        .T.reset_index()
        .rename(columns={"index": "variable", "50%": "median"})
    )
    files["descriptive_statistics"] = _write_csv(
        descriptive, output_dir / "descriptive_statistics.csv"
    )

    trend_report = pd.DataFrame(trend_audit_rows).sort_values(
        ["event_date", "stock_code"]
    )
    files["trend_filter_report"] = _write_csv(
        trend_report, output_dir / "trend_filter_report.csv"
    )
    robustness = pd.DataFrame(
        [
            {
                "version": name,
                "BandwidthThreshold": version.bandwidth_threshold,
                "TrendSlopeThreshold": version.trend_slope_threshold,
                "BreakoutThreshold": breakout_threshold,
                "event_count": len(frames[name]),
                "stock_count": int(frames[name]["stock_code"].nunique()),
            }
            for name, version in versions.items()
        ]
    )
    files["robustness"] = _write_csv(
        robustness, output_dir / "robustness_consolidation_thresholds.csv"
    )
    years = pd.DataFrame(
        {
            "year": range(
                pd.Timestamp(methodology.sample_start_primary).year,
                pd.Timestamp(methodology.sample_end).year + 1,
            )
        }
    )
    for name, frame in frames.items():
        counts = frame.groupby("year").size().rename(name).reset_index()
        years = years.merge(counts, on="year", how="left")
    years = years.fillna(0)
    for name in frames:
        years[name] = years[name].astype(int)
    files["events_by_year"] = _write_csv(
        years, output_dir / "hfq_final_events_by_year.csv"
    )

    original_keys = set(
        zip(old_events["stock_code"].astype(str), pd.to_datetime(old_events["event_date"]))
    )
    final_keys = set(
        zip(final["stock_code"].astype(str), pd.to_datetime(final["event_date"]))
    )
    trend_filtered_old = int(
        (
            trend_report["old_hfq_event"].eq(1)
            & trend_report["passed_trend"].eq(0)
        ).sum()
    )
    summary = {
        "sample_price_source": "eastmoney_hfq",
        "price_analysis_start": str(raw["price_analysis_start"]),
        "price_target_end": str(raw["price_target_end"]),
        "calendar_observed_end": calendar["trade_date"].max().strftime("%Y-%m-%d"),
        "event_start": methodology.sample_start_primary,
        "event_end": methodology.sample_end,
        "latest_event_date_with_full_calendar_R60": (
            latest_event_date_with_full_calendar.strftime("%Y-%m-%d")
        ),
        "all_allowed_event_dates_have_full_calendar_R60": True,
        "historical_codes_with_no_rows_in_required_period": len(empty_processed_codes),
        "final_main_event_count": len(final),
        "future_returns_computed": True,
        "CAR_computed": False,
        "return_horizons_market_days": return_horizons,
        "primary_return_horizon": int(raw["primary_return_horizon"]),
        "future_return_definition": str(raw["future_return_definition"]),
        "volume_used_for_event_selection": False,
        "relative_volume_definition": "volume_t / mean(volume over prior 60 normal trading days)",
        "relative_turnover_definition": "turnover_t / mean(turnover_rate over prior 60 normal trading days)",
        "return_completeness": {
            f"R{int(row['horizon_market_days'])}": {
                "available": int(row["return_available_count"]),
                "missing": int(row["return_missing_count"]),
            }
            for row in completeness.to_dict("records")
        },
        "versions": {
            row["version"]: {
                "event_count": int(row["event_count"]),
                "stock_count": int(row["stock_count"]),
                "bandwidth_threshold": float(row["BandwidthThreshold"]),
                "trend_slope_threshold": float(row["TrendSlopeThreshold"]),
            }
            for row in robustness.to_dict("records")
        },
    }
    if len(old_events):
        summary["legacy_comparison"] = {
            "original_hfq_event_count_in_new_period": len(old_events),
            "event_count_change_vs_original": len(final) - len(old_events),
            "old_events_filtered_by_TrendSlope": trend_filtered_old,
            "old_new_intersection": len(original_keys & final_keys),
            "old_only": len(original_keys - final_keys),
            "new_only": len(final_keys - original_keys),
        }
    files["summary"] = _write_json(summary, output_dir / "hfq_final_summary.json")

    sample_size = min(int(raw["chart_sample_size"]), len(final))
    chart_sample = final.sample(
        n=sample_size, random_state=int(raw["random_seed"])
    ).sort_values(["event_date", "stock_code"])
    files["chart_sample"] = _write_csv(
        chart_sample, output_dir / "event_chart_sample.csv"
    )
    chart_paths, chart_montage = _build_charts(chart_sample, stock_dir, output_dir)
    files["chart_montage"] = chart_montage
    manifest = pd.DataFrame(
        {
            "stock_code": chart_sample["stock_code"].astype(str).tolist(),
            "stock_name": chart_sample["stock_name"].astype(str).tolist(),
            "event_date": chart_sample["event_date"].tolist(),
            "chart_path": [str(path) for path in chart_paths],
        }
    )
    files["chart_manifest"] = _write_csv(
        manifest, output_dir / "event_chart_manifest.csv"
    )

    report_lines = [
        "# 东方财富 hfq 正式事件样本",
        "",
        f"- sample_price_source：eastmoney_hfq",
        f"- main 事件数：{len(final)}",
        "",
        f"事件区间：{methodology.sample_start_primary} 至 {methodology.sample_end}。",
        f"行情目标截止日：{raw['price_target_end']}；本次交易日历实际截止：{calendar['trade_date'].max():%Y-%m-%d}。",
        "已计算 R3、R5、R20、R60；定义为事件收盘价到第 h 个沪深300市场交易日收盘价的后复权收益，不填充停牌或缺失价格。",
        "R20 是主结果。RelativeVolume 使用突破日成交量除以前60个正常交易日平均成交量。",
        "成交量不参与事件筛选；尚未计算CAR或运行回归。",
    ]
    report_path = output_dir / "hfq_final_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    files["report"] = report_path
    return files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the final Eastmoney hfq event pool with normalized linear TrendSlope."
    )
    parser.add_argument(
        "--config",
        default="config/hfq_final_sample.json",
        help="Final hfq sample configuration path",
    )
    args = parser.parse_args()
    outputs = run_final_hfq_sample(args.config)
    print("Final hfq sample outputs:")
    for name, path in outputs.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
