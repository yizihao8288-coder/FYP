from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .acquisition import load_processed_stock
from .adaptive_pipeline import (
    EVENT_COLUMNS,
    _apply_cooldown,
    _build_context,
    _detect_consolidation,
    _evaluate_stock_specification,
    _load_name_history,
    _membership_at_t_minus_1,
    _price_extrema,
    _quality_metrics,
    _recent_120_metrics,
)
from .eastmoney_layer import (
    PriceLayerConfig,
    _cache_is_complete,
    _load_eastmoney,
    _safe_code,
    build_baostock_processed,
    build_eastmoney_processed,
    download_baostock_full_qfq,
    download_eastmoney_histories,
    download_yahoo_histories,
    load_price_layer_config,
    research_bounds_by_stock,
)
from .methodology import MethodologyConfig, load_methodology_config


PRICE_COLUMNS = ["open", "high", "low", "close"]
METRIC_COLUMNS = [
    "drawdown",
    "bandwidth",
    "trend120",
    "resistance",
    "breakout_strength",
]


def audit_cache_inventory(
    layer: PriceLayerConfig,
    layer_path: Path,
    codes: list[str],
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    east_root = layer.resolve(layer.eastmoney_raw_dir, layer_path)
    bao_root = layer.resolve(layer.baostock_raw_dir, layer_path)
    rows: list[dict[str, object]] = []
    for stock_code in codes:
        start, end = bounds[stock_code]
        safe = _safe_code(stock_code)
        row: dict[str, object] = {
            "stock_code": stock_code,
            "required_start": start,
            "required_end": end,
        }
        for adjustment in layer.eastmoney_adjustments:
            data_path = east_root / adjustment / f"{safe}.csv.gz"
            metadata_path = east_root / adjustment / f"{safe}.metadata.json"
            prefix = f"Eastmoney_{adjustment}"
            metadata = (
                json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata_path.exists()
                else {}
            )
            row[f"{prefix}_cache_complete"] = int(
                data_path.exists()
                and _cache_is_complete(metadata_path, start, end)
            )
            row[f"{prefix}_row_count"] = metadata.get("row_count")
            row[f"{prefix}_observed_start"] = metadata.get("observed_start")
            row[f"{prefix}_observed_end"] = metadata.get("observed_end")
            row[f"{prefix}_download_status"] = metadata.get("download_status")
        bao_data = bao_root / f"{safe}.csv.gz"
        bao_metadata_path = bao_root / f"{safe}.metadata.json"
        bao_metadata = (
            json.loads(bao_metadata_path.read_text(encoding="utf-8"))
            if bao_metadata_path.exists()
            else {}
        )
        row["BaoStock_qfq_cache_complete"] = int(
            bao_data.exists()
            and _cache_is_complete(bao_metadata_path, start, end)
        )
        row["BaoStock_qfq_row_count"] = bao_metadata.get("row_count")
        row["BaoStock_qfq_observed_start"] = bao_metadata.get("observed_start")
        row["BaoStock_qfq_observed_end"] = bao_metadata.get("observed_end")
        row["BaoStock_qfq_download_status"] = bao_metadata.get("download_status")
        rows.append(row)
    inventory = pd.DataFrame(rows).sort_values("stock_code")
    summary_rows: list[dict[str, object]] = []
    for source in [
        "Eastmoney_raw",
        "Eastmoney_qfq",
        "Eastmoney_hfq",
        "BaoStock_qfq",
    ]:
        complete = int(inventory[f"{source}_cache_complete"].sum())
        summary_rows.append(
            {
                "source": source,
                "required_stock_count": len(inventory),
                "complete_stock_count": complete,
                "missing_stock_count": len(inventory) - complete,
                "completion_ratio": complete / len(inventory) if len(inventory) else np.nan,
            }
        )
    return inventory, pd.DataFrame(summary_rows)


def _write_csv(frame: pd.DataFrame, path: Path, encoding: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding=encoding)
    return path


def _write_json(value: object, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return path


def _load_inputs(
    methodology: MethodologyConfig, methodology_path: Path
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, tuple[np.ndarray, np.ndarray]]]:
    membership = pd.read_csv(
        methodology.resolve(methodology.membership_path, methodology_path),
        dtype={"stock_code": "string"},
    )
    membership["in_date"] = pd.to_datetime(membership["in_date"], errors="raise")
    membership["out_date"] = pd.to_datetime(membership["out_date"], errors="coerce")
    calendar = pd.read_csv(methodology.resolve(methodology.calendar_path, methodology_path))
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="raise")
    calendar = (
        calendar.loc[calendar["trade_date"].le(methodology.sample_end)]
        .sort_values("trade_date")
        .drop_duplicates("trade_date")
        .reset_index(drop=True)
    )
    histories = _load_name_history(
        methodology.resolve(methodology.raw_dir, methodology_path)
        / "baostock"
        / "hs300_snapshots"
    )
    return membership, calendar, histories


def _universe_by_year(
    membership: pd.DataFrame, calendar: pd.DataFrame
) -> tuple[dict[int, set[str]], dict[str, np.ndarray]]:
    dates = pd.DatetimeIndex(calendar["trade_date"])
    years = dates.year.to_numpy()
    result: dict[int, set[str]] = defaultdict(set)
    masks: dict[str, np.ndarray] = {}
    for stock_code, intervals in membership.groupby("stock_code", sort=False):
        mask = _membership_at_t_minus_1(dates, intervals)
        masks[str(stock_code)] = mask
        for year in np.unique(years[mask]):
            result[int(year)].add(str(stock_code))
    return dict(result), masks


