# 5.6 Failure60 Analysis

## 为什么延长到60日

Failure20只观察较短窗口。Failure60检查突破后更长时间内是否跌回突破价，帮助判断所谓突破能否持续。

## 定义

后60个市场日内任一有效收盘价低于突破价时，`Failure60=1`。只有完整观察60日且从未跌破时才记为0；数据不足且未观察到失败时保持缺失。

## 主要结果

- 回归样本N=192。
- 失败事件178个，完整非失败事件14个。
- `ln_RVOL`系数1.5058。
- odds ratio 4.5079，p=0.0243。
- Low、Medium、High组失败率分别为87.50%、93.75%和96.88%。

方向与Failure20一致，但类别极不平衡。只有14个完整非失败样本，Logit估计可能不稳定，必须把这一点写进限制。

## 对应文件

- [failure60_results_summary.xlsx](failure60_results_summary.xlsx)：公开写作版，包含回归、RVOL分组、状态计数和Notes。

完整源工作簿的Event Data页保存在DataVault。公开版没有重新估计模型，只复制原汇总结果。
