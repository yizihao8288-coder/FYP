# Robustness Results v1

- 这是什么：突破阈值、ATR、RVOL窗口和突破失败的稳健性汇总。
- 输入：冻结股票池、困境和横盘定义，以及相应稳健性重新识别结果。
- 输出：`robustness_summary.xlsx`等精选核心结果。
- 核心逻辑：每次只改变被检验的定义；baseline保持0.5%和RVOL60。
- 可修改：新稳健性只能追加，不能因显著性替换baseline。
- 已知缺陷：原`outputs/hfq_final/threshold_robustness.xlsx`实际为JSON，原件只在DataVault归档。
- 验证：与Table4–Table7、Decision Log和审计记录交叉检查。
- 当前状态：`validated`。
