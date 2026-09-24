# Docs

- 这是什么：研究设计、决策、复现方法、证据链和当前状态的说明中心。
- 为什么存在：让研究结论不依赖记忆或聊天记录。
- 研究步骤：贯穿数据、事件、模型、稳健性、论文写作和答辩。
- 输入：冻结数据的事实、代码实现、审计结果和用户确认的研究选择。
- 输出：Control Center、设计说明、数据字典、Decision Log、Evidence Matrix和项目地图。
- 可修改：说明文字可在新证据出现时更新，但必须写明日期和依据。
- 禁止覆盖：冻结hash和已经确认的历史决策不得无说明改写。
- 如何验证：运行`python tools/project_control.py verify`，并核对文档中的路径和登记表。
- 当前状态：`validated`。

唯一入口是[RESEARCH_CONTROL_CENTER.md](RESEARCH_CONTROL_CENTER.md)。旧状态文件若与其冲突，以Control Center和冻结hash为准。
