"""Scoring: point accuracy, probabilistic accuracy, and how much of it is noise.

Everything here is a pure function of a list of scored rows. Nothing fetches,
nothing writes.
"""

from __future__ import annotations

import math
import random
from statistics import mean

from .models import QUANTILES


def pinball(actual: float, quantiles: dict[str, float]) -> float | None:
    """Mean pinball (quantile) loss over the available deciles.

    This is the proper score the quantile head was trained on; averaged over a
    dense enough quantile grid it approximates CRPS (up to a factor). Point-only
    models score `None` and are simply absent from the probabilistic table.
    """
    if not quantiles:
        return None
    losses = []
    for q in QUANTILES:
        if q not in quantiles:
            continue
        tau = float(q)
        d = actual - quantiles[q]
        losses.append(max(tau * d, (tau - 1) * d))
    return mean(losses) if losses else None


def covered(actual: float, quantiles: dict[str, float], lo: str = "0.1", hi: str = "0.9") -> int | None:
    if lo not in quantiles or hi not in quantiles:
        return None
    return int(quantiles[lo] <= actual <= quantiles[hi])


def summarise(rows: list[dict]) -> dict:
    """rows: dicts with abs_err, sq_err, and optionally pinball / covered80."""
    if not rows:
        return {"n": 0}
    ae = [r["abs_err"] for r in rows]
    se = [r["sq_err"] for r in rows]
    pb = [r["pinball"] for r in rows if r.get("pinball") is not None]
    cv = [r["covered80"] for r in rows if r.get("covered80") is not None]
    out = {
        "n": len(rows),
        "mae": mean(ae),
        "rmse": math.sqrt(mean(se)),
        "bias": mean(r["point"] - r["actual"] for r in rows),
    }
    if pb:
        out["pinball"] = mean(pb)
    if cv:
        out["coverage80"] = mean(cv)
        out["n_prob"] = len(cv)
    return out


def paired_bootstrap(
    a: dict[str, float],
    b: dict[str, float],
    n_boot: int = 4000,
    seed: int = 0,
    blocks: dict[str, str] | None = None,
) -> dict | None:
    """Bootstrap CI for `MAE(a) - MAE(b)` over the periods both models scored.

    Paired on the target period, which is the only honest comparison when
    models cover different subsets (seasonal-naive can miss steps). Returns
    None when the overlap is too small to say anything at all — which, for the
    first weeks of a live leaderboard, is the expected answer.

    `blocks` maps key -> block id and switches to a **block bootstrap**. Use it
    whenever the errors are serially correlated — e.g. the seven steps issued
    from one forecast origin succeed or fail together, so resampling them
    independently would invent evidence and shrink the CI by roughly √7.
    """
    keys = sorted(set(a) & set(b))
    if len(keys) < 3:
        return None
    diffs = {k: a[k] - b[k] for k in keys}
    rng = random.Random(seed)
    boots = []

    if blocks:
        grouped: dict[str, list[float]] = {}
        for k in keys:
            grouped.setdefault(blocks.get(k, k), []).append(diffs[k])
        bids = list(grouped)
        if len(bids) < 3:
            return None
        for _ in range(n_boot):
            sample: list[float] = []
            for _ in bids:
                sample.extend(grouped[bids[rng.randrange(len(bids))]])
            boots.append(mean(sample))
    else:
        vals = [diffs[k] for k in keys]
        for _ in range(n_boot):
            boots.append(mean(vals[rng.randrange(len(vals))] for _ in vals))

    boots.sort()
    lo = boots[int(0.025 * n_boot)]
    hi = boots[int(0.975 * n_boot) - 1]
    return {
        "n_pairs": len(keys),
        "blocks": len({blocks[k] for k in keys}) if blocks else None,
        "delta_mae": mean(diffs.values()),
        "ci95": [lo, hi],
        "significant": (lo > 0) or (hi < 0),
    }


def enough_data(periods: int, origins: int, min_origins: int = 20) -> str:
    """The sentence a leaderboard owes its reader before it shows it a ranking.

    The unit that matters is the *forecast origin*, not the scored day: seven
    days issued from one origin are one draw from the weather, not seven.
    """
    if periods == 0:
        return "No scored forecasts yet — the first forecasts have not matured."
    base = f"{periods} scored forecast-periods from {origins} independent forecast origin(s)"
    if origins < 3:
        return (
            f"{base}. Too few origins to even run a bootstrap. The ranking below is "
            "one week of weather; it carries no information about which model is better."
        )
    if origins < min_origins:
        return (
            f"{base} — well below the ~{min_origins} origins needed before a MAE gap of "
            "this size could clear its own confidence interval. Read the ordering as noise."
        )
    return f"{base}. Treat any gap whose 95% CI straddles zero as a tie."
