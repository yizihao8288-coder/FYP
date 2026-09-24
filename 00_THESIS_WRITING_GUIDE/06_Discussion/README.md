# Chapter 6 Discussion

## 这一章要做什么

Discussion不是重复第四章数字，而是解释这些数字可能意味着什么、哪些解释还不能确认，以及研究设计有什么限制。

## 与结果一致的解释

样本内高`ln_RVOL`没有对应更高未来收益，反而与更低R20/R60和更高失败概率相关。这可能与以下机制一致：

1. 放量可能代表困境股票在突破时出现情绪释放或交易拥挤，而不是持续买盘。
2. 高成交量可能同时包含卖方换手和分歧，不能简单解释为正面确认。
3. 极端放量事件可能在短期内吸收了部分后续价格反应。
4. 高波动或特定市场状态可能同时推动成交量和未来收益。

这些只是可能解释。本项目没有直接测量投资者类型、订单流、新闻或情绪，所以不能在正文中把其中任何一个写成已证实机制。

## 必须讨论的限制

- 样本只有193个事件，且29只股票出现多次事件。
- HC3没有处理同股票或共同日期的误差相关。
- 事件在年份间分布不均，2019年占比最高。
- 当前行业数据不是事件时点历史行业。
- 市场调整只使用沪深300简单收益，不是完整多因子风险调整。
- event-day收盘价和成交量在收盘后才完整可知。
- Failure60只有14个完整非失败观察。
- 多个收益窗口和稳健性规格带来多重检验风险。

## 可以怎样写

“The findings are consistent with high breakout-day relative volume reflecting trading intensity or crowding rather than unambiguously confirming a durable reversal. However, the event-study design does not identify the underlying mechanism.”

## 不应怎样写

- “高成交量导致突破失败。”
- “放量突破在A股一定无效。”
- “本研究已经排除了行业和市场状态影响。”
- “R60显著说明存在可执行套利策略。”

## 对应文件

- [THESIS_EVIDENCE_MATRIX.md](THESIS_EVIDENCE_MATRIX.md)：每个结论可以和不可以怎样表述。
- [Reviewer_Report.md](Reviewer_Report.md)：审稿人视角的问题和答辩风险。
- [model_audit.md](../08_Appendices/model_audit.md)与[variable_audit.md](../08_Appendices/variable_audit.md)：详细方法限制。
