"""Build the thesis-aligned public writing package without changing research data."""

from __future__ import annotations

import csv
import hashlib
import shutil
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "00_THESIS_WRITING_GUIDE"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


COPIES = [
    ("results/audit_20260919/Figures/event_year_distribution.png", "03_Data_and_Methodology/event_year_distribution.png", "Chapter 3", "事件按年份分布"),
    ("outputs/research_audit_20260919/sample_audit.xlsx", "03_Data_and_Methodology/sample_audit.xlsx", "Chapter 3", "样本、股票、行业和年份审计"),
    ("results/baseline_v1/data_quality_report.md", "03_Data_and_Methodology/data_quality_report.md", "Chapter 3", "变量缺失、范围与异常值审计"),
    ("results/baseline_v1/hfq_final_report.md", "03_Data_and_Methodology/hfq_final_report.md", "Chapter 3", "冻结样本简要报告"),
    ("results/baseline_v1/hfq_final_summary.json", "03_Data_and_Methodology/hfq_final_summary.json", "Chapter 3", "冻结样本机器可读摘要"),
    ("results/baseline_v1/model_sample_counts.csv", "03_Data_and_Methodology/model_sample_counts.csv", "Chapter 3", "各模型有效样本量"),
    ("results/baseline_v1/return_window_completeness.csv", "03_Data_and_Methodology/return_window_completeness.csv", "Chapter 3", "收益窗口缺失情况"),
    ("results/paper_v1/Tables/Table1.xlsx", "04_Empirical_Results/01_Descriptive_Statistics/Table1.xlsx", "Chapter 4.1", "论文描述统计表"),
    ("results/baseline_v1/descriptive_statistics.xlsx", "04_Empirical_Results/01_Descriptive_Statistics/descriptive_statistics.xlsx", "Chapter 4.1", "完整描述统计"),
    ("results/paper_v1/Tables/Table2.xlsx", "04_Empirical_Results/02_Main_OLS_Regression/Table2.xlsx", "Chapter 4.2", "论文主回归表"),
    ("results/baseline_v1/regression_results.xlsx", "04_Empirical_Results/02_Main_OLS_Regression/regression_results.xlsx", "Chapter 4.2", "完整OLS加HC3结果"),
    ("results/paper_v1/Tables/Table3.xlsx", "04_Empirical_Results/03_RVOL_Group_Analysis/Table3.xlsx", "Chapter 4.3", "论文RVOL三分组表"),
    ("results/paper_v1/Figures/Figure1.png", "04_Empirical_Results/03_RVOL_Group_Analysis/Figure1.png", "Chapter 4.3", "RVOL组别与R20均值"),
    ("results/paper_v1/Figures/Figure1.pdf", "04_Empirical_Results/03_RVOL_Group_Analysis/Figure1.pdf", "Chapter 4.3", "Figure1论文排版版本"),
    ("results/paper_v1/Figures/Figure2.png", "04_Empirical_Results/03_RVOL_Group_Analysis/Figure2.png", "Chapter 4.3", "RVOL组别与R60均值"),
    ("results/paper_v1/Figures/Figure2.pdf", "04_Empirical_Results/03_RVOL_Group_Analysis/Figure2.pdf", "Chapter 4.3", "Figure2论文排版版本"),
    ("results/baseline_v1/correlation_table.xlsx", "04_Empirical_Results/04_Correlation_Diagnostic/correlation_table.xlsx", "Chapter 4.4", "Pearson相关与散点数据"),
    ("results/paper_v1/Figures/Figure3.png", "04_Empirical_Results/04_Correlation_Diagnostic/Figure3.png", "Chapter 4.4", "ln_RVOL与R20散点图"),
    ("results/paper_v1/Figures/Figure3.pdf", "04_Empirical_Results/04_Correlation_Diagnostic/Figure3.pdf", "Chapter 4.4", "Figure3论文排版版本"),
    ("results/paper_v1/empirical_results_summary.md", "04_Empirical_Results/empirical_results_summary.md", "Chapter 4", "主结果文字摘要"),
    ("results/robustness_v1/robustness_summary.xlsx", "05_Robustness_and_Additional/robustness_summary.xlsx", "Chapter 5", "全部核心稳健性结果汇总"),
    ("results/paper_v1/Tables/Table4.xlsx", "05_Robustness_and_Additional/01_Breakout_Threshold/Table4.xlsx", "Chapter 5.1", "0.5%、1%、2%阈值比较"),
    ("results/paper_v1/Figures/Figure4.png", "05_Robustness_and_Additional/01_Breakout_Threshold/Figure4.png", "Chapter 5.1", "阈值核心系数图"),
    ("results/paper_v1/Figures/Figure4.pdf", "05_Robustness_and_Additional/01_Breakout_Threshold/Figure4.pdf", "Chapter 5.1", "Figure4论文排版版本"),
    ("outputs/research_audit_20260919/threshold_robustness_summary.xlsx", "05_Robustness_and_Additional/01_Breakout_Threshold/threshold_robustness_summary.xlsx", "Chapter 5.1", "阈值与ATR设计比较"),
    ("results/paper_v1/Tables/Table5.xlsx", "05_Robustness_and_Additional/02_ATR20_Breakout/Table5.xlsx", "Chapter 5.2", "0.5%与ATR20比较"),
    ("results/paper_v1/Tables/Table6.xlsx", "05_Robustness_and_Additional/03_Volume_Window/Table6.xlsx", "Chapter 5.3", "RVOL20、60、120比较"),
    ("results/paper_v1/Figures/Figure5.png", "05_Robustness_and_Additional/03_Volume_Window/Figure5.png", "Chapter 5.3", "成交量窗口核心系数图"),
    ("results/paper_v1/Figures/Figure5.pdf", "05_Robustness_and_Additional/03_Volume_Window/Figure5.pdf", "Chapter 5.3", "Figure5论文排版版本"),
    ("results/paper_v1/Tables/Table7.xlsx", "05_Robustness_and_Additional/04_Failure20/Table7.xlsx", "Chapter 5.4", "Failure20 Logit结果"),
    ("results/paper_v1/Figures/Figure6.png", "05_Robustness_and_Additional/04_Failure20/Figure6.png", "Chapter 5.4", "RVOL组别Failure20比例"),
    ("results/paper_v1/Figures/Figure6.pdf", "05_Robustness_and_Additional/04_Failure20/Figure6.pdf", "Chapter 5.4", "Figure6论文排版版本"),
    ("docs/THESIS_EVIDENCE_MATRIX.md", "06_Discussion/THESIS_EVIDENCE_MATRIX.md", "Chapter 6", "结论表述边界"),
    ("results/audit_20260919/Reviewer_Report.md", "06_Discussion/Reviewer_Report.md", "Chapter 6", "审稿人视角缺陷与答辩问题"),
    ("results/audit_20260919/FINAL_PROJECT_STATUS.md", "07_Conclusion/FINAL_PROJECT_STATUS.md", "Chapter 7", "最终状态与结论来源"),
    ("results/audit_20260919/Research_Master_Document.md", "08_Appendices/Research_Master_Document.md", "Appendix", "完整研究流程"),
    ("results/audit_20260919/variable_audit.md", "08_Appendices/variable_audit.md", "Appendix", "变量设计审计"),
    ("results/audit_20260919/model_audit.md", "08_Appendices/model_audit.md", "Appendix", "模型设计审计"),
    ("docs/Research_Project_Map.xlsx", "08_Appendices/Research_Project_Map.xlsx", "Appendix", "研究项目总地图"),
]


