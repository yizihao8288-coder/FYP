# Manifests

- 这是什么：GitHub通道和DataVault通道的文件指纹清单，以及严格验证报告。
- 为什么存在：在不把大数据上传Git的前提下证明数据身份和同步完整性。
- 输入：两个通道中实际存在的文件。
- 输出：`project_channel_manifest.json`、`vault_channel_manifest.json`和`verification_report.json`。
- 可修改：只能由`tools/project_control.py`重新生成。
- 冻结规则：任何hash变化必须解释；baseline和event pool hash不得变化。
- 验证：运行`python tools/project_control.py report`。
- 当前状态：`generated/validated`。

SHA-256只证明字节是否相同，不自动证明数据定义合理；研究合理性由Research Design和审计报告说明。
