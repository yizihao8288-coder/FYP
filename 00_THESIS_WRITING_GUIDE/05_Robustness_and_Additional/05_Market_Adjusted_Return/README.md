# 5.5 Market-adjusted Return

## 为什么做市场调整

股票未来收益可能只是跟随同期大盘。市场调整收益用股票收益减去同一事件窗口内的沪深300收益，检查负向关系是否完全由市场走势造成。

## 定义

- `R20_AR = R20 - CSI300_R20`
- `R60_AR = R60 - CSI300_R60`

事件日和目标日与原收益变量保持一致，不插值、不替换日期。

## 主要结果

- R20_AR Model 2：`ln_RVOL`系数-0.0211，p=0.1080，R²=0.0487，N=192。
- R60_AR Model 2：系数-0.0392，p=0.1291，R²=0.0251，N=192。

市场调整后方向仍为负，但两个控制模型都没有达到传统5%显著水平。正确结论是“方向保持，统计精度下降”，不是“市场调整后完全稳健”。

## 对应文件

- [market_adjusted_results_summary.xlsx](market_adjusted_results_summary.xlsx)：公开写作版，包含Summary、Regression Results和Notes。

完整源工作簿另有Event Data页，保存在DataVault。本公开版本只移除逐事件记录，不改变汇总和回归数值。
