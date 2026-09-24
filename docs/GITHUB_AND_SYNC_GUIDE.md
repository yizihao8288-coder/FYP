# GitHub and DataVault Sync Guide

## 两个通道分别解决什么问题

- 私有GitHub保存小而可读、需要版本差异的内容：代码、测试、配置说明、研究决定、manifest、论文表图。
- DataVault保存大而敏感、适合逐字节校验的内容：原始行情、处理行情、冻结数据、完整旧输出和归档。
- manifest保存每个文件的相对路径、大小和SHA-256。GitHub中的代码通过数据ID与hash引用DataVault中的输入。

Git不是数据备份系统，云盘也不是代码版本控制系统。双通道把两类工作分别交给擅长的工具。

## GitHub规则

1. 仓库必须保持Private，答辩后再单独审查公开范围。
2. 不提交`.runtime`、行情数据、事件级冻结数据、密钥或本机私有配置。
3. 提交前运行`python tools/project_control.py verify`并检查`git status`。
4. 每个稳定研究变更形成一个可解释的commit；不要把不同研究决定混进同一提交。
5. 需要改变事件定义时新建版本，不覆盖baseline v1。

## DataVault云同步规则

本地根目录已经固定为`D:\FYP_DataVault\csi300_breakout_events`。请选择支持私有文件夹且可把同步根目录放在D盘的云盘客户端。当前机器的OneDrive根目录在C盘，因此不符合本项目存储规则。

云盘接入后应同步整个DataVault，保留版本历史和回收站，并确认没有“仅在线”占位导致离线无法校验。同步成功不等于验证成功：仍需在另一D盘目录读取manifest并运行hash验证。

## 一次完整同步

```powershell
& 'D:\Python\Anaconda3\envs\clean\python.exe' tools\project_control.py sync-vault
& 'D:\Python\Anaconda3\envs\clean\python.exe' tools\project_control.py curate-results
& 'D:\Python\Anaconda3\envs\clean\python.exe' tools\project_control.py inventory
& 'D:\Python\Anaconda3\envs\clean\python.exe' tools\project_control.py report
git status --short
git add --all
git commit -m "docs: update research evidence and manifests"
git push
```

## 初学者最需要记住的三点

1. Commit是“研究逻辑的版本”，不能代替数据快照。
2. Hash是文件指纹；同名文件hash不同就不是同一份证据。
3. Sync表示复制到另一位置；只有在另一位置重新读取并校验，才叫恢复测试成功。
