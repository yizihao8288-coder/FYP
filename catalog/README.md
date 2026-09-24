# Catalog

- 这是什么：文件、数据、代码、结果、运行和决策的机器可读总账。
- 为什么存在：保证每个文件都有归属，任何结论都能回溯到输入与代码。
- 研究步骤：项目治理和证据追溯层，不参与事件筛选。
- 输入：workspace扫描、冻结hash、代码与结果登记定义。
- 输出：`migration_inventory.csv`、六类registry、未登记报告和迁移审阅工作簿。
- 可修改：登记定义由`tools/project_control.py`维护；人工备注可通过正式变更补充。
- 冻结规则：不得手工伪造hash或把冻结文件标为可删除。
- 验证：运行`inventory`后再运行`report`；未登记文件数必须为0。
- 当前状态：`validated`。

`Migration_Review.xlsx`用于你逐项审阅迁移和待删除候选；它不会自动删除或移动原文件。
