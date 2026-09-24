from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .eastmoney_layer import effective_download_end, load_price_layer_config
from .methodology import load_methodology_config


def _close(a: pd.Series, b: pd.Series) -> pd.Series:
    left = pd.to_numeric(a, errors="coerce")
    right = pd.to_numeric(b, errors="coerce")
    return (left.isna() & right.isna()) | np.isclose(
        left.fillna(0.0), right.fillna(0.0), rtol=1e-10, atol=1e-10
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_missing_cache_hashes(
    price_config_path: str | Path = "config/eastmoney_price_audit.json",
) -> int:
    config_path = Path(price_config_path).resolve()
    layer = load_price_layer_config(config_path)
    raw_root = layer.resolve(layer.eastmoney_raw_dir, config_path)
    updated = 0
    for adjustment in layer.eastmoney_adjustments:
        for metadata_path in sorted((raw_root / adjustment).glob("*.metadata.json")):
            data_path = metadata_path.with_name(
                metadata_path.name.replace(".metadata.json", ".csv.gz")
            )
            if not data_path.exists():
                continue
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            digest = _sha256_file(data_path)
            if metadata.get("sha256") == digest:
                continue
            metadata["sha256"] = digest
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            updated += 1
    return updated


def run_formal_audit(
    price_config_path: str | Path = "config/eastmoney_price_audit.json",
    final_output_path: str | Path = "outputs/hfq_final/sample_hfq_final.csv",
) -> dict[str, object]:
    config_path = Path(price_config_path).resolve()
    project_root = config_path.parent.parent
    layer = load_price_layer_config(config_path)
    methodology_path = layer.resolve(layer.methodology_config, config_path)
    methodology = load_methodology_config(methodology_path)
    membership = pd.read_csv(
        methodology.resolve(methodology.membership_path, methodology_path),
        dtype={"stock_code": "string"},
    )
    membership["in_date"] = pd.to_datetime(membership["in_date"], errors="raise")
    membership["out_date"] = pd.to_datetime(membership["out_date"], errors="coerce")
    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    raw_root = layer.resolve(layer.eastmoney_raw_dir, config_path)
    processed_root = layer.resolve(layer.eastmoney_processed_dir, config_path) / "hfq"
    start = pd.Timestamp(layer.download_start)
    end = effective_download_end(layer.download_end)

    metrics: dict[str, object] = {
        "audit_status": "pass",
        "formal_price_source": "Eastmoney hfq",
        "configured_target_end": layer.download_end,
        "effective_download_end_on_audit_date": end.strftime("%Y-%m-%d"),
        "historical_constituent_stock_count": len(codes),
        "raw_missing_file_count": 0,
        "hfq_missing_file_count": 0,
        "metadata_missing_file_count": 0,
        "metadata_hash_missing_count": 0,
        "metadata_hash_mismatch_count": 0,
        "metadata_row_count_mismatch_count": 0,
        "processed_missing_file_count": 0,
        "processed_empty_file_count": 0,
        "processed_duplicate_date_count": 0,
        "processed_outside_required_dates_count": 0,
        "processed_nonpositive_hfq_close_count": 0,
        "suspended_rows_with_nonmissing_price_or_activity_count": 0,
        "raw_to_processed_value_mismatch_count": 0,
        "hfq_to_processed_value_mismatch_count": 0,
        "transport_counts": {},
    }

    transport_counts: dict[str, dict[str, int]] = {}
    for adjustment in layer.eastmoney_adjustments:
        counts: dict[str, int] = {}
        for metadata_path in sorted((raw_root / adjustment).glob("*.metadata.json")):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            transport = str(metadata.get("transport", "missing"))
            counts[transport] = counts.get(transport, 0) + 1
        transport_counts[adjustment] = counts
    metrics["transport_counts"] = transport_counts
    metrics["public_relay_cache_count"] = sum(
        count
        for counts in transport_counts.values()
        for transport, count in counts.items()
        if transport.startswith("public_relay_to_")
    )

    for stock_code in codes:
        safe = stock_code.replace(".", "_")
        raw_path = raw_root / "raw" / f"{safe}.csv.gz"
        hfq_path = raw_root / "hfq" / f"{safe}.csv.gz"
        processed_path = processed_root / f"{safe}.csv.gz"
        for label, path in (("raw", raw_path), ("hfq", hfq_path)):
            if not path.exists():
                metrics[f"{label}_missing_file_count"] = int(
                    metrics[f"{label}_missing_file_count"]
                ) + 1
            metadata_path = path.with_name(f"{safe}.metadata.json")
            if not metadata_path.exists():
                metrics["metadata_missing_file_count"] = int(
                    metrics["metadata_missing_file_count"]
                ) + 1
            elif path.exists():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                expected_hash = metadata.get("sha256")
                if not expected_hash:
                    metrics["metadata_hash_missing_count"] = int(
                        metrics["metadata_hash_missing_count"]
                    ) + 1
                elif str(expected_hash).lower() != _sha256_file(path).lower():
                    metrics["metadata_hash_mismatch_count"] = int(
                        metrics["metadata_hash_mismatch_count"]
                    ) + 1
                actual_row_count = sum(
                    len(chunk) for chunk in pd.read_csv(path, chunksize=100000)
                )
                if int(metadata.get("row_count", -1)) != actual_row_count:
                    metrics["metadata_row_count_mismatch_count"] = int(
                        metrics["metadata_row_count_mismatch_count"]
                    ) + 1
        if not processed_path.exists():
            metrics["processed_missing_file_count"] = int(
                metrics["processed_missing_file_count"]
            ) + 1
            continue
        processed = pd.read_csv(
            processed_path, dtype={"stock_code": "string"}, low_memory=False
        )
        if processed.empty:
            metrics["processed_empty_file_count"] = int(
                metrics["processed_empty_file_count"]
            ) + 1
            continue
        processed["trade_date"] = pd.to_datetime(processed["trade_date"], errors="raise")
        metrics["processed_duplicate_date_count"] = int(
            metrics["processed_duplicate_date_count"]
        ) + int(processed["trade_date"].duplicated().sum())
        metrics["processed_outside_required_dates_count"] = int(
            metrics["processed_outside_required_dates_count"]
        ) + int((~processed["trade_date"].between(start, end)).sum())
        normal = pd.to_numeric(processed["tradestatus"], errors="coerce").eq(1)
        adjusted_close = pd.to_numeric(processed["adjusted_close"], errors="coerce")
        metrics["processed_nonpositive_hfq_close_count"] = int(
            metrics["processed_nonpositive_hfq_close_count"]
        ) + int((normal & adjusted_close.le(0)).sum())
        suspended = pd.to_numeric(processed["tradestatus"], errors="coerce").eq(0)
        cleared_columns = [
            "open",
            "high",
            "low",
            "close",
            "adjusted_open",
            "adjusted_high",
            "adjusted_low",
            "adjusted_close",
            "volume",
            "amount",
            "turnover_rate",
            "pctChg",
        ]
        metrics["suspended_rows_with_nonmissing_price_or_activity_count"] = int(
            metrics["suspended_rows_with_nonmissing_price_or_activity_count"]
        ) + int(processed.loc[suspended, cleared_columns].notna().any(axis=1).sum())

        raw = pd.read_csv(raw_path, low_memory=False)
        hfq = pd.read_csv(hfq_path, low_memory=False)
        raw["trade_date"] = pd.to_datetime(raw["trade_date"], errors="raise")
        hfq["trade_date"] = pd.to_datetime(hfq["trade_date"], errors="raise")
        raw_columns = ["open", "high", "low", "close", "volume", "amount", "turnover_rate", "pctChg"]
        hfq_columns = ["open", "high", "low", "close"]
        raw_join = processed.merge(
            raw[["trade_date", *raw_columns]],
            on="trade_date",
            how="inner",
            suffixes=("_processed", "_source"),
        )
        for column in raw_columns:
            valid = ~pd.to_numeric(raw_join["tradestatus"], errors="coerce").eq(0)
            metrics["raw_to_processed_value_mismatch_count"] = int(
                metrics["raw_to_processed_value_mismatch_count"]
            ) + int((valid & ~_close(raw_join[f"{column}_processed"], raw_join[f"{column}_source"])).sum())
        hfq_join = processed.merge(
            hfq[["trade_date", *hfq_columns]],
            on="trade_date",
            how="inner",
            suffixes=("_processed", "_source"),
        )
        for column in hfq_columns:
            valid = ~pd.to_numeric(hfq_join["tradestatus"], errors="coerce").eq(0)
            metrics["hfq_to_processed_value_mismatch_count"] = int(
                metrics["hfq_to_processed_value_mismatch_count"]
            ) + int(
                (
                    valid
                    & ~_close(
                        hfq_join[f"adjusted_{column}"],
                        hfq_join[f"{column}_source"],
                    )
                ).sum()
            )

    snapshot_dir = project_root / "data" / "raw" / "baostock" / "hs300_snapshots"
    snapshot_files = sorted(snapshot_dir.glob("*.csv"))
    snapshot_bad_size = 0
    snapshot_interval_mismatch = 0
    for path in snapshot_files:
        snapshot_date = pd.Timestamp(path.stem)
        if not pd.Timestamp(methodology.sample_start_primary) <= snapshot_date <= end:
            continue
        snapshot = pd.read_csv(path, dtype={"code": "string"})
        snapshot_codes = set(snapshot["code"].dropna().astype(str))
        if len(snapshot_codes) != 300:
            snapshot_bad_size += 1
        active = membership["in_date"].le(snapshot_date) & (
            membership["out_date"].isna() | membership["out_date"].gt(snapshot_date)
        )
        interval_codes = set(membership.loc[active, "stock_code"].astype(str))
        if snapshot_codes != interval_codes:
            snapshot_interval_mismatch += 1
    metrics["membership_snapshots_checked"] = int(
        sum(
            pd.Timestamp(methodology.sample_start_primary) <= pd.Timestamp(path.stem) <= end
            for path in snapshot_files
        )
    )
    metrics["membership_snapshot_bad_member_count"] = snapshot_bad_size
    metrics["membership_snapshot_interval_mismatch_count"] = snapshot_interval_mismatch

    events_path = Path(final_output_path).resolve()
    with events_path.open("r", encoding="utf-8-sig", newline="") as handle:
        event_headers = next(csv.reader(handle))
    normalized_headers = [header.casefold() for header in event_headers]
    metrics["final_output_case_insensitive_duplicate_column_count"] = (
        len(normalized_headers) - len(set(normalized_headers))
    )
    events = pd.read_csv(events_path, dtype={"stock_code": "string"})
    event_dates = pd.to_datetime(events["event_date"], errors="raise")
    metrics["final_event_count"] = len(events)
    metrics["final_event_stock_count"] = int(events["stock_code"].nunique())
    metrics["final_event_outside_frozen_period_count"] = int(
        (~event_dates.between(methodology.sample_start_primary, methodology.sample_end)).sum()
    )
    metrics["final_event_duplicate_key_count"] = int(
        events.duplicated(["stock_code", "event_date"]).sum()
    )
    calendar_path = methodology.resolve(methodology.calendar_path, methodology_path)
    calendar = pd.read_csv(calendar_path)
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="raise")
    calendar = calendar.sort_values("trade_date").drop_duplicates("trade_date")
    calendar_metadata_path = calendar_path.with_name("trading_calendar.metadata.json")
    metrics["calendar_metadata_missing_count"] = int(not calendar_metadata_path.exists())
    metrics["calendar_hash_mismatch_count"] = 0
    if calendar_metadata_path.exists():
        calendar_metadata = json.loads(calendar_metadata_path.read_text(encoding="utf-8"))
        metrics["calendar_hash_mismatch_count"] = int(
            calendar_metadata.get("sha256") != _sha256_file(calendar_path)
        )

    positions = {
        pd.Timestamp(value): index for index, value in enumerate(calendar["trade_date"])
    }
    membership_mismatches = 0
    return_mismatches = 0
    relative_volume_mismatches = 0
    for stock_code, stock_events in events.groupby("stock_code", sort=False):
        daily = pd.read_csv(
            processed_root / f"{str(stock_code).replace('.', '_')}.csv.gz",
            dtype={"stock_code": "string"},
            low_memory=False,
        )
        daily["trade_date"] = pd.to_datetime(daily["trade_date"], errors="raise")
        daily = daily.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
        daily_by_date = daily.set_index("trade_date")
        stock_intervals = membership[membership["stock_code"].eq(str(stock_code))]
        for row in stock_events.itertuples(index=False):
            event_date = pd.Timestamp(row.event_date)
            event_calendar_position = positions[event_date]
            previous_market_date = pd.Timestamp(
                calendar["trade_date"].iloc[event_calendar_position - 1]
            )
            active = stock_intervals["in_date"].le(previous_market_date) & (
                stock_intervals["out_date"].isna()
                | stock_intervals["out_date"].gt(previous_market_date)
            )
            if not active.any():
                membership_mismatches += 1

            event_row = daily_by_date.loc[event_date]
            event_price = float(event_row["adjusted_close"])
            prior = daily.loc[daily["trade_date"].lt(event_date)].copy()
            prior_volume = pd.to_numeric(prior["volume"], errors="coerce")
            prior_status = pd.to_numeric(prior["tradestatus"], errors="coerce")
            eligible_volume = prior_volume.loc[prior_status.eq(1) & prior_volume.gt(0)]
            expected_relative_volume = float(event_row["volume"]) / float(
                eligible_volume.iloc[-60:].mean()
            )
            if not np.isclose(
                float(row.RelativeVolume),
                expected_relative_volume,
                rtol=1e-10,
                atol=1e-10,
            ):
                relative_volume_mismatches += 1

            for horizon in (3, 5, 20, 60):
                target_position = event_calendar_position + horizon
                stored_return = getattr(row, f"R{horizon}")
                stored_status = getattr(row, f"R{horizon}_status")
                if target_position >= len(calendar):
                    expected_return = np.nan
                    expected_status = "calendar_not_yet_available"
                else:
                    target_date = pd.Timestamp(calendar["trade_date"].iloc[target_position])
                    if target_date not in daily_by_date.index:
                        expected_return = np.nan
                        expected_status = "target_price_or_trade_missing"
                    else:
                        target_row = daily_by_date.loc[target_date]
                        target_price = pd.to_numeric(
                            pd.Series([target_row["adjusted_close"]]), errors="coerce"
                        ).iloc[0]
                        target_status = pd.to_numeric(
                            pd.Series([target_row["tradestatus"]]), errors="coerce"
                        ).iloc[0]
                        if not np.isfinite(target_price) or target_price <= 0 or target_status != 1:
                            expected_return = np.nan
                            expected_status = "target_price_or_trade_missing"
                        else:
                            expected_return = float(target_price / event_price - 1.0)
                            expected_status = "available"
                values_match = (
                    pd.isna(stored_return)
                    if pd.isna(expected_return)
                    else np.isclose(
                        float(stored_return),
                        float(expected_return),
                        rtol=1e-10,
                        atol=1e-10,
                    )
                )
                if not values_match or stored_status != expected_status:
                    return_mismatches += 1
    metrics["event_membership_t_minus_1_mismatch_count"] = membership_mismatches
    metrics["relative_volume_recalculation_mismatch_count"] = relative_volume_mismatches
    metrics["future_return_recalculation_mismatch_count"] = return_mismatches

    failure_metrics = [
        key
        for key, value in metrics.items()
        if key.endswith(("mismatch_count", "missing_file_count", "duplicate_date_count", "duplicate_column_count", "outside_required_dates_count", "nonpositive_hfq_close_count", "nonmissing_price_or_activity_count", "bad_member_count", "outside_frozen_period_count", "duplicate_key_count"))
        and int(value) > 0
    ]
    if failure_metrics:
        metrics["audit_status"] = "fail"
        metrics["failed_metrics"] = failure_metrics
    output_path = project_root / "outputs" / "hfq_final" / "formal_data_audit.json"
    output_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the formal raw-to-result data chain.")
    parser.add_argument("--price-config", default="config/eastmoney_price_audit.json")
    parser.add_argument("--events", default="outputs/hfq_final/sample_hfq_final.csv")
    parser.add_argument("--write-missing-hashes", action="store_true")
    args = parser.parse_args()
    if args.write_missing_hashes:
        updated = write_missing_cache_hashes(args.price_config)
        print(f"Cache metadata hashes written: {updated}")
    result = run_formal_audit(args.price_config, args.events)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["audit_status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
