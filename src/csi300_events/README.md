# csi300_events Package

- 这是什么：当前沪深300困境反转事件研究的Python包。
- 输入：Eastmoney HFQ、BaoStock状态/成员、交易日历和JSON配置。
- 处理：下载缓存、标准化、历史成分过滤、困境/横盘/突破识别、质量审计。
- 输出：正式候选、D008、D009上游数据和审计记录。
- 影响事件定义的模块：重点检查`hfq_final_sample.py`、`formal_pipeline.py`和配置读取逻辑。
- 可修改：只在测试覆盖且新建研究版本时修改事件逻辑。
- 冻结规则：baseline v1参数与hash不可覆盖。
- 验证：`tests/`、`formal_audit.py`和固定hash。
- 当前状态：`validated`。
