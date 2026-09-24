# 5.2 ATR20 Breakout

## 为什么使用ATR

固定百分比没有考虑不同股票的日常波动。ATR20用过去20个交易日的真实波幅衡量正常波动，再要求`Close > 横盘最高价 + ATR20`，因此门槛会随股票波动率变化。

## 主要结果

- ATR20定义得到100个事件。
- 受控R20回归的`ln_RVOL`系数为-0.0383。
- HC3标准误约0.0194，p=0.0485，R²约0.0507。

结果方向与baseline一致。ATR20在经济含义上更能适应个股波动率，但样本更小，所以它适合作为补充稳健性，不替代0.5% baseline。

## 对应文件

- [Table5.xlsx](Table5.xlsx)：0.5% baseline和ATR20的直接比较。
- ATR20完整工作簿包含事件明细，保存在DataVault；其路径和状态见[文件manifest](../../THESIS_FILE_MANIFEST.csv)。
