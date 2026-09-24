# Chapter 3 Data and Methodology

## 这一章要回答什么

这一章解释样本从哪里来、一个事件怎样被识别、变量怎样计算、统计模型为什么这样设定。目标是让别人能判断研究设计是否合理，并能追溯到代码和冻结数据。

## 3.1 样本和数据来源

股票池使用事件日前一日的沪深300历史成分身份，而不是用今天的成分股回填历史。股票日线使用后复权OHLCV构造事件和收益；BaoStock提供历史成员、交易状态和ST信息。

最终baseline有193个事件、160只股票，事件日期为2016-02-23至2026-03-10。没有同股票同日期重复事件。29只股票出现多次事件，因此后续需要把同股相关性作为限制。

当前行业快照中最大行业事件份额为8.81%，行业HHI为0.0395，没有单一行业支配样本。但行业数据不是事件时点历史分类，因此不能声称已经控制历史行业固定效应。

## 3.2 事件构造

### 困境

横盘区间内最低收盘价相对之前252个市场日最高收盘价跌幅至少30%。这一步先限定研究对象确实经历过明显下跌。

### 横盘

横盘长度自适应为120–252个市场日。带宽定义为`最高收盘价/最低收盘价-1`，要求不超过25%；近120个有效收盘的归一化线性趋势斜率绝对值不超过0.0004。

这个定义解决的是“价格是否收敛”，但不能保证每张K线图都视觉上完全水平。71个事件的近120日斜率仍为负，说明样本可能包含缓慢下降或U形路径，论文必须如实披露。

### 突破

baseline要求事件日收盘价至少高于横盘最高收盘价0.5%。同一股票的两个事件必须间隔超过60个市场日，避免短时间重复计算同一波行情。

## 3.3 变量构造

- `RVOL = 突破日成交量 / 过去60个正常且成交量为正的交易观察平均成交量`。
- `ln_RVOL = ln(RVOL)`。
- `R1/R3/R5/R20/R60 = 第h个目标市场日收盘价 / 事件日收盘价 - 1`。
- 控制变量为`Drawdown`、`Duration`和`BreakoutStrength`。
- 不插值、不填0、不使用非交易日替代、不修改事件日期。

R1、R3、R20和R60各缺1个观察，R5没有缺失。

## 3.4 统计模型

Pearson相关用于描述两个变量的线性关系。OLS Model 1只放`ln_RVOL`；Model 2加入Drawdown、Duration和BreakoutStrength。OLS使用HC3稳健标准误处理异方差，但HC3不能处理同一股票或同一市场日期的相关误差。

Failure20和Failure60使用Logit，因为结果变量只有失败或不失败两种状态。Logit结果报告系数、odds ratio和p值。

## 这一章得到的主要样本结论

- 193个事件，160只股票。
- 同股票同日期重复数为0。
- 2019年事件占21.24%，说明样本存在年份集中风险。
- 当前行业快照没有显示单一行业支配，但不能替代历史行业分类。

## 对应文件

- [sample_audit.xlsx](sample_audit.xlsx)：股票、行业、年份和重复事件审计。
- [event_year_distribution.png](event_year_distribution.png)：年度事件分布图。
- [data_quality_report.md](data_quality_report.md)：变量缺失、范围和异常值检查。
- [hfq_final_report.md](hfq_final_report.md)与[hfq_final_summary.json](hfq_final_summary.json)：冻结样本摘要。
- [model_sample_counts.csv](model_sample_counts.csv)：各模型有效样本量。
- [return_window_completeness.csv](return_window_completeness.csv)：收益窗口完整性。
- [Research Design](../../docs/RESEARCH_DESIGN.md)和[Data Dictionary](../../docs/DATA_DICTIONARY.md)：正式方法与字段定义。
- [methodology_config.json](../../config/methodology_config.json)：实际采用的参数。
- [事件构造代码](../../src/csi300_events/hfq_final_sample.py)与[baseline分析代码](../../analysis_pipeline.py)。

完整事件池和最终分析CSV只在DataVault，不在公开GitHub。对应hash见本目录上级的文件manifest。
