# Research Design

## Topic and objective

The study examines whether breakout-day abnormal volume predicts subsequent performance after a large drawdown and a long price-consolidation period among historical CSI300 constituents.

## Sample

- Universe: historical CSI300 membership measured at event date minus one market day.
- Actual event period: 2016-02-23 to 2026-03-10.
- Baseline: 193 events from 160 stocks.
- Same-stock events must be more than 60 market days apart.
- ST periods, poor price coverage and long suspensions are excluded according to the frozen configuration.

## Event construction

### Distress

The minimum adjusted close inside the consolidation interval must be at least 30% below the maximum adjusted close in the preceding 252 market days.

This does not mean that the breakout-day close remains 30% below the peak. Only 66 events satisfy that stronger condition.

### Consolidation

- Adaptive duration: 120 to 252 market days.
- Bandwidth: `max(adjusted close) / min(adjusted close) - 1 <= 25%`.
- Trend: the OLS slope of the most recent 120 valid closes, divided by their mean, must have absolute value no greater than 0.0004.
- The first 120 days must pass quality, bandwidth and trend checks. Earlier extension checks quality and bandwidth but does not re-estimate a full-period trend restriction.

There are 46 observations at the 252-day ceiling. The operational definition can include a U-shaped path or an early decline followed by flattening.

### Breakout

Baseline requires:

`event close / consolidation resistance - 1 >= 0.5%`

The 1%, 2% and ATR20 definitions are robustness specifications. They do not replace the baseline.

## Variables

- `ln_RVOL = ln(event-day volume / mean volume over the previous 60 normal positive-volume observations)`.
- `Drawdown`: distress magnitude used by the event algorithm.
- `Duration`: adaptive consolidation length.
- `BreakoutStrength`: event close relative to consolidation resistance.
- R1, R3, R5, R20 and R60 use the exact market-calendar target date. Missing prices remain missing.
- R20_AR and R60_AR subtract the corresponding CSI300 return.
- Failure20 and Failure60 equal one when a later close falls below the breakout price within the defined window.

## Models

- Pearson correlation between`ln_RVOL`and each return horizon.
- Model 1: `R_h ~ ln_RVOL`.
- Model 2: `R_h ~ ln_RVOL + Drawdown + Duration + BreakoutStrength`.
- OLS uses HC3 heteroskedasticity-robust standard errors.
- Failure models use binary logistic regression with model-based standard errors.

## Design rationale

- Historical membership avoids using only current index constituents.
- A 30% decline separates ordinary pullbacks from severe technical distress.
- Adaptive consolidation accommodates different recovery speeds.
- A small positive breakout threshold filters exact resistance touches while preserving sample size.
- Sixty normal volume observations provide a stable benchmark and exclude the event day.
- HC3 is retained as the registered baseline inference method. Clustered inference is a future robustness check.

## Interpretation boundary

The design is an event-study association analysis. It does not establish a causal volume mechanism. Because close and volume are known only after the close, returns measured from that close are not automatically achievable trading returns.

## Alternatives already tested

- Breakout thresholds: 0.5%, 1%, 2%.
- Volatility-scaled breakout: resistance plus ATR20.
- Volume benchmarks: 20, 60 and 120 normal observations.
- Consolidation parameter sets A, B and C.
- Failure windows: 20 and 60 market days.
- Historical fixed-120-day construction: deprecated and kept only in the archive.

