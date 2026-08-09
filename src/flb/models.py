"""Forecast models: the TFM under test, and the baselines it has to beat.

Every model takes the same input — the frozen observation history and the list
of future period labels — and returns one `Prediction` per horizon step:
a point estimate plus, optionally, the nine deciles.

The baselines are not decoration. A forecast leaderboard without persistence
and a seasonal baseline reports nothing: on daily temperature, "tomorrow =
today" is already a hard target, and climatology is harder still.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from .util import parse_day, post_multipart, rows_to_csv_bytes

QUANTILES = ["0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9"]
TABICL_API = "https://api.tabicl.org"


@dataclass
class Prediction:
    timestamp: str
    point: float
    quantiles: dict[str, float] = field(default_factory=dict)


History = list[tuple[str, float]]  # sorted (period label, value)


# --------------------------------------------------------------------------- helpers


def _empirical_quantiles(sample: list[float]) -> dict[str, float]:
    """Type-7 empirical deciles of a sample (numpy's default, by hand)."""
    if len(sample) < 10:
        return {}
    s = sorted(sample)
    out = {}
    for q in QUANTILES:
        h = (len(s) - 1) * float(q)
        lo = int(h)
        hi = min(lo + 1, len(s) - 1)
        out[q] = s[lo] + (h - lo) * (s[hi] - s[lo])
    return out


def _by_day(history: History) -> dict[date, float]:
    return {parse_day(t): v for t, v in history}


# --------------------------------------------------------------------------- baselines


def persistence(history: History, future: list[str], source) -> list[Prediction]:
    """`y_hat(t+h) = y(t)`. Point forecast plus an *empirical* predictive band
    from the historical distribution of h-step changes — so that the
    probabilistic metrics compare like with like instead of handing TabICL a
    free win for being the only model with quantiles."""
    if not history:
        return []
    last = history[-1][1]
    day = _by_day(history)
    preds = []
    for h, ts in enumerate(future, start=1):
        deltas = [
            day[d + _td(h)] - v
            for d, v in day.items()
            if d + _td(h) in day
        ]
        q = _empirical_quantiles(deltas)
        preds.append(
            Prediction(ts, last, {k: last + dv for k, dv in q.items()})
        )
    return preds


def seasonal_naive(history: History, future: list[str], source) -> list[Prediction]:
    """`y_hat(d) = y(d - season)`, season in periods (365 for daily weather).

    Needs the history to actually reach back that far — hence `flb bootstrap`.
    Steps with no matching lag are simply not emitted (and so are excluded from
    that model's sample, which the leaderboard reports as a differing `n`)."""
    day = _by_day(history)
    preds = []
    for ts in future:
        target = parse_day(ts)
        for slack in (0, -1, 1, -2, 2):  # tolerate small gaps in the archive
            src = target - _td(source.season - slack)
            if src in day:
                preds.append(Prediction(ts, day[src]))
                break
    return preds


def climatology(history: History, future: list[str], source, window: int = 5) -> list[Prediction]:
    """Mean of the same calendar day ±`window` days across every year on record.

    For weather this is the baseline that actually hurts: it encodes the season
    with none of the model risk."""
    day = _by_day(history)
    preds = []
    for ts in future:
        target = parse_day(ts)
        doy = target.timetuple().tm_yday
        sample = [
            v
            for d, v in day.items()
            if min(abs(d.timetuple().tm_yday - doy), 366 - abs(d.timetuple().tm_yday - doy))
            <= window
        ]
        if len(sample) < 5:
            continue
        pt = sum(sample) / len(sample)
        preds.append(Prediction(ts, pt, _empirical_quantiles(sample)))
    return preds


def _td(days: int):
    from datetime import timedelta

    return timedelta(days=days)


# --------------------------------------------------------------------------- TabICL


def tabicl(history: History, future: list[str], source, api: str = TABICL_API) -> list[Prediction]:
    """`POST /v1/forecast` on Yann's TabICL FaaS (TabICLv2 quantile head).

    Contract is strict: the context CSV's columns must literally be named
    `timestamp` and `target`; the future CSV carries the horizon's timestamps.
    Response gives a point estimate plus deciles 0.1–0.9 per step.
    """
    ctx = history[-source.context :]
    if len(ctx) < 20:
        return []
    out = post_multipart(
        f"{api}/v1/forecast",
        fields={"data": json.dumps({"task": "forecast"})},
        files={
            "context_file": ("context.csv", rows_to_csv_bytes(["timestamp", "target"], ctx)),
            "future_file": ("future.csv", rows_to_csv_bytes(["timestamp"], [[t] for t in future])),
        },
    )
    cols = out["columns"]
    stamps = [str(ix[-1])[:10] for ix in out["index"]]
    preds = []
    for ts, rec in zip(stamps, out["records"]):
        d = dict(zip(cols, rec))
        preds.append(
            Prediction(ts, float(d["target"]), {q: float(d[q]) for q in QUANTILES if q in d})
        )
    return preds


def warm(api: str = TABICL_API) -> None:
    """Absorb the Cloud Run cold start (10–30 s) before the timed call."""
    from .util import get_json

    try:
        get_json(f"{api}/health", timeout=90)
    except Exception:
        pass


MODELS = {
    "tabicl": tabicl,
    "persistence": persistence,
    "seasonal_naive": seasonal_naive,
    "climatology": climatology,
}

BASELINES = ["persistence", "seasonal_naive", "climatology"]
