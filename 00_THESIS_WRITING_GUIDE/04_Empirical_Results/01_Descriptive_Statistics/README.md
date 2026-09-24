# 4.1 Descriptive Statistics

## 为什么先做这一步

回归之前要先说明变量的大致水平、波动和极端范围。这样读者才能判断样本是否异常，以及后面的系数是否可能被少数事件影响。

## 方法

对Drawdown、Duration、Bandwidth、BreakoutStrength、RVOL、ln_RVOL和R1/R3/R5/R20/R60计算有效数、缺失数、均值、中位数、标准差、最小值、最大值、25%分位数和75%分位数。异常值保留，不删除、不winsorize。

## 主要结果

- 平均Drawdown约为-41.04%，说明事件确实经历了明显下跌。
- 平均横盘长度约185个市场日，中位数172日。
- 平均RVOL约3.96，中位数约3.23，分布右偏。
- 平均R20约-1.27%，中位数约-2.60%。
- 平均R60约-0.92%，中位数约-3.53%。
- R60最大值约108.57%，显示长期收益分布存在明显右尾，因此均值和中位数要同时报告。

## 可以怎样写

“The final sample contains 193 events. Relative volume is right-skewed, with a mean of 3.96 and a median of 3.23. Average 20-day and 60-day returns are -1.27% and -0.92%, respectively, while their medians are more negative.”

## 不应怎样写

不能因为均值为负就写“突破策略亏损”。这些收益从事件日收盘计算，而信号完整信息在收盘后才可知。

## 对应文件

- [Table1.xlsx](Table1.xlsx)：论文中使用的精简描述统计表。
- [descriptive_statistics.xlsx](descriptive_statistics.xlsx)：包含全部变量和各收益窗口正收益比例的完整版本。
