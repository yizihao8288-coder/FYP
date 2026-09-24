# Data Dictionary

## Data layers

| Layer | Meaning | Editing rule |
|---|---|---|
| Raw | Original market, membership and status downloads | Never overwrite; add a new dated acquisition |
| Processed | Standardized HFQ prices, trading calendar and merged status | Rebuild from raw into a new version |
| Frozen | Final event pool and final analysis dataset | Never overwrite or impute |
| Results | Aggregates, models, tables and figures derived from frozen data | Version by analysis release |
| Archive | Old definitions and complete pre-migration outputs | Do not use in current claims |

The detailed dataset registry is `catalog/datasets.csv`.

## Core variables

| Variable | Definition | Unit | Missing policy |
|---|---|---|---|
| event_date | Baseline breakout date | market date | never changed |
| Drawdown | consolidation minimum / preceding 252-day peak - 1 | decimal return | event requires value |
| Duration | consolidation length | market days | event requires value |
| bandwidth | maximum consolidation close / minimum consolidation close - 1 | ratio | event requires value |
| trend_slope | recent 120-close OLS slope divided by mean close | normalized slope | event requires value |
| BreakoutStrength | breakout close / resistance - 1 | decimal return | event requires value |
| RelativeVolume | event volume / previous 60-normal-observation mean volume | ratio | no interpolation |
| ln_RVOL | natural logarithm of RelativeVolume | log ratio | requires positive RVOL |
| R1 | target close at t+1 market day / event close - 1 | decimal return | missing stays missing |
| R3 | target close at t+3 market days / event close - 1 | decimal return | missing stays missing |
| R5 | target close at t+5 market days / event close - 1 | decimal return | missing stays missing |
| R20 | target close at t+20 market days / event close - 1 | decimal return | missing stays missing |
| R60 | target close at t+60 market days / event close - 1 | decimal return | missing stays missing |
| R20_AR | R20 minus CSI300 return over the same stored target dates | decimal return | missing if either component missing |
| R60_AR | R60 minus CSI300 return over the same stored target dates | decimal return | missing if either component missing |
| Failure20 | any valid close in next 20 market days below breakout price | binary | incomplete unresolved case stays missing |
| Failure60 | any valid close in next 60 market days below breakout price | binary | incomplete unresolved case stays missing |

## Missingness rules

- No interpolation.
- No zero filling.
- No non-trading-day substitution.
- No event-date modification.
- A target-date suspension or missing valid price remains missing and receives a reason field.

## Frozen dataset identifiers

- D008: `final_event_pool_v1.csv`.
- D009: `final_analysis_dataset_v1.csv`.
- D009 is the only permitted input for baseline descriptive, correlation and regression analyses.

