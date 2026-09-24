# FYP论文写作入口

如果你的目标是写论文，请从这个目录开始，不需要先理解`catalog`、`src`或`tools`。

这个目录按照论文写作顺序组织。每一章都说明五件事：这一章回答什么问题、为什么这样做、具体方法、已经得到什么结果、应打开哪些文件。表格和图片直接放在对应小节下面。

## 当前论文结构

| 顺序 | 论文部分 | 当前状态 | 主要作用 |
|---|---|---|---|
| 1 | [Introduction](01_Introduction/README.md) | 可开始写作 | 说明研究背景、问题、贡献和边界 |
| 2 | [Literature Review](02_Literature_Review/README.md) | 尚缺正式文献库 | 建立理论依据和研究缺口 |
| 3 | [Data and Methodology](03_Data_and_Methodology/README.md) | 证据齐全 | 说明样本、事件、变量和模型怎么构造 |
| 4 | [Empirical Results](04_Empirical_Results/README.md) | 证据齐全 | 报告描述统计、回归、分组和相关性 |
| 5 | [Robustness and Additional Analysis](05_Robustness_and_Additional/README.md) | 证据齐全 | 检查结论是否依赖定义，并分析突破失败 |
| 6 | [Discussion](06_Discussion/README.md) | 可开始写作 | 解释经济含义、限制和不能过度声称的内容 |
| 7 | [Conclusion](07_Conclusion/README.md) | 可开始写作 | 回答研究问题并总结贡献和未来工作 |
| 8 | [Appendices](08_Appendices/README.md) | 证据齐全 | 保存审计、变量、模型和项目地图 |

## 一分钟理解研究

研究对象是沪深300历史成分股中经历至少30%下跌、随后横盘并向上突破的事件。核心问题是：突破日的异常成交量越高，未来收益是否越好，突破是否更不容易失败？

最终样本有193个事件、160只股票，事件日期为2016-02-23至2026-03-10。核心解释变量是`ln_RVOL`，即突破日成交量相对过去60个正常交易观察平均成交量的自然对数。

实际结果没有支持“放量越大，未来收益越高”。样本内更高的`ln_RVOL`通常对应更低的R20和R60，并对应更高的Failure20和Failure60概率。不过这些结果是相关关系，不是因果关系，也不是可以在突破日收盘价直接执行的交易策略。

## 写作时怎样使用

1. 先打开对应章节的`README.md`，理解该部分的逻辑。
2. 再打开该目录下面的Excel或图片，复制表格、核对数字或插入图形。
3. 使用README中的“可以怎样写”，不要把相关关系写成因果关系。
4. 需要确认文件来源时，查看[THESIS_FILE_MANIFEST.csv](THESIS_FILE_MANIFEST.csv)。
5. 需要确认整套研究是否有遗漏时，查看[FILE_COVERAGE_AUDIT.md](FILE_COVERAGE_AUDIT.md)。

## 文件边界

公开仓库包含写论文所需的汇总表、图、研究设计、代码和审计报告。完整事件池、最终分析数据、原始行情和包含逐事件记录的详细工作簿仍在D盘DataVault。它们没有丢失，也不是忘记上传，而是为了避免把未经许可确认的事件级数据放进公开Git历史。

冻结文件保持不变：

- `final_event_pool_v1.csv`：193个事件，SHA-256为`6e8485a4716caa6035bf0616d0b4dd9b7b91da8adf7c611053933460f836a889`。
- `final_analysis_dataset_v1.csv`：193行、160只股票，SHA-256为`94d52af2f33639cc336a6967d3fecfdccc2a2fc3f8b6ae265d116ae457ce0f76`。

## 尚未完成的论文材料

当前真正缺少的是经过核实的文献综述来源库，而不是实证结果。仓库中没有足够的论文PDF、DOI或参考文献管理文件，因此第二章不能假装已经完成。聚类标准误、事件时点历史行业和额外波动率控制属于后续改进，不属于现有结果。
