from __future__ import annotations

import argparse
from pathlib import Path

from .adaptive_pipeline import run_adaptive_screening
from .data_quality import run_data_quality_audit
from .methodology import load_methodology_config
from .refresh_inputs import refresh_inputs
from .sample_quality_audit import run_sample_quality_audit


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the frozen adaptive CSI300 event sample and data-quality audit. "
            "No future returns or regressions are calculated."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_project_root() / "config" / "methodology_config.json",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Extend free raw-data caches before screening.",
    )
    parser.add_argument(
        "--screen-only",
        action="store_true",
        help="Run event screening without BaoStock cross-validation.",
    )
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    config = load_methodology_config(config_path)
    if args.refresh:
        refresh_inputs(config, config_path)
    screening_paths = run_adaptive_screening(config, config_path)
    print("\nAdaptive screening outputs:")
    for label, path in screening_paths.items():
        print(f"  {label}: {path}")
    if not args.screen_only:
        audit_paths = run_data_quality_audit(config, config_path)
        print("\nData-quality outputs:")
        for label, path in audit_paths.items():
            print(f"  {label}: {path}")
        sample_audit_paths = run_sample_quality_audit(config, config_path)
        print("\nEvent-sample quality-audit outputs:")
        for label, path in sample_audit_paths.items():
            print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