def audit_eastmoney_prices(
    layer: PriceLayerConfig,
    layer_path: Path,
    methodology: MethodologyConfig,
    membership: pd.DataFrame,
    calendar: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    raw_root = layer.resolve(layer.eastmoney_raw_dir, layer_path)
    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    bounds = research_bounds_by_stock(
        membership,
        layer.download_start,
        layer.download_end,
        sample_start=methodology.sample_start_fallback,
        history_padding_calendar_days=methodology.history_padding_calendar_days,
    )
    calendar_dates = pd.DatetimeIndex(calendar["trade_date"])
    calendar_years = calendar_dates.year.to_numpy()
    universe_by_year, membership_masks = _universe_by_year(membership, calendar)
    stock_rows: list[dict[str, object]] = []
    stock_year_rows: list[dict[str, object]] = []
    annual: dict[tuple[str, int], dict[str, object]] = {}

    for adjustment in layer.eastmoney_adjustments:
        for year, universe_codes in universe_by_year.items():
            annual[(adjustment, year)] = {
                "adjustment": adjustment,
                "year": year,
                "number_of_unique_CSI300_constituents_available": len(universe_codes),
                "stocks_with_any_price_data": set(),
                "active_member_stock_days": 0,
                "missing_close_stock_days": 0,
                "nonpositive_close_stock_days": 0,
                "source_row_count": 0,
                "source_missing_OHLC_count": 0,
                "source_nonpositive_OHLC_record_count": 0,
            }
        for number, stock_code in enumerate(codes, start=1):
            path = raw_root / adjustment / f"{_safe_code(stock_code)}.csv.gz"
            frame = _load_eastmoney(path)
            aligned = frame.set_index("trade_date").reindex(calendar_dates)
            member_mask = membership_masks[stock_code]
            missing_fields = int(frame[PRICE_COLUMNS + ["volume", "amount", "turnover_rate"]].isna().sum().sum())
            nonpositive_records = int(frame[PRICE_COLUMNS].le(0).any(axis=1).sum())
            close = pd.to_numeric(aligned["close"], errors="coerce").to_numpy(float)
            stock_rows.append(
                {
                    "adjustment": adjustment,
                    "stock_code": stock_code,
                    "observed_start": frame["trade_date"].min() if len(frame) else pd.NaT,
                    "observed_end": frame["trade_date"].max() if len(frame) else pd.NaT,
                    "row_count": len(frame),
                    "missing_field_count": missing_fields,
                    "nonpositive_OHLC_record_count": nonpositive_records,
                    "nonpositive_close_record_count": int(frame["close"].le(0).sum()),
                    "min_close": frame["close"].min(),
                    "max_close": frame["close"].max(),
                    "active_member_stock_days": int(member_mask.sum()),
                    "missing_active_member_close_days": int(
                        (member_mask & ~np.isfinite(close)).sum()
                    ),
                    "nonpositive_active_member_close_days": int(
                        (member_mask & np.isfinite(close) & (close <= 0)).sum()
                    ),
                }
            )
            for year in universe_by_year:
                active = member_mask & (calendar_years == year)
                if not active.any():
                    continue
                row = annual[(adjustment, year)]
                row["active_member_stock_days"] += int(active.sum())
                missing = active & ~np.isfinite(close)
                nonpositive = active & np.isfinite(close) & (close <= 0)
                row["missing_close_stock_days"] += int(missing.sum())
                row["nonpositive_close_stock_days"] += int(nonpositive.sum())
                valid = active & np.isfinite(close) & (close > 0)
                if valid.any():
                    row["stocks_with_any_price_data"].add(stock_code)
                source_year = frame["trade_date"].dt.year.eq(year)
                row["source_row_count"] += int(source_year.sum())
                row["source_missing_OHLC_count"] += int(
                    frame.loc[source_year, PRICE_COLUMNS].isna().sum().sum()
                )
                row["source_nonpositive_OHLC_record_count"] += int(
                    frame.loc[source_year, PRICE_COLUMNS].le(0).any(axis=1).sum()
                )
                active_days = int(active.sum())
                observed_days = int(valid.sum())
                stock_year_rows.append(
                    {
                        "adjustment": adjustment,
                        "stock_code": stock_code,
                        "year": year,
                        "active_member_stock_days": active_days,
                        "observed_positive_close_days": observed_days,
                        "missing_close_stock_days": int(missing.sum()),
                        "nonpositive_close_stock_days": int(nonpositive.sum()),
                        "active_member_close_coverage_ratio": (
                            observed_days / active_days if active_days else np.nan
                        ),
                        "source_row_count": int(source_year.sum()),
                        "source_missing_OHLC_count": int(
                            frame.loc[source_year, PRICE_COLUMNS].isna().sum().sum()
                        ),
                        "source_nonpositive_OHLC_record_count": int(
                            frame.loc[source_year, PRICE_COLUMNS].le(0).any(axis=1).sum()
                        ),
                    }
                )
            if number % 100 == 0 or number == len(codes):
                print(
                    f"  price audit {adjustment}: {number}/{len(codes)}", flush=True
                )

    annual_rows: list[dict[str, object]] = []
    for key in sorted(annual):
        row = dict(annual[key])
        covered = len(row.pop("stocks_with_any_price_data"))
        row["number_of_stocks_with_price_data"] = covered
        denominator = int(row["number_of_unique_CSI300_constituents_available"])
        row["stock_coverage_ratio"] = covered / denominator if denominator else np.nan
        stock_days = int(row["active_member_stock_days"])
        row["missing_close_stock_day_ratio"] = (
            int(row["missing_close_stock_days"]) / stock_days if stock_days else np.nan
        )
        row["nonpositive_close_stock_day_ratio"] = (
            int(row["nonpositive_close_stock_days"]) / stock_days if stock_days else np.nan
        )
        annual_rows.append(row)
    return {
        "stock": pd.DataFrame(stock_rows).sort_values(["adjustment", "stock_code"]),
        "year_stock": pd.DataFrame(stock_year_rows).sort_values(
            ["adjustment", "year", "stock_code"]
        ),
        "year": pd.DataFrame(annual_rows).sort_values(["adjustment", "year"]),
    }


def audit_adjustment_paths(
    layer: PriceLayerConfig,
    layer_path: Path,
    membership: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    root = layer.resolve(layer.eastmoney_raw_dir, layer_path)
    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    action_rows: list[pd.DataFrame] = []
    stock_rows: list[dict[str, object]] = []
    year_rows: list[dict[str, object]] = []
    for number, stock_code in enumerate(codes, start=1):
        blocks: list[pd.DataFrame] = []
        for adjustment in ("raw", "qfq", "hfq"):
            frame = _load_eastmoney(root / adjustment / f"{_safe_code(stock_code)}.csv.gz")
            blocks.append(
                frame[["trade_date", "close"]].rename(
                    columns={"close": f"{adjustment}_close"}
                )
            )
        merged = blocks[0].merge(blocks[1], on="trade_date", how="outer")
        merged = merged.merge(blocks[2], on="trade_date", how="outer")
        merged = merged.sort_values("trade_date").reset_index(drop=True)
        for adjustment in ("raw", "qfq", "hfq"):
            close = merged[f"{adjustment}_close"]
            valid_pair = close.gt(0) & close.shift(1).gt(0)
            merged[f"{adjustment}_return"] = close.pct_change(fill_method=None).where(
                valid_pair
            )
        merged["raw_qfq_return_abs_diff"] = (
            merged["raw_return"] - merged["qfq_return"]
        ).abs()
        merged["raw_hfq_return_abs_diff"] = (
            merged["raw_return"] - merged["hfq_return"]
        ).abs()
        merged["qfq_hfq_return_abs_diff"] = (
            merged["qfq_return"] - merged["hfq_return"]
        ).abs()
        # Prefer a positive hfq return as the action-day reference.  A qfq path
        # near or below zero can otherwise flag ordinary days as fake actions.
        merged["corporate_action_return_abs_diff"] = merged[
            "raw_hfq_return_abs_diff"
        ].where(
            merged["hfq_return"].notna(), merged["raw_qfq_return_abs_diff"]
        )
        merged["corporate_action_reference_adjustment"] = np.where(
            merged["hfq_return"].notna(),
            "hfq",
            np.where(merged["qfq_return"].notna(), "qfq", pd.NA),
        )
        merged["corporate_action_candidate"] = (
            merged["corporate_action_return_abs_diff"] > 0.02
        ).astype(int)
        merged["qfq_abnormal_jump"] = (
            merged["qfq_return"].abs() > 0.25
        ).astype(int)
        merged["hfq_abnormal_jump"] = (
            merged["hfq_return"].abs() > 0.25
        ).astype(int)
        merged["adjusted_abnormal_jump"] = (
            merged[["qfq_abnormal_jump", "hfq_abnormal_jump"]].max(axis=1)
        ).astype(int)
        merged["stock_code"] = stock_code
        actions = merged.loc[merged["corporate_action_candidate"].eq(1)].copy()
        if len(actions):
            action_rows.append(actions)

        positive = (
            merged["qfq_close"].gt(0)
            & merged["hfq_close"].gt(0)
            & merged[["qfq_close", "hfq_close"]].notna().all(axis=1)
        )
        ratio = merged.loc[positive, "qfq_close"] / merged.loc[positive, "hfq_close"]
        ratio_median = ratio.median()
        ratio_deviation = (
            (ratio / ratio_median - 1).abs() if ratio_median and len(ratio) else pd.Series(dtype=float)
        )
        return_diff = merged["qfq_hfq_return_abs_diff"].dropna()
        stock_rows.append(
            {
                "stock_code": stock_code,
                "common_positive_price_days": int(positive.sum()),
                "qfq_nonpositive_close_count": int(merged["qfq_close"].le(0).sum()),
                "hfq_nonpositive_close_count": int(merged["hfq_close"].le(0).sum()),
                "corporate_action_candidate_count": int(
                    merged["corporate_action_candidate"].sum()
                ),
                "qfq_abnormal_jump_count": int(merged["qfq_abnormal_jump"].sum()),
                "hfq_abnormal_jump_count": int(merged["hfq_abnormal_jump"].sum()),
                "adjusted_abnormal_jump_count": int(merged["adjusted_abnormal_jump"].sum()),
                "qfq_hfq_scale_ratio_median": ratio_median,
                "qfq_hfq_scale_ratio_cv": (
                    ratio.std(ddof=1) / abs(ratio.mean()) if len(ratio) > 1 and ratio.mean() else np.nan
                ),
                "qfq_hfq_scale_max_relative_deviation": ratio_deviation.max(),
                "qfq_hfq_return_abs_diff_median": return_diff.median(),
                "qfq_hfq_return_abs_diff_p95": return_diff.quantile(0.95),
                "qfq_hfq_return_abs_diff_max": return_diff.max(),
                "pure_multiplicative_scale_within_0_2pct": int(
                    bool(len(ratio_deviation)) and ratio_deviation.max() <= 0.002
                ),
            }
        )
        for year, group in merged.groupby(merged["trade_date"].dt.year):
            positive_year = group[
                group["qfq_close"].gt(0)
                & group["hfq_close"].gt(0)
                & group[["qfq_close", "hfq_close"]].notna().all(axis=1)
            ]
            year_ratio = positive_year["qfq_close"] / positive_year["hfq_close"]
            median = year_ratio.median()
            deviations = (
                (year_ratio / median - 1).abs()
                if median and len(year_ratio)
                else pd.Series(dtype=float)
            )
            year_rows.append(
                {
                    "stock_code": stock_code,
                    "year": int(year),
                    "common_positive_price_days": len(positive_year),
                    "qfq_nonpositive_close_count": int(group["qfq_close"].le(0).sum()),
                    "hfq_nonpositive_close_count": int(group["hfq_close"].le(0).sum()),
                    "corporate_action_candidate_count": int(
                        group["corporate_action_candidate"].sum()
                    ),
                    "qfq_abnormal_jump_count": int(group["qfq_abnormal_jump"].sum()),
                    "hfq_abnormal_jump_count": int(group["hfq_abnormal_jump"].sum()),
                    "qfq_hfq_scale_max_relative_deviation": deviations.max(),
                    "qfq_hfq_return_abs_diff_p95": group[
                        "qfq_hfq_return_abs_diff"
                    ].quantile(0.95),
                }
            )
        if number % 100 == 0 or number == len(codes):
            print(f"  adjustment path audit: {number}/{len(codes)}", flush=True)
    actions = (
        pd.concat(action_rows, ignore_index=True)
        if action_rows
        else pd.DataFrame(
            columns=[
                "stock_code",
                "trade_date",
                "raw_close",
                "qfq_close",
                "hfq_close",
                "raw_return",
                "qfq_return",
                "hfq_return",
                "raw_qfq_return_abs_diff",
                "raw_hfq_return_abs_diff",
                "qfq_hfq_return_abs_diff",
                "corporate_action_return_abs_diff",
                "corporate_action_reference_adjustment",
                "corporate_action_candidate",
                "qfq_abnormal_jump",
                "hfq_abnormal_jump",
                "adjusted_abnormal_jump",
            ]
        )
    )
    return {
        "corporate_actions": actions.sort_values(["trade_date", "stock_code"]),
        "stock": pd.DataFrame(stock_rows).sort_values("stock_code"),
        "year_stock": pd.DataFrame(year_rows).sort_values(["year", "stock_code"]),
    }


def screen_price_source(
    base_config: MethodologyConfig,
    methodology_path: Path,
    stock_dir: Path,
    price_source: str,
    adjustment: str,
    membership: pd.DataFrame,
    calendar: pd.DataFrame,
    histories: dict[str, tuple[np.ndarray, np.ndarray]],
) -> pd.DataFrame:
    config = replace(
        base_config,
        price_source=price_source,
        current_adjustment_type=adjustment,
        target_adjustment_type=adjustment,
        processed_stock_dir=str(stock_dir),
    )
    config.validate()
    records: list[dict[str, object]] = []
    specification = config.specifications["baseline"]
    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    for number, stock_code in enumerate(codes, start=1):
        daily = load_processed_stock(stock_dir / f"{_safe_code(stock_code)}.csv.gz")
        context, _ = _build_context(daily, calendar, membership, histories)
        events, _, _, _ = _evaluate_stock_specification(
            context, specification, config, collect_funnel=False
        )
        records.extend(events)
        if number % 50 == 0 or number == len(codes):
            print(
                f"  screening {price_source} {adjustment}: {number}/{len(codes)}; "
                f"events={len(records)}",
                flush=True,
            )
    result = pd.DataFrame(records, columns=EVENT_COLUMNS)
    if len(result):
        result = result.sort_values(["event_date", "stock_code"]).reset_index(drop=True)
    return result


def _event_metrics(context, event_position: int, config: MethodologyConfig) -> dict[str, object]:
    specification = config.specifications["baseline"]
    if event_position <= 0 or event_position >= len(context.dates):
        return {"outcome": "event_date_not_in_calendar"}
    recent = _recent_120_metrics(context, event_position, config)
    base = {
        "consolidation_start": context.dates[int(recent["start"])],
        "consolidation_duration": config.min_consolidation_duration,
        "bandwidth": float(recent["bandwidth"]),
        "trend120": float(recent["trend120"]),
        "resistance": float(recent["high"]),
    }
    event_close = context.close[event_position]
    base["breakout_strength"] = (
        float(event_close / float(recent["high"]) - 1)
        if np.isfinite(event_close) and event_close > 0 and float(recent["high"]) > 0
        else np.nan
    )
    base["drawdown"] = np.nan
    if not recent["passes"]:
        base["outcome"] = "recent_120_data_quality_failed"
        return base
    if float(recent["bandwidth"]) > specification.bandwidth_threshold:
        base["outcome"] = "bandwidth_threshold_failed"
        return base
    if abs(float(recent["trend120"])) > config.trend120_threshold:
        base["outcome"] = "trend120_threshold_failed"
        return base
    consolidation = _detect_consolidation(context, event_position, specification, config)
    if consolidation is None:
        base["outcome"] = "consolidation_not_identified"
        return base
    start = int(consolidation["start"])
    base.update(
        {
            "consolidation_start": context.dates[start],
            "consolidation_duration": int(consolidation["duration"]),
            "bandwidth": float(consolidation["bandwidth"]),
            "trend120": float(consolidation["trend120"]),
            "resistance": float(consolidation["high"]),
        }
    )
    st_days = int(context.final_st[start : event_position + 1].sum())
    if st_days:
        base["outcome"] = "ST_during_consolidation_or_event"
        return base
    peak_start = start - config.peak_lookback_before_consolidation
    peak_end = start - 1
    if peak_start < 0:
        base["outcome"] = "invalid_peak_window_data"
        return base
    peak_quality = _quality_metrics(context, peak_start, peak_end, config)
    peak_price, _, _, _ = _price_extrema(context, peak_start, peak_end)
    if not peak_quality["passes"] or not np.isfinite(peak_price) or peak_price <= 0:
        base["outcome"] = "invalid_peak_window_data"
        return base
    base["drawdown"] = float(consolidation["low"]) / peak_price - 1
    if float(base["drawdown"]) > config.distress_drawdown_threshold:
        base["outcome"] = "drawdown_threshold_failed"
        return base
    base["breakout_strength"] = (
        float(event_close / float(consolidation["high"]) - 1)
        if np.isfinite(event_close) and event_close > 0
        else np.nan
    )
    if not np.isfinite(base["breakout_strength"]) or float(
        base["breakout_strength"]
    ) < specification.breakout_threshold:
        base["outcome"] = "breakout_threshold_failed"
        return base
    if not context.normal_trade[event_position] or not context.valid_price[event_position]:
        base["outcome"] = "event_day_not_normal_trading"
        return base
    base["outcome"] = "selected_before_cooldown"
    return base


def _metric_record_from_event(row: pd.Series) -> dict[str, object]:
    return {
        "outcome": "selected_after_cooldown",
        "consolidation_start": row["ConsolidationStart"],
        "consolidation_duration": row["ConsolidationDuration"],
        "drawdown": row["DistressDrawdown"],
        "bandwidth": row["Bandwidth"],
        "trend120": row["Trend120"],
        "resistance": row["Resistance"],
        "breakout_strength": row["BreakoutStrength"],
    }


def compare_event_pools(
    left_events: pd.DataFrame,
    right_events: pd.DataFrame,
    left_dir: Path,
    right_dir: Path,
    left_name: str,
    right_name: str,
    left_adjustment: str,
    right_adjustment: str,
    base_config: MethodologyConfig,
    membership: pd.DataFrame,
    calendar: pd.DataFrame,
    histories: dict[str, tuple[np.ndarray, np.ndarray]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    key_columns = ["stock_code", "event_date"]
    left = left_events.copy()
    right = right_events.copy()
    left["event_date"] = pd.to_datetime(left["event_date"])
    right["event_date"] = pd.to_datetime(right["event_date"])
    left_keys = set(map(tuple, left[key_columns].itertuples(index=False, name=None)))
    right_keys = set(map(tuple, right[key_columns].itertuples(index=False, name=None)))
    intersection = left_keys & right_keys
    union = left_keys | right_keys
    summary = pd.DataFrame(
        {
            "metric": [
                f"{left_name}_event_count",
                f"{right_name}_event_count",
                f"{left_name}_unique_stock_count",
                f"{right_name}_unique_stock_count",
                "intersection",
                f"{left_name}_only",
                f"{right_name}_only",
                "union",
                "Jaccard_similarity",
            ],
            "value": [
                len(left),
                len(right),
                left["stock_code"].nunique(),
                right["stock_code"].nunique(),
                len(intersection),
                len(left_keys - right_keys),
                len(right_keys - left_keys),
                len(union),
                len(intersection) / len(union) if union else np.nan,
            ],
        }
    )
    if not union:
        return summary, pd.DataFrame()
    left_index = left.set_index(key_columns)
    right_index = right.set_index(key_columns)
    date_positions = {
        pd.Timestamp(date): index
        for index, date in enumerate(pd.DatetimeIndex(calendar["trade_date"]))
    }
    left_config = replace(
        base_config,
        price_source=left_name,
        current_adjustment_type=left_adjustment,
        target_adjustment_type=left_adjustment,
    )
    right_config = replace(
        base_config,
        price_source=right_name,
        current_adjustment_type=right_adjustment,
        target_adjustment_type=right_adjustment,
    )
    records: list[dict[str, object]] = []
    by_stock: dict[str, list[tuple[str, pd.Timestamp]]] = defaultdict(list)
    for stock_code, event_date in sorted(union, key=lambda value: (value[0], value[1])):
        by_stock[str(stock_code)].append((str(stock_code), pd.Timestamp(event_date)))
    for stock_code, keys in by_stock.items():
        left_daily = load_processed_stock(left_dir / f"{_safe_code(stock_code)}.csv.gz")
        right_daily = load_processed_stock(right_dir / f"{_safe_code(stock_code)}.csv.gz")
        left_context, _ = _build_context(left_daily, calendar, membership, histories)
        right_context, _ = _build_context(right_daily, calendar, membership, histories)
        for key in keys:
            event_date = key[1]
            position = date_positions.get(event_date)
            if position is None:
                left_metrics = {"outcome": "event_date_not_in_calendar"}
                right_metrics = {"outcome": "event_date_not_in_calendar"}
            else:
                left_metrics = (
                    _metric_record_from_event(left_index.loc[key])
                    if key in left_keys
                    else _event_metrics(left_context, position, left_config)
                )
                right_metrics = (
                    _metric_record_from_event(right_index.loc[key])
                    if key in right_keys
                    else _event_metrics(right_context, position, right_config)
                )
            record: dict[str, object] = {
                "stock_code": stock_code,
                "event_date": event_date,
                f"{left_name}_event": int(key in left_keys),
                f"{right_name}_event": int(key in right_keys),
                "pool_membership": (
                    "intersection"
                    if key in intersection
                    else f"{left_name}_only"
                    if key in left_keys
                    else f"{right_name}_only"
                ),
                f"{left_name}_outcome": left_metrics.get("outcome"),
                f"{right_name}_outcome": right_metrics.get("outcome"),
                f"{left_name}_consolidation_start": left_metrics.get(
                    "consolidation_start"
                ),
                f"{right_name}_consolidation_start": right_metrics.get(
                    "consolidation_start"
                ),
                f"{left_name}_consolidation_duration": left_metrics.get(
                    "consolidation_duration"
                ),
                f"{right_name}_consolidation_duration": right_metrics.get(
                    "consolidation_duration"
                ),
            }
            for metric in METRIC_COLUMNS:
                left_value = pd.to_numeric(left_metrics.get(metric), errors="coerce")
                right_value = pd.to_numeric(right_metrics.get(metric), errors="coerce")
                record[f"{left_name}_{metric}"] = left_value
                record[f"{right_name}_{metric}"] = right_value
                record[f"{metric}_difference_left_minus_right"] = (
                    float(left_value - right_value)
                    if pd.notna(left_value) and pd.notna(right_value)
                    else np.nan
                )
            left_resistance = pd.to_numeric(left_metrics.get("resistance"), errors="coerce")
            right_resistance = pd.to_numeric(right_metrics.get("resistance"), errors="coerce")
            left_breakout = pd.to_numeric(
                left_metrics.get("breakout_strength"), errors="coerce"
            )
            right_breakout = pd.to_numeric(
                right_metrics.get("breakout_strength"), errors="coerce"
            )
            record["resistance_ratio_difference"] = (
                (1 / (1 + left_breakout)) - (1 / (1 + right_breakout))
                if pd.notna(left_breakout)
                and pd.notna(right_breakout)
                and 1 + left_breakout != 0
                and 1 + right_breakout != 0
                else np.nan
            )
            record["difference_reason"] = _difference_reason(
                left_metrics, right_metrics, key in left_keys, key in right_keys
            )
            records.append(record)
    return summary, pd.DataFrame(records).sort_values(["event_date", "stock_code"])


def _difference_reason(
    left: dict[str, object],
    right: dict[str, object],
    left_event: bool,
    right_event: bool,
) -> str:
    if left_event == right_event:
        return "共同事件"
    outcomes = {str(left.get("outcome")), str(right.get("outcome"))}
    if any("data_quality" in value or "invalid_peak" in value for value in outcomes):
        return "数据缺失或有效价格覆盖差异"
    if "event_date_not_in_calendar" in outcomes or "event_day_not_normal_trading" in outcomes:
        return "交易日期或交易状态差异"
    ordered = [
        ("drawdown_threshold_failed", "Drawdown阈值判定差异"),
        ("bandwidth_threshold_failed", "Bandwidth阈值判定差异"),
        ("trend120_threshold_failed", "Trend120阈值判定差异"),
        ("breakout_threshold_failed", "BreakoutStrength阈值判定差异"),
        ("consolidation_not_identified", "横盘区间识别差异"),
        ("selected_before_cooldown", "60交易日cooldown或前序事件差异"),
    ]
    for outcome, reason in ordered:
        if outcome in outcomes:
            return reason
    return "复权价格路径或其他差异"


def _difference_reason_summary(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            columns=["difference_reason", "event_count", "share_of_disagreements"]
        )
    disagreements = detail.loc[~detail["pool_membership"].eq("intersection")]
    if disagreements.empty:
        return pd.DataFrame(
            columns=["difference_reason", "event_count", "share_of_disagreements"]
        )
    result = (
        disagreements.groupby("difference_reason", dropna=False)
        .size()
        .rename("event_count")
        .reset_index()
        .sort_values(["event_count", "difference_reason"], ascending=[False, True])
    )
    result["share_of_disagreements"] = result["event_count"] / len(disagreements)
    return result


def choose_yahoo_sample(
    membership: pd.DataFrame,
    path_stock: pd.DataFrame,
    sample_size: int,
    random_seed: int,
) -> pd.DataFrame:
    first_dates = membership.groupby("stock_code")["in_date"].min()
    audit = path_stock.set_index("stock_code")
    candidates = pd.DataFrame({"first_membership_date": first_dates})
    candidates["era"] = np.select(
        [
            candidates["first_membership_date"].dt.year <= 2014,
            candidates["first_membership_date"].dt.year <= 2020,
        ],
        ["early_2009_2014", "middle_2015_2020"],
        default="recent_2021_2026",
    )
    candidates["corporate_action_group"] = np.where(
        candidates.index.to_series()
        .map(audit["corporate_action_candidate_count"])
        .fillna(0)
        .gt(0),
        "with_corporate_action",
        "without_corporate_action",
    )
    candidates = candidates.reset_index()
    rng = np.random.default_rng(random_seed)
    selected: list[pd.DataFrame] = []
    target_per_era = max(1, sample_size // 3)
    for era in ["early_2009_2014", "middle_2015_2020", "recent_2021_2026"]:
        era_frame = candidates[candidates["era"].eq(era)]
        target = target_per_era
        for group_name in ["with_corporate_action", "without_corporate_action"]:
            group = era_frame[era_frame["corporate_action_group"].eq(group_name)]
            take = min(len(group), target // 2)
            if take:
                selected.append(group.iloc[rng.choice(len(group), size=take, replace=False)])
        already = (
            pd.concat(selected)["stock_code"].tolist() if selected else []
        )
        era_selected = sum(
            len(block[block["era"].eq(era)]) for block in selected
        )
        remaining = era_frame[~era_frame["stock_code"].isin(already)]
        fill = min(len(remaining), target - era_selected)
        if fill > 0:
            selected.append(remaining.iloc[rng.choice(len(remaining), size=fill, replace=False)])
    result = pd.concat(selected, ignore_index=True).drop_duplicates("stock_code")
    if len(result) < sample_size:
        remaining = candidates[~candidates["stock_code"].isin(result["stock_code"])]
        take = min(len(remaining), sample_size - len(result))
        result = pd.concat(
            [result, remaining.iloc[rng.choice(len(remaining), size=take, replace=False)]],
            ignore_index=True,
        )
    return result.head(sample_size).sort_values(["era", "corporate_action_group", "stock_code"])


def audit_yahoo_sample(
    sample: pd.DataFrame,
    final_adjustment: str,
    layer: PriceLayerConfig,
    layer_path: Path,
) -> pd.DataFrame:
    east_root = layer.resolve(layer.eastmoney_raw_dir, layer_path) / final_adjustment
    yahoo_root = layer.resolve(layer.yahoo_raw_dir, layer_path)
    rows: list[dict[str, object]] = []
    for stock in sample.itertuples(index=False):
        stock_code = str(stock.stock_code)
        east = _load_eastmoney(east_root / f"{_safe_code(stock_code)}.csv.gz")
        yahoo_path = yahoo_root / f"{_safe_code(stock_code)}.csv.gz"
        yahoo = pd.read_csv(yahoo_path, low_memory=False)
        date_column = "Date" if "Date" in yahoo.columns else None
        adjusted_column = "Adj Close" if "Adj Close" in yahoo.columns else None
        if date_column is None or adjusted_column is None:
            common = pd.DataFrame()
        else:
            yahoo_block = yahoo[[date_column, adjusted_column]].rename(
                columns={date_column: "trade_date", adjusted_column: "yahoo_adj_close"}
            )
            yahoo_block["trade_date"] = pd.to_datetime(
                yahoo_block["trade_date"], errors="coerce", utc=True
            ).dt.tz_convert(None).dt.normalize()
            yahoo_block["yahoo_adj_close"] = pd.to_numeric(
                yahoo_block["yahoo_adj_close"], errors="coerce"
            )
            common = east[["trade_date", "close"]].rename(
                columns={"close": "eastmoney_adjusted_close"}
            ).merge(yahoo_block, on="trade_date", how="inner")
            common = common.dropna().sort_values("trade_date")
            common["eastmoney_return"] = common["eastmoney_adjusted_close"].pct_change(
                fill_method=None
            )
            common["yahoo_return"] = common["yahoo_adj_close"].pct_change(
                fill_method=None
            )
            common["return_abs_diff"] = (
                common["eastmoney_return"] - common["yahoo_return"]
            ).abs()
        rows.append(
            {
                "stock_code": stock_code,
                "era": stock.era,
                "corporate_action_group": stock.corporate_action_group,
                "eastmoney_adjustment": final_adjustment,
                "yahoo_row_count": len(yahoo),
                "common_price_days": len(common),
                "common_return_days": int(common.get("return_abs_diff", pd.Series(dtype=float)).notna().sum()),
                "return_correlation": (
                    common[["eastmoney_return", "yahoo_return"]].corr().iloc[0, 1]
                    if len(common) >= 3
                    else np.nan
                ),
                "return_abs_diff_median": common.get(
                    "return_abs_diff", pd.Series(dtype=float)
                ).median(),
                "return_abs_diff_p95": common.get(
                    "return_abs_diff", pd.Series(dtype=float)
                ).quantile(0.95),
                "return_abs_diff_max": common.get(
                    "return_abs_diff", pd.Series(dtype=float)
                ).max(),
                "validation_status": "available" if len(common) >= 30 else "insufficient_or_missing",
            }
        )
    return pd.DataFrame(rows).sort_values(["era", "corporate_action_group", "stock_code"])


def _recommend_adjustment(
    price_stock: pd.DataFrame,
    path_stock: pd.DataFrame,
    qfq_hfq_summary: pd.DataFrame,
    east_bao_summaries: dict[str, pd.DataFrame],
) -> dict[str, object]:
    price_totals = price_stock.groupby("adjustment").agg(
        nonpositive_close_records=("nonpositive_close_record_count", "sum"),
        missing_fields=("missing_field_count", "sum"),
        stocks=("stock_code", "nunique"),
    )
    qfq_nonpositive = int(price_totals.loc["qfq", "nonpositive_close_records"])
    hfq_nonpositive = int(price_totals.loc["hfq", "nonpositive_close_records"])
    bao_jaccards: dict[str, float] = {}
    for adjustment, summary in east_bao_summaries.items():
        values = summary.loc[summary["metric"].eq("Jaccard_similarity"), "value"]
        bao_jaccards[adjustment] = float(values.iloc[0]) if len(values) else np.nan
    nonpositive = {"qfq": qfq_nonpositive, "hfq": hfq_nonpositive}

    def ranking_key(adjustment: str) -> tuple[int, int, float]:
        count = nonpositive[adjustment]
        jaccard = bao_jaccards.get(adjustment, np.nan)
        return (int(count > 0), count, -jaccard if np.isfinite(jaccard) else 0.0)

    chosen = min(("qfq", "hfq"), key=ranking_key)
    recommendation = f"Eastmoney_{chosen}"
    pure_share = float(path_stock["pure_multiplicative_scale_within_0_2pct"].mean())
    if nonpositive[chosen] == 0 and nonpositive["hfq" if chosen == "qfq" else "qfq"] > 0:
        reason = (
            f"{chosen}未发现非正历史价格，而另一口径存在；比例型事件指标要求价格为正，"
            f"因此优先选择{chosen}。"
        )
    elif qfq_nonpositive == 0 and hfq_nonpositive == 0:
        reason = (
            f"两口径均为正价，按与BaoStock完整事件池的一致性选择{chosen}；"
            f"qfq/hfq近似纯乘法尺度的股票占{pure_share:.2%}。"
        )
    else:
        reason = (
            f"qfq与hfq都存在非正价格，当前仅把{chosen}标为两者中问题较少的候选口径；"
            "在构造并验证统一的正值复权收益指数前，不允许冻结主口径。"
        )
    return {
        "recommended_main_source": "Eastmoney via AKShare",
        "recommended_adjustment": recommendation.split("_")[-1],
        "recommendation": recommendation,
        "reason": reason,
        "qfq_nonpositive_close_records": qfq_nonpositive,
        "hfq_nonpositive_close_records": hfq_nonpositive,
        "qfq_hfq_Jaccard": float(
            qfq_hfq_summary.loc[
                qfq_hfq_summary["metric"].eq("Jaccard_similarity"), "value"
            ].iloc[0]
        ),
        "qfq_baostock_Jaccard": bao_jaccards.get("qfq", np.nan),
        "hfq_baostock_Jaccard": bao_jaccards.get("hfq", np.nan),
        "eastmoney_baostock_Jaccard": bao_jaccards.get(chosen, np.nan),
    }


def _events_by_year(events: pd.DataFrame, source: str) -> pd.DataFrame:
    years = pd.DataFrame({"year": range(2009, 2027)})
    counts = (
        events.assign(year=pd.to_datetime(events["event_date"]).dt.year)
        .groupby("year")
        .agg(event_count=("stock_code", "size"), unique_stock_count=("stock_code", "nunique"))
        .reset_index()
        if len(events)
        else pd.DataFrame(columns=["year", "event_count", "unique_stock_count"])
    )
    result = years.merge(counts, on="year", how="left").fillna(
        {"event_count": 0, "unique_stock_count": 0}
    )
    result[["event_count", "unique_stock_count"]] = result[
        ["event_count", "unique_stock_count"]
    ].astype(int)
    result["price_source"] = source
    result["year_status"] = np.where(result["year"].eq(2026), "partial_to_2026-09-04", "complete")
    return result


def _event_catalog_zh(events: pd.DataFrame) -> pd.DataFrame:
    """Return the event pool in a compact, thesis-readable Chinese layout."""
    events = events.copy()
    if "data_source" not in events.columns:
        events["data_source"] = events.get("price_source", "Eastmoney")
    catalog_columns = [
        "stock_code",
        "stock_name",
        "event_date",
        "ConsolidationStart",
        "ConsolidationDuration",
        "DistressDrawdown",
        "Bandwidth",
        "Trend120",
        "Resistance",
        "BreakoutStrength",
        "adjustment_type",
        "data_source",
    ]
    return events[catalog_columns].rename(
        columns={
            "stock_code": "股票代码",
            "stock_name": "股票名称",
            "event_date": "突破日期",
            "ConsolidationStart": "横盘开始日期",
            "ConsolidationDuration": "横盘交易日数",
            "DistressDrawdown": "前期深度回撤",
            "Bandwidth": "横盘带宽",
            "Trend120": "120日等效趋势",
            "Resistance": "突破阻力位",
            "BreakoutStrength": "突破强度",
            "adjustment_type": "复权口径",
            "data_source": "数据源",
        }
    )


def _finalize_existing_eastmoney_only(
    *,
    output_dir: Path,
    layer: PriceLayerConfig,
    methodology: MethodologyConfig,
    membership: pd.DataFrame,
    calendar: pd.DataFrame,
    histories: dict[str, tuple[np.ndarray, np.ndarray]],
    east_processed: Path,
    files: dict[str, Path],
) -> dict[str, Path]:
    """Finish reporting from already completed qfq/hfq screening outputs."""
    qfq_path = output_dir / "eastmoney_qfq_events.csv"
    hfq_path = output_dir / "eastmoney_hfq_events.csv"
    required = [qfq_path, hfq_path]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Cannot reuse screened events because these files are missing: "
            + ", ".join(missing)
        )
    qfq_events = pd.read_csv(qfq_path, low_memory=False)
    hfq_events = pd.read_csv(hfq_path, low_memory=False)
    files["qfq_events"] = qfq_path
    files["hfq_events"] = hfq_path
    files["qfq_event_catalog_zh"] = _write_csv(
        _event_catalog_zh(qfq_events),
        output_dir / "eastmoney_qfq_event_catalog_zh.csv",
        layer.output_encoding,
    )
    files["hfq_event_catalog_zh"] = _write_csv(
        _event_catalog_zh(hfq_events),
        output_dir / "eastmoney_hfq_event_catalog_zh.csv",
        layer.output_encoding,
    )
    files["qfq_events_by_year"] = _write_csv(
        _events_by_year(qfq_events, "Eastmoney_qfq"),
        output_dir / "eastmoney_qfq_events_by_year.csv",
        layer.output_encoding,
    )
    files["hfq_events_by_year"] = _write_csv(
        _events_by_year(hfq_events, "Eastmoney_hfq"),
        output_dir / "eastmoney_hfq_events_by_year.csv",
        layer.output_encoding,
    )
    qh_summary, qh_detail = compare_event_pools(
        qfq_events,
        hfq_events,
        east_processed / "qfq",
        east_processed / "hfq",
        "Eastmoney_qfq",
        "Eastmoney_hfq",
        "qfq",
        "hfq",
        methodology,
        membership,
        calendar,
        histories,
    )
    files["qfq_hfq_summary"] = _write_csv(
        qh_summary, output_dir / "eastmoney_qfq_hfq_event_summary.csv", layer.output_encoding
    )
    files["qfq_hfq_detail"] = _write_csv(
        qh_detail, output_dir / "eastmoney_qfq_hfq_event_comparison.csv", layer.output_encoding
    )
    files["qfq_hfq_difference_reasons"] = _write_csv(
        _difference_reason_summary(qh_detail),
        output_dir / "eastmoney_qfq_hfq_difference_reason_summary.csv",
        layer.output_encoding,
    )

    def qh_value(metric: str) -> float:
        values = qh_summary.loc[qh_summary["metric"].eq(metric), "value"]
        return float(values.iloc[0]) if len(values) else np.nan

    status = {
        "scope": "Eastmoney raw/qfq/hfq full historical CSI300 universe only",
        "historical_universe": "historical_CSI300_t_minus_1",
        "qfq_event_count": int(qh_value("Eastmoney_qfq_event_count")),
        "hfq_event_count": int(qh_value("Eastmoney_hfq_event_count")),
        "intersection": int(qh_value("intersection")),
        "qfq_only": int(qh_value("Eastmoney_qfq_only")),
        "hfq_only": int(qh_value("Eastmoney_hfq_only")),
        "qfq_hfq_jaccard": qh_value("Jaccard_similarity"),
        "final_adjustment_frozen": False,
        "baostock_cross_validation_complete": False,
        "future_returns_or_CAR_computed": False,
        "event_thresholds_changed": False,
    }
    files["eastmoney_only_status"] = _write_json(
        status, output_dir / "eastmoney_only_audit_status.json"
    )
    report_lines = [
        "# 东方财富 raw/qfq/hfq 全样本审计",
        "",
        "本报告只包含东方财富完整价格层和事件识别结果。BaoStock交叉验证尚未纳入，",
        "因此不在本报告中冻结唯一主复权口径。没有计算未来收益、CAR或回归。",
        "",
        f"- Eastmoney qfq事件数：{status['qfq_event_count']}",
        f"- Eastmoney hfq事件数：{status['hfq_event_count']}",
        f"- 共同事件数：{status['intersection']}",
        f"- qfq-only：{status['qfq_only']}",
        f"- hfq-only：{status['hfq_only']}",
        f"- Jaccard：{status['qfq_hfq_jaccard']:.6f}",
        "",
        "历史股票池严格按事件日前一个市场交易日t-1的历史沪深300成分股判断。",
    ]
    report_path = output_dir / "eastmoney_only_audit_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    files["eastmoney_only_report"] = report_path
    return files


def _build_markdown_report(
    recommendation: dict[str, object],
    price_year: pd.DataFrame,
    path_stock: pd.DataFrame,
    qh_summary: pd.DataFrame,
    qh_detail: pd.DataFrame,
    eb_summary: pd.DataFrame,
    eb_detail: pd.DataFrame,
    yahoo_audit: pd.DataFrame,
) -> str:
    def summary_value(frame: pd.DataFrame, metric: str) -> float:
        values = frame.loc[frame["metric"].eq(metric), "value"]
        return float(values.iloc[0]) if len(values) else np.nan

    adjustment = str(recommendation["recommended_adjustment"])
    qfq_n = int(summary_value(qh_summary, "Eastmoney_qfq_event_count"))
    hfq_n = int(summary_value(qh_summary, "Eastmoney_hfq_event_count"))
    qh_intersection = int(summary_value(qh_summary, "intersection"))
    qh_jaccard = summary_value(qh_summary, "Jaccard_similarity")
    east_name = f"Eastmoney_{adjustment}"
    east_n = int(summary_value(eb_summary, f"{east_name}_event_count"))
    bao_n = int(summary_value(eb_summary, "BaoStock_qfq_event_count"))
    eb_intersection = int(summary_value(eb_summary, "intersection"))
    eb_jaccard = summary_value(eb_summary, "Jaccard_similarity")
    qh_reasons = _difference_reason_summary(qh_detail)
    eb_reasons = _difference_reason_summary(eb_detail)
    qh_top_reason = (
        f"{qh_reasons.iloc[0]['difference_reason']}（{int(qh_reasons.iloc[0]['event_count'])}个）"
        if len(qh_reasons)
        else "无分类差异"
    )
    eb_top_reason = (
        f"{eb_reasons.iloc[0]['difference_reason']}（{int(eb_reasons.iloc[0]['event_count'])}个）"
        if len(eb_reasons)
        else "无分类差异"
    )
    minimum_coverage = price_year.loc[
        price_year["adjustment"].eq(adjustment) & price_year["year"].between(2009, 2026),
        "stock_coverage_ratio",
    ].min()
    yahoo_available = int(yahoo_audit["validation_status"].eq("available").sum())
    yahoo_available_rows = yahoo_audit.loc[
        yahoo_audit["validation_status"].eq("available")
    ]
    yahoo_strata_complete = bool(
        set(yahoo_available_rows["era"])
        == {"early_2009_2014", "middle_2015_2020", "recent_2021_2026"}
        and set(yahoo_available_rows["corporate_action_group"])
        == {"with_corporate_action", "without_corporate_action"}
    )
    pure_scale = float(path_stock["pure_multiplicative_scale_within_0_2pct"].mean())
    can_freeze = bool(
        int(recommendation[f"{adjustment}_nonpositive_close_records"]) == 0
        and minimum_coverage >= 0.95
        and len(yahoo_audit) >= 30
        and yahoo_available >= 30
        and yahoo_strata_complete
    )
    freeze_text = (
        "可以冻结东方财富主源与复权口径，进入换手率和未来异常收益的数据准备阶段；"
        "进入下一阶段不代表本轮使用了未来收益选参数。"
        if can_freeze
        else "暂不应冻结。先补齐价格覆盖或Yahoo抽样可用记录，再进入未来收益阶段。"
    )
    return "\n".join(
        [
            "# 东方财富价格源与复权口径审计",
            "",
            "## 研究设计边界",
            "",
            "- Universe：事件日前一个市场交易日 t-1 的历史沪深300成分股。",
            "- Baseline阈值未改变：Drawdown≤-30%，横盘120–252交易日，Bandwidth≤25%，|Trend120|≤5%，BreakoutStrength≥0.5%，60交易日cooldown。",
            "- 本轮未计算未来收益、CAR或回归，也未用成交量筛选事件。",
            "",
            "## 复权口径结果",
            "",
            f"- Eastmoney qfq：{qfq_n} 个事件；Eastmoney hfq：{hfq_n} 个事件。",
            f"- 共同事件 {qh_intersection} 个，Jaccard={qh_jaccard:.4f}。",
            f"- qfq/hfq最大的一类差异原因：{qh_top_reason}。",
            f"- qfq/hfq价格路径在允许0.2%舍入误差后可视为纯乘法尺度的股票占 {pure_scale:.2%}。若两条路径只是常数倍，Drawdown、Bandwidth、Trend120和BreakoutStrength这些比例指标应一致；实际分类不一致来自比率并非常数、非正qfq价格、舍入或公司行为处理差异。",
            f"- 推荐唯一主口径：Eastmoney {adjustment}。{recommendation['reason']}",
            "",
            "## 与BaoStock全池交叉验证",
            "",
            f"- Eastmoney {adjustment} 全池 {east_n} 个事件；BaoStock qfq 全池 {bao_n} 个事件。",
            f"- 共同事件 {eb_intersection} 个，Jaccard={eb_jaccard:.4f}。这不是旧腾讯230个事件的子集验证，而是两个数据源各自完整重跑后的集合比较。",
            f"- 东方财富/BaoStock最大的一类差异原因：{eb_top_reason}。",
            "",
            "## Yahoo抽样",
            "",
            f"- 固定随机种子抽取 {len(yahoo_audit)} 只股票，覆盖早期、中期、近期以及有/无公司行为组；其中 {yahoo_available} 只至少有30个共同交易日可比较。Yahoo不参与主模型，也不用于换手率。",
            "",
            "## 五个结论",
            "",
            f"1. 东方财富是否可作为最终主数据源：{'是' if can_freeze else '暂时否'}。2009–2026推荐口径年度最低股票覆盖率为 {minimum_coverage:.2%}。",
            f"2. qfq还是hfq：推荐 {adjustment}，依据是正价格、长历史稳定性、公司行为处理和跨源一致性，不是事件数量更多。",
            "3. BaoStock定位：稳健性验证源。它继续提供历史沪深300、ST/停牌，并独立生成完整事件池，但不取代推荐的东方财富主价格源。",
            "4. 腾讯旧230个事件：保留作旧口径对照，不再作为正式主事件池，不与新事件池混合。",
            f"5. 是否进入下一阶段：{freeze_text}",
            "",
            "## 数据源",
            "",
            "- 东方财富日线：AKShare stock_zh_a_hist，raw/qfq/hfq分别缓存。官方文档：https://akshare.akfamily.xyz/data/stock/stock.html",
            "- AKShare已公开提示部分股票的复权历史可能出现负价，因此本项目必须逐股全样本审计：https://akshare.akfamily.xyz/data_tips.html",
            "- BaoStock：query_history_k_data_plus，frequency=d，adjustflag=2。",
            "- Yahoo Finance：固定随机种子分层抽样，Adj Close只用于收益路径核验。yfinance下载参数文档：https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html",
        ]
    )


def run_price_source_audit(
    layer_path: str | Path,
    *,
    skip_eastmoney_download: bool = False,
    skip_baostock_download: bool = False,
    skip_yahoo_download: bool = False,
    eastmoney_only: bool = False,
    reuse_screened_events: bool = False,
) -> dict[str, Path]:
    layer_path = Path(layer_path).resolve()
    layer = load_price_layer_config(layer_path)
    methodology_path = layer.resolve(layer.methodology_config, layer_path)
    methodology = load_methodology_config(methodology_path)
    membership, calendar, histories = _load_inputs(methodology, methodology_path)
    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    bounds = research_bounds_by_stock(
        membership,
        layer.download_start,
        layer.download_end,
        sample_start=methodology.sample_start_fallback,
        history_padding_calendar_days=methodology.history_padding_calendar_days,
    )
    output_dir = layer.resolve(layer.output_dir, layer_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    if not skip_eastmoney_download:
        download_eastmoney_histories(codes, layer, layer_path, bounds_by_code=bounds)
    if not skip_baostock_download and not eastmoney_only:
        download_baostock_full_qfq(codes, layer, layer_path, bounds_by_code=bounds)

    cache_inventory, cache_summary = audit_cache_inventory(
        layer, layer_path, codes, bounds
    )
    files["cache_inventory"] = _write_csv(
        cache_inventory,
        output_dir / "download_cache_inventory.csv",
        layer.output_encoding,
    )
    files["cache_summary"] = _write_csv(
        cache_summary,
        output_dir / "download_cache_summary.csv",
        layer.output_encoding,
    )
    incomplete = cache_summary.loc[
        cache_summary["missing_stock_count"].gt(0),
        ["source", "missing_stock_count"],
    ]
    if eastmoney_only:
        incomplete = incomplete.loc[
            incomplete["source"].astype(str).str.startswith("Eastmoney_")
        ]
    if len(incomplete):
        detail = ", ".join(
            f"{row.source} missing {int(row.missing_stock_count)}"
            for row in incomplete.itertuples(index=False)
        )
        raise RuntimeError(
            "Price-source audit stopped before screening because required raw caches "
            f"are incomplete ({detail}). See {files['cache_summary']} and resume with "
            "scripts/resume_market_download.py. No partial event pool was labelled final."
        )

    east_processed = layer.resolve(layer.eastmoney_processed_dir, layer_path)
    if eastmoney_only and reuse_screened_events:
        return _finalize_existing_eastmoney_only(
            output_dir=output_dir,
            layer=layer,
            methodology=methodology,
            membership=membership,
            calendar=calendar,
            histories=histories,
            east_processed=east_processed,
            files=files,
        )

    build_eastmoney_processed(codes, layer, layer_path)

    price_audit = audit_eastmoney_prices(
        layer, layer_path, methodology, membership, calendar
    )
    path_audit = audit_adjustment_paths(layer, layer_path, membership)
    files["price_audit_by_stock"] = _write_csv(
        price_audit["stock"], output_dir / "eastmoney_price_audit_by_stock.csv", layer.output_encoding
    )
    files["price_audit_by_year"] = _write_csv(
        price_audit["year"], output_dir / "eastmoney_price_audit_by_year.csv", layer.output_encoding
    )
    files["price_audit_by_year_stock"] = _write_csv(
        price_audit["year_stock"],
        output_dir / "eastmoney_price_audit_by_year_stock.csv",
        layer.output_encoding,
    )
    files["corporate_actions"] = _write_csv(
        path_audit["corporate_actions"], output_dir / "eastmoney_corporate_action_audit.csv", layer.output_encoding
    )
    files["path_audit_by_stock"] = _write_csv(
        path_audit["stock"], output_dir / "qfq_hfq_path_audit_by_stock.csv", layer.output_encoding
    )
    files["path_audit_by_year_stock"] = _write_csv(
        path_audit["year_stock"], output_dir / "qfq_hfq_path_audit_by_year_stock.csv", layer.output_encoding
    )

    qfq_events = screen_price_source(
        methodology,
        methodology_path,
        east_processed / "qfq",
        "Eastmoney",
        "qfq",
        membership,
        calendar,
        histories,
    )
    hfq_events = screen_price_source(
        methodology,
        methodology_path,
        east_processed / "hfq",
        "Eastmoney",
        "hfq",
        membership,
        calendar,
        histories,
    )
    files["qfq_events"] = _write_csv(
        qfq_events, output_dir / "eastmoney_qfq_events.csv", layer.output_encoding
    )
    files["hfq_events"] = _write_csv(
        hfq_events, output_dir / "eastmoney_hfq_events.csv", layer.output_encoding
    )
    files["qfq_event_catalog_zh"] = _write_csv(
        _event_catalog_zh(qfq_events),
        output_dir / "eastmoney_qfq_event_catalog_zh.csv",
        layer.output_encoding,
    )
    files["hfq_event_catalog_zh"] = _write_csv(
        _event_catalog_zh(hfq_events),
        output_dir / "eastmoney_hfq_event_catalog_zh.csv",
        layer.output_encoding,
    )
    files["qfq_events_by_year"] = _write_csv(
        _events_by_year(qfq_events, "Eastmoney_qfq"),
        output_dir / "eastmoney_qfq_events_by_year.csv",
        layer.output_encoding,
    )
    files["hfq_events_by_year"] = _write_csv(
        _events_by_year(hfq_events, "Eastmoney_hfq"),
        output_dir / "eastmoney_hfq_events_by_year.csv",
        layer.output_encoding,
    )
    qh_summary, qh_detail = compare_event_pools(
        qfq_events,
        hfq_events,
        east_processed / "qfq",
        east_processed / "hfq",
        "Eastmoney_qfq",
        "Eastmoney_hfq",
        "qfq",
        "hfq",
        methodology,
        membership,
        calendar,
        histories,
    )
    files["qfq_hfq_summary"] = _write_csv(
        qh_summary, output_dir / "eastmoney_qfq_hfq_event_summary.csv", layer.output_encoding
    )
    files["qfq_hfq_detail"] = _write_csv(
        qh_detail, output_dir / "eastmoney_qfq_hfq_event_comparison.csv", layer.output_encoding
    )
    files["qfq_hfq_difference_reasons"] = _write_csv(
        _difference_reason_summary(qh_detail),
        output_dir / "eastmoney_qfq_hfq_difference_reason_summary.csv",
        layer.output_encoding,
    )

    if eastmoney_only:
        def qh_value(metric: str) -> float:
            values = qh_summary.loc[qh_summary["metric"].eq(metric), "value"]
            return float(values.iloc[0]) if len(values) else np.nan

        status = {
            "scope": "Eastmoney raw/qfq/hfq full historical CSI300 universe only",
            "historical_universe": "historical_CSI300_t_minus_1",
            "qfq_event_count": int(qh_value("Eastmoney_qfq_event_count")),
            "hfq_event_count": int(qh_value("Eastmoney_hfq_event_count")),
            "intersection": int(qh_value("intersection")),
            "qfq_only": int(qh_value("Eastmoney_qfq_only")),
            "hfq_only": int(qh_value("Eastmoney_hfq_only")),
            "qfq_hfq_jaccard": qh_value("Jaccard_similarity"),
            "final_adjustment_frozen": False,
            "baostock_cross_validation_complete": False,
            "future_returns_or_CAR_computed": False,
            "event_thresholds_changed": False,
        }
        files["eastmoney_only_status"] = _write_json(
            status, output_dir / "eastmoney_only_audit_status.json"
        )
        report_lines = [
            "# 东方财富 raw/qfq/hfq 全样本审计",
            "",
            "本报告只包含东方财富完整价格层和事件识别结果。BaoStock交叉验证尚未纳入，",
            "因此不在本报告中冻结唯一主复权口径。没有计算未来收益、CAR或回归。",
            "",
            f"- Eastmoney qfq事件数：{status['qfq_event_count']}",
            f"- Eastmoney hfq事件数：{status['hfq_event_count']}",
            f"- 共同事件数：{status['intersection']}",
            f"- qfq-only：{status['qfq_only']}",
            f"- hfq-only：{status['hfq_only']}",
            f"- Jaccard：{status['qfq_hfq_jaccard']:.6f}",
            "",
            "历史股票池严格按事件日前一个市场交易日t-1的历史沪深300成分股判断。",
        ]
        report_path = output_dir / "eastmoney_only_audit_report.md"
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        files["eastmoney_only_report"] = report_path
        return files

    build_baostock_processed(codes, layer, layer_path)
    bao_dir = layer.resolve(layer.baostock_processed_dir, layer_path)
    bao_events = screen_price_source(
        methodology,
        methodology_path,
        bao_dir,
        "BaoStock",
        "qfq",
        membership,
        calendar,
        histories,
    )
    files["baostock_events"] = _write_csv(
        bao_events, output_dir / "baostock_full_events.csv", layer.output_encoding
    )
    files["baostock_events_by_year"] = _write_csv(
        _events_by_year(bao_events, "BaoStock_qfq"),
        output_dir / "baostock_full_events_by_year.csv",
        layer.output_encoding,
    )
    east_bao_summaries: dict[str, pd.DataFrame] = {}
    east_bao_details: dict[str, pd.DataFrame] = {}
    for adjustment, east_events in (("qfq", qfq_events), ("hfq", hfq_events)):
        summary, detail = compare_event_pools(
            east_events,
            bao_events,
            east_processed / adjustment,
            bao_dir,
            f"Eastmoney_{adjustment}",
            "BaoStock_qfq",
            adjustment,
            "qfq",
            methodology,
            membership,
            calendar,
            histories,
        )
        east_bao_summaries[adjustment] = summary
        east_bao_details[adjustment] = detail
        files[f"{adjustment}_baostock_summary"] = _write_csv(
            summary,
            output_dir / f"eastmoney_{adjustment}_baostock_full_pool_summary.csv",
            layer.output_encoding,
        )
        files[f"{adjustment}_baostock_detail"] = _write_csv(
            detail,
            output_dir / f"eastmoney_{adjustment}_baostock_full_pool_comparison.csv",
            layer.output_encoding,
        )
        files[f"{adjustment}_baostock_difference_reasons"] = _write_csv(
            _difference_reason_summary(detail),
            output_dir
            / f"eastmoney_{adjustment}_baostock_difference_reason_summary.csv",
            layer.output_encoding,
        )

    recommendation = _recommend_adjustment(
        price_audit["stock"],
        path_audit["stock"],
        qh_summary,
        east_bao_summaries,
    )
    final_adjustment = str(recommendation["recommended_adjustment"])
    east_final_events = hfq_events if final_adjustment == "hfq" else qfq_events
    eb_summary = east_bao_summaries[final_adjustment]
    eb_detail = east_bao_details[final_adjustment]
    files["eastmoney_baostock_summary"] = _write_csv(
        eb_summary,
        output_dir / "eastmoney_baostock_full_pool_summary.csv",
        layer.output_encoding,
    )
    files["eastmoney_baostock_detail"] = _write_csv(
        eb_detail,
        output_dir / "eastmoney_baostock_full_pool_comparison.csv",
        layer.output_encoding,
    )
    files["eastmoney_baostock_difference_reasons"] = _write_csv(
        _difference_reason_summary(eb_detail),
        output_dir / "eastmoney_baostock_difference_reason_summary.csv",
        layer.output_encoding,
    )

    yahoo_sample = choose_yahoo_sample(
        membership,
        path_audit["stock"],
        layer.yahoo_sample_size,
        layer.random_seed,
    )
    files["yahoo_sample"] = _write_csv(
        yahoo_sample, output_dir / "yahoo_validation_sample.csv", layer.output_encoding
    )
    if not skip_yahoo_download:
        download_yahoo_histories(
            yahoo_sample["stock_code"].astype(str).tolist(), layer, layer_path
        )
    yahoo_audit = audit_yahoo_sample(
        yahoo_sample, final_adjustment, layer, layer_path
    )
    files["yahoo_audit"] = _write_csv(
        yahoo_audit, output_dir / "yahoo_eastmoney_return_path_audit.csv", layer.output_encoding
    )

    recommendation["historical_universe"] = "historical_CSI300_t_minus_1"
    recommendation["event_thresholds_changed"] = False
    recommendation["future_returns_or_CAR_computed"] = False
    files["recommendation"] = _write_json(
        recommendation, output_dir / "price_source_recommendation.json"
    )
    recommended_events = east_final_events.copy()
    recommended_events["data_source"] = "Eastmoney"
    files["recommended_events"] = _write_csv(
        recommended_events,
        output_dir / "recommended_main_events.csv",
        layer.output_encoding,
    )
    files["event_dataset"] = _write_csv(
        recommended_events,
        output_dir / "event_dataset.csv",
        layer.output_encoding,
    )
    recommended_by_year = _events_by_year(
        recommended_events, f"Eastmoney_{final_adjustment}"
    )
    files["recommended_events_by_year"] = _write_csv(
        recommended_by_year,
        output_dir / "events_by_year.csv",
        layer.output_encoding,
    )
    recommended_by_stock = (
        recommended_events.groupby(["stock_code", "stock_name"], dropna=False)
        .agg(
            event_count=("event_date", "size"),
            first_event_date=("event_date", "min"),
            last_event_date=("event_date", "max"),
        )
        .reset_index()
        .sort_values(["event_count", "stock_code"], ascending=[False, True])
    )
    files["recommended_events_by_stock"] = _write_csv(
        recommended_by_stock,
        output_dir / "events_by_stock.csv",
        layer.output_encoding,
    )
    catalog = _event_catalog_zh(recommended_events)
    files["recommended_event_catalog_zh"] = _write_csv(
        catalog,
        output_dir / "recommended_event_catalog_zh.csv",
        layer.output_encoding,
    )
    report = _build_markdown_report(
        recommendation,
        price_audit["year"],
        path_audit["stock"],
        qh_summary,
        qh_detail,
        eb_summary,
        eb_detail,
        yahoo_audit,
    )
    report_path = output_dir / "price_source_audit_report.md"
    report_path.write_text(report, encoding="utf-8")
    files["report"] = report_path
    return files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Eastmoney raw/qfq/hfq, compare full event pools, and sample Yahoo."
    )
    parser.add_argument(
        "--config",
        default="config/eastmoney_price_audit.json",
        help="Price-layer audit configuration path",
    )
    parser.add_argument("--skip-eastmoney-download", action="store_true")
    parser.add_argument("--skip-baostock-download", action="store_true")
    parser.add_argument("--skip-yahoo-download", action="store_true")
    parser.add_argument(
        "--eastmoney-only",
        action="store_true",
        help="Produce the complete Eastmoney raw/qfq/hfq audit without waiting for BaoStock.",
    )
    parser.add_argument(
        "--reuse-screened-events",
        action="store_true",
        help="Reuse completed Eastmoney qfq/hfq event CSVs and only rebuild comparisons/reports.",
    )
    args = parser.parse_args()
    outputs = run_price_source_audit(
        args.config,
        skip_eastmoney_download=args.skip_eastmoney_download,
        skip_baostock_download=args.skip_baostock_download,
        skip_yahoo_download=args.skip_yahoo_download,
        eastmoney_only=args.eastmoney_only,
        reuse_screened_events=args.reuse_screened_events,
    )
    print("Price-source audit outputs:")
    for name, path in outputs.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
