from __future__ import annotations

import json
import ipaddress
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit
from unittest.mock import patch

import numpy as np
import pandas as pd

from .acquisition import BaoStockSession, load_processed_stock


EASTMONEY_COLUMNS = [
    "stock_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "turnover_rate",
    "amplitude",
    "pctChg",
    "change",
]

PROCESSED_COLUMNS = [
    "stock_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "adjusted_open",
    "adjusted_high",
    "adjusted_low",
    "adjusted_close",
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


@dataclass(frozen=True)
class PriceLayerConfig:
    methodology_config: str
    download_start: str
    download_end: str
    eastmoney_adjustments: list[str]
    eastmoney_raw_dir: str
    eastmoney_processed_dir: str
    baostock_raw_dir: str
    baostock_processed_dir: str
    yahoo_raw_dir: str
    baostock_status_dir: str
    output_dir: str
    random_seed: int
    yahoo_sample_size: int
    request_timeout_seconds: float
    request_pause_seconds: float
    max_download_attempts: int
    output_encoding: str

    def resolve(self, value: str, config_path: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = Path(config_path).resolve().parent / path
        return path.resolve()

    def validate(self) -> None:
        if self.eastmoney_adjustments != ["raw", "hfq"]:
            raise ValueError("The formal Eastmoney pipeline must contain raw and hfq only")
        if pd.Timestamp(self.download_start) > pd.Timestamp(self.download_end):
            raise ValueError("download_start cannot be later than download_end")
        if self.yahoo_sample_size < 30:
            raise ValueError("Yahoo audit sample must contain at least 30 stocks")
        if self.max_download_attempts < 1:
            raise ValueError("max_download_attempts must be positive")
        for value in (
            self.eastmoney_raw_dir,
            self.eastmoney_processed_dir,
            self.baostock_raw_dir,
            self.baostock_processed_dir,
            self.yahoo_raw_dir,
            self.baostock_status_dir,
            self.output_dir,
        ):
            if Path(value).drive.upper() == "C:":
                raise ValueError("Project data, cache and outputs must not be stored on C drive")


def load_price_layer_config(path: str | Path) -> PriceLayerConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    config = PriceLayerConfig(**raw)
    config.validate()
    return config


def _safe_code(stock_code: str) -> str:
    return stock_code.replace(".", "_")


def _numeric_code(stock_code: str) -> str:
    return stock_code.split(".")[-1].zfill(6)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_is_complete(metadata_path: Path, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    if not metadata_path.exists():
        return False
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    destination = metadata_path.with_name(
        metadata_path.name.replace(".metadata.json", ".csv.gz")
    )
    expected_hash = metadata.get("sha256")
    hash_matches = (
        destination.exists()
        and (
            expected_hash is None
            or str(expected_hash).lower() == _sha256_file(destination).lower()
        )
    )
    return (
        hash_matches
        and
        pd.Timestamp(metadata.get("requested_start")) <= start
        and pd.Timestamp(metadata.get("requested_end")) >= end
        and metadata.get("download_status") == "complete"
    )


def effective_download_end(configured_end: str, *, today: pd.Timestamp | None = None) -> pd.Timestamp:
    """Never request or certify market data for a date that has not happened yet."""
    current_day = (today if today is not None else pd.Timestamp.today()).normalize()
    return min(pd.Timestamp(configured_end), current_day - pd.Timedelta(days=1))


def research_bounds_by_stock(
    membership: pd.DataFrame,
    layer_start: str,
    layer_end: str,
    *,
    sample_start: str = "2009-01-01",
    history_padding_calendar_days: int = 800,
    future_padding_calendar_days: int = 0,
) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    """Return the only date span that can affect a stock's point-in-time events."""
    floor = pd.Timestamp(layer_start)
    ceiling = pd.Timestamp(layer_end)
    sample_floor = pd.Timestamp(sample_start)
    result: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for stock_code, intervals in membership.groupby("stock_code", sort=False):
        first_membership = pd.to_datetime(intervals["in_date"], errors="raise").min()
        possible_first_event = max(sample_floor, first_membership)
        start = max(
            floor,
            possible_first_event - pd.Timedelta(days=history_padding_calendar_days),
        )
        out_dates = pd.to_datetime(intervals["out_date"], errors="coerce")
        end = (
            ceiling
            if out_dates.isna().any()
            else min(
                ceiling,
                out_dates.max() + pd.Timedelta(days=future_padding_calendar_days),
            )
        )
        result[str(stock_code)] = (start, end)
    return result


def _normalise_eastmoney(frame: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    rename = {
        "日期": "trade_date",
        "股票代码": "source_stock_code",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "pctChg",
        "涨跌额": "change",
        "换手率": "turnover_rate",
    }
    result = frame.rename(columns=rename).copy()
    for column in EASTMONEY_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA
    result["stock_code"] = stock_code
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise")
    for column in EASTMONEY_COLUMNS:
        if column not in {"stock_code", "trade_date"}:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return (
        result[EASTMONEY_COLUMNS]
        .sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )


_AKSHARE_TRANSPORT = "unknown"
_EASTMONEY_HISTORY_HOSTS = (
    "push2his.eastmoney.com",
    "33.push2his.eastmoney.com",
    "7.push2his.eastmoney.com",
    "63.push2his.eastmoney.com",
    "91.push2his.eastmoney.com",
)
_EASTMONEY_RELAY_HOSTS = (
    "push2his.eastmoney.com",
    "33.push2his.eastmoney.com",
    "91.push2his.eastmoney.com",
)
_EASTMONEY_REAL_IP_POOL: list[str] = []
_EASTMONEY_REAL_IP_INDEX = 0
_EASTMONEY_RELAY_INDEX = 0


class _JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def _extract_jina_json(text: str) -> dict[str, object]:
    marker = "Markdown Content:\n"
    if marker not in text:
        raise RuntimeError("The public relay did not return a Markdown Content block")
    payload = text.split(marker, 1)[1].strip()
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("The relayed Eastmoney response was not a JSON object")
    return value


def _next_eastmoney_real_ip(request_get) -> str:
    """Resolve and rotate public addresses around a synthetic-DNS environment."""
    global _EASTMONEY_REAL_IP_INDEX
    if len(_EASTMONEY_REAL_IP_POOL) < 6:
        for _ in range(8):
            response = request_get(
                "https://dns.google/resolve",
                params={"name": "push2his.eastmoney.com", "type": "A"},
                headers={"Cache-Control": "no-cache"},
                timeout=20,
            )
            response.raise_for_status()
            for answer in response.json().get("Answer", []):
                if int(answer.get("type", 0)) != 1:
                    continue
                value = str(answer.get("data", ""))
                address = ipaddress.ip_address(value)
                if (
                    not address.is_private
                    and not address.is_reserved
                    and not address.is_loopback
                    and value not in _EASTMONEY_REAL_IP_POOL
                ):
                    _EASTMONEY_REAL_IP_POOL.append(value)
            if len(_EASTMONEY_REAL_IP_POOL) >= 6:
                break
            time.sleep(0.15)
    if not _EASTMONEY_REAL_IP_POOL:
        raise RuntimeError("Public DNS did not return a usable Eastmoney IPv4 address")
    value = _EASTMONEY_REAL_IP_POOL[
        _EASTMONEY_REAL_IP_INDEX % len(_EASTMONEY_REAL_IP_POOL)
    ]
    _EASTMONEY_REAL_IP_INDEX += 1
    return value


def _akshare_history_once(
    stock_code: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    adjustment: str,
    timeout: float,
) -> tuple[pd.DataFrame, str]:
    """Call the documented AKShare function with Eastmoney-host fallbacks.

    Some networks or temporary rate limits terminate one Eastmoney HTTPS node while
    another public Eastmoney node remains available.  AKShare still constructs,
    parses and labels the response; only its request hostname is rotated.
    """
    import akshare as ak
    import akshare.stock_feature.stock_hist_em as stock_hist_em

    global _AKSHARE_TRANSPORT, _EASTMONEY_RELAY_INDEX
    kwargs = {
        "symbol": _numeric_code(stock_code),
        "period": "daily",
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "adjust": "" if adjustment == "raw" else adjustment,
        "timeout": timeout,
    }
    preferred_host = None
    if _AKSHARE_TRANSPORT.startswith("https_host:"):
        preferred_host = _AKSHARE_TRANSPORT.split(":", 1)[1]
    hosts = list(_EASTMONEY_HISTORY_HOSTS)
    if preferred_host in hosts:
        hosts.remove(preferred_host)
        hosts.insert(0, preferred_host)

    original_get = stock_hist_em.requests.get
    last_https_error: Exception | None = None

    relay_hosts = list(_EASTMONEY_RELAY_HOSTS)
    rotation = _EASTMONEY_RELAY_INDEX % len(relay_hosts)
    relay_hosts = relay_hosts[rotation:] + relay_hosts[:rotation]
    _EASTMONEY_RELAY_INDEX += 1

    def relay_call(relay_host: str) -> pd.DataFrame:
        def jina_get(url: str, *args, **call_kwargs):
            parts = urlsplit(url)
            if parts.hostname != "push2his.eastmoney.com":
                return original_get(url, *args, **call_kwargs)
            params = call_kwargs.pop("params", None)
            requested_timeout = call_kwargs.pop("timeout", None)
            relay_url = f"https://r.jina.ai/http://{relay_host}{parts.path}"
            response = original_get(
                relay_url,
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/plain,*/*",
                },
                timeout=max(60.0, float(requested_timeout or 0)),
                **call_kwargs,
            )
            response.raise_for_status()
            return _JsonResponse(_extract_jina_json(response.text))

        with patch.object(stock_hist_em.requests, "get", side_effect=jina_get):
            return ak.stock_zh_a_hist(**kwargs)

    if _AKSHARE_TRANSPORT.startswith("jina_proxy"):
        relay_error: Exception | None = None
        for relay_host in relay_hosts:
            try:
                frame = relay_call(relay_host)
                _AKSHARE_TRANSPORT = f"jina_proxy:{relay_host}"
                return frame, f"public_relay_to_{relay_host}"
            except Exception as exc:
                relay_error = exc
        raise RuntimeError("All public relay routes to Eastmoney failed") from relay_error

    for host in hosts:
        try:
            def headered_get(url: str, *args, _host: str = host, **call_kwargs):
                parts = urlsplit(url)
                if parts.hostname == "push2his.eastmoney.com":
                    url = urlunsplit(
                        (parts.scheme, _host, parts.path, parts.query, parts.fragment)
                    )
                headers = dict(call_kwargs.pop("headers", {}) or {})
                headers.setdefault(
                    "User-Agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140 Safari/537.36",
                )
                headers.setdefault("Referer", "https://quote.eastmoney.com/")
                headers.setdefault("Accept", "application/json,text/plain,*/*")
                headers.setdefault("Connection", "close")
                return original_get(url, *args, headers=headers, **call_kwargs)

            with patch.object(stock_hist_em.requests, "get", side_effect=headered_get):
                frame = ak.stock_zh_a_hist(**kwargs)
            if frame.empty:
                _AKSHARE_TRANSPORT = f"https_host:{host}"
                return frame, f"https_host:{host}:empty_response"
            _AKSHARE_TRANSPORT = f"https_host:{host}"
            return frame, f"https_host:{host}"
        except Exception as exc:
            last_https_error = exc

    for relay_host in relay_hosts:
        try:
            frame = relay_call(relay_host)
            _AKSHARE_TRANSPORT = f"jina_proxy:{relay_host}"
            return frame, f"public_relay_to_{relay_host}"
        except Exception as exc:
            last_https_error = exc

    try:
        real_ip = _next_eastmoney_real_ip(original_get)
    except Exception as exc:
        if last_https_error is not None:
            raise RuntimeError(
                "All public Eastmoney HTTPS nodes, the public relay, and the "
                "direct-IP fallback failed"
            ) from last_https_error
        raise exc
    direct_session = stock_hist_em.requests.Session()
    direct_session.trust_env = False

    def http_get(url: str, *args, **call_kwargs):
        parts = urlsplit(url)
        if parts.hostname != "push2his.eastmoney.com":
            return original_get(url, *args, **call_kwargs)
        url = urlunsplit(("http", real_ip, parts.path, parts.query, parts.fragment))
        headers = dict(call_kwargs.pop("headers", {}) or {})
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/140 Safari/537.36",
        )
        headers.setdefault("Referer", "https://quote.eastmoney.com/")
        headers.setdefault("Accept", "application/json,text/plain,*/*")
        headers["Connection"] = "close"
        headers["Host"] = "push2his.eastmoney.com"
        return direct_session.get(url, *args, headers=headers, **call_kwargs)

    with patch.object(stock_hist_em.requests, "get", side_effect=http_get):
        frame = ak.stock_zh_a_hist(**kwargs)
    return frame, "http_direct_ip_dns_fallback"


def download_eastmoney_histories(
    stock_codes: Iterable[str],
    config: PriceLayerConfig,
    config_path: str | Path,
    *,
    force: bool = False,
    continue_on_error: bool = False,
    bounds_by_code: dict[str, tuple[pd.Timestamp, pd.Timestamp]] | None = None,
) -> dict[str, list[Path]]:
    """Download and cache Eastmoney raw/qfq/hfq data through AKShare."""
    raw_root = config.resolve(config.eastmoney_raw_dir, config_path)
    default_start = pd.Timestamp(config.download_start)
    default_end = effective_download_end(config.download_end)
    codes = sorted(set(str(code) for code in stock_codes))
    outputs: dict[str, list[Path]] = {name: [] for name in config.eastmoney_adjustments}
    total = len(codes) * len(config.eastmoney_adjustments)
    completed = 0
    failures: list[dict[str, str]] = []
    for stock_code in codes:
        start, end = (
            bounds_by_code.get(stock_code, (default_start, default_end))
            if bounds_by_code is not None
            else (default_start, default_end)
        )
        safe = _safe_code(stock_code)
        for adjustment in config.eastmoney_adjustments:
            directory = raw_root / adjustment
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"{safe}.csv.gz"
            metadata_path = directory / f"{safe}.metadata.json"
            if not force and destination.exists() and _cache_is_complete(
                metadata_path, start, end
            ):
                outputs[adjustment].append(destination)
                completed += 1
                continue

            last_error: Exception | None = None
            for attempt in range(config.max_download_attempts):
                try:
                    raw, transport = _akshare_history_once(
                        stock_code,
                        start,
                        end,
                        adjustment,
                        config.request_timeout_seconds,
                    )
                    frame = _normalise_eastmoney(raw, stock_code)
                    if frame.empty:
                        raise RuntimeError(
                            f"Eastmoney returned an empty history for {stock_code} {adjustment}"
                        )
                    frame.to_csv(
                        destination,
                        index=False,
                        encoding=config.output_encoding,
                        compression="gzip",
                    )
                    _write_json(
                        metadata_path,
                        {
                            "stock_code": stock_code,
                            "source": "Eastmoney via AKShare stock_zh_a_hist",
                            "akshare_adjustment": "" if adjustment == "raw" else adjustment,
                            "adjustment_label": adjustment,
                            "requested_start": start.strftime("%Y-%m-%d"),
                            "requested_end": end.strftime("%Y-%m-%d"),
                            "observed_start": (
                                frame["trade_date"].min().strftime("%Y-%m-%d")
                                if len(frame)
                                else None
                            ),
                            "observed_end": (
                                frame["trade_date"].max().strftime("%Y-%m-%d")
                                if len(frame)
                                else None
                            ),
                            "row_count": len(frame),
                            "sha256": _sha256_file(destination),
                            "transport": transport,
                            "download_status": "complete",
                            "downloaded_at": pd.Timestamp.now().isoformat(),
                        },
                    )
                    outputs[adjustment].append(destination)
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(min(2.0 * (attempt + 1), 10.0))
            else:
                if not continue_on_error:
                    raise RuntimeError(
                        f"Eastmoney/AKShare download failed for {stock_code} {adjustment}"
                    ) from last_error
                failures.append(
                    {
                        "stock_code": stock_code,
                        "adjustment": adjustment,
                        "error": f"{type(last_error).__name__}: {last_error}",
                    }
                )
                print(
                    f"  Eastmoney/AKShare deferred after retries: {stock_code} {adjustment}",
                    flush=True,
                )
                completed += 1
                continue
            completed += 1
            if config.request_pause_seconds > 0:
                time.sleep(config.request_pause_seconds)
            if completed % 25 == 0 or completed == total:
                print(f"  Eastmoney/AKShare caches: {completed}/{total}", flush=True)
    if failures:
        failure_path = raw_root / "download_failures.csv"
        previous = (
            pd.read_csv(failure_path, dtype={"stock_code": "string"})
            if failure_path.exists()
            else pd.DataFrame()
        )
        combined = pd.concat([previous, pd.DataFrame(failures)], ignore_index=True)
        combined["recorded_at"] = pd.Timestamp.now().isoformat()
        combined.to_csv(failure_path, index=False, encoding=config.output_encoding)
    return outputs


def download_baostock_status_histories(
    stock_codes: Iterable[str],
    config: PriceLayerConfig,
    config_path: str | Path,
    *,
    bounds_by_code: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> list[Path]:
    """Extend the raw BaoStock tradestatus/isST cache without Tencent prices."""
    status_root = config.resolve(config.baostock_status_dir, config_path)
    status_root.mkdir(parents=True, exist_ok=True)
    codes = sorted(set(str(code) for code in stock_codes))
    written: list[Path] = []
    with BaoStockSession() as session:
        for number, stock_code in enumerate(codes, start=1):
            start, end = bounds_by_code[stock_code]
            safe = _safe_code(stock_code)
            existing = sorted(status_root.glob(f"{safe}__*.csv"))
            requested_ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
            for path in existing:
                parts = path.stem.split("__")
                if len(parts) == 3:
                    requested_ranges.append(
                        (pd.Timestamp(parts[1]), pd.Timestamp(parts[2]))
                    )
            segments: list[tuple[pd.Timestamp, pd.Timestamp]] = []
            if not requested_ranges:
                segments.append((start, end))
            else:
                covered_start = min(value[0] for value in requested_ranges)
                covered_end = max(value[1] for value in requested_ranges)
                if start < covered_start:
                    segments.append((start, covered_start - pd.Timedelta(days=1)))
                if end > covered_end:
                    segments.append((covered_end + pd.Timedelta(days=1), end))
            for segment_start, segment_end in segments:
                frame = session.stock_status(stock_code, segment_start, segment_end)
                path = status_root / (
                    f"{safe}__{segment_start:%Y%m%d}__{segment_end:%Y%m%d}.csv"
                )
                frame.to_csv(path, index=False, encoding=config.output_encoding)
                written.append(path)
                if config.request_pause_seconds > 0:
                    time.sleep(config.request_pause_seconds)
            if number % 50 == 0 or number == len(codes):
                print(f"  BaoStock status caches checked: {number}/{len(codes)}", flush=True)
    return written


def _load_eastmoney(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"stock_code": "string"}, low_memory=False)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    for column in EASTMONEY_COLUMNS:
        if column not in {"stock_code", "trade_date"}:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def build_eastmoney_processed(
    stock_codes: Iterable[str],
    config: PriceLayerConfig,
    config_path: str | Path,
) -> dict[str, list[Path]]:
    """Build the formal hfq layer from Eastmoney prices and raw BaoStock status."""
    raw_root = config.resolve(config.eastmoney_raw_dir, config_path)
    processed_root = config.resolve(config.eastmoney_processed_dir, config_path)
    status_root = config.resolve(config.baostock_status_dir, config_path)
    outputs: dict[str, list[Path]] = {"hfq": []}
    codes = sorted(set(str(code) for code in stock_codes))
    processed_start = pd.Timestamp(config.download_start)
    processed_end = effective_download_end(config.download_end)
    for adjustment in ("hfq",):
        destination_root = processed_root / adjustment
        destination_root.mkdir(parents=True, exist_ok=True)
        for number, stock_code in enumerate(codes, start=1):
            safe = _safe_code(stock_code)
            raw = _load_eastmoney(raw_root / "raw" / f"{safe}.csv.gz")
            adjusted = _load_eastmoney(raw_root / adjustment / f"{safe}.csv.gz")
            status_segments = sorted(status_root.glob(f"{safe}__*.csv"))
            if not status_segments:
                raise FileNotFoundError(
                    f"Raw BaoStock status cache is missing for {stock_code}: {status_root}"
                )
            status = pd.concat(
                [
                    pd.read_csv(
                        path,
                        dtype={"code": "string", "tradestatus": "string", "isST": "string"},
                    )
                    for path in status_segments
                ],
                ignore_index=True,
            )
            status = status.rename(columns={"date": "trade_date", "code": "stock_code"})
            status["trade_date"] = pd.to_datetime(status["trade_date"], errors="raise")
            status = (
                status.loc[status["trade_date"].between(processed_start, processed_end)]
                .sort_values("trade_date")
                .drop_duplicates("trade_date", keep="last")
            )
            status["tradestatus"] = pd.to_numeric(status["tradestatus"], errors="coerce")
            status["isST"] = pd.to_numeric(status["isST"], errors="coerce")
            status["ST_status"] = status["isST"]
            status["suspension_status"] = status["tradestatus"].eq(0).astype("Int64")
            status = status[
                ["stock_code", "trade_date", "tradestatus", "isST", "ST_status", "suspension_status"]
            ].copy()

            raw_block = raw[
                [
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                    "turnover_rate",
                    "pctChg",
                ]
            ].copy()
            raw_block["preclose"] = raw_block["close"].shift(1)
            adjusted_block = adjusted[
                ["trade_date", "open", "high", "low", "close"]
            ].rename(
                columns={
                    "open": "adjusted_open",
                    "high": "adjusted_high",
                    "low": "adjusted_low",
                    "close": "adjusted_close",
                }
            )
            frame = status.merge(raw_block, on="trade_date", how="left", validate="one_to_one")
            frame = frame.merge(
                adjusted_block, on="trade_date", how="left", validate="one_to_one"
            )
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
            frame["industry"] = pd.NA
            destination = destination_root / f"{safe}.csv.gz"
            frame[PROCESSED_COLUMNS].to_csv(
                destination,
                index=False,
                encoding=config.output_encoding,
                compression="gzip",
            )
            outputs[adjustment].append(destination)
            if number % 50 == 0 or number == len(codes):
                print(
                    f"  Eastmoney processed {adjustment}: {number}/{len(codes)}",
                    flush=True,
                )
    return outputs


def build_eastmoney_trading_calendar(
    config: PriceLayerConfig,
    config_path: str | Path,
    destination: str | Path,
) -> Path:
    """Build the market-day calendar from the union of formal Eastmoney dates."""
    raw_root = config.resolve(config.eastmoney_raw_dir, config_path) / "raw"
    start = pd.Timestamp(config.download_start)
    end = effective_download_end(config.download_end)
    dates: set[pd.Timestamp] = set()
    source_files = sorted(raw_root.glob("*.csv.gz"))
    if not source_files:
        raise FileNotFoundError(f"No Eastmoney raw caches were found in {raw_root}")
    for path in source_files:
        frame = pd.read_csv(path, usecols=["trade_date"])
        values = pd.to_datetime(frame["trade_date"], errors="raise")
        dates.update(pd.Timestamp(value) for value in values if start <= value <= end)
    calendar = pd.DataFrame({"trade_date": sorted(dates)})
    calendar["is_open"] = 1
    output_path = Path(destination).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    calendar.to_csv(output_path, index=False, encoding=config.output_encoding)
    metadata = {
        "source": "union of Eastmoney raw A-share daily cache dates",
        "source_file_count": len(source_files),
        "requested_start": start.strftime("%Y-%m-%d"),
        "effective_end": end.strftime("%Y-%m-%d"),
        "observed_start": calendar["trade_date"].min().strftime("%Y-%m-%d"),
        "observed_end": calendar["trade_date"].max().strftime("%Y-%m-%d"),
        "market_day_count": len(calendar),
        "sha256": _sha256_file(output_path),
    }
    _write_json(output_path.with_name("trading_calendar.metadata.json"), metadata)
    return output_path


def download_baostock_full_qfq(
    stock_codes: Iterable[str],
    config: PriceLayerConfig,
    config_path: str | Path,
    *,
    force: bool = False,
    bounds_by_code: dict[str, tuple[pd.Timestamp, pd.Timestamp]] | None = None,
) -> list[Path]:
    raw_root = config.resolve(config.baostock_raw_dir, config_path)
    raw_root.mkdir(parents=True, exist_ok=True)
    default_start = pd.Timestamp(config.download_start)
    default_end = effective_download_end(config.download_end)
    codes = sorted(set(str(code) for code in stock_codes))
    outputs: list[Path] = []
    with BaoStockSession() as session:
        for number, stock_code in enumerate(codes, start=1):
            start, end = (
                bounds_by_code.get(stock_code, (default_start, default_end))
                if bounds_by_code is not None
                else (default_start, default_end)
            )
            safe = _safe_code(stock_code)
            destination = raw_root / f"{safe}.csv.gz"
            metadata_path = raw_root / f"{safe}.metadata.json"
            if not force and destination.exists() and _cache_is_complete(
                metadata_path, start, end
            ):
                outputs.append(destination)
                continue
            last_error: Exception | None = None
            for attempt in range(config.max_download_attempts):
                try:
                    frame = session.stock_price_validation(stock_code, start, end)
                    if frame.empty:
                        raise RuntimeError(
                            "BaoStock returned an empty history for a historical CSI300 stock"
                        )
                    break
                except Exception as exc:
                    last_error = exc
                    session.__exit__(None, None, None)
                    time.sleep(min(2.0 * (attempt + 1), 10.0))
                    session.__enter__()
            else:
                raise RuntimeError(f"BaoStock download failed for {stock_code}") from last_error
            frame.to_csv(
                destination,
                index=False,
                encoding=config.output_encoding,
                compression="gzip",
            )
            dates = pd.to_datetime(frame.get("date"), errors="coerce")
            _write_json(
                metadata_path,
                {
                    "stock_code": stock_code,
                    "source": "BaoStock query_history_k_data_plus",
                    "frequency": "d",
                    "adjustflag": "2",
                    "requested_start": start.strftime("%Y-%m-%d"),
                    "requested_end": end.strftime("%Y-%m-%d"),
                    "observed_start": dates.min().strftime("%Y-%m-%d") if dates.notna().any() else None,
                    "observed_end": dates.max().strftime("%Y-%m-%d") if dates.notna().any() else None,
                    "row_count": len(frame),
                    "download_status": "complete",
                    "downloaded_at": pd.Timestamp.now().isoformat(),
                },
            )
            outputs.append(destination)
            if number % 25 == 0 or number == len(codes):
                print(f"  BaoStock qfq caches: {number}/{len(codes)}", flush=True)
            if config.request_pause_seconds > 0:
                time.sleep(config.request_pause_seconds)
    return outputs


def build_baostock_processed(
    stock_codes: Iterable[str],
    config: PriceLayerConfig,
    config_path: str | Path,
) -> list[Path]:
    raw_root = config.resolve(config.baostock_raw_dir, config_path)
    output_root = config.resolve(config.baostock_processed_dir, config_path)
    output_root.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    codes = sorted(set(str(code) for code in stock_codes))
    for number, stock_code in enumerate(codes, start=1):
        safe = _safe_code(stock_code)
        frame = pd.read_csv(
            raw_root / f"{safe}.csv.gz", dtype={"code": "string"}, low_memory=False
        )
        rename = {"date": "trade_date", "turn": "turnover_rate"}
        frame = frame.rename(columns=rename)
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        for column in [
            "open",
            "high",
            "low",
            "close",
            "preclose",
            "volume",
            "amount",
            "turnover_rate",
            "pctChg",
            "tradestatus",
            "isST",
        ]:
            if column not in frame.columns:
                frame[column] = np.nan
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["stock_code"] = stock_code
        for source, target in [
            ("open", "adjusted_open"),
            ("high", "adjusted_high"),
            ("low", "adjusted_low"),
            ("close", "adjusted_close"),
        ]:
            frame[target] = frame[source]
        suspended = frame["tradestatus"].eq(0)
        frame["suspension_status"] = suspended.astype("Int64")
        frame["ST_status"] = frame["isST"]
        frame["industry"] = pd.NA
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
        destination = output_root / f"{safe}.csv.gz"
        frame[PROCESSED_COLUMNS].to_csv(
            destination,
            index=False,
            encoding=config.output_encoding,
            compression="gzip",
        )
        outputs.append(destination)
        if number % 50 == 0 or number == len(codes):
            print(f"  BaoStock processed qfq: {number}/{len(codes)}", flush=True)
    return outputs


def yahoo_symbol(stock_code: str) -> str:
    exchange, code = stock_code.split(".")
    return f"{code}.SS" if exchange.lower() == "sh" else f"{code}.SZ"


def download_yahoo_histories(
    stock_codes: Iterable[str],
    config: PriceLayerConfig,
    config_path: str | Path,
    *,
    force: bool = False,
) -> list[Path]:
    import yfinance as yf

    output_root = config.resolve(config.yahoo_raw_dir, config_path)
    output_root.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(output_root / "cache"))
    start = pd.Timestamp(config.download_start)
    end_exclusive = pd.Timestamp(config.download_end) + pd.Timedelta(days=1)
    codes = [str(code) for code in stock_codes]
    outputs: list[Path] = []
    for number, stock_code in enumerate(codes, start=1):
        safe = _safe_code(stock_code)
        destination = output_root / f"{safe}.csv.gz"
        metadata_path = output_root / f"{safe}.metadata.json"
        if not force and destination.exists() and _cache_is_complete(
            metadata_path, start, pd.Timestamp(config.download_end)
        ):
            outputs.append(destination)
            continue
        symbol = yahoo_symbol(stock_code)
        last_error: Exception | None = None
        for attempt in range(config.max_download_attempts):
            try:
                frame = yf.download(
                    symbol,
                    start=start.strftime("%Y-%m-%d"),
                    end=end_exclusive.strftime("%Y-%m-%d"),
                    interval="1d",
                    auto_adjust=False,
                    actions=True,
                    repair=False,
                    keepna=True,
                    progress=False,
                    threads=False,
                    timeout=config.request_timeout_seconds,
                    multi_level_index=False,
                )
                if frame is None:
                    frame = pd.DataFrame()
                break
            except Exception as exc:
                last_error = exc
                time.sleep(min(2.0 * (attempt + 1), 10.0))
        else:
            raise RuntimeError(f"Yahoo download failed for {stock_code}") from last_error
        frame = frame.reset_index()
        frame.columns = [str(value) for value in frame.columns]
        frame["stock_code"] = stock_code
        frame.to_csv(
            destination,
            index=False,
            encoding=config.output_encoding,
            compression="gzip",
        )
        date_column = "Date" if "Date" in frame.columns else None
        dates = pd.to_datetime(frame[date_column], errors="coerce") if date_column else pd.Series(dtype="datetime64[ns]")
        _write_json(
            metadata_path,
            {
                "stock_code": stock_code,
                "yahoo_symbol": symbol,
                "source": "Yahoo Finance sampled validation via yfinance",
                "requested_start": start.strftime("%Y-%m-%d"),
                "requested_end": pd.Timestamp(config.download_end).strftime("%Y-%m-%d"),
                "observed_start": dates.min().strftime("%Y-%m-%d") if dates.notna().any() else None,
                "observed_end": dates.max().strftime("%Y-%m-%d") if dates.notna().any() else None,
                "row_count": len(frame),
                "download_status": "complete" if len(frame) else "empty_response",
                "downloaded_at": pd.Timestamp.now().isoformat(),
            },
        )
        outputs.append(destination)
        print(f"  Yahoo sample: {number}/{len(codes)}", flush=True)
        if config.request_pause_seconds > 0:
            time.sleep(config.request_pause_seconds)
    return outputs
