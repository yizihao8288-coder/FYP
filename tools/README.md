# Tools

- 这是什么：项目治理和工作簿生成工具，不是事件筛选算法。
- 输入：workspace、DataVault、冻结hash和登记定义。
- 输出：迁移清单、manifest、验证报告、项目地图和同步状态。
- 核心逻辑：扫描、分类、逐文件SHA-256、只复制变化文件、检查Git禁入路径。
- 可修改：可以改展示和登记；不得更改baseline hash来掩盖数据变化。
- 删除行为：`project_control.py`没有删除命令，待删除项只列给用户审批。
- 验证：Python语法检查、`tests/test_project_control.py`和严格`verify`。
- 当前状态：`validated`。

## 本目录的重要工具

- `project_control.py`：扫描项目、核对冻结hash、生成登记表和检查遗漏文件。
- `build_thesis_writing_package.py`：把已经验证的表、图和报告按论文章节复制到`00_THESIS_WRITING_GUIDE`，并用SHA-256确认副本没有被改写。
- `build_public_summary_workbooks.mjs`：从完整审计工作簿提取可以公开的汇总页。它不公开事件级明细，并为工作簿加入用途、方法和限制说明。

这两个“build”工具只整理和展示既有结果，不重新筛选事件，也不改动冻结数据。
