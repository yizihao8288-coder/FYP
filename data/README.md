# Data

- 这是什么：项目内原始层、处理层和轻量manifest入口。
- 为什么存在：分离不可改写的来源证据、可重建处理层与Git可追踪索引。
- 研究步骤：市场数据获取、标准化、事件识别前的输入层。
- 输入：Eastmoney、BaoStock、历史成分和覆盖元数据。
- 输出：标准化HFQ日线、交易日历和manifest。
- 可修改：只允许在新版本目录中重建处理层；manifest可重新生成。
- 冻结规则：原始文件和正式处理版本不原地覆盖；行情与事件级数据不进入GitHub。
- 验证：DataVault manifest逐文件保存SHA-256；冻结数据另有固定hash。
- 当前状态：原始层`frozen_source`，正式处理层`frozen_input`。

Git中只保留本说明和`manifests/`；完整数据同步到`D:\FYP_DataVault\csi300_breakout_events`。
