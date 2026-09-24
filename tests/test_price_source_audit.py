from __future__ import annotations

from dataclasses import replace

import pandas as pd

from csi300_events.eastmoney_layer import (
    _EASTMONEY_HISTORY_HOSTS,
    _EASTMONEY_RELAY_HOSTS,
    _cache_is_complete,
    _extract_jina_json,
    _normalise_eastmoney,
)
from csi300_events.methodology import load_methodology_config
from csi300_events.price_source_audit import _difference_reason, choose_yahoo_sample


def test_normalise_eastmoney_keeps_identifiers_and_numeric_prices() -> None:
    source = pd.DataFrame(
        {
            "日期": ["2020-01-02"],
            "股票代码": ["000001"],
            "开盘": ["10.0"],
            "收盘": ["10.2"],
            "最高": ["10.3"],
            "最低": ["9.9"],
            "成交量": [100],
            "成交额": [1000],
            "振幅": [4.0],
            "涨跌幅": [2.0],
            "涨跌额": [0.2],
            "换手率": [1.5],
        }
    )
    result = _normalise_eastmoney(source, "sz.000001")
    assert result.loc[0, "stock_code"] == "sz.000001"
    assert result.loc[0, "close"] == 10.2
    assert result.loc[0, "trade_date"] == pd.Timestamp("2020-01-02")


def test_eastmoney_history_fallbacks_stay_on_eastmoney_domains() -> None:
    assert "push2his.eastmoney.com" in _EASTMONEY_HISTORY_HOSTS
    assert "33.push2his.eastmoney.com" in _EASTMONEY_HISTORY_HOSTS
    assert all(host.endswith(".eastmoney.com") for host in _EASTMONEY_HISTORY_HOSTS)
    assert all(host.endswith("eastmoney.com") for host in _EASTMONEY_RELAY_HOSTS)


def test_empty_eastmoney_response_is_not_a_completed_cache(tmp_path) -> None:
    metadata = tmp_path / "stock.metadata.json"
    metadata.write_text(
        '{"requested_start":"2020-01-01","requested_end":"2020-12-31",'
        '"download_status":"complete_empty_response"}',
        encoding="utf-8",
    )
    assert not _cache_is_complete(
        metadata, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-11-30")
    )


def test_extract_jina_json_keeps_eastmoney_payload() -> None:
    value = _extract_jina_json(
        "Title: x\n\nURL Source: http://example\n\nMarkdown Content:\n"
        '{"rc":0,"data":{"klines":["2020-01-02,1,2"]}}'
    )
    assert value["rc"] == 0
    assert value["data"]["klines"][0].startswith("2020-01-02")


def test_methodology_accepts_one_consistent_hfq_series() -> None:
    config = load_methodology_config("config/methodology_config.json")
    replace(
        config,
        current_adjustment_type="hfq",
        target_adjustment_type="hfq",
    ).validate()


def test_yahoo_sample_is_deterministic_and_stratified() -> None:
    rows = []
    audit_rows = []
    for index in range(60):
        code = f"sh.{600000 + index:06d}"
        year = 2010 if index < 20 else 2017 if index < 40 else 2023
        rows.append({"stock_code": code, "in_date": pd.Timestamp(year, 1, 1)})
        audit_rows.append(
            {
                "stock_code": code,
                "corporate_action_candidate_count": index % 2,
            }
        )
    membership = pd.DataFrame(rows)
    audit = pd.DataFrame(audit_rows)
    first = choose_yahoo_sample(membership, audit, 30, 7)
    second = choose_yahoo_sample(membership, audit, 30, 7)
    assert first["stock_code"].tolist() == second["stock_code"].tolist()
    assert len(first) == 30
    assert set(first["era"]) == {
        "early_2009_2014",
        "middle_2015_2020",
        "recent_2021_2026",
    }
    assert set(first["corporate_action_group"]) == {
        "with_corporate_action",
        "without_corporate_action",
    }


def test_difference_reason_labels_threshold_and_cooldown() -> None:
    assert (
        _difference_reason(
            {"outcome": "selected_after_cooldown"},
            {"outcome": "bandwidth_threshold_failed"},
            True,
            False,
        )
        == "Bandwidth阈值判定差异"
    )
    assert (
        _difference_reason(
            {"outcome": "selected_after_cooldown"},
            {"outcome": "selected_before_cooldown"},
            True,
            False,
        )
        == "60交易日cooldown或前序事件差异"
    )
