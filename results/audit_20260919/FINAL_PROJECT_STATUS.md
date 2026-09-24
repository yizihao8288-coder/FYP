# Project Overview

本项目研究A股沪深300历史成分股的技术性困境反转突破，检验突破日异常成交量与未来收益及失败风险的关系。研究已完成事件识别、冻结分析数据、描述统计、分组、相关、OLS、阈值/ATR/成交量窗口稳健性与Failure20；本次审计新增行业集中度诊断、CSI300市场调整收益与Failure60。

# Research Question

突破日`ln_RVOL`是否与R20/R60及突破失败风险系统相关？结论定位为条件相关，不是因果识别。

# Data

**Existing Result:** Eastmoney/AKShare后复权股票日线；BaoStock历史成员与状态；冻结baseline SHA-256 `94d52af2f33639cc336a6967d3fecfdccc2a2fc3f8b6ae265d116ae457ce0f76`。  
**Potential Improvement:** 外部验证point-in-time复权、官方交易日历和官方历史成分变更。  
**Recommended Future Work:** 增加可审计的事件时点行业与中国因子库。

# Sample

193个事件、160只股票，2016-02-23至2026-03-10；无同股票同日期重复。股票事件HHI=0.0071。当前行业快照下最大行业为“C27医药制造业”，事件份额8.81%，行业HHI=0.0395；这说明当前分类下没有单一行业支配，但不能排除历史行业构成偏差。

# Event Definition

困境跌幅至少30%；自适应120–252市场日横盘，Bandwidth≤25%、近120日归一化趋势斜率绝对值≤0.0004；baseline突破≥0.5%；同股事件间隔>60市场日。0.5%保持baseline，ATR20作为概念上更能适应波动率的补充稳健性，不替换主定义。

# Variables

`ln_RVOL=ln(突破日成交量/过去60个正常正成交量观察均量)`；控制为Drawdown、Duration、BreakoutStrength；收益为既有R1/R3/R5/R20/R60。本次新增R20_AR、R60_AR和Failure60。

# Models

Pearson；OLS+HC3 Model 1/2；Failure20/60 Logit。HC3未解决聚类依赖。

# Main Findings

**Existing Result:** R20 Model 2 `ln_RVOL`=-0.0237, p=0.0985；R60 Model 2=-0.0545, p=0.0407。方向均为负，R²约3%。  
**Existing Result — audit extension:** R20_AR Model 2=-0.0211, p=0.1080；R60_AR Model 2=-0.0392, p=0.1291。

# Robustness Findings

1%、2%和ATR20的R20受控系数仍为负；RVOL20结果较弱，RVOL120较强。Failure20显示高ln_RVOL与更高失败概率相关；Failure60同方向，但类别极不平衡，需谨慎。

# Remaining Issues

- 缺乏事件时点行业分类；当前行业结论仅为诊断。
- 缺少股票/日期聚类标准误、行业/年份固定效应与波动率控制。
- `threshold_robustness.xlsx`内容为JSON而非有效XLSX。
- README/PROJECT_CONTEXT含过时说法；本次未修改。
- close-based信号存在收盘后可知与同收盘价可交易性差异。

# Recommended Next Steps

1. 冻结本次审计输出并记录hash。
2. 在论文中优先加入市场调整表、研究限制和Reviewer问题回应。
3. 获得历史行业数据后，补做行业固定效应及行业集中度的事件时点审计。
4. 保持baseline不变，追加聚类标准误、年份固定效应和事件前波动率控制。
5. 修复旧的错误扩展名文件应通过“复制到新文件名”的归档流程完成，而不是覆盖原件。

Research audit completed.
