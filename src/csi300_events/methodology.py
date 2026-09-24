from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EventSpecification:
    name: str
    bandwidth_threshold: float
    breakout_threshold: float
    breakout_operator: str


@dataclass(frozen=True)
class MethodologyConfig:
    universe: str
    sample_start_primary: str
    sample_start_fallback: str
    sample_end: str
    min_consolidation_duration: int
    max_consolidation_duration: int
    trend_equivalent_days: int
    trend120_threshold: float
    distress_drawdown_threshold: float
    peak_lookback_before_consolidation: int
    cooldown_market_days: int
    min_valid_price_ratio: float
    max_consecutive_suspension: int
    peak_window_min_valid_ratio: float
    price_source: str
    current_adjustment_type: str
    target_adjustment_type: str
    membership_source: str
    status_source: str
    industry_used_for_selection: bool
    turnover_used_for_selection: bool
    cross_validation_source: str
    cross_validation_random_stock_count: int
    cross_validation_random_seed: int
    history_padding_calendar_days: int
    download_workers: int
    output_encoding: str
    specifications: dict[str, EventSpecification]
    processed_stock_dir: str
    membership_path: str
    calendar_path: str
    raw_dir: str
    output_dir: str

    @property
    def required_history_days(self) -> int:
        return self.max_consolidation_duration + self.peak_lookback_before_consolidation

    def resolve(self, value: str, config_path: str | Path) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = Path(config_path).resolve().parent / candidate
        return candidate.resolve()

    def validate(self) -> None:
        if self.universe != "historical_CSI300_t_minus_1":
            raise ValueError("The frozen universe must be historical_CSI300_t_minus_1")
        if self.sample_start_primary != "2016-01-01" or self.sample_start_fallback != "2016-01-01":
            raise ValueError("Event sample start is frozen at 2016-01-01")
        if self.sample_end != "2026-06-08":
            raise ValueError("Event sample end is frozen at 2026-06-08")
        if self.min_consolidation_duration != 120:
            raise ValueError("Minimum consolidation duration is frozen at 120 market days")
        if self.max_consolidation_duration != 252:
            raise ValueError("Maximum consolidation duration is frozen at 252 market days")
        if self.peak_lookback_before_consolidation != 252:
            raise ValueError("Peak lookback is frozen at 252 pre-consolidation market days")
        if self.trend_equivalent_days != 120:
            raise ValueError("Trend must be expressed as a 120-market-day equivalent")
        if self.cooldown_market_days != 60:
            raise ValueError("Cooldown is frozen at 60 market days")
        if not math.isclose(self.trend120_threshold, 0.05):
            raise ValueError("Trend120 threshold is frozen at 5%")
        if not math.isclose(self.distress_drawdown_threshold, -0.30):
            raise ValueError("Distress drawdown threshold is frozen at -30%")
        if not math.isclose(self.min_valid_price_ratio, 0.95):
            raise ValueError("Consolidation valid-price ratio is frozen at 95%")
        if not math.isclose(self.peak_window_min_valid_ratio, 0.95):
            raise ValueError("Peak-window valid-price ratio is frozen at 95%")
        if self.max_consecutive_suspension != 5:
            raise ValueError("Maximum consecutive suspension is frozen at five market days")
        if self.history_padding_calendar_days < 750:
            raise ValueError(
                "Adaptive 252+252 market-day history needs at least 750 calendar days of padding"
            )
        if not 1 <= self.download_workers <= 8:
            raise ValueError("download_workers must be between 1 and 8")
        if not 0 < self.min_valid_price_ratio <= 1:
            raise ValueError("min_valid_price_ratio must be in (0, 1]")
        if not 0 < self.peak_window_min_valid_ratio <= 1:
            raise ValueError("peak_window_min_valid_ratio must be in (0, 1]")
        if (
            self.current_adjustment_type != self.target_adjustment_type
            or self.target_adjustment_type not in {"qfq", "hfq"}
        ):
            raise ValueError(
                "Event prices must use one internally consistent qfq or hfq convention"
            )
        expected_specs = {"strict", "baseline", "relaxed"}
        if set(self.specifications) != expected_specs:
            raise ValueError("specifications must contain strict, baseline and relaxed")
        for specification in self.specifications.values():
            if specification.breakout_operator not in {"ge", "gt"}:
                raise ValueError("breakout_operator must be ge or gt")
        frozen_specs = {
            "strict": (0.20, 0.01, "ge"),
            "baseline": (0.25, 0.005, "ge"),
            "relaxed": (0.30, 0.0, "gt"),
        }
        for name, expected in frozen_specs.items():
            actual = self.specifications[name]
            if not (
                math.isclose(actual.bandwidth_threshold, expected[0])
                and math.isclose(actual.breakout_threshold, expected[1])
                and actual.breakout_operator == expected[2]
            ):
                raise ValueError(f"{name} specification does not match the frozen methodology")


def load_methodology_config(path: str | Path) -> MethodologyConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    specifications = {
        name: EventSpecification(name=name, **values)
        for name, values in raw.pop("specifications").items()
    }
    config = MethodologyConfig(specifications=specifications, **raw)
    config.validate()
    return config
