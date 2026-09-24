# Paper Result Package v1

- 这是什么：用于论文Chapter 4的Table1–Table7、Figure1–Figure6和结果摘要。
- 输入：D009、baseline与robustness汇总。
- 输出：Excel表、PNG/PDF图和解释文字。
- 核心逻辑：整理已有实证结果，不改变样本或模型数值。
- 可修改：排版可在不改变数值时修订；数值变化必须重跑代码并创建新版本。
- 禁止：手工选择性删除不显著结果。
- 验证：每个编号都在artifact registry和Evidence Matrix中登记。
- 当前状态：`validated`。
