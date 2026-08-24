# forecast-leaderboard

A GitHub-Action cron that, once a day:

1. **scrapes** a public data source and commits the raw observation to git,
2. **scores** the forecasts it made earlier, now that the outcome exists,
3. **forecasts** the next 7 periods with a tabular foundation model *and* three
   baselines, and commits that too — in a **separate, final commit**,
4. rebuilds a static leaderboard page.

The point is not the model. The point is that every prediction is in git with a
commit that provably contains no knowledge of the outcome, so the track record
cannot be retro-fitted. [`CREDIBILITY.md`](CREDIBILITY.md) sets out exactly how
a skeptic checks that, and — at equal length — where the guarantee is weak.

Generalises the pattern in [`yanndebray/git-scraping101`](https://github.com/yanndebray/git-scraping101)
(scrape-and-commit) with the forecasting engine behind
tabcast (`api.tabicl.org`).

---

## Quick start

No dependencies. The whole package is stdlib-only Python 3.11+, so CI needs
nothing but `actions/setup-python`.

```bash
export PYTHONPATH=src

python -m flb sources                 # what's registered
python -m flb bootstrap --years 3     # one-off: backfill history from the archive
python -m flb tick                    # scrape → score → forecast → rebuild page
python -m flb backtest --origins 40   # offline rolling-origin replay (calls the FaaS)
open site/index.html
```

`tick` is what the Action runs, except the Action splits it into three commits.

## Layout

```
.github/workflows/tick.yml   the cron; three ordered commits per day
src/flb/
  sources.py     pluggable data sources (Source ABC + OpenMeteoDaily)
  models.py      tabicl (FaaS) + persistence / seasonal_naive / climatology
  pipeline.py    scrape · score · forecast, all append-only
  metrics.py     MAE, RMSE, pinball, 80% coverage, block bootstrap
  backtest.py    rolling-origin replay, with a per-fold response cache
  leaderboard.py renders site/index.html — no JS, no CDN
data/<source>/
  observations.csv   timestamp,value,first_seen_at     (frozen on first sight)
  forecasts.csv      issued_at,model,target_ts,point,0.1…0.9
  scores.csv         the join, computed only after the outcome landed
  backtest.json      offline replay results (clearly labelled, never merged in)
site/index.html      the leaderboard
```

## Adding a source

Subclass `Source`, implement `fetch()` (recent settled periods) and optionally
`backfill(start, end)`, then `register()` it. Nothing else changes — models,
scoring, and the page are source-agnostic.

```python
@dataclass
class MySource(Source):
    def fetch(self) -> list[Observation]: ...
```

A source must be public/keyless, on a fixed cadence, and either non-revising or
frozen-on-first-sight (this repo does the latter for all sources).

## Models

| model | what it is | why it's here |
|---|---|---|
| `tabicl` | `POST /v1/forecast` on the TabICL FaaS — TabICLv2 quantile head, point + deciles 0.1–0.9 | the thing under test |
| `persistence` | `y(t+h) = y(t)`, with a predictive band from the empirical distribution of historical h-step changes | the baseline everyone forgets is hard |
| `seasonal_naive` | `y(d) = y(d − 365)` | encodes seasonality with zero model risk |
| `climatology` | mean of the same calendar day ±5 days across all years, with empirical deciles | usually the one that actually hurts |

Persistence and climatology emit **quantiles**, not just points, so the
probabilistic metrics (pinball loss, 80% band coverage) compare like with like
instead of handing the TFM a free win for being the only model with a band.

## Metrics

Point: MAE, RMSE, bias. Probabilistic: mean pinball loss over the nine deciles
(the proper score the head was trained on) and empirical 80%-band coverage.

Comparisons use a **paired block bootstrap** over forecast origins, not over
days: the seven steps issued from one origin share a weather regime and fail
together, so resampling them independently would fabricate about √7 of
precision. The leaderboard prints `n` on every row and refuses to call a
difference real when the 95% CI straddles zero.

---

## Actual results (run 2026-08-09)

### Offline backtest — Paris daily mean temperature

Rolling origin, 40 non-overlapping origins, one week apart, spanning
2025-11-01 → 2026-08-01; 7-day horizon; 180-day context; 280 scored
forecast-days per model.

```
model              n     MAE    RMSE  pinball  cov80
tabicl           280   3.205   4.155    1.261   0.69
persistence      280   3.245   4.185    1.276   0.69
climatology      280   3.393   4.317    1.409   0.64
seasonal_naive   280   3.790   4.705        —      —

paired block bootstrap over the 40 origins (negative Δ = TabICL wins)
tabicl − persistence      Δ -0.040  CI95 [-0.475, +0.399]   not distinguishable
tabicl − climatology      Δ -0.187  CI95 [-0.757, +0.420]   not distinguishable
tabicl − seasonal_naive   Δ -0.585  CI95 [-1.244, +0.117]   not distinguishable

MAE by horizon      h+1    h+2    h+3    h+4    h+5    h+6    h+7
tabicl             1.84   2.79   2.57   3.19   3.68   4.14   4.22
persistence        1.74   2.77   2.83   3.08   3.83   4.22   4.26
climatology        2.53   2.89   3.31   3.71   3.79   3.80   3.72
seasonal_naive     2.79   3.61   4.38   4.71   4.38   3.60   3.06
```

**Read this honestly.** TabICL comes first on every aggregate metric and
**none of the gaps are real**: the confidence interval against persistence
(-0.475, +0.399 °C) is ten times the point estimate (-0.040 °C). Over 40
origins of Paris temperature, a foundation model with a quantile head is
indistinguishable from *"tomorrow will be like today"*. Its 80% band covers
69% of outcomes — under-covered, and identical to what the persistence band
achieves from empirical h-step changes.

The one structural finding that does survive: the models split by horizon.
Persistence and TabICL win the first three days; climatology overtakes both
from h+5 (3.79 → 3.72 while persistence degrades to 4.26), and seasonal-naive
is best of all at h+7. That is the expected shape — memory decays, seasonality
doesn't — and it is a better reason to keep all four in the table than any
ranking.

An earlier 12-origin run of the same code had persistence ahead of TabICL
(4.069 vs 4.201 MAE). Same pipeline, same data, opposite ordering — which is
precisely what "n is too small" looks like from the inside, and why the live
page leads with the sample-size caveat rather than a winner.

### Live git-attested leaderboard

Starts empty, by construction: the first forecasts were issued 2026-08-09 for
2026-08-09 … 2026-08-15, and the earliest of them cannot be scored until
2026-08-10. That emptiness is the feature. See `site/index.html`.

---

## Cost and etiquette

Each `/v1/forecast` call re-conditions the model from scratch on Yann's GCP
project. One daily tick is one call per source. `backtest` is `origins` calls
and caches every response in `data/<source>/backtest_cache.json` (gitignored),
so re-running the analysis is free.

## Status

v0, local only. Not published, no repo created, no cron enabled anywhere.
