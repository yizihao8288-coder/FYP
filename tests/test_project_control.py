"""Guardrails for the dual-channel research control system."""

from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("project_control", PROJECT / "tools" / "project_control.py")
assert SPEC and SPEC.loader
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


def test_frozen_baseline_identity_and_shape() -> None:
    assert CONTROL.sha256(CONTROL.BASELINE) == CONTROL.EXPECTED_BASELINE_SHA256
    assert CONTROL.sha256(CONTROL.EVENT_POOL) == CONTROL.EXPECTED_EVENT_POOL_SHA256
    data = CONTROL.pd.read_csv(CONTROL.BASELINE, encoding="utf-8-sig")
    assert len(data) == 193
    assert data["stock_code"].nunique() == 160
    assert not data.duplicated(["stock_code", "event_date"]).any()


def test_sensitive_and_regenerable_paths_are_not_routed_to_github() -> None:
    raw = CONTROL.classify(PROJECT / "data" / "raw" / "eastmoney" / "hfq" / "sample.csv.gz")
    runtime = CONTROL.classify(PROJECT / ".runtime" / "cache" / "sample.bin")
    frozen = CONTROL.classify(PROJECT / "outputs" / "hfq_final" / "final_analysis_dataset_v1.csv")
    assert raw["channel"] == "DataVault"
    assert runtime["channel"] == "Regenerable"
    assert frozen["channel"] == "DataVault"
    assert frozen["status"] == "frozen"


def test_control_outputs_and_explanations_are_registered_for_github() -> None:
    generated = CONTROL.classify(PROJECT / "docs" / "Research_Project_Map.xlsx")
    explanation = CONTROL.classify(PROJECT / "outputs" / "README.md")
    assert generated["channel"] == "GitHub"
    assert explanation["channel"] == "GitHub"


def test_all_required_directory_explanations_exist() -> None:
    required = [
        "README.md",
        "docs/README.md",
        "catalog/README.md",
        "config/README.md",
        "data/README.md",
        "data/manifests/README.md",
        "results/README.md",
        "results/baseline_v1/README.md",
        "results/robustness_v1/README.md",
        "results/audit_20260919/README.md",
        "results/paper_v1/README.md",
        "results/paper_v1/Tables/README.md",
        "results/paper_v1/Figures/README.md",
        "src/README.md",
        "src/csi300_events/README.md",
        "tests/README.md",
        "tools/README.md",
        "outputs/README.md",
    ]
    assert all((PROJECT / item).is_file() for item in required)