DATAVAULT_ONLY = [
    ("D008", "frozen/final_event_pool_v1/final_event_pool_v1.csv", "最终事件池，193行", "冻结事件级数据，不进入公开Git历史"),
    ("D009", "frozen/final_analysis_dataset_v1.csv", "最终分析数据，一事件一行", "冻结事件级数据，不进入公开Git历史"),
    ("D014-QC", "archive/project_outputs_snapshot_20260924/hfq_final/quality_check_report.xlsx", "完整质量检查", "包含股票与事件明细；公开包使用文字审计"),
    ("D014-GROUP", "archive/project_outputs_snapshot_20260924/hfq_final/rvol_group_analysis.xlsx", "完整RVOL分组", "包含193行事件分组明细；公开包使用Table3"),
    ("D014-ATR", "archive/project_outputs_snapshot_20260924/hfq_final/atr_robustness.xlsx", "完整ATR20稳健性", "包含事件明细；公开包使用Table5"),
    ("D014-VOL", "archive/project_outputs_snapshot_20260924/hfq_final/volume_window_robustness.xlsx", "完整成交量窗口稳健性", "包含事件明细；公开包使用Table6"),
    ("D014-F20", "archive/project_outputs_snapshot_20260924/hfq_final/breakout_failure_analysis.xlsx", "完整Failure20分析", "包含事件明细；公开包使用Table7"),
    ("D014-MKT", "archive/project_outputs_snapshot_20260924/research_audit_20260919/market_adjusted_results.xlsx", "完整市场调整分析", "公开包只保留汇总、回归与方法说明"),
    ("D014-F60", "archive/project_outputs_snapshot_20260924/research_audit_20260919/failure60_results.xlsx", "完整Failure60分析", "公开包只保留回归、分组、状态与方法说明"),
    ("D014-DEFECT", "archive/project_outputs_snapshot_20260924/hfq_final/threshold_robustness.xlsx", "旧threshold文件", "已知缺陷：扩展名为xlsx但内容是JSON；不得作为论文Excel证据"),
]


