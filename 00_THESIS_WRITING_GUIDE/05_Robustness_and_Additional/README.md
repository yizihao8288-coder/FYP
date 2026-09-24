# Chapter 5 Robustness and Additional Analysis

## 为什么需要这一章

主结果可能只是某个突破阈值或成交量窗口的产物。稳健性检验的原则是每次只改变一个关键定义，其余股票池、困境和横盘逻辑保持不变，然后检查核心方向是否稳定。

## 本章结构

1. [Breakout Threshold](01_Breakout_Threshold/README.md)：0.5%、1%和2%。
2. [ATR20 Breakout](02_ATR20_Breakout/README.md)：用波动率调整突破门槛。
3. [Volume Window](03_Volume_Window/README.md)：RVOL20、RVOL60和RVOL120。
4. [Failure20](04_Failure20/README.md)：20日内是否跌回突破价下方。
5. [Market-adjusted Return](05_Market_Adjusted_Return/README.md)：股票收益减同期沪深300收益。
6. [Failure60](06_Failure60/README.md)：把失败观察窗口延长至60日。

## 总体判断

不同定义下`ln_RVOL`与R20的系数多数保持负向，但显著性会随阈值和成交量窗口变化。Failure20和Failure60都显示更高`ln_RVOL`与更高失败概率相关。结果方向具有一定一致性，但不能写成“所有规格完全稳健”。

## 对应文件

- [robustness_summary.xlsx](robustness_summary.xlsx)：全部核心模型的样本量、系数、标准误、p值和R²汇总。
- 各小节的Table4–Table7、Figure4–Figure6和补充汇总工作簿。
- 生成代码：[robustness_pipeline.py](../../robustness_pipeline.py)与[final_paper_analysis_pipeline.py](../../final_paper_analysis_pipeline.py)。

完整事件级稳健性工作簿仍在DataVault。公开写作包提供论文所需的聚合结果，不改变任何模型数值。
