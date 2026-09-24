# Baseline Results v1

- 这是什么：193个冻结事件的描述统计、RVOL分组、相关和OLS+HC3结果。
- 输入：D009，hash见Control Center。
- 输出：统计表、回归表、样本完整性表和辅助图。
- 核心逻辑：完全读取已有字段，不重新识别事件，不删异常值、不winsorize。
- 可修改：README说明可更新；数值结果必须由`analysis_pipeline.py`重跑到新版本。
- 禁止：覆盖D009、改变事件定义、把相关性写成因果。
- 验证：与`catalog/artifacts.csv`和审计报告交叉检查。
- 当前状态：`validated`。
