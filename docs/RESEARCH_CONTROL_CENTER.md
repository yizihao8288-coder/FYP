# Research Control Center

Last substantive research audit: 2026-09-20  
Control system established: 2026-09-24  
Authority: this page supersedes older status descriptions.

## Current research question

在沪深300历史成分股的技术性困境反转突破事件中，突破日异常成交量`ln_RVOL`是否与未来收益及突破失败风险相关？本研究识别的是条件相关关系，不是因果关系。

## Frozen baseline

| Item | Authoritative value |
|---|---|
| Dataset ID | D009 |
| File | `outputs/hfq_final/final_analysis_dataset_v1.csv` |
| Events | 193 |
| Stocks | 160 |
| Event dates | 2016-02-23 to 2026-03-10 |
| Duplicate stock-date keys | 0 |
| SHA-256 | `94d52af2f33639cc336a6967d3fecfdccc2a2fc3f8b6ae265d116ae457ce0f76` |

The event pool D008 has SHA-256 `6e8485a4716caa6035bf0616d0b4dd9b7b91da8adf7c611053933460f836a889`.

## Completed

- Historical CSI300 member screening and data-quality filters.
- Distress, adaptive consolidation and 0.5% breakout construction.
- Frozen event pool and one-row-per-event analysis dataset.
- Descriptive statistics, RVOL terciles, Pearson correlation and OLS+HC3.
- 1%, 2%, ATR20 and RVOL20/60/120 robustness checks.
- Failure20 and Failure60 analysis.
- Current-industry concentration diagnostic and CSI300 market-adjusted returns.
- Full research audit, reviewer report and project reconstruction.
- D-drive DataVault, file registries, curated results and automated verification.
- Public GitHub repository connected at `https://github.com/yizihao8288-coder/FYP`; only approved code, documentation, manifests and curated results are published.

## In progress

- Connect `D:\FYP_DataVault\csi300_breakout_events` to an off-device private cloud client located on D drive.
- Use the evidence matrix to draft Chapter 4 and defence responses.

## Next recommended analysis

1. Add stock-clustered and, if feasible, two-way clustered standard errors without changing the event pool.
2. Add year or market-state controls.
3. Add event-preceding volatility control.
4. Obtain event-time historical industry classifications before using industry fixed effects.

These are future robustness extensions. They must not replace the current baseline because of statistical significance.

## Authoritative components

| Component | Location | Role |
|---|---|---|
| Research design | `docs/RESEARCH_DESIGN.md` | Exact definitions, rationale and limitations |
| Data and variables | `docs/DATA_DICTIONARY.md` | Dataset layers and formulas |
| Decisions | `docs/DECISION_LOG.md` | Why important choices were made |
| Reproduction | `docs/REPRODUCIBILITY.md` | Commands, paths and validation |
| Data registry | `catalog/datasets.csv` | All logical datasets |
| Code registry | `catalog/code_registry.csv` | Purpose of each hand-written code file |
| Result registry | `catalog/artifacts.csv` | Tables, figures and reports |
| Run registry | `catalog/runs.csv` | Input-code-output history |
| Migration inventory | `catalog/migration_inventory.csv` | Routing of every scanned file |
| Current sync check | `docs/PROJECT_SYNC_STATUS.md` | Machine-generated latest verification |

## Main findings

- Pearson correlation between`ln_RVOL`andR20 is approximately -0.157.
- R20 Model 2 coefficient is -0.0237, p=0.0985, R²=0.0302.
- R60 Model 2 coefficient is -0.0545, p=0.0407, R²=0.0308.
- Market-adjusted R20 and R60 coefficients remain negative but have p-values 0.1080 and 0.1291.
- Failure20 and Failure60 estimates associate higher`ln_RVOL`with higher observed failure probability.

These results do not establish a causal effect of volume and do not prove an executable trading strategy.

## Known defects and interpretation risks

- `outputs/hfq_final/threshold_robustness.xlsx`contains JSON bytes despite its XLSX extension. The original remains unchanged and archived.
- HC3 handles heteroskedasticity but not within-stock or common-date dependence.
- Failure60 has only 14 complete non-failure observations and one unresolved event.
- Current BaoStock industry data are not event-time classifications.
- The event-day price and volume are only fully known after market close.
- The project trading calendar is the union of stock dates, not an official exchange calendar.

## Deletion policy

- Frozen data: never delete or overwrite.
- Raw evidence: retain in DataVault.
- Processed data: retain the frozen version; rebuild only into a new version.
- Old methods: archive until thesis completion.
- Temporary files: delete only after they appear in the review workbook and receive user approval.
