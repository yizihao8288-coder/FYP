"""Project inventory, dual-channel vault sync, and provenance checks.

This utility never changes the frozen event definitions or analysis values.  It
copies files into a D-drive vault, builds registries, and verifies hashes.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable

import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
VAULT = Path(os.environ.get("FYP_DATA_ROOT", r"D:\FYP_DataVault\csi300_breakout_events"))
CATALOG = PROJECT / "catalog"
MANIFESTS = PROJECT / "data" / "manifests"
DOCS = PROJECT / "docs"
RESULTS = PROJECT / "results"
BASELINE = PROJECT / "outputs" / "hfq_final" / "final_analysis_dataset_v1.csv"
EVENT_POOL = PROJECT / "outputs" / "hfq_final" / "final_event_pool_v1.csv"
EXPECTED_BASELINE_SHA256 = "94d52af2f33639cc336a6967d3fecfdccc2a2fc3f8b6ae265d116ae457ce0f76"
EXPECTED_EVENT_POOL_SHA256 = "6e8485a4716caa6035bf0616d0b4dd9b7b91da8adf7c611053933460f836a889"
DATE_STAMP = "20260924"

GENERATED_SELF_PATHS = {
    "csi300_breakout_events/catalog/migration_inventory.csv",
    "csi300_breakout_events/catalog/workbook_payload.json",
    "csi300_breakout_events/catalog/unregistered_files_report.md",
    "csi300_breakout_events/catalog/Migration_Review.xlsx",
    "csi300_breakout_events/docs/Research_Project_Map.xlsx",
    "csi300_breakout_events/data/manifests/project_channel_manifest.json",
    "csi300_breakout_events/data/manifests/vault_channel_manifest.json",
    "csi300_breakout_events/data/manifests/verification_report.json",
    "csi300_breakout_events/docs/PROJECT_SYNC_STATUS.md",
}
GENERATED_SELF_PATHS_LOWER = {item.lower() for item in GENERATED_SELF_PATHS}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def relative_to_workspace(path: Path) -> str:
    return path.relative_to(WORKSPACE).as_posix()


def scan_tree(root: Path, skip_git: bool = True) -> list[Path]:
    files: list[Path] = []

    def visit(folder: Path) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda item: item.name.lower())
        except OSError:
            return
        for entry in entries:
            if skip_git and entry.name == ".git":
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
                is_reparse = bool(getattr(stat, "st_file_attributes", 0) & 1024)
                if is_reparse:
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
            except OSError:
                continue
    visit(root)
    return files


def classify(path: Path) -> dict[str, str]:
    rel = relative_to_workspace(path)
    low = rel.lower()
    project_prefix = "csi300_breakout_events/"
    filename = path.name.lower()

    if low in GENERATED_SELF_PATHS_LOWER:
        return {"channel": "GitHub", "category": "control_generated", "status": "validated",
                "dataset_id": "", "purpose": "研究控制系统自动生成的索引或验证产物",
                "destination": rel, "delete_policy": "保留最新验证版本"}
    if low.startswith(project_prefix + ".git/"):
        return {"channel": "Regenerable", "category": "git_internal", "status": "generated",
                "dataset_id": "", "purpose": "Git内部对象，不进入研究清单",
                "destination": "local .git", "delete_policy": "由Git管理"}
    if low.startswith(project_prefix + ".runtime/") or "/__pycache__/" in low or filename.endswith((".pyc", ".pyo")):
        return {"channel": "Regenerable", "category": "runtime", "status": "generated",
                "dataset_id": "", "purpose": "依赖环境或Python缓存，可由requirements-lock重建",
                "destination": "not synced; rebuild from lock file", "delete_policy": "可重建，不手工编辑"}
    if low.startswith(project_prefix + "data/raw/eastmoney/"):
        dataset = "D001" if low.startswith(project_prefix + "data/raw/eastmoney/hfq/") else "D002"
        return {"channel": "DataVault", "category": "raw_data", "status": "frozen_source",
                "dataset_id": dataset, "purpose": "东方财富股票日线原始缓存",
                "destination": "raw/eastmoney", "delete_policy": "禁止删除；原始证据"}
    if low.startswith(project_prefix + "data/raw/baostock/status/"):
        return {"channel": "DataVault", "category": "raw_data", "status": "frozen_source",
                "dataset_id": "D003", "purpose": "BaoStock交易状态与ST历史缓存",
                "destination": "raw/baostock/status", "delete_policy": "禁止删除；原始证据"}
    if low.startswith(project_prefix + "data/raw/baostock/hs300_snapshots/") or low.startswith(project_prefix + "data/raw/membership/"):
        return {"channel": "DataVault", "category": "raw_data", "status": "frozen_source",
                "dataset_id": "D004", "purpose": "沪深300历史成分与候选调整日期",
                "destination": "raw/membership", "delete_policy": "禁止删除；样本身份依据"}
    if low.startswith(project_prefix + "data/raw/coverage/"):
        return {"channel": "DataVault", "category": "raw_metadata", "status": "validated",
                "dataset_id": "D005", "purpose": "股票数据覆盖范围记录",
                "destination": "raw/coverage", "delete_policy": "随原始数据保留"}
    if low.startswith(project_prefix + "data/processed/eastmoney/hfq/"):
        return {"channel": "DataVault", "category": "processed_data", "status": "frozen_input",
                "dataset_id": "D006", "purpose": "标准化后复权股票日线，供正式事件识别读取",
                "destination": "processed/eastmoney/hfq", "delete_policy": "冻结版本禁止覆盖"}
    if low.startswith(project_prefix + "data/processed/"):
        return {"channel": "DataVault", "category": "processed_data", "status": "validated",
                "dataset_id": "D007", "purpose": "交易日历和处理层元数据",
                "destination": "processed", "delete_policy": "可由原始数据重建但当前版本需保留"}
    if low.endswith("outputs/hfq_final/final_event_pool_v1.csv") or low.endswith("final_event_pool_v1.csv.sha256"):
        return {"channel": "DataVault", "category": "frozen_data", "status": "frozen",
                "dataset_id": "D008", "purpose": "193个主规格事件的冻结事件池",
                "destination": "frozen/final_event_pool_v1", "delete_policy": "绝对禁止覆盖或删除"}
    if low.endswith("outputs/hfq_final/final_analysis_dataset_v1.csv"):
        return {"channel": "DataVault", "category": "frozen_data", "status": "frozen",
                "dataset_id": "D009", "purpose": "最终分析数据，一事件一行",
                "destination": "frozen/final_analysis_dataset_v1.csv", "delete_policy": "绝对禁止覆盖或删除"}
    if low.startswith(project_prefix + "outputs/research_audit_20260919/sources/"):
        return {"channel": "DataVault", "category": "external_source", "status": "validated",
                "dataset_id": "D010", "purpose": "市场调整与当前行业诊断的外部审计数据",
                "destination": "external_sources/research_audit_20260919", "delete_policy": "保留来源元数据与hash"}
    if low == project_prefix + "outputs/readme.md":
        return {"channel": "GitHub", "category": "documentation", "status": "validated",
                "dataset_id": "", "purpose": "解释旧输出为何只归档、正式结果应从何处读取",
                "destination": rel, "delete_policy": "由Git版本管理"}
    if low.startswith(project_prefix + "outputs/"):
        purpose = "既有输出快照；正式子集另复制到results"
        if filename == "threshold_robustness.xlsx":
            purpose = "已知归档缺陷：扩展名为xlsx但内容实际为JSON"
        return {"channel": "Archive", "category": "legacy_output", "status": "archived",
                "dataset_id": "D014", "purpose": purpose,
                "destination": f"archive/project_outputs_snapshot_{DATE_STAMP}", "delete_policy": "原路径暂不删除"}
    if low.startswith(project_prefix + "final_empirical_result/"):
        return {"channel": "GitHub", "category": "paper_result", "status": "validated",
                "dataset_id": "", "purpose": "论文级Table1-7、Figure1-6与结果摘要",
                "destination": "results/paper_v1", "delete_policy": "保留；已复制到规范目录"}
    if low.startswith(project_prefix + "results/"):
        return {"channel": "GitHub", "category": "curated_result", "status": "validated",
                "dataset_id": "", "purpose": "可追溯的精选研究结果",
                "destination": rel, "delete_policy": "按版本保留"}
    if any(low.startswith(project_prefix + prefix) for prefix in ("src/", "tests/", "tools/", "docs/", "catalog/", "config/")):
        category = low.split("/", 2)[1]
        return {"channel": "GitHub", "category": category, "status": "validated",
                "dataset_id": "", "purpose": "研究代码、测试、配置或说明",
                "destination": rel, "delete_policy": "由Git版本管理"}
    if low.startswith(project_prefix + "data/manifests/") or low.endswith("data/readme.md"):
        return {"channel": "GitHub", "category": "manifest", "status": "validated",
                "dataset_id": "", "purpose": "数据说明与hash索引，不包含行情记录",
                "destination": rel, "delete_policy": "由Git版本管理"}
    if low.startswith(project_prefix) and path.suffix.lower() in {".py", ".ps1", ".toml", ".md", ".txt", ".json", ".gitignore", ".gitattributes"}:
        return {"channel": "GitHub", "category": "project_file", "status": "validated",
                "dataset_id": "", "purpose": "项目入口、脚本、参数或研究说明",
                "destination": rel, "delete_policy": "由Git版本管理"}
    if low.startswith("_obsolete_csi300_fixed120_20260906/"):
        return {"channel": "Archive", "category": "obsolete_method", "status": "deprecated",
                "dataset_id": "D012", "purpose": "旧固定120日事件方案，不能与当前baseline混用",
                "destination": "archive/obsolete_csi300_fixed120_20260906", "delete_policy": "保留至论文完成"}
    if low.startswith("trend_volume_research/"):
        return {"channel": "Archive", "category": "separate_project", "status": "separate",
                "dataset_id": "D015", "purpose": "独立趋势成交量演示项目，不属于当前FYP证据链",
                "destination": "archive/separate_project_trend_volume_research", "delete_policy": "独立保存，不并入本研究"}
    if low.startswith((".tmp/", "tmp/")) or filename.startswith("~$"):
        return {"channel": "Delete Candidate", "category": "temporary", "status": "pending_review",
                "dataset_id": "", "purpose": "临时或锁文件，尚未删除",
                "destination": "original location pending approval", "delete_policy": "用户批准后删除"}
    if any(low.startswith(prefix) for prefix in ["01_", "02_", "03_", "bnbu fyp format and handbook/", "coding/"]) or filename.endswith(".doc"):
        return {"channel": "DataVault", "category": "reference_material", "status": "reference",
                "dataset_id": "D013", "purpose": "课程、格式手册或写作参考资料",
                "destination": "external_sources/fyp_reference_materials", "delete_policy": "保留参考"}
    return {"channel": "Archive", "category": "uncategorized_legacy", "status": "reviewed_archive",
            "dataset_id": "", "purpose": "未进入正式证据链的历史项目文件",
            "destination": "archive/workspace_misc", "delete_policy": "保留并等待人工复核"}


def copy_if_changed(source: Path, destination: Path) -> tuple[int, int]:
    copied = skipped = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size == source.stat().st_size and sha256(destination) == sha256(source):
        return copied, skipped + 1
    shutil.copy2(source, destination)
    return copied + 1, skipped


def copy_tree(source: Path, destination: Path) -> tuple[int, int]:
    copied = skipped = 0
    for path in scan_tree(source, skip_git=True):
        rel = path.relative_to(source)
        c, s = copy_if_changed(path, destination / rel)
        copied += c
        skipped += s
    return copied, skipped


def sync_vault() -> None:
    if VAULT.drive.upper() != "D:":
        raise RuntimeError(f"Vault must remain on D drive: {VAULT}")
    mappings: list[tuple[Path, Path]] = [
        (PROJECT / "data" / "raw", VAULT / "raw"),
        (PROJECT / "data" / "processed", VAULT / "processed"),
        (PROJECT / "outputs" / "research_audit_20260919" / "sources", VAULT / "external_sources" / "research_audit_20260919"),
        (PROJECT / "outputs", VAULT / "archive" / f"project_outputs_snapshot_{DATE_STAMP}"),
        (PROJECT / "Final_Empirical_Result", VAULT / "archive" / f"Final_Empirical_Result_snapshot_{DATE_STAMP}"),
        (WORKSPACE / "_obsolete_csi300_fixed120_20260906", VAULT / "archive" / "obsolete_csi300_fixed120_20260906"),
        (WORKSPACE / "trend_volume_research", VAULT / "archive" / "separate_project_trend_volume_research"),
        (WORKSPACE / "BNBU FYP format and handbook", VAULT / "external_sources" / "fyp_reference_materials" / "BNBU FYP format and handbook"),
        (WORKSPACE / "01_保险精算与随机过程", VAULT / "external_sources" / "fyp_reference_materials" / "01_保险精算与随机过程"),
        (WORKSPACE / "02_金融市场_价格与成交量", VAULT / "external_sources" / "fyp_reference_materials" / "02_金融市场_价格与成交量"),
        (WORKSPACE / "03_医学统计", VAULT / "external_sources" / "fyp_reference_materials" / "03_医学统计"),
        (WORKSPACE / "Coding", VAULT / "external_sources" / "fyp_reference_materials" / "Coding"),
    ]
    total_copied = total_skipped = 0
    for source, destination in mappings:
        if not source.exists():
            continue
        c, s = copy_tree(source, destination)
        total_copied += c
        total_skipped += s
        print(f"SYNC {source} -> {destination}: copied={c}, unchanged={s}")
    frozen = [EVENT_POOL, BASELINE, PROJECT / "outputs" / "hfq_final" / "final_event_pool_v1.csv.sha256"]
    for source in frozen:
        if source.exists():
            c, s = copy_if_changed(source, VAULT / "frozen" / source.name)
            total_copied += c
            total_skipped += s
    stat_doc = WORKSPACE / "STAT4004 Final Year Project I (STAT) (3).doc"
    if stat_doc.exists():
        c, s = copy_if_changed(stat_doc, VAULT / "external_sources" / "fyp_reference_materials" / stat_doc.name)
        total_copied += c
        total_skipped += s
    (VAULT / "manifests").mkdir(parents=True, exist_ok=True)
    print(f"VAULT_COMPLETE copied={total_copied}, unchanged={total_skipped}, root={VAULT}")


def curate_results() -> None:
    mappings: list[tuple[Path, Path]] = []
    paper = PROJECT / "Final_Empirical_Result"
    if paper.exists():
        for path in scan_tree(paper):
            mappings.append((path, RESULTS / "paper_v1" / path.relative_to(paper)))
    baseline_names = [
        "descriptive_statistics.xlsx", "correlation_table.xlsx", "regression_results.xlsx",
        "paper_tables.xlsx", "data_quality_report.md", "hfq_final_report.md",
        "hfq_final_summary.json", "model_sample_counts.csv", "return_window_completeness.csv",
        "rvol_group_r20_mean.png", "rvol_group_r60_mean.png", "rvol_r20_scatter.png",
    ]
    for name in baseline_names:
        source = PROJECT / "outputs" / "hfq_final" / name
        if source.exists():
            mappings.append((source, RESULTS / "baseline_v1" / name))
    robustness = PROJECT / "outputs" / "hfq_final" / "robustness_summary.xlsx"
    if robustness.exists():
        mappings.append((robustness, RESULTS / "robustness_v1" / robustness.name))
    audit = PROJECT / "outputs" / "research_audit_20260919"
    audit_names = [
        "FINAL_PROJECT_STATUS.md", "Research_Master_Document.md", "Reviewer_Report.md",
        "variable_audit.md", "model_audit.md", "Project_File_Inventory.md", "output_hashes.json",
    ]
    for name in audit_names:
        source = audit / name
        if source.exists():
            mappings.append((source, RESULTS / "audit_20260919" / name))
    audit_figure = audit / "Figures" / "event_year_distribution.png"
    if audit_figure.exists():
        mappings.append((audit_figure, RESULTS / "audit_20260919" / "Figures" / audit_figure.name))
    for source, destination in mappings:
        copy_if_changed(source, destination)
    print(f"CURATED_RESULTS files={len(mappings)} root={RESULTS}")


def dataset_registry() -> list[dict[str, Any]]:
    baseline = pd.read_csv(BASELINE, encoding="utf-8-sig")
    rows = [
        ["D001", "东方财富HFQ原始缓存", "data/raw/eastmoney/hfq/**", "raw", "保留接口原始响应和下载证据", "Eastmoney through AKShare", "按股票和下载批次缓存，不改写", "src/csi300_events/eastmoney_layer.py", "外部接口", "hfq formal", "1,654 files", "827", "varies", "vault manifest", "DataVault/raw", "frozen_source", "yes", "no", "不能进入公开Git历史"],
        ["D002", "东方财富原始价格缓存", "data/raw/eastmoney/raw/**", "raw", "价格源审计与重建输入", "Eastmoney", "原始下载缓存", "src/csi300_events/eastmoney_layer.py", "外部接口", "source audit", "1,654 files", "827", "varies", "vault manifest", "DataVault/raw", "frozen_source", "yes", "no", "与正式HFQ处理层分离"],
        ["D003", "BaoStock状态历史", "data/raw/baostock/status/**", "raw", "ST、停牌和交易状态过滤依据", "BaoStock", "按股票保存日状态", "src/csi300_events/acquisition.py", "BaoStock API", "formal", "3,129 files", "multiple", "varies", "vault manifest", "DataVault/raw", "frozen_source", "yes", "no", "事件质量控制输入"],
        ["D004", "沪深300历史成员", "data/raw/baostock/hs300_snapshots/**; data/raw/membership/**", "raw", "确认事件日前一日指数成员身份", "BaoStock plus candidate dates", "历史快照与候选日期组合", "src/csi300_events/acquisition.py", "外部成员数据", "formal", "49 files", "multiple", "varies", "vault manifest", "DataVault/raw", "frozen_source", "yes", "no", "候选日期来源风险需披露"],
        ["D005", "数据覆盖记录", "data/raw/coverage/**", "metadata", "记录每只股票缓存覆盖范围", "project generated", "下载后写入覆盖元数据", "src/csi300_events/refresh_inputs.py", "D001-D004", "formal", "827 files", "827", "varies", "vault manifest", "DataVault/raw", "validated", "yes", "no", "用于发现数据缺口"],
        ["D006", "正式HFQ处理层", "data/processed/eastmoney/hfq/**", "processed", "正式事件识别的股票日线输入", "D001-D003", "标准化后复权OHLCV并合并状态", "src/csi300_events/formal_pipeline.py", "D001-D003", "hfq_final", "827 files", "827", "2013-10-24 onward", "vault manifest", "DataVault/processed", "frozen_input", "yes", "no", "不在GitHub上传行情记录"],
        ["D007", "交易日历", "data/processed/trading_calendar.csv", "processed", "定义t+h收益和事件间隔", "827只股票日期并集", "股票日期去重并集，不是交易所官方日历", "src/csi300_events/formal_pipeline.py", "D006", "hfq_final", "one file", "n.a.", "varies", "vault manifest", "DataVault/processed", "validated", "yes", "no", "论文需披露定义"],
        ["D008", "最终事件池v1", "outputs/hfq_final/final_event_pool_v1.csv", "frozen", "193个主规格事件的唯一事件池", "formal screening", "30%困境、120-252日横盘、0.5%突破、60日冷却", "src/csi300_events/hfq_final_sample.py", "D004,D006,D007", "baseline_v1", len(baseline), baseline.stock_code.nunique(), f"{baseline.event_date.min()} to {baseline.event_date.max()}", EXPECTED_EVENT_POOL_SHA256, "DataVault/frozen", "frozen", "yes", "never", "后续分析不得重新筛选"],
        ["D009", "最终分析数据v1", "outputs/hfq_final/final_analysis_dataset_v1.csv", "frozen", "一事件一行的baseline分析输入", "D008 plus D006", "加入RVOL及既有未来收益，不插值不填0", "analysis_pipeline.py", "D006,D008", "baseline_v1", len(baseline), baseline.stock_code.nunique(), f"{baseline.event_date.min()} to {baseline.event_date.max()}", EXPECTED_BASELINE_SHA256, "DataVault/frozen", "frozen", "yes", "never", "论文实证主数据"],
        ["D010", "市场与行业审计源", "outputs/research_audit_20260919/sources/**", "external", "市场调整收益和当前行业集中度诊断", "BaoStock", "CSI300日线与当前行业快照", "outputs/research_audit_20260919/audit_pipeline.py", "external API", "audit_20260919", "multiple", "multiple", "2015-2026", "vault manifest", "DataVault/external_sources", "validated", "yes", "no", "当前行业不能当历史行业"],
        ["D012", "固定120日旧方案", "../_obsolete_csi300_fixed120_20260906/**", "archive", "保存旧事件定义与历史尝试", "legacy project", "固定120日及旧换手率方案", "legacy scripts", "legacy data", "deprecated", "39 files", "n.a.", "legacy", "vault manifest", "DataVault/archive", "deprecated", "partial", "no", "不能与adaptive baseline混报"],
        ["D013", "FYP课程与格式资料", "../BNBU*; ../01_*; ../02_*; ../03_*; ../Coding; ../STAT4004*", "reference", "论文格式、课程和方法参考", "university and user files", "原样保存", "none", "external documents", "reference", "multiple", "n.a.", "n.a.", "vault manifest", "DataVault/external_sources", "reference", "no", "no", "与实证证据链分开"],
        ["D014", "既有输出完整快照", "outputs/**", "archive", "保留迁移前全部结果和诊断输出", "project generated", "原路径逐文件复制", "multiple", "D006-D010", DATE_STAMP, "all output files", "n.a.", "n.a.", "vault manifest", "DataVault/archive", "archived", "mixed", "no", "正式子集另存results"],
        ["D015", "独立trend-volume项目", "../trend_volume_research/**", "archive", "防止与当前FYP混淆", "separate project", "原样归档", "separate scripts", "separate data", "separate", "86 files", "n.a.", "n.a.", "vault manifest", "DataVault/archive", "separate", "yes", "no", "不进入当前论文结论"],
    ]
    keys = ["数据ID","名称","路径规则","数据层级","作用","来源","构造原理","生成脚本","输入","参数版本","行数或文件数","股票数","时间范围","hash或manifest","同步位置","状态","能否重建","能否删除","备注"]
    return [dict(zip(keys, row)) for row in rows]


def code_registry() -> list[dict[str, str]]:
    purposes = {
        "analysis_pipeline.py": ("baseline描述、分组、相关和OLS分析", "读取D009，输出baseline统计结果", "no"),
        "robustness_pipeline.py": ("阈值、ATR、成交量窗口和Failure20稳健性", "按指定维度改变定义并保存稳健性结果", "yes, robustness only"),
        "final_paper_analysis_pipeline.py": ("生成论文Table1-7和Figure1-6", "读取冻结结果并整理为论文格式", "no"),
        "run.ps1": ("D盘运行入口", "设置D盘临时目录、运行测试或正式流程", "no"),
        "tools/project_control.py": ("双通道同步、登记和hash验证", "复制到DataVault并生成研究索引", "no"),
    }
    paths = [PROJECT/name for name in ["analysis_pipeline.py","robustness_pipeline.py","final_paper_analysis_pipeline.py","run.ps1"]]
    paths += scan_tree(PROJECT/"src") + scan_tree(PROJECT/"tests") + scan_tree(PROJECT/"tools")
    rows = []
    code_paths = [path for path in sorted(set(paths)) if path.suffix.lower() in {".py", ".ps1", ".mjs"}]
    for index, path in enumerate(code_paths, start=1):
        rel = path.relative_to(PROJECT).as_posix()
        p, logic, affects = purposes.get(rel, (f"{path.stem}模块或测试", "详见文件docstring及对应测试", "inspect before change"))
        rows.append({"代码ID":f"C{index:03d}","路径":rel,"作用":p,"核心逻辑":logic,
                     "读取文件":"见代码和REPRODUCIBILITY.md","写出文件":"见结果登记表或测试输出",
                     "是否影响事件定义":affects,"对应测试":"tests/ or internal assertions",
                     "当前或旧版":"current" if "legacy" not in rel.lower() else "legacy",
                     "维护说明":"修改前先确认是否触及baseline；所有命令写入D盘"})
    return rows


def artifact_registry() -> list[dict[str, str]]:
    entries = [
        ("T01","Table1.xlsx","table","描述193个事件的变量分布","D009","final_paper_analysis_pipeline.py","描述统计","报告样本分布，不作因果解释","Chapter 4 Descriptive Statistics"),
        ("T02","Table2.xlsx","table","检验ln_RVOL与未来收益关系","D009","final_paper_analysis_pipeline.py","OLS + HC3, Model1/2","条件相关，不是因果或可交易收益","Chapter 4 Regression"),
        ("T03","Table3.xlsx","table","比较RVOL三分位组后续收益","D009","final_paper_analysis_pipeline.py","ln_RVOL terciles","分组差异是描述性证据","Chapter 4 Group Analysis"),
        ("T04","Table4.xlsx","table","检验0.5%、1%、2%突破阈值敏感性","D009,D014","final_paper_analysis_pipeline.py","controlled R20 OLS HC3","事件池重新识别，不能视为机械子样本","Chapter 4 Robustness"),
        ("T05","Table5.xlsx","table","比较0.5%与ATR20突破","D009,D014","final_paper_analysis_pipeline.py","controlled R20 OLS HC3","ATR是补充，不替代baseline","Chapter 4 Robustness"),
        ("T06","Table6.xlsx","table","比较RVOL20/60/120","D009,D014","final_paper_analysis_pipeline.py","controlled R20 OLS HC3","显著性随窗口改变","Chapter 4 Robustness"),
        ("T07","Table7.xlsx","table","检验Failure20与ln_RVOL关系","D009,D014","final_paper_analysis_pipeline.py","Logit Model2","模型型标准误，不是HC3","Chapter 4 Failure Analysis"),
        ("F01","Figure1.png/pdf","figure","展示RVOL组别R20均值","D009","final_paper_analysis_pipeline.py","bar chart","均值差异不等于因果影响","Chapter 4 Group Analysis"),
        ("F02","Figure2.png/pdf","figure","展示RVOL组别R60均值","D009","final_paper_analysis_pipeline.py","bar chart","受长期收益波动影响","Chapter 4 Group Analysis"),
        ("F03","Figure3.png/pdf","figure","展示ln_RVOL与R20散点及拟合线","D009","final_paper_analysis_pipeline.py","scatter + linear fit","少数极端点保留","Chapter 4 Correlation"),
        ("F04","Figure4.png/pdf","figure","展示不同突破阈值核心系数","D009,D014","final_paper_analysis_pipeline.py","coefficient plot","样本随阈值变化","Chapter 4 Robustness"),
        ("F05","Figure5.png/pdf","figure","展示成交量窗口核心系数","D009,D014","final_paper_analysis_pipeline.py","coefficient plot","窗口选择影响精度","Chapter 4 Robustness"),
        ("F06","Figure6.png/pdf","figure","展示RVOL组别Failure20比例","D009,D014","final_paper_analysis_pipeline.py","bar chart","失败定义严格且非因果","Chapter 4 Failure Analysis"),
        ("A01","event_year_distribution.png","figure","展示年度事件集中度","D009","audit_pipeline.py","event count by year","2019年集中提示市场状态风险","Sample Audit"),
        ("R01","empirical_results_summary.md","report","汇总主结果与解释边界","T01-T07,F01-F06","final_paper_analysis_pipeline.py","n.a.","只报告关联","Chapter 4 writing source"),
        ("R02","Research_Master_Document.md","report","重建完整研究流程","D001-D015","audit_pipeline.py","research audit","行业数据为当前快照","Project documentation"),
    ]
    rows=[]
    for aid, name, typ, question, inputs, code, model, boundary, section in entries:
        if aid.startswith("T"):
            path=f"results/paper_v1/Tables/{name}"
        elif aid.startswith("F"):
            path=f"results/paper_v1/Figures/{name}"
        elif aid=="A01":
            path="results/audit_20260919/Figures/event_year_distribution.png"
        elif aid=="R01":
            path="results/paper_v1/empirical_results_summary.md"
        else:
            path="results/audit_20260919/Research_Master_Document.md"
        rows.append({"结果ID":aid,"文件名":name,"类型":typ,"路径":path,"研究问题":question,
                     "输入数据ID及hash":inputs,"生成代码":code,"模型或参数":model,
                     "主要结论":"见对应文件与THESIS_EVIDENCE_MATRIX.md","解释边界":boundary,
                     "论文位置":section,"验证状态":"validated"})
    return rows


def run_registry() -> list[dict[str, str]]:
    control_commit = git_head() or "not committed yet"
    control_success = "yes" if control_commit != "not committed yet" else "pending"
    return [
        {"运行ID":"RUN001","日期":"2026-09","Git commit":"pre-Git historical run","输入数据hash":EXPECTED_EVENT_POOL_SHA256,"参数版本":"baseline_v1","执行命令":"formal pipeline; exact invocation not independently recorded","输出结果ID":"D008,D009","测试结果":"hash verified","是否成功":"yes","备注":"历史运行，时间粒度仅能确认到现有文件记录"},
        {"运行ID":"RUN002","日期":"2026-09-10","Git commit":"pre-Git historical run","输入数据hash":EXPECTED_BASELINE_SHA256,"参数版本":"baseline_v1","执行命令":"analysis_pipeline.py","输出结果ID":"T01-T03","测试结果":"independently audited","是否成功":"yes","备注":"未修改冻结样本"},
        {"运行ID":"RUN003","日期":"2026-09-19","Git commit":"pre-Git historical run","输入数据hash":EXPECTED_BASELINE_SHA256,"参数版本":"robustness_v1","执行命令":"robustness_pipeline.py","输出结果ID":"T04-T07","测试结果":"core models reproduced","是否成功":"yes","备注":"threshold_robustness.xlsx扩展名缺陷另行登记"},
        {"运行ID":"RUN004","日期":"2026-09-19","Git commit":"pre-Git historical run","输入数据hash":EXPECTED_BASELINE_SHA256,"参数版本":"paper_v1","执行命令":"final_paper_analysis_pipeline.py","输出结果ID":"T01-T07,F01-F06,R01","测试结果":"saved validation completed","是否成功":"yes","备注":"论文级结果包"},
        {"运行ID":"RUN005","日期":"2026-09-20","Git commit":"pre-Git historical run","输入数据hash":EXPECTED_BASELINE_SHA256,"参数版本":"audit_20260919","执行命令":"audit_pipeline.py","输出结果ID":"A01,R02","测试结果":"8754 original hashes unchanged","是否成功":"yes","备注":"新增市场调整与Failure60，不改baseline"},
        {"运行ID":"RUN006","日期":"2026-09-24","Git commit":control_commit,"输入数据hash":EXPECTED_BASELINE_SHA256,"参数版本":"control_v1","执行命令":"python tools/project_control.py all","输出结果ID":"project registries and manifests","测试结果":"由verification_report.json记录","是否成功":control_success,"备注":"双通道同步与研究说明系统"},
    ]


def decision_registry() -> list[dict[str, str]]:
    return [
        {"决策ID":"ADR001","日期":"2026-09","问题":"股票池如何避免当前成分回填","备选方案":"当前成分; 历史t-1成分","最终决定":"历史t-1成分","为什么":"降低幸存者和回填偏差","代价":"依赖历史快照完整性","重新评估条件":"获得官方完整变更历史"},
        {"决策ID":"ADR002","日期":"2026-09","问题":"困境阈值","备选方案":"20%;30%;40%","最终决定":"至少30%","为什么":"保证明显技术性困境，同时保留样本","代价":"突破日未必仍低于峰值30%","重新评估条件":"只作预先声明的稳健性"},
        {"决策ID":"ADR003","日期":"2026-09","问题":"横盘窗口","备选方案":"固定120; adaptive120-252","最终决定":"adaptive120-252","为什么":"允许不同股票形成更长底部","代价":"252日上限堆积，扩展阶段趋势约束较弱","重新评估条件":"未来研究单独处理删失"},
        {"决策ID":"ADR004","日期":"2026-09","问题":"baseline突破定义","备选方案":"0.5%;1%;2%;ATR20","最终决定":"0.5% baseline","为什么":"主规格样本量与噪声过滤平衡","代价":"固定阈值不随波动率变化","重新评估条件":"不替换baseline，ATR仅稳健性"},
        {"决策ID":"ADR005","日期":"2026-09","问题":"相对成交量窗口","备选方案":"20;60;120","最终决定":"60个正常正成交量观察","为什么":"平衡近期性与稳定性","代价":"停牌时日历跨度超过60日","重新评估条件":"窗口稳健性已报告"},
        {"决策ID":"ADR006","日期":"2026-09","问题":"主回归误差结构","备选方案":"classical;HC3;cluster","最终决定":"OLS+HC3 baseline","为什么":"处理异方差且与现有实现一致","代价":"不处理同股或同日期相关","重新评估条件":"下一阶段补充聚类标准误"},
        {"决策ID":"ADR007","日期":"2026-09-24","问题":"项目同步架构","备选方案":"GitHub全上传;DVC;双通道","最终决定":"GitHub+DataVault双通道","为什么":"兼顾可读版本管理、数据完整性和许可风险","代价":"需要维护manifest和外部云盘","重新评估条件":"超过三个并行数据版本时考虑DVC"},
        {"决策ID":"ADR008","日期":"2026-09","问题":"异常值处理","备选方案":"删除;winsorize;原样保留","最终决定":"原样保留","为什么":"避免结果导向处理并维持冻结样本","代价":"小样本估计可能受极端事件影响","重新评估条件":"只允许透明敏感性分析，不替代原结果"},
        {"决策ID":"ADR009","日期":"2026-09","问题":"突破信号执行时点","备选方案":"同收盘价可交易;收盘后确认","最终决定":"收盘后确认","为什么":"当日收盘和成交量只有收盘后完整可知","代价":"不能直接解释为同收盘价执行策略","重新评估条件":"未来构造次日开盘执行版本"},
    ]


def inventory() -> None:
    CATALOG.mkdir(parents=True, exist_ok=True)
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    paths = scan_tree(WORKSPACE)
    records=[]
    for path in paths:
        rel=relative_to_workspace(path)
        if rel.lower() in GENERATED_SELF_PATHS_LOWER:
            continue
        meta=classify(path)
        stat=path.stat()
        records.append({"relative_path":rel,"bytes":stat.st_size,
                        "modified":dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                        "sha256":sha256(path),**meta})
    records.sort(key=lambda row: row["relative_path"].lower())
    inv_fields=["relative_path","bytes","modified","sha256","channel","category","status","dataset_id","purpose","destination","delete_policy"]
    write_csv(CATALOG/"migration_inventory.csv",records,inv_fields)
    datasets=dataset_registry(); codes=code_registry(); artifacts=artifact_registry(); runs=run_registry(); decisions=decision_registry()
    write_csv(CATALOG/"datasets.csv",datasets,list(datasets[0]))
    write_csv(CATALOG/"code_registry.csv",codes,list(codes[0]))
    write_csv(CATALOG/"artifacts.csv",artifacts,list(artifacts[0]))
    write_csv(CATALOG/"runs.csv",runs,list(runs[0]))
    write_csv(CATALOG/"decisions.csv",decisions,list(decisions[0]))
    project_files=[]
    for path in scan_tree(PROJECT):
        rel=path.relative_to(PROJECT).as_posix()
        if rel.startswith((".git/",".runtime/","__pycache__/","data/raw/","data/processed/","Final_Empirical_Result/")):
            continue
        if rel.startswith("outputs/") and rel != "outputs/README.md":
            continue
        if rel.startswith("data/manifests/") and rel != "data/manifests/README.md":
            continue
        if rel in {"catalog/workbook_payload.json", "catalog/unregistered_files_report.md",
                   "docs/PROJECT_SYNC_STATUS.md", "config/local_paths.psd1"}:
            continue
        project_files.append({"path":rel,"bytes":path.stat().st_size,"sha256":sha256(path)})
    vault_files=[]
    if VAULT.exists():
        for path in scan_tree(VAULT):
            rel=path.relative_to(VAULT).as_posix()
            if rel.startswith("manifests/"):
                continue
            vault_files.append({"path":rel,"bytes":path.stat().st_size,"sha256":sha256(path)})
    write_json(MANIFESTS/"project_channel_manifest.json",{"generated_utc":now_iso(),"root":str(PROJECT),"files":project_files})
    write_json(MANIFESTS/"vault_channel_manifest.json",{"generated_utc":now_iso(),"root":str(VAULT),"files":vault_files})
    write_json(VAULT/"manifests"/"vault_channel_manifest.json",{"generated_utc":now_iso(),"root":str(VAULT),"files":vault_files})
    counts={}
    for row in records: counts[row["channel"]]=counts.get(row["channel"],0)+1
    payload={"generated_utc":now_iso(),"baseline":{"events":len(pd.read_csv(BASELINE)),"stocks":pd.read_csv(BASELINE)["stock_code"].nunique(),"sha256":sha256(BASELINE)},
             "channel_counts":counts,"datasets":datasets,"code":codes,"artifacts":artifacts,"runs":runs,"decisions":decisions,
             "migration":records}
    write_json(CATALOG/"workbook_payload.json",payload)
    print(json.dumps({"files":len(records),"channels":counts,"vault_files":len(vault_files)},ensure_ascii=False,indent=2))


def git_tracked_files() -> list[str]:
    if not (PROJECT/".git").exists():
        return []
    result=subprocess.run(["git","-C",str(PROJECT),"ls-files"],capture_output=True,text=True,encoding="utf-8",errors="replace")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def git_head() -> str:
    if not (PROJECT/".git").exists():
        return ""
    result=subprocess.run(["git","-C",str(PROJECT),"rev-parse","--verify","HEAD"],capture_output=True,text=True,encoding="utf-8",errors="replace")
    return result.stdout.strip() if result.returncode == 0 else ""


def git_remote() -> str:
    if not (PROJECT/".git").exists():
        return ""
    result=subprocess.run(["git","-C",str(PROJECT),"remote","get-url","origin"],capture_output=True,text=True,encoding="utf-8",errors="replace")
    return result.stdout.strip() if result.returncode == 0 else ""


def verify() -> dict[str, Any]:
    issues=[]
    baseline_hash=sha256(BASELINE)
    event_hash=sha256(EVENT_POOL)
    if baseline_hash != EXPECTED_BASELINE_SHA256: issues.append("Frozen baseline SHA-256 mismatch")
    if event_hash != EXPECTED_EVENT_POOL_SHA256: issues.append("Frozen event-pool SHA-256 mismatch")
    data=pd.read_csv(BASELINE,encoding="utf-8-sig")
    if len(data)!=193: issues.append(f"Baseline event count is {len(data)}, expected 193")
    if data["stock_code"].nunique()!=160: issues.append("Baseline stock count mismatch")
    if data.duplicated(["stock_code","event_date"]).any(): issues.append("Duplicate stock-date event keys found")
    for source in [BASELINE,EVENT_POOL]:
        copy=VAULT/"frozen"/source.name
        if not copy.exists(): issues.append(f"Missing frozen vault copy: {copy}")
        elif sha256(copy)!=sha256(source): issues.append(f"Frozen vault hash mismatch: {copy.name}")
    manifest_checks = [
        (MANIFESTS/"project_channel_manifest.json", PROJECT, set()),
        (MANIFESTS/"vault_channel_manifest.json", VAULT, {"manifests"}),
    ]
    for manifest_path, root, excluded_roots in manifest_checks:
        if not manifest_path.exists():
            issues.append(f"Missing channel manifest: {manifest_path}")
            continue
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        registered={item["path"]:item for item in manifest.get("files",[])}
        for rel,item in registered.items():
            candidate=root/Path(rel)
            if not candidate.exists():
                issues.append(f"Manifest file missing: {root.name}/{rel}")
            elif candidate.stat().st_size!=item["bytes"] or sha256(candidate)!=item["sha256"]:
                issues.append(f"Manifest hash mismatch: {root.name}/{rel}")
        if root == VAULT:
            actual={path.relative_to(root).as_posix() for path in scan_tree(root)
                    if path.relative_to(root).parts[0] not in excluded_roots}
            extras=sorted(actual-set(registered))
            for rel in extras[:50]:
                issues.append(f"Unregistered vault file: {rel}")
            if len(extras)>50:
                issues.append(f"Additional unregistered vault files omitted: {len(extras)-50}")
    required_readmes=[PROJECT/"README.md",DOCS/"README.md",CATALOG/"README.md",PROJECT/"config"/"README.md",
                      PROJECT/"data"/"README.md",MANIFESTS/"README.md",RESULTS/"README.md",
                      RESULTS/"baseline_v1"/"README.md",RESULTS/"robustness_v1"/"README.md",
                      RESULTS/"audit_20260919"/"README.md",RESULTS/"paper_v1"/"README.md",
                      RESULTS/"audit_20260919"/"Figures"/"README.md",
                      RESULTS/"paper_v1"/"Tables"/"README.md",RESULTS/"paper_v1"/"Figures"/"README.md",
                      PROJECT/"src"/"README.md",PROJECT/"src"/"csi300_events"/"README.md",
                      PROJECT/"tests"/"README.md",PROJECT/"tools"/"README.md",PROJECT/"outputs"/"README.md"]
    for readme in required_readmes:
        if not readme.exists(): issues.append(f"Missing directory explanation: {readme.relative_to(PROJECT)}")
    for artifact in artifact_registry():
        if "png/pdf" in artifact["文件名"]:
            base=artifact["路径"].replace(".png/pdf","")
            png=PROJECT/(base+".png")
            pdf=PROJECT/(base+".pdf")
            if not png.exists() or not pdf.exists(): issues.append(f"Missing figure pair: {artifact['结果ID']}")
        else:
            path=PROJECT/artifact["路径"]
            if not path.exists(): issues.append(f"Missing registered artifact: {artifact['结果ID']} {path}")
    forbidden_prefixes=(".runtime/","data/raw/","data/processed/","__pycache__/")
    for tracked in git_tracked_files():
        forbidden_output=tracked.startswith("outputs/") and tracked != "outputs/README.md"
        if tracked.startswith(forbidden_prefixes) or forbidden_output or tracked.endswith((".pyc",".pyo")):
            issues.append(f"Forbidden Git-tracked file: {tracked}")
    report={"verified_utc":now_iso(),"pass":not issues,"issues":issues,"baseline_sha256":baseline_hash,
            "event_pool_sha256":event_hash,"events":len(data),"stocks":int(data.stock_code.nunique()),
            "duplicates":int(data.duplicated(["stock_code","event_date"]).sum()),
            "git_tracked_files":len(git_tracked_files()),"vault_root":str(VAULT)}
    write_json(MANIFESTS/"verification_report.json",report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def report() -> None:
    inventory_path=CATALOG/"migration_inventory.csv"
    if not inventory_path.exists(): inventory()
    records=list(csv.DictReader(inventory_path.open(encoding="utf-8-sig")))
    counts={}
    for row in records: counts[row["channel"]]=counts.get(row["channel"],0)+1
    unregistered=[]
    known={row["relative_path"] for row in records}
    for path in scan_tree(WORKSPACE):
        rel=relative_to_workspace(path)
        if rel.lower() in GENERATED_SELF_PATHS_LOWER or rel.startswith("csi300_breakout_events/.git/"):
            continue
        if rel not in known: unregistered.append(rel)
    lines=["# Unregistered Files Report","",f"Generated: {now_iso()}","",f"Unregistered file count: {len(unregistered)}",""]
    lines += [f"- `{item}`" for item in unregistered] if unregistered else ["No unregistered files were found in the governed workspace scan."]
    (CATALOG/"unregistered_files_report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    verify_result=verify()
    status=f"""# Project Sync Status

