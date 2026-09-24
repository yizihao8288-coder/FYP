# Empirical Results Summary

## Sample description

The frozen baseline contains **193 events** from **160 stocks**, spanning **2016-02-23 to 2026-03-10**. There are no duplicate stock-date event keys. The baseline file is read without deleting outliers, winsorizing variables, imputing missing values, or re-screening events. R1, R3, R20, and R60 each have one missing observation; R5 is complete.

## Main findings

The Pearson correlation between ln_RVOL and R20 is **-0.157** (N=192). Mean R20 declines from **-0.30%** in the Low group to **-2.53%** in the High group. Mean R60 changes from **3.09%** to **-3.80%** across the same groups. These patterns describe associations in the observed event sample.

## Regression results

In the simple R20 model, the ln_RVOL coefficient is **-0.0262** (HC3 SE=0.0113, p=0.0204). With controls, the R20 coefficient is **-0.0237** (HC3 SE=0.0143, p=0.0985); this is marginal at the 10% level. In the controlled R60 model, the coefficient is **-0.0545** (HC3 SE=0.0266, p=0.0407). The estimates indicate that higher relative volume is associated with lower subsequent returns in these specifications.

## Robustness checks

Under the 0.5%, 1%, and 2% breakout thresholds, the controlled R20 coefficients are **-0.0237**, **-0.0376**, and **-0.0617**, respectively. The ATR20 specification reports **-0.0383** (p=0.0485). Across RVOL20, RVOL60, and RVOL120, the coefficients are **-0.0137**, **-0.0237**, and **-0.0298**. The sign is stable, while statistical precision varies with the volume benchmark window.

## Breakout failure analysis

In the saved logistic specification, the ln_RVOL coefficient is **1.1108** (odds ratio=3.0366, p=0.0161). The observed Failure20 rates are **78.46%**, **87.50%**, and **92.19%** for the Low, Medium, and High groups. Higher relative volume is therefore associated with a higher observed breakout-failure rate in this sample.

## Interpretation boundary

The evidence is associational. The event-study design and these regressions do not establish a directional mechanism. All reported estimates retain the original sample and model definitions.
