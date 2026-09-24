# Reproducibility

## 复现边界

当前baseline是冻结证据，不应通过重新筛选来“重造”它。复现分为两层：

1. 完整性复现：验证193个事件、160只股票、无同股同日重复和两个冻结hash。
2. 计算复现：在明确需要时，用冻结输入运行分析脚本并比较输出；不得覆盖冻结文件。

## 必要路径

- Git项目：`D:\HuaweiMoveData\Users\yish8288\Desktop\FYP\csi300_breakout_events`
- DataVault：`D:\FYP_DataVault\csi300_breakout_events`
- 本机路径模板：`config/local_paths.example.psd1`
- 精确依赖版本：`requirements-lock.txt`

任何临时目录、Python环境和DataVault都必须位于D盘。不要使用当前位于C盘的OneDrive作为研究数据同步根目录。

## 首选命令

```powershell
Set-Location 'D:\HuaweiMoveData\Users\yish8288\Desktop\FYP\csi300_breakout_events'
.\run.ps1 -Test
.\run.ps1 -Audit
.\run.ps1 -FinalHFQ
.\run.ps1 -Test
```

研究控制命令：

```powershell
.\run.ps1 -Test
& 'D:\Python\Anaconda3\envs\clean\python.exe' tools\project_control.py inventory
& 'D:\Python\Anaconda3\envs\clean\python.exe' tools\project_control.py verify
& 'D:\Python\Anaconda3\envs\clean\python.exe' tools\project_control.py report
```

`sync-vault`只复制变化的文件；它不删除源文件。`curate-results`只把已验证的小型结果复制到规范目录。

## 验收标准

- baseline SHA-256为`94d52af2f33639cc336a6967d3fecfdccc2a2fc3f8b6ae265d116ae457ce0f76`。
- event pool SHA-256为`6e8485a4716caa6035bf0616d0b4dd9b7b91da8adf7c611053933460f836a889`。
- 193个事件、160只股票、重复键为0。
- `data/manifests/verification_report.json`的`pass`为`true`。
- `catalog/unregistered_files_report.md`的未登记数为0。
- Git不跟踪`data/raw`、`data/processed`、`outputs`、`.runtime`或密钥。
- 两本控制工作簿均可重新打开且没有公式错误。

## 恢复测试

在另一个D盘目录执行：克隆GitHub仓库；将DataVault接入同一路径或设置`FYP_DATA_ROOT`；安装锁定依赖；运行`verify`。恢复测试只有在hash、文件登记和正式结果来源链全部一致时才算成功。当前本机已建立恢复规则，但真正的第二目录/第二设备恢复仍需在GitHub远端和D盘云同步完成后执行。
