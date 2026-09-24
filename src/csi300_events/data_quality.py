from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from .acquisition import BaoStockSession, load_processed_stock
from .adaptive_pipeline import (
    _build_context,
    _evaluate_after_recent_filters,
    _load_name_history,
    _recent_120_metrics,
)
from .methodology import MethodologyConfig, load_methodology_config


COMPARISON_COLUMNS = [
    "SampleType",
    "stock_code",
    "date",
    "ComparisonReason",
    "TencentCloseQFQ",
    "BaoStockCloseQFQ",
    "BaoStockCloseScaled",
    "TencentHighQFQ",
    "BaoStockHighScaled",
    "TencentLowQFQ",
    "BaoStockLowScaled",
    "TencentVolumeShares",
    "BaoStockVolumeRaw",
    "TencentReturn",
    "BaoStockReturn",
    "ReturnDifference",
    "DirectionAgreement",
    "CloseAbsolutePctDifference",
    "HighAbsolutePctDifference",
    "LowAbsolutePctDifference",
]


def _safe_code(stock_code: str) -> str:
    return stock_code.replace(".", "_")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _normalise_baostock(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.rename(columns={"date": "trade_date"}).copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise")
    for column in ("open", "high", "low", "close", "volume", "tradestatus", "isST"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    suspended = result["tradestatus"].eq(0)
    result.loc[suspended, ["open", "high", "low", "close", "volume"]] = np.nan
    return result.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def _download_validation_jobs(
    jobs: list[dict[str, object]], cache_dir: Path
) -> dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    pending: list[dict[str, object]] = []
    for job in jobs:
        key = str(job["key"])
        path = cache_dir / (
            f"{key}__{_safe_code(str(job['stock_code']))}__"
            f"{pd.Timestamp(job['start']):%Y%m%d}__{pd.Timestamp(job['end']):%Y%m%d}.csv"
        )
        paths[key] = path
        if not path.exists() or path.stat().st_size < 30:
            pending.append(job)
    if not pending:
        print(f"BaoStock validation cache complete: {len(jobs)} jobs", flush=True)
        return paths

    print(f"BaoStock validation downloads: {len(pending)}/{len(jobs)} jobs", flush=True)
    session = BaoStockSession()
    session.__enter__()
    try:
        for number, job in enumerate(pending, start=1):
            if number > 1 and (number - 1) % 75 == 0:
                session.__exit__(None, None, None)
                time.sleep(2)
                session.__enter__()
            last_error: Exception | None = None
            for attempt in range(4):
                try:
                    frame = session.stock_price_validation(
                        str(job["stock_code"]),
                        pd.Timestamp(job["start"]),
                        pd.Timestamp(job["end"]),
                    )
                    if frame.empty:
                        raise RuntimeError("BaoStock returned no validation rows")
                    _write_csv(frame, paths[str(job["key"])])
                    break
                except Exception as exc:  # BaoStock occasionally closes idle sockets.
                    last_error = exc
                    session.__exit__(None, None, None)
                    time.sleep(1.5 * (attempt + 1))
                    session.__enter__()
            else:
                raise RuntimeError(f"Validation job failed: {job}") from last_error
            if number % 20 == 0 or number == len(pending):
                print(f"  BaoStock validation: {number}/{len(pending)}", flush=True)
    finally:
        session.__exit__(None, None, None)
    return paths


def _active_members(membership: pd.DataFrame, date: pd.Timestamp) -> list[str]:
    active = membership["in_date"].le(date) & (
        membership["out_date"].isna() | membership["out_date"].gt(date)
    )
    return sorted(membership.loc[active, "stock_code"].dropna().astype(str).unique())


def _nearest_calendar_position(dates: pd.DatetimeIndex, date: str) -> int:
    target = pd.Timestamp(date)
    candidates = np.flatnonzero(dates <= target)
    if not len(candidates):
        raise ValueError(f"No market date on or before {target:%Y-%m-%d}")
    return int(candidates[-1])


def _random_validation_jobs(
    membership: pd.DataFrame,
    dates: pd.DatetimeIndex,
    config: MethodologyConfig,
) -> tuple[list[dict[str, object]], pd.DataFrame]:
    anchors = {
        "early": "2011-06-30",
        "middle": "2018-06-29",
        "recent": "2025-06-30",
    }
    rng = np.random.default_rng(config.cross_validation_random_seed)
    jobs: list[dict[str, object]] = []
    sample_rows: list[dict[str, object]] = []
    per_era = config.cross_validation_random_stock_count // len(anchors)
    remainder = config.cross_validation_random_stock_count % len(anchors)
    for era_index, (era, anchor_text) in enumerate(anchors.items()):
        position = _nearest_calendar_position(dates, anchor_text)
        anchor = dates[position]
        codes = _active_members(membership, anchor)
        count = per_era + int(era_index < remainder)
        selected = sorted(rng.choice(codes, size=count, replace=False).tolist())
        start = dates[max(0, position - 20)]
        end = dates[min(len(dates) - 1, position + 20)]
        for stock_code in selected:
            key = f"random_{era}_{stock_code}"
            jobs.append(
                {
                    "key": key,
                    "stock_code": stock_code,
                    "start": start,
                    "end": end,
                }
            )
            sample_rows.append(
                {
                    "Era": era,
                    "AnchorDate": anchor,
                    "stock_code": stock_code,
                    "WindowStart": start,
                    "WindowEnd": end,
                }
            )
    return jobs, pd.DataFrame(sample_rows)


def _event_validation_jobs(
    events: pd.DataFrame, dates: pd.DatetimeIndex
) -> tuple[list[dict[str, object]], dict[str, str]]:
    date_to_position = {date: position for position, date in enumerate(dates)}
    jobs: list[dict[str, object]] = []
    key_by_code: dict[str, str] = {}
    for stock_code, group in events.groupby("stock_code", sort=True):
        positions = [date_to_position[pd.Timestamp(value)] for value in group["event_date"]]
        start = dates[max(0, min(positions) - 504)]
        end = dates[min(len(dates) - 1, max(positions) + 5)]
        key = f"events_{stock_code}"
        jobs.append(
            {"key": key, "stock_code": stock_code, "start": start, "end": end}
        )
        key_by_code[str(stock_code)] = key
    return jobs, key_by_code


def _merge_sources(tencent: pd.DataFrame, bao: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    left = tencent[
        ["trade_date", "adjusted_close", "adjusted_high", "adjusted_low", "volume", "tradestatus"]
    ].copy()
    left = left.rename(
        columns={
            "adjusted_close": "TencentCloseQFQ",
            "adjusted_high": "TencentHighQFQ",
            "adjusted_low": "TencentLowQFQ",
            "volume": "TencentVolumeShares",
            "tradestatus": "TencentTradeStatus",
        }
    )
    right = bao[
        ["trade_date", "close", "high", "low", "volume", "tradestatus"]
    ].rename(
        columns={
            "close": "BaoStockCloseQFQ",
            "high": "BaoStockHighQFQ",
            "low": "BaoStockLowQFQ",
            "volume": "BaoStockVolumeRaw",
            "tradestatus": "BaoStockTradeStatus",
        }
    )
    merged = left.merge(right, on="trade_date", how="inner", validate="one_to_one")
    valid_scale = (
        merged["TencentCloseQFQ"].gt(0)
        & merged["BaoStockCloseQFQ"].gt(0)
        & merged["TencentTradeStatus"].eq(1)
        & merged["BaoStockTradeStatus"].eq(1)
    )
    scale = float(
        np.median(
            merged.loc[valid_scale, "TencentCloseQFQ"]
            / merged.loc[valid_scale, "BaoStockCloseQFQ"]
        )
    ) if valid_scale.any() else np.nan
    for field in ("Close", "High", "Low"):
        merged[f"BaoStock{field}Scaled"] = merged[f"BaoStock{field}QFQ"] * scale
    merged = merged.sort_values("trade_date")
    merged["TencentReturn"] = merged["TencentCloseQFQ"].pct_change(fill_method=None)
    merged["BaoStockReturn"] = merged["BaoStockCloseQFQ"].pct_change(fill_method=None)
    return merged, scale


def _comparison_rows(
    merged: pd.DataFrame,
    reasons_by_date: dict[pd.Timestamp, list[str]],
    sample_type: str,
    stock_code: str,
) -> list[dict[str, object]]:
    indexed = merged.set_index("trade_date")
    rows: list[dict[str, object]] = []
    for date, reasons in sorted(reasons_by_date.items()):
        if date not in indexed.index:
            continue
        source = indexed.loc[date]
        tencent_close = float(source["TencentCloseQFQ"])
        bao_scaled = float(source["BaoStockCloseScaled"])
        tencent_return = float(source["TencentReturn"])
        bao_return = float(source["BaoStockReturn"])
        rows.append(
            {
                "SampleType": sample_type,
                "stock_code": stock_code,
                "date": date,
                "ComparisonReason": ";".join(sorted(set(reasons))),
                "TencentCloseQFQ": tencent_close,
                "BaoStockCloseQFQ": source["BaoStockCloseQFQ"],
                "BaoStockCloseScaled": bao_scaled,
                "TencentHighQFQ": source["TencentHighQFQ"],
                "BaoStockHighScaled": source["BaoStockHighScaled"],
                "TencentLowQFQ": source["TencentLowQFQ"],
                "BaoStockLowScaled": source["BaoStockLowScaled"],
                "TencentVolumeShares": source["TencentVolumeShares"],
                "BaoStockVolumeRaw": source["BaoStockVolumeRaw"],
                "TencentReturn": tencent_return,
                "BaoStockReturn": bao_return,
                "ReturnDifference": tencent_return - bao_return,
                "DirectionAgreement": (
                    int(np.sign(tencent_return) == np.sign(bao_return))
                    if np.isfinite(tencent_return) and np.isfinite(bao_return)
                    else np.nan
                ),
                "CloseAbsolutePctDifference": (
                    abs(tencent_close - bao_scaled) / abs(tencent_close)
                    if np.isfinite(tencent_close) and tencent_close != 0 and np.isfinite(bao_scaled)
                    else np.nan
                ),
                "HighAbsolutePctDifference": (
                    abs(float(source["TencentHighQFQ"]) - float(source["BaoStockHighScaled"]))
                    / abs(float(source["TencentHighQFQ"]))
                    if pd.notna(source["TencentHighQFQ"])
                    and float(source["TencentHighQFQ"]) != 0
                    and pd.notna(source["BaoStockHighScaled"])
                    else np.nan
                ),
                "LowAbsolutePctDifference": (
                    abs(float(source["TencentLowQFQ"]) - float(source["BaoStockLowScaled"]))
                    / abs(float(source["TencentLowQFQ"]))
                    if pd.notna(source["TencentLowQFQ"])
                    and float(source["TencentLowQFQ"]) != 0
                    and pd.notna(source["BaoStockLowScaled"])
                    else np.nan
                ),
            }
        )
    return rows


def _classify_with_baostock(
    stock_code: str,
    bao: pd.DataFrame,
    event: pd.Series,
    calendar: pd.DataFrame,
    membership: pd.DataFrame,
    histories: dict[str, tuple[np.ndarray, np.ndarray]],
    config: MethodologyConfig,
) -> dict[str, object]:
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
    matches = np.flatnonzero(context.dates == pd.Timestamp(event["event_date"]))
    if not len(matches):
        return {"BaoStockSelected": 0, "BaoStockDecision": "event_date_not_in_calendar"}
    position = int(matches[0])
    if not context.member_t_minus_1[position]:
        return {"BaoStockSelected": 0, "BaoStockDecision": "membership_mismatch"}
    recent = _recent_120_metrics(context, position, config)
    if not recent["passes"]:
        reason = (
            "recent_120_long_suspension"
            if recent["long_suspension"]
            else "recent_120_insufficient_valid_price"
        )
        return {"BaoStockSelected": 0, "BaoStockDecision": reason}
    specification = config.specifications["baseline"]
    if not np.isfinite(recent["bandwidth"]) or recent["bandwidth"] > specification.bandwidth_threshold:
        return {"BaoStockSelected": 0, "BaoStockDecision": "recent_120_bandwidth_failed"}
    if not np.isfinite(recent["trend120"]) or abs(recent["trend120"]) > config.trend120_threshold:
        return {"BaoStockSelected": 0, "BaoStockDecision": "recent_120_trend_failed"}
    record, outcome, _ = _evaluate_after_recent_filters(
        context, position, specification, config
    )
    if record is None:
        return {"BaoStockSelected": 0, "BaoStockDecision": outcome}
    return {
        "BaoStockSelected": 1,
        "BaoStockDecision": "selected_before_cooldown",
        "BaoConsolidationDuration": record["ConsolidationDuration"],
        "BaoBandwidth": record["Bandwidth"],
        "BaoTrend120": record["Trend120"],
        "BaoBreakoutStrength": record["BreakoutStrength"],
        "BaoDistressDrawdown": record["DistressDrawdown"],
        "BaoResistance": record["Resistance"],
        "BaoPeakPrice": record["PeakPrice"],
    }


def _corporate_action_rows(
    events: pd.DataFrame,
    stock_frames: dict[str, pd.DataFrame],
    dates: pd.DatetimeIndex,
    classifications: pd.DataFrame,
) -> pd.DataFrame:
    position_by_date = {date: index for index, date in enumerate(dates)}
    classification_lookup = classifications.set_index(["stock_code", "event_date"])
    rows: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        stock_code = str(event.stock_code)
        daily = stock_frames[stock_code].sort_values("trade_date").copy()
        daily["RawReturn"] = daily["close"].pct_change(fill_method=None)
        daily["QFQReturn"] = daily["adjusted_close"].pct_change(fill_method=None)
        daily["RawVsQFQReturnGap"] = (daily["RawReturn"] - daily["QFQReturn"]).abs()
        critical = {
            pd.Timestamp(event.event_date): "event_date",
            pd.Timestamp(event.ConsolidationStart): "consolidation_start",
            pd.Timestamp(event.ResistanceDate): "resistance_date",
            pd.Timestamp(event.PeakDate): "peak_date",
        }
        positions: set[int] = set()
        for date in critical:
            center = position_by_date[date]
            positions.update(range(max(0, center - 1), min(len(dates), center + 2)))
        event_position = position_by_date[pd.Timestamp(event.event_date)]
        positions.update(range(max(0, event_position - 5), min(len(dates), event_position + 6)))
        selected_dates = {dates[index] for index in positions}
        flagged = daily[
            daily["trade_date"].isin(selected_dates)
            & daily["RawVsQFQReturnGap"].gt(0.02)
        ]
        bao_selected = int(
            classification_lookup.loc[(stock_code, pd.Timestamp(event.event_date)), "BaoStockSelected"]
        )
        for source in flagged.itertuples(index=False):
            reasons = [name for date, name in critical.items() if date == source.trade_date]
            if abs(position_by_date[source.trade_date] - event_position) <= 5:
                reasons.append("event_plus_minus_5")
            rows.append(
                {
                    "stock_code": stock_code,
                    "event_date": event.event_date,
                    "adjustment_date": source.trade_date,
                    "CriticalWindow": ";".join(sorted(set(reasons))),
                    "RawReturn": source.RawReturn,
                    "QFQReturn": source.QFQReturn,
                    "RawVsQFQReturnGap": source.RawVsQFQReturnGap,
                    "BaoStockEventClassificationAgrees": bao_selected,
                    # A corporate action is not a fake event when two independent
                    # qfq series still agree on the event classification.
                    "PotentialAdjustmentDrivenEventDisagreement": int(
                        bao_selected == 0 and pd.Timestamp(source.trade_date) == pd.Timestamp(event.event_date)
                    ),
                }
            )
    columns = [
        "stock_code",
        "event_date",
        "adjustment_date",
        "CriticalWindow",
        "RawReturn",
        "QFQReturn",
        "RawVsQFQReturnGap",
        "BaoStockEventClassificationAgrees",
        "PotentialAdjustmentDrivenEventDisagreement",
    ]
    return pd.DataFrame(rows, columns=columns)


def _quantile(series: pd.Series, value: float) -> float | None:
    valid = pd.to_numeric(series, errors="coerce").dropna()
    return float(valid.quantile(value)) if len(valid) else None


def _markdown_table(frame: pd.DataFrame) -> str:
    """Render a compact Markdown table without an optional tabulate dependency."""
    if frame.empty:
        return "（无记录）"
    display = frame.copy()
    for column in display.columns:
        display[column] = display[column].map(
            lambda value: ""
            if pd.isna(value)
            else (
                f"{value:%Y-%m-%d}"
                if isinstance(value, (pd.Timestamp, np.datetime64))
                else str(value)
            )
        )
        display[column] = display[column].str.replace("|", "\\|", regex=False)
    header = "| " + " | ".join(map(str, display.columns)) + " |"
    separator = "| " + " | ".join(["---"] * len(display.columns)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in display.to_numpy(dtype=str)]
    return "\n".join([header, separator, *body])


def _coverage_table(
    config: MethodologyConfig,
    config_path: Path,
    stock_frames: dict[str, pd.DataFrame],
    comparisons: pd.DataFrame,
) -> pd.DataFrame:
    raw_dir = config.resolve(config.raw_dir, config_path)
    all_stock = pd.concat(
        [
            frame[["trade_date", "adjusted_close", "tradestatus", "isST"]]
            for frame in stock_frames.values()
        ],
        ignore_index=True,
    )
    calendar = pd.read_csv(config.resolve(config.calendar_path, config_path))
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"])
    snapshot_files = sorted((raw_dir / "baostock" / "hs300_snapshots").glob("*.csv"))
    industry_files = sorted((raw_dir / "baostock" / "industry").glob("*.csv"))
    industry_dates: list[pd.Timestamp] = []
    for path in industry_files:
        try:
            industry_dates.append(pd.Timestamp(path.stem.split("__")[-1]))
        except Exception:
            pass
    rows = [
        {
            "DataSource": "Tencent qfq stock prices",
            "EarliestDate": all_stock["trade_date"].min(),
            "LatestDate": all_stock["trade_date"].max(),
            "RecordCount": len(all_stock),
            "MissingCount": int(all_stock["adjusted_close"].isna().sum()),
            "MissingRate": float(all_stock["adjusted_close"].isna().mean()),
            "Role": "event price source",
        },
        {
            "DataSource": "BaoStock trade status/isST",
            "EarliestDate": all_stock["trade_date"].min(),
            "LatestDate": all_stock["trade_date"].max(),
            "RecordCount": len(all_stock),
            "MissingCount": int(all_stock[["tradestatus", "isST"]].isna().any(axis=1).sum()),
            "MissingRate": float(all_stock[["tradestatus", "isST"]].isna().any(axis=1).mean()),
            "Role": "status and ST screen",
        },
        {
            "DataSource": "CSI300 market calendar/index",
            "EarliestDate": calendar["trade_date"].min(),
            "LatestDate": calendar["trade_date"].max(),
            "RecordCount": len(calendar),
            "MissingCount": int(calendar["trade_date"].isna().sum()),
            "MissingRate": float(calendar["trade_date"].isna().mean()),
            "Role": "unified A-share market-day calendar",
        },
        {
            "DataSource": "BaoStock historical CSI300 snapshots",
            "EarliestDate": pd.Timestamp(snapshot_files[0].stem) if snapshot_files else pd.NaT,
            "LatestDate": pd.Timestamp(snapshot_files[-1].stem) if snapshot_files else pd.NaT,
            "RecordCount": len(snapshot_files) * 300,
            "MissingCount": 0 if snapshot_files else 1,
            "MissingRate": 0.0 if snapshot_files else 1.0,
            "Role": "point-in-time membership and observed names",
        },
        {
            "DataSource": "BaoStock qfq validation sample",
            "EarliestDate": comparisons["date"].min(),
            "LatestDate": comparisons["date"].max(),
            "RecordCount": len(comparisons),
            "MissingCount": int(comparisons["BaoStockCloseQFQ"].isna().sum()),
            "MissingRate": float(comparisons["BaoStockCloseQFQ"].isna().mean()),
            "Role": "independent cross-validation only",
        },
        {
            "DataSource": "BaoStock industry point queries",
            "EarliestDate": min(industry_dates) if industry_dates else pd.NaT,
            "LatestDate": max(industry_dates) if industry_dates else pd.NaT,
            "RecordCount": len(industry_files),
            "MissingCount": max(0, len(all_stock) - len(industry_files)),
            "MissingRate": 1.0 - len(industry_files) / len(all_stock),
            "Role": "not used; no complete daily industry panel",
        },
    ]
    return pd.DataFrame(rows)


def _report_markdown(
    config: MethodologyConfig,
    events: pd.DataFrame,
    coverage: pd.DataFrame,
    comparisons: pd.DataFrame,
    classifications: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    screening_audit: dict[str, object],
    metrics: dict[str, object],
) -> str:
    disagreements = classifications[classifications["EventClassificationAgreement"].eq(0)]
    disagreement_reason_text = ", ".join(
        f"{reason}={count}"
        for reason, count in disagreements["BaoStockDecision"].value_counts().items()
    )
    top_price = comparisons.nlargest(10, "CloseAbsolutePctDifference")
    lines = [
        "# 数据质量报告",
        "",
        f"DataAsOfDate：**{config.sample_end}**。2026 年仅覆盖至该日，是不完整自然年。",
        "",
        "## 结论",
        "",
        str(metrics["ResearchReadinessConclusion"]),
        "",
        "本报告只审计事件形成当日及历史数据，不计算未来 5/20/60 日收益、CAR 或回归，也不依据任何未来表现修改阈值。",
        "",
        "## 数据源与覆盖",
        "",
        _markdown_table(coverage),
        "",
        "行业文件只是过去少量事件的点查询，不构成 2023–2026 的完整日度行业面板；行业没有参与筛选，行业缺失也没有删除事件。",
        "",
        "## 价格口径",
        "",
        "修改前的现有核心路径读取腾讯 `qfqday`，即前复权；修改后仍统一使用 qfq。PeakPrice、横盘高低点、Bandwidth、Trend120、Resistance、BreakoutStrength 和 DistressDrawdown 全部来自同一份腾讯 qfq 序列。未混用不复权价格。",
        "",
        f"股票记录总数 {screening_audit['TotalProcessedStockRecords']:,}；复权收盘价缺失 {screening_audit['MissingAdjustedCloseRecords']:,} 条（{metrics['TencentAdjustedCloseMissingRate']:.4%}）。停牌记录 {screening_audit['SuspensionRecords']:,} 条，停牌行仍有复权价格的记录为 {screening_audit['SuspensionRowsWithNonmissingAdjustedPrice']:,}。",
        "",
        "## ST 与停牌",
        "",
        f"isST 与可观察历史名称冲突 {screening_audit['STNameStatusConflictRecords']:,} 个股票日，涉及 {screening_audit['STNameStatusConflictStocks']:,} 只股票。双重 ST 规则在漏斗对应阶段删除 {screening_audit['BaselineCandidatesDeletedBySTRule']:,} 个候选股票日。名称只在历史沪深300成员区间内由 BaoStock 快照向前延续，区间外不把旧名称当作日度真值。",
        "",
        f"最近120日数据质量阶段共删除 {metrics['Recent120DataQualityExcluded']:,} 个候选股票日：有效价格比例不足95%的有 {screening_audit['BaselineRecent120LowValidRatioExcluded']:,} 个，连续停牌超过5个市场日的有 {screening_audit['BaselineRecent120LongSuspensionExcluded']:,} 个（两类可重叠）。Peak窗口另有 {screening_audit['BaselinePeakWindowLongSuspensionExcluded']:,} 个因长期停牌、{screening_audit['BaselinePeakWindowLowValidRatioExcluded']:,} 个因有效价格不足、{screening_audit['BaselinePeakWindowBeforeCalendarOrNoPriceExcluded']:,} 个因超出交易日历或无有效Peak价格而被排除。程序没有前向填价、没有插值、没有把停牌成交量填成0，也没有跳过停牌日重新凑满120日。",
        "",
        "## 腾讯与 BaoStock 交叉验证",
        "",
        f"比较记录 {len(comparisons):,} 条，覆盖全部 {len(events):,} 个 baseline 事件的 t±5 市场日和关键日期，并另抽取 {metrics['RandomValidationStockCount']} 只股票（早/中/近期）。",
        "",
        f"成交量单位检查得到腾讯/BaoStock原始成交量中位比 {metrics['RawVolumeMedianRatio']:.6g}；对 BaoStock 成交量采用乘数 {metrics['BaoStockVolumeScaleApplied']:.6g} 后再比较。",
        "",
        f"统一成交量单位后，成交量绝对相对差中位数为 {metrics['VolumeAbsPctDiffMedian']:.6%}，95分位为 {metrics['VolumeAbsPctDiffP95']:.6%}。",
        "",
        f"缩放复权基准后，收盘价绝对相对差的中位数为 {metrics['CloseAbsPctDiffMedian']:.6%}，95分位为 {metrics['CloseAbsPctDiffP95']:.6%}；最高价95分位 {metrics['HighAbsPctDiffP95']:.6%}，最低价95分位 {metrics['LowAbsPctDiffP95']:.6%}。",
        "",
        f"日收益率差异绝对值中位数为 {metrics['ReturnAbsDiffMedian']:.6%}，95分位为 {metrics['ReturnAbsDiffP95']:.6%}；涨跌方向一致率为 {metrics['DirectionAgreementRate']:.4%}。",
        "",
        f"EventClassificationAgreement = {metrics['EventClassificationAgreement']:.4%}（{metrics['EventAgreementCount']}/{len(classifications)}）。这里比较的是同一个腾讯 baseline 候选事件，在 BaoStock qfq 数据上是否仍满足冻结的事件条件；没有使用 t+1 以后数据参与分类。",
        "",
        f"不一致原因汇总：{disagreement_reason_text or '无'}。",
        "",
        "### 事件分类不一致",
        "",
    ]
    if disagreements.empty:
        lines.append("无。全部 baseline 事件在 BaoStock qfq 上得到相同的事件成立判断。")
    else:
        lines.append(_markdown_table(disagreements))
    lines.extend(["", "### 最大价格差异案例", ""])
    lines.append(
        _markdown_table(
            top_price[
                ["SampleType", "stock_code", "date", "ComparisonReason", "CloseAbsolutePctDifference"]
            ]
        )
        if not top_price.empty
        else "无可比较记录。"
    )
    lines.extend(["", "## 公司行为异常检查", ""])
    if corporate_actions.empty:
        lines.append("在事件日±5及 Peak/横盘起点/阻力形成日附近，没有发现不复权收益与 qfq 收益相差超过2个百分点的记录。")
    else:
        potential = int(
            corporate_actions["PotentialAdjustmentDrivenEventDisagreement"].sum()
        )
        lines.append(
            f"检测到 {len(corporate_actions)} 个可能的除权调整点（审计阈值：不复权与 qfq 单日收益差超过2个百分点），其中 {potential} 个发生在事件日且 BaoStock 对该事件分类不一致，列为“可能由复权差异驱动、需复核”。仅凭两个免费源不能把它们断言为已确认假突破；本轮确认的假突破数为 0。"
        )
        lines.append("")
        lines.append(_markdown_table(corporate_actions.head(30)))
    lines.extend(
        [
            "",
            "## 已知限制",
            "",
            "- BaoStock 的沪深300名称来自离散成分快照，不是全市场逐日名称主表；因此名称冲突统计只覆盖可观察的历史成员区间，isST 仍是其他日期的主要 ST 状态字段。",
            "- 腾讯和 BaoStock 的前复权绝对基准可能不同，所以绝对价先用同股重叠日的中位比例对齐；事件判断同时比较无量纲的收益、Bandwidth、BreakoutStrength 和最终分类。",
            "- 2026 年截至 9 月 4 日，不能与完整自然年直接比较。",
            "- 行业数据不完整，本阶段明确不使用。",
            "",
            "## 可复核文件",
            "",
            "详细逐笔对照、事件分类不一致列表、公司行为审计、随机样本和数据源覆盖均与本报告保存在同一输出目录。",
        ]
    )
    return "\n".join(lines) + "\n"


def run_data_quality_audit(
    config: MethodologyConfig, config_path: str | Path
) -> dict[str, Path]:
    config_path = Path(config_path).resolve()
    output_dir = config.resolve(config.output_dir, config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "baseline_events.csv"
    if not events_path.exists():
        raise FileNotFoundError("Run adaptive screening before the data-quality audit")
    events = pd.read_csv(events_path, dtype={"stock_code": "string"})
    for column in ("event_date", "ConsolidationStart", "ConsolidationEnd", "PeakDate", "ResistanceDate"):
        events[column] = pd.to_datetime(events[column], errors="raise")
    membership = pd.read_csv(
        config.resolve(config.membership_path, config_path),
        dtype={"stock_code": "string"},
    )
    membership["in_date"] = pd.to_datetime(membership["in_date"], errors="raise")
    membership["out_date"] = pd.to_datetime(membership["out_date"], errors="coerce")
    calendar = pd.read_csv(config.resolve(config.calendar_path, config_path))
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="raise")
    calendar = calendar[calendar["trade_date"].le(config.sample_end)].sort_values("trade_date")
    dates = pd.DatetimeIndex(calendar["trade_date"].unique())
    histories = _load_name_history(
        config.resolve(config.raw_dir, config_path) / "baostock" / "hs300_snapshots"
    )
    stock_dir = config.resolve(config.processed_stock_dir, config_path)
    stock_frames = {
        stock_code: load_processed_stock(stock_dir / f"{_safe_code(stock_code)}.csv.gz")
        for stock_code in sorted(membership["stock_code"].dropna().astype(str).unique())
    }

    event_jobs, event_key_by_code = _event_validation_jobs(events, dates)
    random_jobs, random_sample = _random_validation_jobs(membership, dates, config)
    jobs = event_jobs + random_jobs
    validation_dir = config.resolve(config.raw_dir, config_path) / "baostock" / "price_validation"
    cache_paths = _download_validation_jobs(jobs, validation_dir)

    bao_cache = {
        key: _normalise_baostock(pd.read_csv(path, dtype={"code": "string"}))
        for key, path in cache_paths.items()
    }
    comparison_records: list[dict[str, object]] = []
    classification_records: list[dict[str, object]] = []
    position_by_date = {date: index for index, date in enumerate(dates)}

    for stock_code, group in events.groupby("stock_code", sort=True):
        key = event_key_by_code[str(stock_code)]
        bao = bao_cache[key]
        tencent = stock_frames[str(stock_code)]
        merged, price_scale = _merge_sources(tencent, bao)
        for _, event in group.iterrows():
            event_date = pd.Timestamp(event["event_date"])
            position = position_by_date[event_date]
            reasons: dict[pd.Timestamp, list[str]] = defaultdict(list)
            for offset in range(-5, 6):
                target = position + offset
                if 0 <= target < len(dates):
                    reasons[dates[target]].append(f"event_t{offset:+d}")
            for column, label in (
                ("PeakDate", "peak_date"),
                ("ConsolidationStart", "consolidation_start"),
                ("ResistanceDate", "resistance_date"),
                ("event_date", "event_date"),
            ):
                reasons[pd.Timestamp(event[column])].append(label)
            comparison_records.extend(
                _comparison_rows(merged, reasons, "baseline_event", str(stock_code))
            )
            decision = _classify_with_baostock(
                str(stock_code), bao, event, calendar, membership, histories, config
            )
            bao_selected = int(decision.get("BaoStockSelected", 0))
            classification_records.append(
                {
                    "stock_code": stock_code,
                    "stock_name": event["stock_name"],
                    "event_date": event_date,
                    "TencentSelected": 1,
                    **decision,
                    "EventClassificationAgreement": bao_selected,
                    "BaoToTencentPriceScale": price_scale,
                    "TencentConsolidationDuration": event["ConsolidationDuration"],
                    "TencentBandwidth": event["Bandwidth"],
                    "TencentTrend120": event["Trend120"],
                    "TencentBreakoutStrength": event["BreakoutStrength"],
                    "TencentDistressDrawdown": event["DistressDrawdown"],
                }
            )

    for job in random_jobs:
        stock_code = str(job["stock_code"])
        bao = bao_cache[str(job["key"])]
        tencent = stock_frames[stock_code]
        merged, _ = _merge_sources(tencent, bao)
        reasons = {
            pd.Timestamp(value): ["random_window"]
            for value in merged["trade_date"]
            if pd.Timestamp(job["start"]) <= pd.Timestamp(value) <= pd.Timestamp(job["end"])
        }
        comparison_records.extend(
            _comparison_rows(merged, reasons, "random_stock", stock_code)
        )

    comparisons = pd.DataFrame(comparison_records, columns=COMPARISON_COLUMNS)
    classifications = pd.DataFrame(classification_records)
    classifications["event_date"] = pd.to_datetime(classifications["event_date"])
    disagreements = classifications[
        classifications["EventClassificationAgreement"].eq(0)
    ].copy()
    validation_flags = classifications[
        [
            "stock_code",
            "event_date",
            "BaoStockSelected",
            "BaoStockDecision",
            "EventClassificationAgreement",
        ]
    ]
    validated_events = events.merge(
        validation_flags,
        on=["stock_code", "event_date"],
        how="left",
        validate="one_to_one",
    )
    validated_events["DataQualityStatus"] = np.where(
        validated_events["EventClassificationAgreement"].eq(1),
        "two_source_consensus",
        "review_required",
    )
    consensus_events = validated_events[
        validated_events["DataQualityStatus"].eq("two_source_consensus")
    ].copy()
    review_events = validated_events[
        validated_events["DataQualityStatus"].eq("review_required")
    ].copy()
    event_catalog_zh = validated_events[
        [
            "stock_code",
            "stock_name",
            "event_date",
            "ConsolidationStart",
            "ConsolidationDuration",
            "Bandwidth",
            "Trend120",
            "PeakDate",
            "DistressDrawdown",
            "Resistance",
            "BreakoutStrength",
            "DataQualityStatus",
            "BaoStockDecision",
        ]
    ].rename(
        columns={
            "stock_code": "股票代码",
            "stock_name": "股票名称",
            "event_date": "突破日期",
            "ConsolidationStart": "横盘开始日期",
            "ConsolidationDuration": "横盘市场交易日数",
            "Bandwidth": "横盘宽度",
            "Trend120": "120日等效趋势",
            "PeakDate": "前期高点日期",
            "DistressDrawdown": "困境回撤",
            "Resistance": "突破阻力位",
            "BreakoutStrength": "突破强度",
            "DataQualityStatus": "两数据源复核状态",
            "BaoStockDecision": "BaoStock复核结论",
        }
    )

    valid_volume = comparisons[
        comparisons["TencentVolumeShares"].gt(0) & comparisons["BaoStockVolumeRaw"].gt(0)
    ]
    raw_volume_ratio = float(
        np.median(valid_volume["TencentVolumeShares"] / valid_volume["BaoStockVolumeRaw"])
    ) if len(valid_volume) else np.nan
    candidate_scales = np.array([0.01, 1.0, 100.0])
    volume_scale = float(
        candidate_scales[np.argmin(np.abs(np.log(candidate_scales) - np.log(raw_volume_ratio)))]
    ) if np.isfinite(raw_volume_ratio) and raw_volume_ratio > 0 else np.nan
    comparisons["BaoStockVolumeShares"] = comparisons["BaoStockVolumeRaw"] * volume_scale
    comparisons["VolumeAbsolutePctDifference"] = (
        comparisons["TencentVolumeShares"] - comparisons["BaoStockVolumeShares"]
    ).abs() / comparisons["TencentVolumeShares"].abs()

    corporate_actions = _corporate_action_rows(
        events, stock_frames, dates, classifications
    )
    screening_audit = json.loads(
        (output_dir / "screening_audit.json").read_text(encoding="utf-8")
    )
    coverage = _coverage_table(
        config, config_path, stock_frames, comparisons
    )
    event_agreement = float(classifications["EventClassificationAgreement"].mean())
    potential_adjustment_count = int(
        corporate_actions["PotentialAdjustmentDrivenEventDisagreement"].sum()
    ) if len(corporate_actions) else 0
    confirmed_fake_count = 0
    direction_rate = float(pd.to_numeric(comparisons["DirectionAgreement"], errors="coerce").mean())
    readiness = (
        "腾讯 qfq 与 BaoStock qfq 的事件分类和局部价格结构高度一致，且未确认公司行为造成的假突破；在明确保留历史名称稀疏和行业面板不完整这两个限制的前提下，价格事件样本足以进入后续统计研究。"
        if event_agreement >= 0.95 and direction_rate >= 0.99 and confirmed_fake_count == 0
        else "交叉验证仍存在足以影响事件分类的差异；在逐项解决分类不一致或潜在假突破之前，不建议直接进入后续统计研究。"
    )
    metrics: dict[str, object] = {
        "DataAsOfDate": config.sample_end,
        "CurrentAdjustmentType": config.current_adjustment_type,
        "TargetAdjustmentType": config.target_adjustment_type,
        "TencentAdjustedCloseMissingRate": screening_audit["MissingAdjustedCloseRecords"]
        / screening_audit["TotalProcessedStockRecords"],
        "RandomValidationStockCount": int(random_sample["stock_code"].nunique()),
        "ComparisonRecordCount": len(comparisons),
        "RawVolumeMedianRatio": raw_volume_ratio,
        "BaoStockVolumeScaleApplied": volume_scale,
        "CloseAbsPctDiffMedian": _quantile(comparisons["CloseAbsolutePctDifference"], 0.5),
        "CloseAbsPctDiffP95": _quantile(comparisons["CloseAbsolutePctDifference"], 0.95),
        "HighAbsPctDiffP95": _quantile(comparisons["HighAbsolutePctDifference"], 0.95),
        "LowAbsPctDiffP95": _quantile(comparisons["LowAbsolutePctDifference"], 0.95),
        "VolumeAbsPctDiffMedian": _quantile(comparisons["VolumeAbsolutePctDifference"], 0.5),
        "VolumeAbsPctDiffP95": _quantile(comparisons["VolumeAbsolutePctDifference"], 0.95),
        "ReturnAbsDiffMedian": _quantile(comparisons["ReturnDifference"].abs(), 0.5),
        "ReturnAbsDiffP95": _quantile(comparisons["ReturnDifference"].abs(), 0.95),
        "DirectionAgreementRate": direction_rate,
        "EventClassificationAgreement": event_agreement,
        "EventAgreementCount": int(classifications["EventClassificationAgreement"].sum()),
        "EventDisagreementCount": len(disagreements),
        "CorporateActionAuditPointCount": len(corporate_actions),
        "PotentialAdjustmentDrivenEventDisagreementCount": potential_adjustment_count,
        "ConfirmedFakeBreakoutCount": confirmed_fake_count,
        "Recent120DataQualityExcluded": int(
            pd.read_csv(output_dir / "sample_selection_funnel.csv")
            .set_index("FilterStep")
            .loc["recent_120_data_quality", "ExcludedCount"]
        ),
        "ResearchReadinessConclusion": readiness,
    }

    paths = {
        "comparison": output_dir / "tencent_baostock_price_comparison.csv",
        "classification": output_dir / "event_classification_validation.csv",
        "disagreements": output_dir / "event_classification_disagreements.csv",
        "corporate_actions": output_dir / "corporate_action_audit.csv",
        "random_sample": output_dir / "random_validation_sample.csv",
        "coverage": output_dir / "data_source_coverage.csv",
        "metrics": output_dir / "data_quality_metrics.json",
        "report": output_dir / "data_quality_report.md",
        "validated_events": output_dir / "baseline_events_with_validation.csv",
        "consensus_events": output_dir / "crossvalidated_consensus_events.csv",
        "review_events": output_dir / "review_required_events.csv",
        "event_catalog_zh": output_dir / "baseline_event_catalog_zh.csv",
    }
    _write_csv(comparisons, paths["comparison"])
    _write_csv(classifications, paths["classification"])
    _write_csv(disagreements, paths["disagreements"])
    _write_csv(corporate_actions, paths["corporate_actions"])
    _write_csv(random_sample, paths["random_sample"])
    _write_csv(coverage, paths["coverage"])
    _write_csv(validated_events, paths["validated_events"])
    _write_csv(consensus_events, paths["consensus_events"])
    _write_csv(review_events, paths["review_events"])
    _write_csv(event_catalog_zh, paths["event_catalog_zh"])
    paths["metrics"].write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["report"].write_text(
        _report_markdown(
            config,
            events,
            coverage,
            comparisons,
            classifications,
            corporate_actions,
            screening_audit,
            metrics,
        ),
        encoding="utf-8",
    )
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-validate Tencent qfq event data against free BaoStock qfq data."
    )
    parser.add_argument("--config", default="config/methodology_config.json")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = load_methodology_config(config_path)
    paths = run_data_quality_audit(config, config_path)
    print("Saved data-quality outputs:")
    for label, path in paths.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
