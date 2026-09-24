from __future__ import annotations

import hashlib
import io
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .config import EventStudyConfig, resolve_config_path


MEMBERSHIP_SCHEDULE_URL = (
    "https://raw.githubusercontent.com/unliftedq/index-constitution/main/history/csi300.csv"
)
TENCENT_KLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
PROCESSED_STOCK_VERSION = "adaptive-v2-no-suspension-price-fill"


def _guard_d_drive(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.drive.upper() == "C:":
        raise ValueError(f"Refusing to write project data to C drive: {resolved}")
    return resolved


def _mkdir(path: Path) -> Path:
    checked = _guard_d_drive(path)
    checked.mkdir(parents=True, exist_ok=True)
    return checked


def _atomic_write(path: Path, content: bytes) -> None:
    path = _guard_d_drive(path)
    _mkdir(path.parent)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_bytes(content)
    partial.replace(path)


def _http_get(url: str, *, params: dict[str, str] | None = None) -> bytes:
    target = url if not params else f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        target,
        headers={
            "User-Agent": "Mozilla/5.0 CSI300 academic event-study downloader",
            "Accept": "application/json,text/csv,*/*",
        },
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except Exception as exc:  # network errors vary by Python/Windows version
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Download failed after four attempts: {target}") from last_error


def _source_record(url: str, content: bytes) -> dict[str, str | int]:
    return {
        "url": url,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _write_json(path: Path, value: object) -> None:
    _atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def _safe_code(stock_code: str) -> str:
    return stock_code.replace(".", "_")


def _to_tencent_symbol(stock_code: str) -> str:
    exchange, digits = stock_code.lower().split(".", maxsplit=1)
    if exchange == "sh":
        return f"sh{digits}"
    if exchange == "sz":
        return f"sz{digits}"
    raise ValueError(f"Unsupported A-share code: {stock_code}")


def _download_tencent_segment(
    stock_code: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    adjustment: str,
    raw_dir: Path,
) -> Path:
    if adjustment not in {"qfq", "raw"}:
        raise ValueError("adjustment must be qfq or raw")
    date_token = f"{start_date:%Y%m%d}__{end_date:%Y%m%d}"
    destination = (
        raw_dir
        / "tencent"
        / adjustment
        / f"{_safe_code(stock_code)}__{date_token}.json"
    )
    if destination.exists() and destination.stat().st_size > 50:
        cached = json.loads(destination.read_text(encoding="utf-8"))
        cached_rows: list[list[object]] = []
        symbol = _to_tencent_symbol(stock_code)
        preferred_key = "qfqday" if adjustment == "qfq" else "day"
        for page in cached.get("pages", []):
            stock_payload = (page.get("data") or {}).get(symbol) or {}
            cached_rows.extend(
                stock_payload.get(preferred_key)
                or (stock_payload.get("day") if adjustment == "qfq" else [])
                or []
            )
        if cached_rows:
            oldest_cached = min(pd.Timestamp(row[0]) for row in cached_rows)
            if len(cached_rows) % 640 != 0 or oldest_cached <= start_date:
                return destination
    symbol = _to_tencent_symbol(stock_code)
    cursor_end = end_date
    pages: list[dict[str, object]] = []
    while cursor_end >= start_date:
        adjustment_token = "qfq" if adjustment == "qfq" else ""
        params = {
            "_var": "kline_data",
            "param": (
                f"{symbol},day,{start_date:%Y-%m-%d},{cursor_end:%Y-%m-%d},"
                f"640,{adjustment_token}"
            ),
        }
        content = _http_get(TENCENT_KLINE_URL, params=params)
        text = content.decode("utf-8")
        payload = json.loads(text.split("=", maxsplit=1)[-1])
        if payload.get("code") != 0:
            raise RuntimeError(f"Tencent returned code={payload.get('code')} for {stock_code}")
        pages.append(payload)
        stock_payload = (payload.get("data") or {}).get(symbol) or {}
        key = "qfqday" if adjustment == "qfq" else "day"
        rows = (
            stock_payload.get(key)
            or (stock_payload.get("day") if adjustment == "qfq" else [])
            or []
        )
        if not rows:
            break
        oldest = min(pd.Timestamp(row[0]) for row in rows)
        if oldest <= start_date:
            break
        next_end = oldest - pd.Timedelta(days=1)
        if next_end >= cursor_end:
            raise RuntimeError(f"Tencent pagination did not advance for {stock_code}")
        cursor_end = next_end
    stored = {
        "source": TENCENT_KLINE_URL,
        "stock_code": stock_code,
        "adjustment": adjustment,
        "requested_start": start_date.strftime("%Y-%m-%d"),
        "requested_end": end_date.strftime("%Y-%m-%d"),
        "pages": pages,
    }
    _atomic_write(destination, json.dumps(stored, ensure_ascii=False).encode("utf-8"))
    return destination


def _parse_tencent_files(paths: Iterable[Path], stock_code: str) -> pd.DataFrame:
    rows: list[list[object]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        symbol = _to_tencent_symbol(stock_code)
        key = "qfqday" if payload.get("adjustment") == "qfq" else "day"
        for page in payload.get("pages", []):
            stock_payload = (page.get("data") or {}).get(symbol) or {}
            rows.extend(
                stock_payload.get(key)
                or (stock_payload.get("day") if payload.get("adjustment") == "qfq" else [])
                or []
            )
    columns = [
        "trade_date",
        "open",
        "close",
        "high",
        "low",
        "volume",
        "turnover_rate",
        "amount",
    ]
    normalized = [
        [
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[7] if len(row) > 7 else None,
            row[8] if len(row) > 8 else None,
        ]
        for row in rows
        if len(row) >= 6
    ]
    frame = pd.DataFrame(normalized, columns=columns)
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] *= 100.0
    frame["amount"] *= 10_000.0
    frame["stock_code"] = stock_code
    frame = (
        frame.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )
    frame["pctChg"] = frame["close"].pct_change(fill_method=None) * 100.0
    return frame


class BaoStockSession(AbstractContextManager["BaoStockSession"]):
    def __init__(self) -> None:
        self.bs = None

    def __enter__(self) -> "BaoStockSession":
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError(
                "BaoStock 0.9.3 is required for historical ST/trading status. "
                "Run .\\run.ps1 -InstallDependencies once."
            ) from exc
        self.bs = bs
        last_result = None
        for attempt in range(5):
            try:
                result = bs.login()
                last_result = result
                if result.error_code == "0":
                    return self
            except Exception:
                pass
            time.sleep(min(3 * (2**attempt), 30))
        if last_result is None:
            raise RuntimeError("BaoStock login failed without a response")
        raise RuntimeError(
            f"BaoStock login failed: {last_result.error_code} {last_result.error_msg}"
        )

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.bs is not None:
            try:
                self.bs.logout()
            except Exception:
                pass

    @staticmethod
    def _rows(result) -> tuple[list[str], list[list[str]]]:
        rows: list[list[str]] = []
        while result.error_code == "0" and result.next():
            rows.append(result.get_row_data())
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock query failed: {result.error_code} {result.error_msg}")
        return list(result.fields), rows

    def hs300_snapshot(self, date: pd.Timestamp) -> pd.DataFrame:
        assert self.bs is not None
        result = self.bs.query_hs300_stocks(date=date.strftime("%Y-%m-%d"))
        fields, rows = self._rows(result)
        return pd.DataFrame(rows, columns=fields)

    def stock_status(
        self, stock_code: str, start_date: pd.Timestamp, end_date: pd.Timestamp
    ) -> pd.DataFrame:
        assert self.bs is not None
        result = self.bs.query_history_k_data_plus(
            stock_code,
            "date,code,tradestatus,isST",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag="2",
        )
        fields, rows = self._rows(result)
        return pd.DataFrame(rows, columns=fields)

    def stock_price_validation(
        self, stock_code: str, start_date: pd.Timestamp, end_date: pd.Timestamp
    ) -> pd.DataFrame:
        """Download BaoStock qfq OHLCV for independent price validation."""
        assert self.bs is not None
        result = self.bs.query_history_k_data_plus(
            stock_code,
            "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg,isST",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag="2",
        )
        fields, rows = self._rows(result)
        return pd.DataFrame(rows, columns=fields)

def download_index_history(
    config: EventStudyConfig,
    config_path: str | Path,
    download_start: pd.Timestamp,
    download_end: pd.Timestamp,
) -> tuple[Path, Path]:
    """Download the CSI 300 index directly and create the open-day calendar."""
    raw_dir = _mkdir(resolve_config_path(config.raw_dir, config_path))
    processed_dir = _mkdir(resolve_config_path(config.processed_dir, config_path))
    raw_path = _download_tencent_segment(
        "sh.000300", download_start, download_end, "raw", raw_dir
    )
    index = _parse_tencent_files([raw_path], "sh.000300")
    index = index[index["trade_date"].between(download_start, download_end)].copy()
    if index.empty:
        raise RuntimeError("The CSI 300 index download returned no rows")
    index_path = processed_dir / "csi300_index.csv"
    calendar_path = processed_dir / "trading_calendar.csv"
    index[["trade_date", "close"]].rename(columns={"close": "index_close"}).to_csv(
        index_path, index=False, encoding=config.output_encoding
    )
    pd.DataFrame({"trade_date": index["trade_date"], "is_open": 1}).to_csv(
        calendar_path, index=False, encoding=config.output_encoding
    )
    return index_path, calendar_path


def _membership_change_schedule(raw_path: Path) -> pd.DataFrame:
    schedule = pd.read_csv(raw_path, encoding="utf-8-sig", dtype={"symbol": "string"})
    required = {"symbol", "opt-in", "opt-out"}
    if not required.issubset(schedule.columns):
        raise ValueError("Historical membership schedule has an unexpected schema")
    schedule["in_date"] = pd.to_datetime(schedule["opt-in"], errors="raise")
    schedule["out_date"] = pd.to_datetime(schedule["opt-out"], errors="coerce")
    return schedule


def download_hs300_membership(
    config: EventStudyConfig,
    config_path: str | Path,
    calendar: pd.DataFrame,
) -> Path:
    """Cache BaoStock point-in-time snapshots and convert them to membership intervals.

    The directly downloaded change table supplies candidate transition dates only. Every
    actual member set used by the study comes from query_hs300_stocks(date=...).
    """
    raw_dir = _mkdir(resolve_config_path(config.raw_dir, config_path))
    processed_dir = _mkdir(resolve_config_path(config.processed_dir, config_path))
    schedule_path = raw_dir / "membership" / "csi300_change_schedule.csv"
    source_record_path = raw_dir / "membership" / "source.json"
    if not schedule_path.exists():
        content = _http_get(MEMBERSHIP_SCHEDULE_URL)
        _atomic_write(schedule_path, content)
        _write_json(source_record_path, _source_record(MEMBERSHIP_SCHEDULE_URL, content))
    schedule = _membership_change_schedule(schedule_path)

    open_dates = pd.DatetimeIndex(pd.to_datetime(calendar["trade_date"]).sort_values().unique())
    event_start = pd.Timestamp(config.start_date)
    event_end = pd.Timestamp(config.end_date)
    earlier = open_dates[open_dates < event_start]
    if len(earlier) == 0:
        raise ValueError("Index calendar does not contain a t-1 date before sample start")
    initial_date = earlier[-1]
    candidate_dates = pd.concat([schedule["in_date"], schedule["out_date"]]).dropna()
    candidate_dates = candidate_dates[(candidate_dates > initial_date) & (candidate_dates <= event_end)]
    mapped_dates: set[pd.Timestamp] = {pd.Timestamp(initial_date)}
    for candidate in candidate_dates.unique():
        following = open_dates[open_dates >= pd.Timestamp(candidate)]
        if len(following):
            mapped_dates.add(pd.Timestamp(following[0]))
    snapshot_dates = sorted(mapped_dates)
    snapshot_dir = _mkdir(raw_dir / "baostock" / "hs300_snapshots")
    snapshots: list[tuple[pd.Timestamp, set[str]]] = []
    uncached = [d for d in snapshot_dates if not (snapshot_dir / f"{d:%Y-%m-%d}.csv").exists()]
    session_context = BaoStockSession() if uncached else None
    if session_context is not None:
        session_context.__enter__()
    try:
        for number, date in enumerate(snapshot_dates, start=1):
            path = snapshot_dir / f"{date:%Y-%m-%d}.csv"
            if path.exists():
                frame = pd.read_csv(path, dtype={"code": "string"})
            else:
                assert session_context is not None
                frame = session_context.hs300_snapshot(date)
                frame.to_csv(path, index=False, encoding=config.output_encoding)
            codes = set(frame["code"].dropna().astype(str).str.strip())
            if len(codes) != 300:
                raise ValueError(
                    f"BaoStock snapshot {date:%Y-%m-%d} has {len(codes)} unique members, not 300"
                )
            snapshots.append((date, codes))
            if number % 10 == 0 or number == len(snapshot_dates):
                print(f"  membership snapshots: {number}/{len(snapshot_dates)}", flush=True)
    finally:
        if session_context is not None:
            session_context.__exit__(None, None, None)

    active_since = {code: snapshots[0][0] for code in snapshots[0][1]}
    intervals: list[dict[str, object]] = []
    previous_codes = snapshots[0][1]
    for date, codes in snapshots[1:]:
        for code in previous_codes - codes:
            intervals.append(
                {"stock_code": code, "in_date": active_since.pop(code), "out_date": date}
            )
        for code in codes - previous_codes:
            active_since[code] = date
        previous_codes = codes
    for code, in_date in active_since.items():
        intervals.append({"stock_code": code, "in_date": in_date, "out_date": pd.NaT})
    membership = pd.DataFrame(intervals).sort_values(["stock_code", "in_date"])
    membership_path = processed_dir / "csi300_membership.csv"
    membership.to_csv(membership_path, index=False, encoding=config.output_encoding)
    return membership_path


def _requested_segments(
    metadata_path: Path, start_date: pd.Timestamp, end_date: pd.Timestamp
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if not metadata_path.exists():
        return [(start_date, end_date)]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    covered_start = pd.Timestamp(metadata["requested_start"])
    covered_end = pd.Timestamp(metadata["requested_end"])
    segments: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    if start_date < covered_start:
        segments.append((start_date, covered_start - pd.Timedelta(days=1)))
    if end_date > covered_end:
        segments.append((covered_end + pd.Timedelta(days=1), end_date))
    return segments


def _read_all_segments(directory: Path, safe_code: str) -> list[Path]:
    return sorted(directory.glob(f"{safe_code}__*.json"))


def _read_all_status_segments(directory: Path, safe_code: str) -> pd.DataFrame:
    frames = [
        pd.read_csv(
            path,
            dtype={
                "code": "string",
                "tradestatus": "string",
                "isST": "string",
            },
        )
        for path in sorted(directory.glob(f"{safe_code}__*.csv"))
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=["date", "code", "tradestatus", "isST"])
    result = pd.concat(frames, ignore_index=True)
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    return result.sort_values("date").drop_duplicates("date", keep="last")


def _build_processed_stock(
    stock_code: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    raw_dir: Path,
    destination: Path,
    encoding: str,
) -> None:
    safe = _safe_code(stock_code)
    qfq = _parse_tencent_files(
        _read_all_segments(raw_dir / "tencent" / "qfq", safe), stock_code
    )
    raw = _parse_tencent_files(
        _read_all_segments(raw_dir / "tencent" / "raw", safe), stock_code
    )
    status = _read_all_status_segments(raw_dir / "baostock" / "status", safe)
    if qfq.empty or raw.empty or status.empty:
        raise RuntimeError(f"Required historical data is empty for {stock_code}")

    qfq = qfq.rename(
        columns={
            "open": "adjusted_open",
            "high": "adjusted_high",
            "low": "adjusted_low",
            "close": "adjusted_close",
        }
    )[["trade_date", "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"]]
    raw["preclose"] = raw["close"].shift(1)
    raw = raw[
        [
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "preclose",
            "volume",
            "amount",
            "turnover_rate",
            "pctChg",
        ]
    ]
    status = status.rename(columns={"date": "trade_date"})
    frame = status.merge(raw, on="trade_date", how="left", validate="one_to_one")
    frame = frame.merge(qfq, on="trade_date", how="left", validate="one_to_one")
    frame = frame[frame["trade_date"].between(start_date, end_date)].copy()
    frame["tradestatus"] = pd.to_numeric(frame["tradestatus"], errors="coerce")
    frame["isST"] = pd.to_numeric(frame["isST"], errors="coerce")

    suspended = frame["tradestatus"].eq(0)
    price_columns = [
        "open",
        "high",
        "low",
        "close",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
    ]
    frame.loc[suspended, price_columns] = np.nan
    frame.loc[suspended, ["volume", "amount", "turnover_rate", "pctChg"]] = np.nan
    frame["stock_code"] = stock_code
    frame["industry"] = pd.NA
    frame["ST_status"] = frame["isST"]
    frame["suspension_status"] = suspended.astype("Int64")
    output_columns = [
        "stock_code",
        "trade_date",
        *price_columns,
        "preclose",
        "volume",
        "amount",
        "turnover_rate",
        "pctChg",
        "industry",
        "ST_status",
        "suspension_status",
        "tradestatus",
        "isST",
    ]
    _mkdir(destination.parent)
    frame[output_columns].to_csv(
        destination, index=False, encoding=encoding, compression="gzip"
    )


def download_stock_history(
    stock_codes: Iterable[str],
    config: EventStudyConfig,
    config_path: str | Path,
    download_start: pd.Timestamp,
    download_end: pd.Timestamp,
) -> list[Path]:
    """Download stock prices/status with segment caching and build one gzip per stock."""
    codes = sorted(set(stock_codes))
    raw_dir = _mkdir(resolve_config_path(config.raw_dir, config_path))
    processed_stock_dir = _mkdir(resolve_config_path(config.processed_stock_dir, config_path))
    metadata_dir = _mkdir(raw_dir / "coverage")
    jobs: list[tuple[str, pd.Timestamp, pd.Timestamp, str]] = []
    segments_by_code: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    for code in codes:
        metadata = metadata_dir / f"{_safe_code(code)}.json"
        segments = _requested_segments(metadata, download_start, download_end)
        segments_by_code[code] = segments
        for segment_start, segment_end in segments:
            jobs.extend(
                [
                    (code, segment_start, segment_end, "qfq"),
                    (code, segment_start, segment_end, "raw"),
                ]
            )

    if jobs:
        print(f"Direct price downloads: {len(jobs)} cached segments", flush=True)
        with ThreadPoolExecutor(max_workers=config.download_workers) as executor:
            futures = {
                executor.submit(
                    _download_tencent_segment, code, start, end, adjustment, raw_dir
                ): (code, adjustment)
                for code, start, end, adjustment in jobs
            }
            for number, future in enumerate(as_completed(futures), start=1):
                future.result()
                if number % 50 == 0 or number == len(futures):
                    print(f"  price segments: {number}/{len(futures)}", flush=True)

        status_dir = _mkdir(raw_dir / "baostock" / "status")
        status_jobs = [
            (code, segment_start, segment_end)
            for code, segments in segments_by_code.items()
            for segment_start, segment_end in segments
        ]
        with BaoStockSession() as session:
            for number, (code, segment_start, segment_end) in enumerate(status_jobs, start=1):
                if number > 1 and (number - 1) % 100 == 0:
                    session.__exit__(None, None, None)
                    time.sleep(3)
                    session.__enter__()
                path = status_dir / (
                    f"{_safe_code(code)}__{segment_start:%Y%m%d}__{segment_end:%Y%m%d}.csv"
                )
                if not path.exists():
                    last_error: Exception | None = None
                    for attempt in range(4):
                        try:
                            frame = session.stock_status(code, segment_start, segment_end)
                            break
                        except RuntimeError as exc:
                            last_error = exc
                            session.__exit__(None, None, None)
                            time.sleep(1.5 * (attempt + 1))
                            session.__enter__()
                    else:
                        raise RuntimeError(
                            f"BaoStock status failed repeatedly for {code} "
                            f"{segment_start:%Y-%m-%d}..{segment_end:%Y-%m-%d}"
                        ) from last_error
                    frame.to_csv(path, index=False, encoding=config.output_encoding)
                if number % 25 == 0 or number == len(status_jobs):
                    print(f"  BaoStock status: {number}/{len(status_jobs)}", flush=True)

    destinations: list[Path] = []
    for number, code in enumerate(codes, start=1):
        destination = processed_stock_dir / f"{_safe_code(code)}.csv.gz"
        metadata_path = metadata_dir / f"{_safe_code(code)}.json"
        metadata = (
            json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_path.exists()
            else {}
        )
        if not segments_by_code[code] and destination.exists():
            existing_columns = set(pd.read_csv(destination, nrows=0).columns)
            if (
                {"preclose", "tradestatus", "isST"}.issubset(existing_columns)
                and metadata.get("processing_version") == PROCESSED_STOCK_VERSION
            ):
                destinations.append(destination)
                continue
        _build_processed_stock(
            code,
            download_start,
            download_end,
            raw_dir,
            destination,
            config.output_encoding,
        )
        old = (
            json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_path.exists()
            else {}
        )
        covered_start = min(pd.Timestamp(old.get("requested_start", download_start)), download_start)
        covered_end = max(pd.Timestamp(old.get("requested_end", download_end)), download_end)
        _write_json(
            metadata_path,
            {
                "stock_code": code,
                "requested_start": covered_start.strftime("%Y-%m-%d"),
                "requested_end": covered_end.strftime("%Y-%m-%d"),
                "price_source": "Tencent Finance public kline endpoint",
                "status_source": "BaoStock query_history_k_data_plus",
                "processing_version": PROCESSED_STOCK_VERSION,
            },
        )
        destinations.append(destination)
        if number % 50 == 0 or number == len(codes):
            print(f"  processed stocks: {number}/{len(codes)}", flush=True)
    return destinations


def load_processed_stock(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"stock_code": "string"}, low_memory=False)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    numeric = [
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
        "ST_status",
        "suspension_status",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["_is_st"] = frame["ST_status"].map({0.0: False, 1.0: True}).astype("boolean")
    frame["_is_suspended"] = (
        frame["suspension_status"].map({0.0: False, 1.0: True}).astype("boolean")
    )
    return frame
