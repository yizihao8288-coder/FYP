# Chapter 8 Appendices and Audit Materials

## 这一部分放什么

附录保存正文不宜展开、但答辩或复核时必须能找到的研究细节。它不是把所有原始数据塞进论文，而是提供变量、模型、流程和文件来源的审计链。

## 对应文件

- [Research_Master_Document.md](Research_Master_Document.md)：从研究问题到稳健性检验的完整流程。
- [variable_audit.md](variable_audit.md)：变量公式、信息泄露和遗漏变量审查。
- [model_audit.md](model_audit.md)：OLS、样本量、控制变量和进一步模型建议。
- [Research_Project_Map.xlsx](Research_Project_Map.xlsx)：数据、代码、结果、运行和决策总地图。
- [Project File Inventory](../../results/audit_20260919/Project_File_Inventory.md)：完整历史文件扫描结果。
- [Reproducibility](../../docs/REPRODUCIBILITY.md)：环境、命令、hash和恢复测试规则。

## 答辩时怎样使用

如果老师问“为什么是30%”“为什么是0.5%”“为什么用60日成交量”“有没有行业偏差”“数据能不能复现”，先在Research Master Document找到设计理由，再用Project Map定位对应代码和结果文件。

## 不应放入公开附录的内容

原始行情、处理行情、逐事件冻结数据和包含全部事件记录的工作簿不进入公开GitHub附录。这些材料在DataVault通过hash保存，可以现场验证，但公开论文只使用必要的汇总证据。
