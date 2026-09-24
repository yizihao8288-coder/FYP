# Config

- 这是什么：研究参数与本机路径配置。
- 为什么存在：把“研究定义”与“这台电脑在哪里装Python”分开。
- 研究步骤：正式样本构造和本地执行入口。
- 输入：研究设计选择与本机D盘路径。
- 输出：脚本读取的JSON参数和可选PowerShell路径配置。
- 可修改：`local_paths.psd1`只可改本机路径；研究JSON参数若改变必须新建版本并记录Decision Log。
- 冻结规则：`hfq_final_sample.json`和正式价格审计配置属于baseline定义，不得为提高显著性覆盖。
- 验证：配置由现有测试和正式审计读取；本机路径由`run.ps1`强制检查为D盘。
- 当前状态：baseline配置`frozen`，路径示例`validated`。

从`local_paths.example.psd1`复制生成`local_paths.psd1`；后者被Git忽略，因为它只描述一台机器。
