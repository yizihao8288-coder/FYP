from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .eastmoney_layer import (
    build_eastmoney_processed,
    build_eastmoney_trading_calendar,
    download_baostock_status_histories,
    download_eastmoney_histories,
    effective_download_end,
    load_price_layer_config,
    research_bounds_by_stock,
)
from .hfq_final_sample import run_final_hfq_sample
from .methodology import load_methodology_config


def run_formal_pipeline(
    price_config_path: str | Path,
    final_config_path: str | Path,
    *,
    refresh: bool = False,
    rebuild_processed: bool = False,
) -> dict[str, Path]:
    """Run the one supported formal path: Eastmoney hfq -> events -> returns."""
    price_path = Path(price_config_path).resolve()
    price = load_price_layer_config(price_path)
    methodology_path = price.resolve(price.methodology_config, price_path)
    methodology = load_methodology_config(methodology_path)
    membership = pd.read_csv(
        methodology.resolve(methodology.membership_path, methodology_path),
        dtype={"stock_code": "string"},
    )
    codes = sorted(membership["stock_code"].dropna().astype(str).unique())
    effective_end = effective_download_end(price.download_end)
    bounds = research_bounds_by_stock(
        membership,
        price.download_start,
        effective_end.strftime("%Y-%m-%d"),
        sample_start=methodology.sample_start_primary,
        history_padding_calendar_days=methodology.history_padding_calendar_days,
        future_padding_calendar_days=120,
    )

    if refresh:
        download_eastmoney_histories(
            codes,
            price,
            price_path,
            bounds_by_code=bounds,
        )
        download_baostock_status_histories(
            codes,
            price,
            price_path,
            bounds_by_code=bounds,
        )
    if refresh or rebuild_processed:
        build_eastmoney_processed(codes, price, price_path)
        build_eastmoney_trading_calendar(
            price,
            price_path,
            methodology.resolve(methodology.calendar_path, methodology_path),
        )

    return run_final_hfq_sample(final_config_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the formal 2016-2026 Eastmoney hfq event and return dataset."
    )
    parser.add_argument("--price-config", default="config/eastmoney_price_audit.json")
    parser.add_argument("--final-config", default="config/hfq_final_sample.json")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--rebuild-processed", action="store_true")
    args = parser.parse_args()
    outputs = run_formal_pipeline(
        args.price_config,
        args.final_config,
        refresh=args.refresh,
        rebuild_processed=args.rebuild_processed,
    )
    print("Formal pipeline outputs:")
    for name, path in outputs.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
