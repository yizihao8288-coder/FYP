from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .acquisition import (
    download_hs300_membership,
    download_index_history,
    download_stock_history,
)
from .config import EventStudyConfig
from .methodology import MethodologyConfig, load_methodology_config


def _acquisition_config(config: MethodologyConfig) -> EventStudyConfig:
    """Adapt the single frozen methodology file to the legacy downloader API."""
    return EventStudyConfig(
        start_date=config.sample_start_fallback,
        end_date=config.sample_end,
        raw_dir=config.raw_dir,
        processed_dir="../data/processed",
        processed_stock_dir=config.processed_stock_dir,
        membership_path=config.membership_path,
        calendar_path=config.calendar_path,
        history_padding_calendar_days=config.history_padding_calendar_days,
        download_workers=config.download_workers,
        output_encoding=config.output_encoding,
    )


def refresh_inputs(config: MethodologyConfig, config_path: str | Path) -> None:
    acquisition = _acquisition_config(config)
    start = pd.Timestamp(config.sample_start_fallback) - pd.Timedelta(
        days=config.history_padding_calendar_days
    )
    end = min(pd.Timestamp(config.sample_end), pd.Timestamp.now().normalize())
    print(
        f"Refreshing free cached inputs for {config.sample_start_fallback}..{config.sample_end}; "
        f"history starts {start:%Y-%m-%d}",
        flush=True,
    )
    _, calendar_path = download_index_history(
        acquisition, config_path, start, end
    )
    calendar = pd.read_csv(calendar_path)
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="raise")
    membership_path = download_hs300_membership(
        acquisition, config_path, calendar
    )
    membership = pd.read_csv(membership_path, dtype={"stock_code": "string"})
    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    download_stock_history(
        codes, acquisition, config_path, start, end
    )
    print(
        f"Cached {len(codes)} historical CSI300 stocks through {end:%Y-%m-%d}.",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh only free event-selection inputs; no future outcomes."
    )
    parser.add_argument("--config", default="config/methodology_config.json")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    refresh_inputs(load_methodology_config(config_path), config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
