# 5.4 Failure20 Analysis

## 为什么增加失败分析

收益回归只看目标日的最终收益，不能说明突破过程中是否跌回突破价。Failure20直接检验突破后20个市场日内是否出现任一收盘价低于突破日价格。

## 定义和模型

- 如果后20个市场日最低有效收盘价低于突破价，`Failure20=1`。
- 否则为0。
- Logit模型：`Failure20 ~ ln_RVOL + Drawdown + Duration + BreakoutStrength`。

## 主要结果

- `ln_RVOL`系数为1.1108。
- odds ratio为3.0366。
- p=0.0161，N=193。
- Low、Medium、High组失败率分别为78.46%、87.50%和92.19%。

样本内更高相对成交量与更高失败概率相关。odds ratio不是概率增加倍数，也不能解释为因果效应。

## 对应文件

- [Table7.xlsx](Table7.xlsx)：Failure20 Logit论文表。
- [Figure6.png](Figure6.png)与[Figure6.pdf](Figure6.pdf)：三组Failure20比例。

现有Logit使用模型型标准误，不是HC3。论文中必须如实说明。
