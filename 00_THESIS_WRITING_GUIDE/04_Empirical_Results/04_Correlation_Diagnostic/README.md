# 4.4 Correlation Diagnostic

## 这一部分的作用

Pearson相关系数是最简单的线性关联描述，不控制其他变量。它用于帮助读者理解方向，不替代回归。

## 方法

分别计算`ln_RVOL`与R1、R3、R5、R20和R60的Pearson相关。缺失配对不填0，按每个窗口可用数据计算。

## 主要结果

- R1：-0.1342，N=192。
- R3：-0.0835，N=192。
- R5：-0.0128，N=193。
- R20：-0.1568，N=192。
- R60：-0.1755，N=192。

短窗口R5几乎没有线性关系，R20和R60负相关更明显。散点图保留所有极端值，因此拟合线可能受少数观察影响。

## 对应文件

- [correlation_table.xlsx](correlation_table.xlsx)：相关系数、配对样本量和散点数据。
- [Figure3.png](Figure3.png)与[Figure3.pdf](Figure3.pdf)：`ln_RVOL`和R20散点及线性拟合。

## 解释边界

相关系数不能控制Drawdown、Duration或BreakoutStrength，也不能说明因果方向。主结论应以回归和稳健性结果共同判断。
