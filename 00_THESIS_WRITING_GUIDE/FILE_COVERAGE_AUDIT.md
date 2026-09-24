# 论文文件完整性核对

## 结论

原GitHub仓库没有漏掉Table1–Table7或Figure1–Figure6，但组织方式不适合直接写论文。旧目录`Final_Empirical_Result`中的20个文件与`results/paper_v1`逐字节一致，因此只上传规范副本并不是遗漏。

从论文写作角度，原仓库确实缺少清晰的章节入口，也没有把样本审计、市场调整收益和Failure60放到论文对应位置。本次写作包补足这些入口，并为后两项生成不含事件明细的公开汇总工作簿。

## 四类文件

| 类别 | 是否上传公开GitHub | 判断 |
|---|---|---|
| 论文表格、图片、汇总结果 | 是 | 写作必需，放入对应章节 |
| 研究设计、变量字典、代码、审计报告 | 是 | 解释方法和支持复现 |
| 原始行情、处理行情、冻结事件池、最终分析数据 | 否 | DataVault保存；不是遗漏 |
| 包含逐事件记录的详细工作簿 | 不上传完整版 | 公开包保留论文需要的汇总和回归页 |

## 已验证的重复目录

`Final_Empirical_Result`包含1份结果摘要、7张Excel表和12个PNG/PDF图形，共20个文件。它们与`results/paper_v1`的对应文件SHA-256完全一致。重复上传只会造成以后不知道该改哪一份，因此公开仓库以`results/paper_v1`和本写作包为准。

## 故意不公开但仍完整保存的文件

- `final_event_pool_v1.csv`和`final_analysis_dataset_v1.csv`。
- `quality_check_report.xlsx`完整版本。
- `rvol_group_analysis.xlsx`完整版本。
- `atr_robustness.xlsx`完整版本。
- `volume_window_robustness.xlsx`完整版本。
- `breakout_failure_analysis.xlsx`完整版本。
- `market_adjusted_results.xlsx`完整版本。
- `failure60_results.xlsx`完整版本。
- 原始和处理行情缓存。

这些文件的DataVault路径与状态记录在[THESIS_FILE_MANIFEST.csv](THESIS_FILE_MANIFEST.csv)及`data/manifests/vault_channel_manifest.json`中。

## 已知缺陷

旧的`threshold_robustness.xlsx`并不是有效Excel文件，实际内容为JSON。它被保留在DataVault作为历史证据，但不能放进论文。论文应使用本写作包中的`Table4.xlsx`与`threshold_robustness_summary.xlsx`。

## 真正仍缺少的内容

1. 经核实的文献来源和完整参考文献表。
2. 股票聚类或股票与日期双向聚类标准误。
3. 事件时点历史行业分类。
4. 预先声明的年份、市场状态与事件前波动率扩展。

后面三项是Potential Improvement或Recommended Future Work，不能写成已经完成。
