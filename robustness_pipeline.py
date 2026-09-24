"""Robustness checks for the frozen A-share distress-reversal event study.

The baseline CSV is read-only. Breakout-threshold and ATR event sets are
reconstructed from the same frozen universe, distress, consolidation, data
quality, cooldown, volume, and return rules used by the baseline pipeline.
Only the robustness dimension named by each test is changed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# The screening module imports matplotlib only for optional event charts. The
# robustness pipeline does not create those charts, so provide a harmless stub
# when the bundled D-drive Python runtime does not include matplotlib.
if importlib.util.find_spec("matplotlib") is None:
    matplotlib_stub = types.ModuleType("matplotlib")
    pyplot_stub = types.ModuleType("matplotlib.pyplot")
    matplotlib_stub.pyplot = pyplot_stub
    sys.modules["matplotlib"] = matplotlib_stub
    sys.modules["matplotlib.pyplot"] = pyplot_stub

from csi300_events.acquisition import load_processed_stock
from csi300_events.adaptive_pipeline import (
    StockContext,
    _apply_cooldown,
    _build_context,
    _load_name_history,
)
from csi300_events.hfq_final_sample import (
    ConsolidationVersion,
    _detect_consolidation,
    _evaluate_detected_event,
    _make_linear_state,
)
from csi300_events.methodology import load_methodology_config


DEFAULT_INPUT = PROJECT_ROOT / "outputs" / "hfq_final" / "final_analysis_dataset_v1.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "hfq_final"
FINAL_CONFIG = PROJECT_ROOT / "config" / "hfq_final_sample.json"
RUNTIME_DIR = PROJECT_ROOT / ".runtime" / "robustness_pipeline"

DEFAULT_NODE = Path(
    r"D:\programming _Learning\.codex\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
DEFAULT_NODE_MODULES = Path(
    r"D:\programming _Learning\.codex\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
)

THRESHOLDS = [("0.5%", 0.005), ("1%", 0.01), ("2%", 0.02)]
RETURN_WINDOWS = ["R1", "R3", "R5", "R20", "R60"]
REGRESSION_RETURNS = ["R1", "R5", "R20", "R60"]
MODEL_SPECS = {
    "M1": ["ln_RVOL"],
    "M2": ["ln_RVOL", "Drawdown", "Duration", "BreakoutStrength"],
}
CONTROLS = ["Drawdown", "Duration", "BreakoutStrength"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_from(value: str, config_path: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()


def clean_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    return value


def records(frame: pd.DataFrame, columns: list[str] | None = None) -> list[list[Any]]:
    selected = frame if columns is None else frame[columns]
    return clean_json(selected.to_numpy(dtype=object).tolist())


def normal_two_sided_p(z_value: float) -> float:
    if not math.isfinite(z_value):
        return math.nan
    return math.erfc(abs(z_value) / math.sqrt(2.0))


def ols_hc3(
    data: pd.DataFrame,
    dependent: str,
    predictors: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    subset = data[[dependent, *predictors]].dropna().copy()
    if len(subset) <= len(predictors) + 1:
        raise ValueError(f"Insufficient complete observations for {dependent} ~ {predictors}")
    y = subset[dependent].to_numpy(dtype=float)
    x = np.column_stack(
        [np.ones(len(subset)), subset[predictors].to_numpy(dtype=float)]
    )
    names = ["Intercept", *predictors]
    xtx_inv = np.linalg.pinv(x.T @ x)
    beta = xtx_inv @ x.T @ y
    residual = y - x @ beta
    leverage = np.sum((x @ xtx_inv) * x, axis=1)
    denominator = np.maximum(1.0 - leverage, np.finfo(float).eps)
    adjusted = residual / denominator
    meat = x.T @ (x * np.square(adjusted)[:, None])
    covariance = xtx_inv @ meat @ xtx_inv
    standard_error = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    z_values = np.divide(
        beta,
        standard_error,
        out=np.full_like(beta, np.nan),
        where=standard_error > 0,
    )
    total_ss = float(np.sum(np.square(y - y.mean())))
    residual_ss = float(np.sum(np.square(residual)))
    r_squared = math.nan if total_ss == 0 else 1.0 - residual_ss / total_ss
    rows: list[dict[str, Any]] = []
    for index, term in enumerate(names):
        rows.append(
            {
                "dependent": dependent,
                "term": term,
                "coefficient": float(beta[index]),
                "standard_error": float(standard_error[index]),
                "test_statistic": float(z_values[index]),
                "p_value": normal_two_sided_p(float(z_values[index])),
                "n": int(len(subset)),
                "r_squared": float(r_squared),
            }
        )
    return rows, {
        "dependent": dependent,
        "n": int(len(subset)),
        "r_squared": float(r_squared),
    }


def run_ols_suite(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for dependent in REGRESSION_RETURNS:
        for model, predictors in MODEL_SPECS.items():
            rows, summary = ols_hc3(data, dependent, predictors)
            specification = dependent + " ~ " + " + ".join(predictors)
            for row in rows:
                row.update({"model": model, "specification": specification})
                detail.append(row)
            summary.update({"model": model, "specification": specification})
            summaries.append(summary)
    return pd.DataFrame(detail), pd.DataFrame(summaries)


def logistic_regression(
    data: pd.DataFrame,
    dependent: str,
    predictors: list[str],
    max_iterations: int = 200,
    tolerance: float = 1e-10,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    subset = data[[dependent, *predictors]].dropna().copy()
    y = subset[dependent].to_numpy(dtype=float)
    x = np.column_stack(
        [np.ones(len(subset)), subset[predictors].to_numpy(dtype=float)]
    )
    names = ["Intercept", *predictors]
    if len(np.unique(y)) != 2:
        raise ValueError("Failure20 must contain both 0 and 1 in the model sample")

    beta = np.zeros(x.shape[1], dtype=float)
    converged = False
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        eta = np.clip(x @ beta, -35.0, 35.0)
        probability = 1.0 / (1.0 + np.exp(-eta))
        weight = np.maximum(probability * (1.0 - probability), 1e-12)
        information = x.T @ (x * weight[:, None])
        score = x.T @ (y - probability)
        step = np.linalg.pinv(information) @ score
        beta_next = beta + step
        if float(np.max(np.abs(step))) < tolerance:
            beta = beta_next
            converged = True
            break
        beta = beta_next

    eta = np.clip(x @ beta, -35.0, 35.0)
    probability = 1.0 / (1.0 + np.exp(-eta))
    weight = np.maximum(probability * (1.0 - probability), 1e-12)
    covariance = np.linalg.pinv(x.T @ (x * weight[:, None]))
    standard_error = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    z_values = np.divide(
        beta,
        standard_error,
        out=np.full_like(beta, np.nan),
        where=standard_error > 0,
    )
    epsilon = 1e-15
    log_likelihood = float(
        np.sum(
            y * np.log(np.clip(probability, epsilon, 1.0 - epsilon))
            + (1.0 - y) * np.log(np.clip(1.0 - probability, epsilon, 1.0))
        )
    )
    null_probability = float(y.mean())
    null_log_likelihood = float(
        np.sum(
            y * math.log(max(null_probability, epsilon))
            + (1.0 - y) * math.log(max(1.0 - null_probability, epsilon))
        )
    )
    pseudo_r_squared = (
        math.nan
        if null_log_likelihood == 0
        else 1.0 - log_likelihood / null_log_likelihood
    )

    rows = []
    for index, term in enumerate(names):
        rows.append(
            {
                "term": term,
                "coefficient": float(beta[index]),
                "standard_error": float(standard_error[index]),
                "z_value": float(z_values[index]),
                "p_value": normal_two_sided_p(float(z_values[index])),
                "odds_ratio": float(math.exp(float(np.clip(beta[index], -700, 700)))),
                "n": int(len(subset)),
                "mcfadden_pseudo_r2": float(pseudo_r_squared),
            }
        )
    return pd.DataFrame(rows), {
        "n": int(len(subset)),
        "converged": bool(converged),
        "iterations": int(iterations),
        "log_likelihood": log_likelihood,
        "null_log_likelihood": null_log_likelihood,
        "mcfadden_pseudo_r2": float(pseudo_r_squared),
    }


def prepare_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    numeric = [
        "Drawdown",
        "Duration",
        "bandwidth",
        "BreakoutStrength",
        "RelativeVolume",
        *RETURN_WINDOWS,
    ]
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if (result["RelativeVolume"].dropna() <= 0).any():
        raise ValueError("RelativeVolume must be positive where observed")
    result["ln_RVOL"] = np.log(result["RelativeVolume"])
    return result


def descriptive_statistics(frame: pd.DataFrame) -> pd.DataFrame:
    variables = [
        "Drawdown",
        "Duration",
        "bandwidth",
        "BreakoutStrength",
        "RelativeVolume",
        "ln_RVOL",
        *RETURN_WINDOWS,
    ]
    rows = []
    for variable in variables:
        values = pd.to_numeric(frame[variable], errors="coerce").dropna()
        rows.append(
            {
                "variable": variable,
                "n": int(len(values)),
                "missing": int(len(frame) - len(values)),
                "mean": float(values.mean()),
                "median": float(values.median()),
                "std_dev": float(values.std(ddof=1)),
                "minimum": float(values.min()),
                "p25": float(values.quantile(0.25, interpolation="linear")),
                "p75": float(values.quantile(0.75, interpolation="linear")),
                "maximum": float(values.max()),
            }
        )
    return pd.DataFrame(rows)


def rvol_group_statistics(frame: pd.DataFrame) -> tuple[pd.DataFrame, tuple[float, float]]:
    q1, q2 = frame["ln_RVOL"].quantile(
        [1 / 3, 2 / 3], interpolation="linear"
    ).tolist()
    groups = pd.cut(
        frame["ln_RVOL"],
        bins=[-np.inf, q1, q2, np.inf],
        labels=["Low", "Medium", "High"],
        include_lowest=True,
        right=True,
    )
    work = frame.assign(RVOL_group=groups)
    rows = []
    for group in ["Low", "Medium", "High"]:
        group_data = work.loc[work["RVOL_group"].eq(group)]
        for window in RETURN_WINDOWS:
            returns = pd.to_numeric(group_data[window], errors="coerce").dropna()
            rows.append(
                {
                    "group": group,
                    "event_count": int(len(group_data)),
                    "return_window": window,
                    "return_n": int(len(returns)),
                    "missing": int(len(group_data) - len(returns)),
                    "mean": float(returns.mean()),
                    "median": float(returns.median()),
                    "positive_rate": float((returns > 0).mean()),
                }
            )
    return pd.DataFrame(rows), (float(q1), float(q2))


def pearson_correlations(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for window in RETURN_WINDOWS:
        pair = frame[["ln_RVOL", window]].dropna()
        correlation = float(pair["ln_RVOL"].corr(pair[window]))
        fisher_z = (
            float(np.arctanh(np.clip(correlation, -0.999999999, 0.999999999)))
            * math.sqrt(max(len(pair) - 3, 0))
            if len(pair) > 3
            else math.nan
        )
        rows.append(
            {
                "return_window": window,
                "n": int(len(pair)),
                "pearson_correlation": correlation,
                "p_value_fisher_z": normal_two_sided_p(fisher_z),
                "missing_pairs": int(len(frame) - len(pair)),
            }
        )
    return pd.DataFrame(rows)


def activity_window(context: StockContext, event_position: int, lookback: int) -> dict[str, float]:
    prior_positions = np.flatnonzero(
        context.normal_trade[:event_position]
        & np.isfinite(context.volume[:event_position])
        & (context.volume[:event_position] > 0)
    )
    volume_t = context.volume[event_position]
    if len(prior_positions) < lookback:
        return {"mean_volume": math.nan, "relative_volume": math.nan}
    mean_volume = float(np.mean(context.volume[prior_positions[-lookback:]]))
    relative_volume = (
        float(volume_t / mean_volume)
        if np.isfinite(volume_t) and volume_t > 0 and mean_volume > 0
        else math.nan
    )
    return {"mean_volume": mean_volume, "relative_volume": relative_volume}


def atr20_before_event(context: StockContext, event_position: int) -> float:
    valid_positions = np.flatnonzero(
        context.normal_trade
        & context.valid_price
        & np.isfinite(context.high)
        & np.isfinite(context.low)
        & np.isfinite(context.close)
    )
    stop = int(np.searchsorted(valid_positions, event_position, side="left"))
    if stop < 21:
        return math.nan
    selected_indices = range(stop - 20, stop)
    true_ranges = []
    for index in selected_indices:
        position = int(valid_positions[index])
        previous_position = int(valid_positions[index - 1])
        high = float(context.high[position])
        low = float(context.low[position])
        previous_close = float(context.close[previous_position])
        true_ranges.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
    return float(np.mean(true_ranges))


def failure20_metric(
    context: StockContext,
    event_position: int,
    breakout_price: float,
) -> dict[str, Any]:
    target_positions = np.arange(event_position + 1, event_position + 21)
    if len(target_positions) < 20 or int(target_positions[-1]) >= len(context.dates):
        return {
            "Failure20": math.nan,
            "future20_min_close": math.nan,
            "future20_available_closes": 0,
            "Failure20_status": "calendar_not_available",
        }
    available = (
        context.normal_trade[target_positions]
        & context.valid_price[target_positions]
        & np.isfinite(context.close[target_positions])
    )
    observed = context.close[target_positions][available]
    minimum = float(np.min(observed)) if len(observed) else math.nan
    if len(observed) and minimum < breakout_price:
        failure = 1.0
        status = "failure_observed"
    elif bool(np.all(available)):
        failure = 0.0
        status = "no_failure_full_window"
    else:
        failure = math.nan
        status = "incomplete_window_without_observed_failure"
    return {
        "Failure20": failure,
        "future20_min_close": minimum,
        "future20_available_closes": int(np.sum(available)),
        "Failure20_status": status,
    }


def load_screening_inputs() -> dict[str, Any]:
    raw = json.loads(FINAL_CONFIG.read_text(encoding="utf-8"))
    methodology_path = resolve_from(str(raw["base_methodology_config"]), FINAL_CONFIG)
    methodology = load_methodology_config(methodology_path)
    stock_dir = resolve_from(str(raw["processed_stock_dir"]), FINAL_CONFIG)
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
    version_values = raw["versions"]["A_main"]
    version = ConsolidationVersion(name="A_main", **version_values)
    return {
        "raw": raw,
        "methodology": methodology,
        "stock_dir": stock_dir,
        "membership": membership,
        "calendar": calendar,
        "histories": histories,
        "version": version,
    }


def reconstruct_samples(
    baseline: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    inputs = load_screening_inputs()
    methodology = inputs["methodology"]
    membership = inputs["membership"]
    calendar = inputs["calendar"]
    histories = inputs["histories"]
    version = inputs["version"]
    stock_dir: Path = inputs["stock_dir"]

    threshold_records: dict[str, list[dict[str, Any]]] = {
        label: [] for label, _ in THRESHOLDS
    }
    atr_records: list[dict[str, Any]] = []
    baseline_metrics: list[dict[str, Any]] = []

    baseline_work = baseline.copy()
    baseline_work["event_date"] = pd.to_datetime(baseline_work["event_date"], errors="raise")
    baseline_by_code = {
        str(code): group.copy()
        for code, group in baseline_work.groupby("stock_code", sort=False)
    }

    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    for number, stock_code in enumerate(codes, start=1):
        daily_path = stock_dir / f"{stock_code.replace('.', '_')}.csv.gz"
        if not daily_path.exists():
            raise FileNotFoundError(f"Missing frozen price cache: {daily_path}")
        daily = load_processed_stock(daily_path)
        if daily.empty:
            continue
        context, _ = _build_context(daily, calendar, membership, histories)
        state = _make_linear_state(context)

        sample_positions = np.flatnonzero(
            (context.dates >= pd.Timestamp(methodology.sample_start_primary))
            & (context.dates <= pd.Timestamp(methodology.sample_end))
        )
        known = np.flatnonzero(context.status_known)
        if len(known):
            minimum_position = int(known[0]) + (
                methodology.min_consolidation_duration
                + methodology.peak_lookback_before_consolidation
            )
            positions = sample_positions[
                (sample_positions >= minimum_position)
                & context.member_t_minus_1[sample_positions]
            ]
            rolling_high = (
                pd.Series(np.where(context.valid_price, context.close, np.nan))
                .rolling(methodology.min_consolidation_duration, min_periods=1)
                .max()
                .shift(1)
                .to_numpy(dtype=float)
            )
            before_threshold = {label: [] for label, _ in THRESHOLDS}
            before_atr: list[dict[str, Any]] = []
            for event_position_value in positions:
                event_position = int(event_position_value)
                event_close = context.close[event_position]
                if (
                    not context.normal_trade[event_position]
                    or not context.valid_price[event_position]
                    or not np.isfinite(event_close)
                    or event_close <= 0
                    or not np.isfinite(rolling_high[event_position])
                    or event_close <= rolling_high[event_position]
                ):
                    continue
                consolidation = _detect_consolidation(
                    context,
                    state,
                    event_position,
                    version,
                    methodology,
                )
                record, outcome = _evaluate_detected_event(
                    context,
                    event_position,
                    version,
                    methodology,
                    -np.inf,
                    consolidation,
                    [1, 3, 5, 20, 60],
                )
                if outcome != "selected_before_cooldown" or record is None:
                    continue
                breakout_strength = float(record["BreakoutStrength"])
                for label, cutoff in THRESHOLDS:
                    if breakout_strength >= cutoff:
                        threshold_record = record.copy()
                        threshold_record["threshold_label"] = label
                        threshold_record["breakout_threshold"] = cutoff
                        before_threshold[label].append(threshold_record)

                atr20 = atr20_before_event(context, event_position)
                if (
                    np.isfinite(atr20)
                    and float(event_close) > float(record["resistance"]) + atr20
                ):
                    atr_record = record.copy()
                    atr_record["ATR20"] = float(atr20)
                    atr_record["ATR20_to_resistance"] = float(
                        atr20 / float(record["resistance"])
                    )
                    atr_record["breakout_excess_over_ATR"] = float(
                        event_close - float(record["resistance"]) - atr20
                    )
                    before_atr.append(atr_record)

            for label, _ in THRESHOLDS:
                threshold_records[label].extend(
                    _apply_cooldown(
                        before_threshold[label], methodology.cooldown_market_days
                    )
                )
            atr_records.extend(
                _apply_cooldown(before_atr, methodology.cooldown_market_days)
            )

        if stock_code in baseline_by_code:
            date_to_position = {
                pd.Timestamp(date): index for index, date in enumerate(context.dates)
            }
            for _, row in baseline_by_code[stock_code].iterrows():
                event_date = pd.Timestamp(row["event_date"])
                if event_date not in date_to_position:
                    raise ValueError(f"Baseline event date absent from calendar: {stock_code} {event_date}")
                event_position = int(date_to_position[event_date])
                if int(row["_calendar_index"]) != event_position:
                    raise ValueError(
                        f"Calendar index mismatch: {stock_code} {event_date:%Y-%m-%d}"
                    )
                item: dict[str, Any] = {
                    "stock_code": stock_code,
                    "event_date": event_date,
                }
                for lookback in [20, 60, 120]:
                    metric = activity_window(context, event_position, lookback)
                    item[f"mean_volume{lookback}"] = metric["mean_volume"]
                    item[f"RVOL{lookback}"] = metric["relative_volume"]
                    item[f"ln_RVOL{lookback}"] = (
                        float(math.log(metric["relative_volume"]))
                        if np.isfinite(metric["relative_volume"])
                        and metric["relative_volume"] > 0
                        else math.nan
                    )
                item.update(
                    failure20_metric(
                        context,
                        event_position,
                        float(row["breakout_price"]),
                    )
                )
                baseline_metrics.append(item)

        if number % 25 == 0 or number == len(codes):
            print(
                f"robustness screening: {number}/{len(codes)} stocks; "
                f"threshold 0.5%={len(threshold_records['0.5%'])}, "
                f"ATR20={len(atr_records)}",
                flush=True,
            )

    threshold_frames: dict[str, pd.DataFrame] = {}
    for label, _ in THRESHOLDS:
        frame = pd.DataFrame(threshold_records[label])
        if len(frame):
            frame = frame.sort_values(["event_date", "stock_code"]).reset_index(drop=True)
        threshold_frames[label] = frame
    atr_frame = pd.DataFrame(atr_records)
    if len(atr_frame):
        atr_frame = atr_frame.sort_values(["event_date", "stock_code"]).reset_index(drop=True)
    metrics_frame = pd.DataFrame(baseline_metrics).sort_values(
        ["event_date", "stock_code"]
    )

    baseline_keys = set(
        zip(
            baseline_work["stock_code"].astype(str),
            baseline_work["event_date"].dt.strftime("%Y-%m-%d"),
        )
    )
    reconstructed = threshold_frames["0.5%"].copy()
    reconstructed["event_date"] = pd.to_datetime(reconstructed["event_date"])
    reconstructed_keys = set(
        zip(
            reconstructed["stock_code"].astype(str),
            reconstructed["event_date"].dt.strftime("%Y-%m-%d"),
        )
    )
    missing_from_reconstruction = sorted(baseline_keys - reconstructed_keys)
    extra_in_reconstruction = sorted(reconstructed_keys - baseline_keys)
    if missing_from_reconstruction or extra_in_reconstruction:
        raise RuntimeError(
            "0.5% reconstruction does not match the frozen baseline event keys: "
            f"missing={missing_from_reconstruction[:5]}, extra={extra_in_reconstruction[:5]}"
        )
    if len(metrics_frame) != len(baseline_work):
        raise RuntimeError(
            f"Baseline metric coverage mismatch: {len(metrics_frame)} vs {len(baseline_work)}"
        )
    volume60_check = metrics_frame.merge(
        baseline_work[["stock_code", "event_date", "RelativeVolume"]],
        on=["stock_code", "event_date"],
        how="left",
        validate="one_to_one",
    )
    rvol60_difference = float(
        np.nanmax(
            np.abs(
                pd.to_numeric(volume60_check["RVOL60"], errors="coerce")
                - pd.to_numeric(volume60_check["RelativeVolume"], errors="coerce")
            )
        )
    )
    if rvol60_difference > 1e-12:
        raise RuntimeError(f"RVOL60 reconstruction differs from baseline by {rvol60_difference}")
    validation = {
        "baseline_event_count": int(len(baseline_work)),
        "reconstructed_event_count": int(len(reconstructed)),
        "event_key_match": True,
        "missing_event_keys": 0,
        "extra_event_keys": 0,
        "max_abs_rvol60_difference": rvol60_difference,
    }
    return threshold_frames, atr_frame, metrics_frame, validation


def significance(p_value: float) -> str:
    if not math.isfinite(p_value):
        return ""
    if p_value < 0.01:
        return "***"
    if p_value < 0.05:
        return "**"
    if p_value < 0.10:
        return "*"
    return ""


def add_variant(frame: pd.DataFrame, column: str, value: Any) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, column, value)
    return result


def sheet(
    name: str,
    title: str,
    subtitle: str,
    headers: list[str],
    rows: list[list[Any]],
    widths: list[int],
    formats: dict[int, str] | None = None,
    freeze: bool = False,
    preview_rows: int = 38,
    cell_formats: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "subtitle": subtitle,
        "headers": headers,
        "rows": rows,
        "widths": widths,
        "formats": {str(key): value for key, value in (formats or {}).items()},
        "cell_formats": [
            {"range": address, "format": number_format}
            for address, number_format in (cell_formats or [])
        ],
        "freeze": freeze,
        "preview_rows": preview_rows,
    }


def build_results(
    baseline: pd.DataFrame,
    source_path: Path,
    source_hash: str,
    threshold_frames: dict[str, pd.DataFrame],
    atr_frame: pd.DataFrame,
    metrics: pd.DataFrame,
    validation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_info = {
        "filename": source_path.name,
        "sha256": source_hash,
        "events": int(len(baseline)),
        "stocks": int(baseline["stock_code"].nunique()),
    }
    core_rows: list[dict[str, Any]] = []

    threshold_summary_rows: list[dict[str, Any]] = []
    threshold_desc_parts = []
    threshold_group_parts = []
    threshold_corr_parts = []
    threshold_ols_parts = []
    threshold_event_parts = []
    for label, cutoff in THRESHOLDS:
        analysis = prepare_analysis(threshold_frames[label])
        desc = add_variant(descriptive_statistics(analysis), "threshold", label)
        groups, cutoffs = rvol_group_statistics(analysis)
        groups = add_variant(groups, "threshold", label)
        corr = add_variant(pearson_correlations(analysis), "threshold", label)
        ols, _ = run_ols_suite(analysis)
        ols = add_variant(ols, "threshold", label)
        threshold_desc_parts.append(desc)
        threshold_group_parts.append(groups)
        threshold_corr_parts.append(corr)
        threshold_ols_parts.append(ols)
        event_columns = [
            "stock_code",
            "stock_name",
            "event_date",
            "Drawdown",
            "Duration",
            "bandwidth",
            "BreakoutStrength",
            "RelativeVolume",
            "ln_RVOL",
            *RETURN_WINDOWS,
        ]
        threshold_event_parts.append(
            add_variant(analysis[event_columns], "threshold", label)
        )
        threshold_summary_rows.append(
            {
                "threshold": label,
                "cutoff": cutoff,
                "events": int(len(analysis)),
                "stocks": int(analysis["stock_code"].nunique()),
                "first_event": pd.to_datetime(analysis["event_date"]).min(),
                "last_event": pd.to_datetime(analysis["event_date"]).max(),
                "ln_RVOL_q1": cutoffs[0],
                "ln_RVOL_q2": cutoffs[1],
            }
        )
        for row in ols.loc[ols["term"].eq("ln_RVOL")].to_dict(orient="records"):
            core_rows.append(
                {
                    "analysis": "Breakout threshold",
                    "variant": label,
                    "outcome": row["dependent"],
                    "model": row["model"],
                    "n": row["n"],
                    "rvol_coefficient": row["coefficient"],
                    "standard_error": row["standard_error"],
                    "p_value": row["p_value"],
                    "r_squared": row["r_squared"],
                    "r2_type": "OLS R2",
                    "odds_ratio": math.nan,
                }
            )
    threshold_summary = pd.DataFrame(threshold_summary_rows)
    threshold_desc = pd.concat(threshold_desc_parts, ignore_index=True)
    threshold_groups = pd.concat(threshold_group_parts, ignore_index=True)
    threshold_corr = pd.concat(threshold_corr_parts, ignore_index=True)
    threshold_ols = pd.concat(threshold_ols_parts, ignore_index=True)
    threshold_events = pd.concat(threshold_event_parts, ignore_index=True)

    atr_analysis = prepare_analysis(atr_frame)
    atr_desc = descriptive_statistics(atr_analysis)
    atr_groups, atr_cutoffs = rvol_group_statistics(atr_analysis)
    atr_ols, _ = run_ols_suite(atr_analysis)
    for row in atr_ols.loc[atr_ols["term"].eq("ln_RVOL")].to_dict(orient="records"):
        core_rows.append(
            {
                "analysis": "ATR20 breakout",
                "variant": "Close > resistance + ATR20",
                "outcome": row["dependent"],
                "model": row["model"],
                "n": row["n"],
                "rvol_coefficient": row["coefficient"],
                "standard_error": row["standard_error"],
                "p_value": row["p_value"],
                "r_squared": row["r_squared"],
                "r2_type": "OLS R2",
                "odds_ratio": math.nan,
            }
        )

    baseline_dates = baseline.copy()
    baseline_dates["event_date"] = pd.to_datetime(baseline_dates["event_date"], errors="raise")
    augmented = baseline_dates.merge(
        metrics,
        on=["stock_code", "event_date"],
        how="left",
        validate="one_to_one",
    )
    volume_summary_rows = []
    volume_ols_parts = []
    for lookback in [20, 60, 120]:
        work = augmented.copy()
        work["RelativeVolume"] = pd.to_numeric(work[f"RVOL{lookback}"], errors="coerce")
        work["ln_RVOL"] = pd.to_numeric(work[f"ln_RVOL{lookback}"], errors="coerce")
        ols, _ = run_ols_suite(work)
        ols = add_variant(ols, "volume_window", f"RVOL{lookback}")
        volume_ols_parts.append(ols)
        valid = work["RelativeVolume"].dropna()
        volume_summary_rows.append(
            {
                "volume_window": f"RVOL{lookback}",
                "events": int(len(work)),
                "valid_rvol": int(len(valid)),
                "missing_rvol": int(len(work) - len(valid)),
                "mean_rvol": float(valid.mean()),
                "median_rvol": float(valid.median()),
            }
        )
        for row in ols.loc[ols["term"].eq("ln_RVOL")].to_dict(orient="records"):
            core_rows.append(
                {
                    "analysis": "Volume window",
                    "variant": f"RVOL{lookback}",
                    "outcome": row["dependent"],
                    "model": row["model"],
                    "n": row["n"],
                    "rvol_coefficient": row["coefficient"],
                    "standard_error": row["standard_error"],
                    "p_value": row["p_value"],
                    "r_squared": row["r_squared"],
                    "r2_type": "OLS R2",
                    "odds_ratio": math.nan,
                }
            )
    volume_summary = pd.DataFrame(volume_summary_rows)
    volume_ols = pd.concat(volume_ols_parts, ignore_index=True)

    failure_work = augmented.copy()
    failure_work["ln_RVOL"] = pd.to_numeric(failure_work["ln_RVOL60"], errors="coerce")
    logit, logit_summary = logistic_regression(
        failure_work,
        "Failure20",
        ["ln_RVOL", *CONTROLS],
    )
    logit_ln = logit.loc[logit["term"].eq("ln_RVOL")].iloc[0]
    core_rows.append(
        {
            "analysis": "Breakout failure",
            "variant": "Failure20 logistic",
            "outcome": "Failure20",
            "model": "Logit M2",
            "n": int(logit_ln["n"]),
            "rvol_coefficient": float(logit_ln["coefficient"]),
            "standard_error": float(logit_ln["standard_error"]),
            "p_value": float(logit_ln["p_value"]),
            "r_squared": float(logit_ln["mcfadden_pseudo_r2"]),
            "r2_type": "McFadden pseudo R2",
            "odds_ratio": float(logit_ln["odds_ratio"]),
        }
    )

    core = pd.DataFrame(core_rows)
    core["significance"] = core["p_value"].map(significance)
    table5 = core.loc[
        (
            core["analysis"].isin(
                ["Breakout threshold", "ATR20 breakout", "Volume window"]
            )
            & core["outcome"].eq("R20")
            & core["model"].eq("M2")
        )
        | core["analysis"].eq("Breakout failure")
    ].copy()
    table5["coefficient_with_stars"] = table5.apply(
        lambda row: f"{row['rvol_coefficient']:.6f}{row['significance']}", axis=1
    )

    failure_valid = pd.to_numeric(failure_work["Failure20"], errors="coerce").dropna()
    failure_summary_rows = [
        ["Baseline events", int(len(failure_work))],
        ["Failure20 available", int(len(failure_valid))],
        ["Failure20 missing", int(len(failure_work) - len(failure_valid))],
        ["Failures", int((failure_valid == 1).sum())],
        ["Non-failures", int((failure_valid == 0).sum())],
        ["Failure rate", float(failure_valid.mean())],
        ["Logit model N", int(logit_summary["n"])],
        ["Converged", "Yes" if logit_summary["converged"] else "No"],
        ["Iterations", int(logit_summary["iterations"])],
        ["McFadden pseudo R2", float(logit_summary["mcfadden_pseudo_r2"])],
    ]

    threshold_method_rows = [
        ["Baseline source", source_info["filename"]],
        ["Baseline SHA-256", source_info["sha256"]],
        ["0.5% event-key validation", "PASS: reconstructed keys exactly match all 193 frozen events"],
        ["Changed dimension", "BreakoutStrength cutoff only: 0.5%, 1%, 2%"],
        ["Held fixed", "Historical CSI300 universe, distress, consolidation, ST/data-quality rules, 60-day cooldown, RVOL60, return dates"],
        ["OLS inference", "HC3 heteroskedasticity-robust standard errors; asymptotic normal p-values"],
        ["Outlier handling", "No deletion and no winsorization"],
    ]
    atr_method_rows = [
        ["ATR20", "Mean of 20 true ranges before the event over normal stock trading days"],
        ["True range", "max(high-low, abs(high-previous valid close), abs(low-previous valid close)); hfq adjusted prices"],
        ["Breakout rule", "Adjusted close > sideways resistance + ATR20 (strict >)"],
        ["Held fixed", "Universe, distress, consolidation, ST/data quality, cooldown, RVOL60, returns"],
        ["Outlier handling", "No deletion and no winsorization"],
    ]
    volume_method_rows = [
        ["Event set", "Frozen 193-event baseline; event dates are not reconstructed or changed"],
        ["RVOL window", "Event-day volume divided by mean volume over prior 20, 60, or 120 normal stock trading days"],
        ["RVOL60 validation", f"Maximum absolute difference from baseline = {validation['max_abs_rvol60_difference']:.3g}"],
        ["OLS inference", "HC3 heteroskedasticity-robust standard errors; asymptotic normal p-values"],
        ["Outlier handling", "No deletion and no winsorization"],
    ]
    failure_method_rows = [
        ["Event set", "Frozen 193-event baseline"],
        ["Failure20=1", "At least one observed close in the next 20 CSI300 market days is below the breakout close"],
        ["Failure20=0", "All 20 future market-day closes are observed and none is below the breakout close"],
        ["Missing", "No observed failure, but one or more future closes are unavailable; no fill or interpolation"],
        ["Model", "Failure20 ~ ln_RVOL60 + Drawdown + Duration + BreakoutStrength"],
        ["Inference", "Logit maximum likelihood, model-based standard errors, asymptotic normal p-values"],
    ]

    event_cols = [
        "stock_code",
        "stock_name",
        "event_date",
        "Drawdown",
        "Duration",
        "bandwidth",
        "BreakoutStrength",
        "RelativeVolume",
        "ln_RVOL",
        "R1",
        "R3",
        "R5",
        "R20",
        "R60",
    ]
    atr_event_cols = [
        "stock_code",
        "stock_name",
        "event_date",
        "ATR20",
        "ATR20_to_resistance",
        "breakout_excess_over_ATR",
        "Drawdown",
        "Duration",
        "bandwidth",
        "BreakoutStrength",
        "RelativeVolume",
        "ln_RVOL",
        *RETURN_WINDOWS,
    ]
    volume_event_cols = [
        "stock_code",
        "stock_name",
        "event_date",
        "RVOL20",
        "ln_RVOL20",
        "RVOL60",
        "ln_RVOL60",
        "RVOL120",
        "ln_RVOL120",
        "R1",
        "R5",
        "R20",
        "R60",
    ]
    failure_event_cols = [
        "stock_code",
        "stock_name",
        "event_date",
        "breakout_price",
        "future20_min_close",
        "future20_available_closes",
        "Failure20",
        "Failure20_status",
        "ln_RVOL60",
        "Drawdown",
        "Duration",
        "BreakoutStrength",
    ]

    books = [
        {
            "filename": "threshold_robustness.xlsx",
            "sheets": [
                sheet("Summary", "Breakout threshold robustness", "Only the breakout threshold changes; the frozen 0.5% reconstruction matches all baseline event keys", ["Threshold", "Cutoff", "Events", "Stocks", "First event", "Last event", "ln_RVOL Q1", "ln_RVOL Q2"], records(threshold_summary), [14, 12, 11, 11, 15, 15, 15, 15], {1: "0.0%", 2: "0", 3: "0", 4: "yyyy-mm-dd", 5: "yyyy-mm-dd", 6: "0.0000", 7: "0.0000"}),
                sheet("Descriptive", "Descriptive statistics by threshold", "Each scenario retains its full reconstructed sample", list(threshold_desc.columns), records(threshold_desc), [14, 20, 10, 10, 15, 15, 15, 15, 15, 15, 15], {2: "0", 3: "0", 4: "0.0000", 5: "0.0000", 6: "0.0000", 7: "0.0000", 8: "0.0000", 9: "0.0000", 10: "0.0000"}, True),
                sheet("RVOL Groups", "RVOL tercile returns by threshold", "Tercile cutoffs are recalculated within each reconstructed threshold sample", list(threshold_groups.columns), records(threshold_groups), [14, 13, 11, 15, 12, 11, 15, 15, 16], {2: "0", 4: "0", 5: "0", 6: "0.00%", 7: "0.00%", 8: "0.00%"}, True),
                sheet("Pearson", "Pearson correlations by threshold", "p-values use the Fisher-z asymptotic approximation", list(threshold_corr.columns), records(threshold_corr), [14, 16, 11, 22, 20, 16], {2: "0", 3: "0.0000", 4: "0.0000", 5: "0"}),
                sheet("OLS", "OLS robustness by breakout threshold", "M1 and M2 for R1, R5, R20, and R60; HC3 standard errors", ["Threshold", "Dependent", "Term", "Coefficient", "HC3 SE", "z", "p-value", "N", "R2", "Model", "Specification"], records(threshold_ols, ["threshold", "dependent", "term", "coefficient", "standard_error", "test_statistic", "p_value", "n", "r_squared", "model", "specification"]), [14, 12, 20, 15, 15, 12, 14, 10, 12, 10, 54], {3: "0.000000", 4: "0.000000", 5: "0.000", 6: "0.0000", 7: "0", 8: "0.0000"}, True),
                sheet("Events", "Reconstructed threshold events", "One row per event per threshold scenario; no outlier deletion", list(threshold_events.columns), records(threshold_events), [14, 17, 17, 15, 14, 14, 14, 18, 14, 14, 13, 13, 13, 13, 13], {2: "yyyy-mm-dd", 3: "0.00%", 4: "0", 5: "0.00%", 6: "0.00%", 7: "0.0000", 8: "0.0000", 9: "0.00%", 10: "0.00%", 11: "0.00%", 12: "0.00%", 13: "0.00%"}, True),
                sheet("Method", "Threshold robustness method", "The source CSV remains unchanged", ["Item", "Definition"], clean_json(threshold_method_rows), [28, 96]),
            ],
        },
        {
            "filename": "atr_robustness.xlsx",
            "sheets": [
                sheet("Summary", "ATR20 breakout robustness", "Alternative event rule: adjusted close > sideways resistance + ATR20", ["Metric", "Value"], [["Events", int(len(atr_analysis))], ["Stocks", int(atr_analysis["stock_code"].nunique())], ["First event", pd.to_datetime(atr_analysis["event_date"]).min()], ["Last event", pd.to_datetime(atr_analysis["event_date"]).max()], ["ln_RVOL Q1", atr_cutoffs[0]], ["ln_RVOL Q2", atr_cutoffs[1]]], [30, 28], cell_formats=[("B7:B8", "0"), ("B9:B10", "yyyy-mm-dd"), ("B11:B12", "0.0000")]),
                sheet("Descriptive", "ATR20 sample descriptive statistics", "Returns and ratios use decimal units", list(atr_desc.columns), records(atr_desc), [20, 10, 10, 15, 15, 15, 15, 15, 15, 15], {1: "0", 2: "0", 3: "0.0000", 4: "0.0000", 5: "0.0000", 6: "0.0000", 7: "0.0000", 8: "0.0000", 9: "0.0000"}),
                sheet("RVOL Groups", "ATR20 sample RVOL terciles", "Groups are formed from ln_RVOL within the ATR20 sample", list(atr_groups.columns), records(atr_groups), [13, 11, 15, 12, 11, 15, 15, 16], {1: "0", 3: "0", 4: "0", 5: "0.00%", 6: "0.00%", 7: "0.00%"}),
                sheet("OLS", "ATR20 sample OLS results", "M1 and M2 for R1, R5, R20, and R60; HC3 standard errors", ["Dependent", "Term", "Coefficient", "HC3 SE", "z", "p-value", "N", "R2", "Model", "Specification"], records(atr_ols, ["dependent", "term", "coefficient", "standard_error", "test_statistic", "p_value", "n", "r_squared", "model", "specification"]), [12, 20, 15, 15, 12, 14, 10, 12, 10, 54], {2: "0.000000", 3: "0.000000", 4: "0.000", 5: "0.0000", 6: "0", 7: "0.0000"}, True),
                sheet("Events", "ATR20 reconstructed events", "One row per ATR20 event; no outlier deletion", atr_event_cols, records(atr_analysis, atr_event_cols), [16, 17, 15, 14, 20, 24, 14, 12, 14, 18, 14, 14, 13, 13, 13, 13, 13], {2: "yyyy-mm-dd", 3: "0.0000", 4: "0.00%", 5: "0.0000", 6: "0.00%", 7: "0", 8: "0.00%", 9: "0.00%", 10: "0.0000", 11: "0.0000", 12: "0.00%", 13: "0.00%", 14: "0.00%", 15: "0.00%", 16: "0.00%"}, True),
                sheet("Method", "ATR20 method", "ATR20 is based only on observations strictly before each event", ["Item", "Definition"], clean_json(atr_method_rows), [28, 104]),
            ],
        },
        {
            "filename": "volume_window_robustness.xlsx",
            "sheets": [
                sheet("Summary", "Volume-window robustness", "The 193 baseline events remain fixed; only the RVOL lookback changes", list(volume_summary.columns), records(volume_summary), [18, 12, 14, 15, 16, 16], {1: "0", 2: "0", 3: "0", 4: "0.0000", 5: "0.0000"}),
                sheet("OLS", "RVOL20, RVOL60, and RVOL120 coefficients", "M1 and M2 for R1, R5, R20, and R60; HC3 standard errors", ["Volume window", "Dependent", "Term", "Coefficient", "HC3 SE", "z", "p-value", "N", "R2", "Model", "Specification"], records(volume_ols, ["volume_window", "dependent", "term", "coefficient", "standard_error", "test_statistic", "p_value", "n", "r_squared", "model", "specification"]), [17, 12, 20, 15, 15, 12, 14, 10, 12, 10, 54], {3: "0.000000", 4: "0.000000", 5: "0.000", 6: "0.0000", 7: "0", 8: "0.0000"}, True),
                sheet("Event Measures", "Event-level volume measures", "Each frozen event remains one row; missing lookbacks remain blank", volume_event_cols, records(augmented, volume_event_cols), [16, 17, 15, 14, 15, 14, 15, 14, 15, 13, 13, 13, 13], {2: "yyyy-mm-dd", 3: "0.0000", 4: "0.0000", 5: "0.0000", 6: "0.0000", 7: "0.0000", 8: "0.0000", 9: "0.00%", 10: "0.00%", 11: "0.00%", 12: "0.00%"}, True),
                sheet("Method", "Volume-window method", "No baseline event, return, or control variable is changed", ["Item", "Definition"], clean_json(volume_method_rows), [28, 104]),
            ],
        },
        {
            "filename": "breakout_failure_analysis.xlsx",
            "sheets": [
                sheet("Summary", "Breakout failure analysis", "Failure20 uses the next 20 CSI300 market days without fill or interpolation", ["Metric", "Value"], clean_json(failure_summary_rows), [34, 24], cell_formats=[("B7:B11", "0"), ("B12", "0.00%"), ("B13", "0"), ("B15", "0"), ("B16", "0.0000")]),
                sheet("Logit", "Failure20 logistic regression", "Model-based standard errors; controls are Drawdown, Duration, and BreakoutStrength", list(logit.columns), records(logit), [22, 16, 17, 14, 14, 16, 10, 22], {1: "0.000000", 2: "0.000000", 3: "0.000", 4: "0.0000", 5: "0.0000", 6: "0", 7: "0.0000"}),
                sheet("Events", "Event-level Failure20 outcomes", "Unknown non-failures remain blank when future closes are missing", failure_event_cols, records(failure_work, failure_event_cols), [16, 17, 15, 16, 22, 24, 14, 46, 15, 14, 12, 20], {2: "yyyy-mm-dd", 3: "0.0000", 4: "0.0000", 5: "0", 6: "0", 8: "0.0000", 9: "0.00%", 10: "0", 11: "0.00%"}, True),
                sheet("Method", "Failure20 method", "A missing future close is never replaced by a non-trading-day or prior price", ["Item", "Definition"], clean_json(failure_method_rows), [28, 108]),
            ],
        },
        {
            "filename": "robustness_summary.xlsx",
            "sheets": [
                sheet("Core Results", "All robustness model core results", "The reported coefficient is always the coefficient on the relevant ln_RVOL measure", ["Analysis", "Variant", "Outcome", "Model", "N", "RVOL coefficient", "SE", "p-value", "R2", "R2 type", "Odds ratio", "Sig."], records(core, ["analysis", "variant", "outcome", "model", "n", "rvol_coefficient", "standard_error", "p_value", "r_squared", "r2_type", "odds_ratio", "significance"]), [21, 30, 13, 12, 10, 18, 16, 14, 13, 22, 16, 10], {4: "0", 5: "0.000000", 6: "0.000000", 7: "0.0000", 8: "0.0000", 10: "0.0000"}, True),
                sheet("Table5", "Table 5. Robustness checks", "Primary controlled R20 specifications plus the Failure20 logistic model; *, **, *** denote 10%, 5%, and 1%", ["Check", "Variant", "Outcome", "Model", "N", "ln_RVOL coefficient", "SE", "p-value", "R2 / pseudo R2", "R2 type", "Odds ratio"], records(table5, ["analysis", "variant", "outcome", "model", "n", "coefficient_with_stars", "standard_error", "p_value", "r_squared", "r2_type", "odds_ratio"]), [22, 31, 13, 12, 10, 22, 16, 14, 17, 22, 16], {4: "0", 6: "0.000000", 7: "0.0000", 8: "0.0000", 10: "0.0000"}),
                sheet("Validation", "Reproducibility validation", "The frozen baseline file is read-only and hash-checked before and after the run", ["Item", "Value"], [["Baseline filename", source_info["filename"]], ["Baseline SHA-256", source_info["sha256"]], ["Baseline events", source_info["events"]], ["Baseline stocks", source_info["stocks"]], ["0.5% reconstruction events", validation["reconstructed_event_count"]], ["Event keys match", "PASS" if validation["event_key_match"] else "FAIL"], ["RVOL60 max absolute difference", validation["max_abs_rvol60_difference"]], ["Outliers", "Retained; no winsorization"]], [34, 78], cell_formats=[("B9:B11", "0"), ("B13", "0.00E+00")]),
                sheet("Method", "Robustness design", "Each test changes only its named dimension", ["Analysis", "Changed dimension", "Held fixed"], [["Threshold sensitivity", "Breakout cutoff: 0.5%, 1%, 2%", "Universe, distress, consolidation, data quality, cooldown, RVOL60, returns"], ["ATR20", "Close > resistance + ATR20", "Universe, distress, consolidation, data quality, cooldown, RVOL60, returns"], ["Volume window", "RVOL lookback: 20, 60, 120 normal trading days", "Frozen 193 events, controls, returns"], ["Failure20", "Binary downside outcome over next 20 market days", "Frozen 193 events and baseline RVOL60"]], [22, 48, 78]),
            ],
        },
    ]

    payload = clean_json({"source": source_info, "books": books})
    findings = {
        "source": source_info,
        "validation": validation,
        "threshold_event_counts": {
            row["threshold"]: row["events"] for row in threshold_summary_rows
        },
        "atr_event_count": int(len(atr_analysis)),
        "failure_available": int(len(failure_valid)),
        "failure_rate": float(failure_valid.mean()),
        "table5": clean_json(table5.to_dict(orient="records")),
    }
    return payload, findings


ARTIFACT_BUILDER = r'''import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [payloadPath, outputDir, previewDir] = process.argv.slice(2);
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const FONT = "Arial";
const NAVY = "#1F4E78";
const TEXT = "#172B4D";
const LINE = "#D9E2F3";

function colLetter(index) {
  let n = index + 1;
  let result = "";
  while (n > 0) {
    n -= 1;
    result = String.fromCharCode(65 + (n % 26)) + result;
    n = Math.floor(n / 26);
  }
  return result;
}

function excelValue(value) {
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return new Date(value + "T00:00:00");
  }
  return value;
}

function safeName(value) {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "_");
}

function buildSheet(workbook, spec) {
  const ws = workbook.worksheets.add(spec.name);
  ws.showGridLines = false;
  const lastCol = colLetter(Math.max(spec.headers.length - 1, 1));
  ws.getRange("A2").values = [[spec.title]];
  ws.getRange(`A2:${lastCol}2`).format = {
    font: { name: FONT, size: 15, bold: true, color: TEXT },
    borders: { bottom: { style: "thin", color: NAVY } },
    verticalAlignment: "center",
    rowHeight: 25,
  };
  ws.getRange("A3").values = [[spec.subtitle]];
  ws.getRange(`A3:${lastCol}3`).format = {
    font: { name: FONT, size: 10, italic: true, color: "#4B5563" },
    verticalAlignment: "center",
    rowHeight: 22,
  };
  const convertedRows = spec.rows.map(row => row.map(excelValue));
  const matrix = [spec.headers, ...convertedRows];
  const endRow = 5 + matrix.length;
  ws.getRange(`A6:${lastCol}${endRow}`).values = matrix;
  ws.getRange(`A6:${lastCol}6`).format = {
    fill: NAVY,
    font: { name: FONT, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { insideVertical: { style: "thin", color: "#FFFFFF" } },
    rowHeight: 25,
  };
  if (convertedRows.length) {
    ws.getRange(`A7:${lastCol}${endRow}`).format = {
      font: { name: FONT, size: 10, color: TEXT },
      verticalAlignment: "center",
      borders: {
        insideHorizontal: { style: "thin", color: LINE },
        bottom: { style: "thin", color: NAVY },
      },
      rowHeight: 21,
    };
  }
  Object.entries(spec.formats ?? {}).forEach(([indexText, format]) => {
    const index = Number(indexText);
    if (convertedRows.length) {
      ws.getRange(`${colLetter(index)}7:${colLetter(index)}${endRow}`).format.numberFormat = format;
    }
  });
  for (const item of spec.cell_formats ?? []) {
    ws.getRange(item.range).format.numberFormat = item.format;
  }
  spec.widths.forEach((width, index) => {
    ws.getRange(`${colLetter(index)}1:${colLetter(index)}${Math.max(endRow, 7)}`).format.columnWidth = width;
  });
  if (spec.freeze) ws.freezePanes.freezeRows(6);
  const previewEnd = Math.min(endRow, Math.max(spec.preview_rows, 12));
  return { ws, previewRange: `A1:${lastCol}${previewEnd}` };
}

for (const book of payload.books) {
  const workbook = Workbook.create();
  const previews = [];
  for (const spec of book.sheets) {
    previews.push({ spec, ...buildSheet(workbook, spec) });
  }
  workbook.recalculate();
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 200 },
    summary: book.filename + " formula error scan",
  });
  console.log(`ERROR_SCAN=${book.filename}=${errors.ndjson}`);
  for (const item of previews) {
    const image = await workbook.render({
      sheetName: item.spec.name,
      range: item.previewRange,
      scale: 1,
      format: "png",
    });
    const previewName = `${path.parse(book.filename).name}__${safeName(item.spec.name)}.png`;
    await fs.writeFile(path.join(previewDir, previewName), new Uint8Array(await image.arrayBuffer()));
  }
  const outputPath = path.join(outputDir, book.filename);
  const blob = await SpreadsheetFile.exportXlsx(workbook);
  await blob.save(outputPath);
  const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  const first = previews[0];
  const inspection = await reopened.inspect({
    kind: "table",
    range: `${first.spec.name}!${first.previewRange}`,
    include: "values,formulas",
    tableMaxRows: 12,
    tableMaxCols: 12,
    maxChars: 5000,
  });
  console.log(`SAVED_INSPECT=${book.filename}=${inspection.ndjson}`);
}
console.log(`WORKBOOKS_CREATED=${payload.books.length}`);
'''


def ensure_junction(link: Path, target: Path) -> None:
    if link.exists():
        return
    if not target.exists():
        raise FileNotFoundError(f"Artifact-tool node_modules not found: {target}")
    link.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Could not create node_modules junction: {completed.stdout}\n{completed.stderr}"
        )


def cleanup_runtime() -> None:
    resolved = RUNTIME_DIR.resolve()
    expected_parent = (PROJECT_ROOT / ".runtime").resolve()
    if resolved.parent != expected_parent:
        raise RuntimeError(f"Refusing to clean unexpected runtime path: {resolved}")
    junction = RUNTIME_DIR / "node_modules"
    if junction.exists():
        os.rmdir(junction)
    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)


def run_pipeline(
    input_path: Path,
    output_dir: Path,
    keep_runtime: bool = False,
) -> dict[str, Any]:
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_hash_before = sha256_file(input_path)
    baseline = pd.read_csv(
        input_path,
        dtype={"stock_code": "string", "stock_name": "string"},
        low_memory=False,
    )
    required = {
        "stock_code",
        "stock_name",
        "event_date",
        "breakout_price",
        "Drawdown",
        "Duration",
        "bandwidth",
        "BreakoutStrength",
        "RelativeVolume",
        "_calendar_index",
        *RETURN_WINDOWS,
    }
    missing = sorted(required.difference(baseline.columns))
    if missing:
        raise ValueError(f"Baseline is missing required columns: {missing}")

    threshold_frames, atr_frame, metrics, validation = reconstruct_samples(baseline)
    payload, findings = build_results(
        baseline,
        input_path,
        source_hash_before,
        threshold_frames,
        atr_frame,
        metrics,
        validation,
    )

    cleanup_runtime()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload_path = RUNTIME_DIR / "robustness_results.json"
    builder_path = RUNTIME_DIR / "artifact_builder.mjs"
    preview_dir = RUNTIME_DIR / "previews"
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    builder_path.write_text(ARTIFACT_BUILDER, encoding="utf-8")

    node_path = Path(os.environ.get("CODEX_ARTIFACT_NODE", str(DEFAULT_NODE)))
    node_modules = Path(
        os.environ.get("CODEX_ARTIFACT_NODE_MODULES", str(DEFAULT_NODE_MODULES))
    )
    if not node_path.exists():
        raise FileNotFoundError(f"D-drive Node runtime not found: {node_path}")
    ensure_junction(RUNTIME_DIR / "node_modules", node_modules)
    temp_dir = RUNTIME_DIR / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["TEMP"] = str(temp_dir)
    environment["TMP"] = str(temp_dir)
    completed = subprocess.run(
        [
            str(node_path),
            str(builder_path),
            str(payload_path),
            str(output_dir),
            str(preview_dir),
        ],
        cwd=RUNTIME_DIR,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError("Artifact workbook generation failed")

    expected = [book["filename"] for book in payload["books"]]
    missing_outputs = [name for name in expected if not (output_dir / name).exists()]
    if missing_outputs:
        raise RuntimeError(f"Expected outputs missing: {missing_outputs}")
    source_hash_after = sha256_file(input_path)
    if source_hash_before != source_hash_after:
        raise RuntimeError("The baseline CSV hash changed during robustness analysis")

    findings["source_unchanged"] = True
    findings["outputs"] = {
        name: sha256_file(output_dir / name) for name in expected
    }
    findings["preview_dir"] = str(preview_dir)
    print(json.dumps(clean_json(findings), ensure_ascii=False, indent=2))
    if not keep_runtime:
        cleanup_runtime()
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--keep-runtime", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(args.input, args.output_dir, keep_runtime=args.keep_runtime)
