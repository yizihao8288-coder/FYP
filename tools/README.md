# Tools

- 这是什么：项目治理和工作簿生成工具，不是事件筛选算法。
- 输入：workspace、DataVault、冻结hash和登记定义。
- 输出：迁移清单、manifest、验证报告、项目地图和同步状态。
- 核心逻辑：扫描、分类、逐文件SHA-256、只复制变化文件、检查Git禁入路径。
- 可修改：可以改展示和登记；不得更改baseline hash来掩盖数据变化。
- 删除行为：`project_control.py`没有删除命令，待删除项只列给用户审批。
- 验证：Python语法检查、`tests/test_project_control.py`和严格`verify`。
- 当前状态：`validated`。
