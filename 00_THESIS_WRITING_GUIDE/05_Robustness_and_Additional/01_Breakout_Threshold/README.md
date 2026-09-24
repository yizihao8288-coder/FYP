# 5.1 Breakout Threshold Sensitivity

## 检查什么

baseline把突破定义为收盘价高于横盘阻力位至少0.5%。这一小节把门槛改为1%和2%，检查结果是否只依赖较低门槛。

## 保持不变的部分

股票池、30%困境定义、自适应横盘、成交量算法和冷却规则保持不变。每个阈值重新识别事件并重新应用冷却，所以它们是不同事件池，不是从193个baseline事件中简单删除几行。

## 主要结果

| 突破阈值 | 事件数 | R20 Model 2样本量 | `ln_RVOL`系数 | p值 |
|---|---:|---:|---:|---:|
| 0.5% | 193 | 192 | -0.0237 | 0.0985 |
| 1% | 167 | 166 | -0.0376 | 0.0284 |
| 2% | 115 | 115 | -0.0617 | 0.0108 |

门槛提高后系数更负，但样本量明显下降。不能据此倒过来选择2%作为baseline，因为这会形成结果导向的参数选择。

## 对应文件

- [Table4.xlsx](Table4.xlsx)：三个阈值的论文结果表。
- [Figure4.png](Figure4.png)与[Figure4.pdf](Figure4.pdf)：核心系数比较。
- [threshold_robustness_summary.xlsx](threshold_robustness_summary.xlsx)：阈值、ATR20及设计优缺点的汇总。

旧`threshold_robustness.xlsx`是错误扩展名的JSON文件，不能作为Excel证据。
