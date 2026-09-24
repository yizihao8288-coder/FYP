from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class EventStudyConfig:
    """Downloader-only settings adapted from the frozen methodology config."""

    start_date: str = "2009-01-01"
    end_date: str = "2026-09-04"
    raw_dir: str = "../data/raw"
    processed_dir: str = "../data/processed"
    processed_stock_dir: str = "../data/processed/stocks"
    history_padding_calendar_days: int = 800
    download_workers: int = 4
    membership_path: str = "../data/csi300_membership.csv"
    calendar_path: str = "../data/trading_calendar.csv"
    output_encoding: str = "utf-8-sig"

    def validate(self) -> None:
        start = pd.Timestamp(self.start_date)
        end = pd.Timestamp(self.end_date)
        if start > end:
            raise ValueError("start_date cannot be later than end_date")
        if self.history_padding_calendar_days < 750:
            raise ValueError("history_padding_calendar_days must be at least 750")
        if not 1 <= self.download_workers <= 8:
            raise ValueError("download_workers must be between 1 and 8")


def load_config(path: str | Path) -> EventStudyConfig:
    config_path = Path(path)
    raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    allowed = {field.name for field in fields(EventStudyConfig)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(unknown)}")
    config = EventStudyConfig(**raw)
    config.validate()
    return config


def resolve_config_path(value: str, config_path: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = Path(config_path).resolve().parent / path
    return path.resolve()
