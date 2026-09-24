# Legacy and Full Outputs

本目录保留迁移前的完整分析输出及历史运行证据，因此默认不进入GitHub；它被逐文件复制到D盘DataVault的归档快照。论文实际使用的精选结果位于`results/`。

- 输入：历史pipeline和冻结数据。
- 输出：完整工作簿、报告、诊断、源数据副本和日志。
- 可修改：不得原地覆盖历史证据；新运行使用新版本目录。
- 冻结文件：`hfq_final/final_event_pool_v1.csv`与`final_analysis_dataset_v1.csv`绝对禁止覆盖。
- 已知缺陷：`hfq_final/threshold_robustness.xlsx`实际包含JSON字节，保留原件用于审计。
- 验证：DataVault快照manifest、两个固定hash和精选结果登记。
- 当前状态：`archived`，其中冻结数据状态为`frozen`。
