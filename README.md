# 沪深300困境反转量价研究

本仓库保存研究代码、研究设计、验证规则、论文级结果和文件索引。原始行情、处理缓存和冻结事件级数据保存在D盘私有DataVault，不上传GitHub。

## 从这里开始

1. [Research Control Center](docs/RESEARCH_CONTROL_CENTER.md)：当前做到哪里、哪些文件有效、下一步是什么。
2. [Research Design](docs/RESEARCH_DESIGN.md)：事件定义、变量、模型和设计理由。
3. [Data Dictionary](docs/DATA_DICTIONARY.md)：数据层级与变量公式。
4. [Thesis Evidence Matrix](docs/THESIS_EVIDENCE_MATRIX.md)：论文结论由哪些表、图和模型支持。
5. [Reproducibility](docs/REPRODUCIBILITY.md)：如何验证和重新运行。
6. [Project Map workbook](docs/Research_Project_Map.xlsx)：适合在Excel中浏览的项目总览。

## 当前权威状态

- baseline：`final_analysis_dataset_v1.csv`
- 事件：193
- 股票：160
- 事件日期：2016-02-23至2026-03-10
- baseline SHA-256：`94d52af2f33639cc336a6967d3fecfdccc2a2fc3f8b6ae265d116ae457ce0f76`
- 事件池 SHA-256：`6e8485a4716caa6035bf0616d0b4dd9b7b91da8adf7c611053933460f836a889`
- 事件筛选逻辑、原始baseline和既有统计结果在项目整理中不改变。

## 双通道同步

| 通道 | 保存内容 | 默认位置 |
|---|---|---|
| GitHub | 代码、测试、配置、说明、manifest、论文级结果 | 本仓库 |
| DataVault | 原始数据、处理数据、冻结数据、完整旧输出和旧方案 | `D:\FYP_DataVault\csi300_breakout_events` |

DataVault目前已经在D盘建立。它仍需接入一个同样位于D盘的私有云盘客户端，才能成为真正的异地同步副本。

## 研究流程

```text
原始市场与成员数据
    ↓
标准化HFQ处理层
    ↓
困境、横盘、突破事件识别
    ↓
final_event_pool_v1.csv
    ↓
RVOL与未来收益
    ↓
final_analysis_dataset_v1.csv
    ↓
baseline分析、稳健性、研究审计
    ↓
Table1-7、Figure1-6及论文结论
```

## 常用命令

```powershell
.\run.ps1 -Test

D:\Python\Anaconda3\envs\clean\python.exe tools\project_control.py verify
D:\Python\Anaconda3\envs\clean\python.exe tools\project_control.py inventory
D:\Python\Anaconda3\envs\clean\python.exe tools\project_control.py report
```

## 修改规则

- 不覆盖`final_event_pool_v1.csv`或`final_analysis_dataset_v1.csv`。
- 不因显著性改变事件参数。
- 新结果必须登记输入数据ID、hash、代码和模型。
- 每次稳定变更都更新Control Center和Run Registry。
- 旧文件先归档，再经用户确认删除。
- 不把行情数据、缓存、密钥或本机配置提交到Git。

## 已知问题

- `outputs/hfq_final/threshold_robustness.xlsx`实际是JSON内容，不是有效XLSX；原件只归档，不覆盖。
- 现有HC3标准误不处理同股或同日期相关。
- 当前行业快照不能代替事件时点历史行业。
- close-based信号在收盘后才完整可知，不能直接解释为同一收盘价可执行策略。