def main() -> None:
    rows: list[dict[str, str]] = []
    for source_rel, target_rel, chapter, purpose in COPIES:
        source = PROJECT / source_rel
        target = PACKAGE / target_rel
        if not source.is_file():
            raise FileNotFoundError(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_hash = sha256(source)
        target_hash = sha256(target)
        if source_hash != target_hash:
            raise RuntimeError(f"Copy hash mismatch: {target}")
        rows.append({
            "chapter": chapter,
            "public_path": target.relative_to(PROJECT).as_posix(),
            "canonical_source": source_rel,
            "sha256": target_hash,
            "purpose": purpose,
            "status": "validated public copy",
            "data_boundary": "aggregate result or research document",
        })

    generated = [
        ("Chapter 5.5", "05_Robustness_and_Additional/05_Market_Adjusted_Return/market_adjusted_results_summary.xlsx", "市场调整收益公开汇总"),
        ("Chapter 5.6", "05_Robustness_and_Additional/06_Failure60/failure60_results_summary.xlsx", "Failure60公开汇总"),
    ]
    for chapter, relative_path, purpose in generated:
        target = PACKAGE / relative_path
        if not target.is_file():
            raise FileNotFoundError(target)
        rows.append({
            "chapter": chapter,
            "public_path": target.relative_to(PROJECT).as_posix(),
            "canonical_source": "summary extracted from full DataVault workbook",
            "sha256": sha256(target),
            "purpose": purpose,
            "status": "validated public summary",
            "data_boundary": "event-level sheet intentionally omitted",
        })

    for dataset_id, vault_path, purpose, reason in DATAVAULT_ONLY:
        rows.append({
            "chapter": "DataVault",
            "public_path": "",
            "canonical_source": f"{dataset_id}:{vault_path}",
            "sha256": "see data/manifests/vault_channel_manifest.json",
            "purpose": purpose,
            "status": "DataVault only",
            "data_boundary": reason,
        })

    manifest = PACKAGE / "THESIS_FILE_MANIFEST.csv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Copied {len(COPIES)} files and wrote {manifest}")


if __name__ == "__main__":
    main()
