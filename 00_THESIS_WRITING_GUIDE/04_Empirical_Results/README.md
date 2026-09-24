# Chapter 4 Empirical Results

## 这一章的写作顺序

这一章先说明样本长什么样，再报告主回归，然后用分组和相关图帮助读者理解关系。不要一开始就只挑显著结果。

1. [Descriptive Statistics](01_Descriptive_Statistics/README.md)
2. [Main OLS Regression](02_Main_OLS_Regression/README.md)
3. [RVOL Group Analysis](03_RVOL_Group_Analysis/README.md)
4. [Correlation Diagnostic](04_Correlation_Diagnostic/README.md)

## 总体结果

样本内，`ln_RVOL`越高，R20和R60总体越低。R20简单模型在5%水平显著，但加入控制变量后只在10%水平边际显著；R60控制模型在5%水平显著。模型R²约为3%，说明成交量只能解释未来收益的一小部分。

这个结果回答的是“变量之间是否系统相关”，不是“成交量是否造成未来收益变化”。

## 对应文件

- [empirical_results_summary.md](empirical_results_summary.md)：第四章主结果文字摘要。
- 各小节目录中的Table1–Table3、Figure1–Figure3和完整分析工作簿。
- 结果由[final_paper_analysis_pipeline.py](../../final_paper_analysis_pipeline.py)和[analysis_pipeline.py](../../analysis_pipeline.py)生成。

当前状态：`validated`。数字可以写进论文，但必须保留样本量、p值、R²和解释边界。
