# 4.2 Main OLS Regression

## 这一部分解决什么问题

分组均值可能同时受到前期跌幅、横盘长度和突破幅度影响。回归用于在控制这些可观测特征后，检查`ln_RVOL`与未来收益是否仍有系统关系。

## 模型

- Model 1：`R_h ~ ln_RVOL`
- Model 2：`R_h ~ ln_RVOL + Drawdown + Duration + BreakoutStrength`

分别对R1、R5、R20和R60运行OLS，并使用HC3稳健标准误。

## 主要结果

- R20 Model 1：`ln_RVOL`系数-0.0262，HC3 SE 0.0113，p=0.0204，R²=0.0246，N=192。
- R20 Model 2：系数-0.0237，HC3 SE 0.0143，p=0.0985，R²=0.0302，N=192。
- R60 Model 2：系数-0.0545，HC3 SE 0.0266，p=0.0407，R²约0.0308，N=192。

加入控制变量后，R20证据减弱到10%水平，而R60仍在5%水平显著。所有模型解释度都较低，因此不能把成交量视为完整的收益预测模型。

## 系数怎样理解

`ln_RVOL`增加1个单位时，Model 2估计的R20平均下降约2.37个百分点。这个说法描述回归中的条件相关，不代表把成交量人为提高就会造成收益下降。

## 对应文件

- [Table2.xlsx](Table2.xlsx)：论文主回归表。
- [regression_results.xlsx](regression_results.xlsx)：全部因变量、模型、控制变量、HC3标准误和p值。
- 生成代码：[analysis_pipeline.py](../../../analysis_pipeline.py)。

## 关键限制

HC3处理异方差，但没有处理同股票重复事件或共同市场冲击造成的误差相关。论文应把聚类标准误列为后续改进。