Generated: {now_iso()}

## Frozen baseline

- Events: {verify_result['events']}
- Stocks: {verify_result['stocks']}
- Duplicate stock-date keys: {verify_result['duplicates']}
- Baseline SHA-256: `{verify_result['baseline_sha256']}`

## File routing

{os.linesep.join(f'- {key}: {value} files' for key,value in sorted(counts.items()))}

## Verification

- Strict verification: {'PASS' if verify_result['pass'] and not unregistered else 'FAIL'}
- Unregistered files: {len(unregistered)}
- Issues: {len(verify_result['issues'])}
- DataVault: `{VAULT}`
- Git commit: `{git_head() or 'not committed yet'}`
- GitHub remote: `{git_remote() or 'not configured'}`

## External connection status

The D-drive vault is prepared and hash-verified locally. A cloud-sync client has not been configured on D drive, so off-device data synchronization remains external setup work.
"""
    (DOCS/"PROJECT_SYNC_STATUS.md").write_text(status,encoding="utf-8")
    print(json.dumps({"unregistered":len(unregistered),"verify_pass":verify_result["pass"]},ensure_ascii=False))


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["sync-vault","curate-results","inventory","verify","report","all"])
    args=parser.parse_args()
    if args.command in {"sync-vault","all"}: sync_vault()
    if args.command in {"curate-results","all"}: curate_results()
    if args.command in {"inventory","all"}: inventory()
    if args.command in {"report","all"}: report()
    if args.command=="verify":
        result=verify()
        if not result["pass"]: raise SystemExit(1)


if __name__=="__main__":
    main()
