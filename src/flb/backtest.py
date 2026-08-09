"""Rolling-origin replay over the bootstrapped history.

This exists to give the leaderboard a *large-n* companion table on day one,
because the live git-attested track legitimately starts at n = 0 and stays
statistically mute for weeks.

Its weakness is stated on the page and worth repeating here: TabICL is
pretrained, and a backtest over the past cannot prove the model never saw that
past. The live track can. Do not merge the two tables.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import timedelta

from . import models as M
from .metrics import covered, paired_bootstrap, pinball, summarise
from .pipeline import ROOT, history, paths
from .sources import Source
from .util import parse_day


def _cache_path(source_id: str) -> str:
    return os.path.join(ROOT, "data", source_id, "backtest_cache.json")


def _load_cache(source_id: str) -> dict:
    p = _cache_path(source_id)
    return json.load(open(p)) if os.path.exists(p) else {}


def _save_cache(source_id: str, cache: dict) -> None:
    os.makedirs(os.path.dirname(_cache_path(source_id)), exist_ok=True)
    with open(_cache_path(source_id), "w") as f:
        json.dump(cache, f)


def run(source: Source, origins: int = 12, spacing: int = 7, min_context: int = 400) -> dict:
    hist = history(source.id)
    if len(hist) < min_context + origins * spacing:
        raise SystemExit(
            f"{source.id}: need ~{min_context + origins * spacing} observations for this "
            f"backtest, have {len(hist)} — run `flb bootstrap` first"
        )
    actual = dict(hist)
    idx = [len(hist) - 1 - source.horizon - k * spacing for k in range(origins)][::-1]
    idx = [i for i in idx if i >= min_context]

    cache = _load_cache(source.id)
    if not cache:
        M.warm()
    rows = defaultdict(list)
    for n, i in enumerate(idx, 1):
        ctx = hist[: i + 1]
        last = parse_day(ctx[-1][0])
        future = [(last + timedelta(days=h)).isoformat() for h in range(1, source.horizon + 1)]
        origin = ctx[-1][0]
        print(f"  origin {n}/{len(idx)}  {origin} → {future[-1]}")
        for name, fn in M.MODELS.items():
            ck = f"{name}|{origin}"
            if ck in cache:
                preds = [M.Prediction(**p) for p in cache[ck]]
            else:
                try:
                    preds = fn(ctx, future, source)
                except Exception as e:
                    print(f"    ! {name}: {e}")
                    continue
                # Each FaaS call re-conditions from scratch; never pay twice
                # for the same fold when re-running the analysis.
                cache[ck] = [vars(p) for p in preds]
                _save_cache(source.id, cache)
            for pr in preds:
                if pr.timestamp not in actual:
                    continue
                y = actual[pr.timestamp]
                rows[name].append(
                    {
                        "key": f"{origin}|{pr.timestamp}",
                        "origin": origin,
                        "target_ts": pr.timestamp,
                        "horizon": (parse_day(pr.timestamp) - last).days,
                        "point": pr.point,
                        "actual": y,
                        "abs_err": abs(pr.point - y),
                        "sq_err": (pr.point - y) ** 2,
                        "pinball": pinball(y, pr.quantiles),
                        "covered80": covered(y, pr.quantiles),
                    }
                )

    out = {
        "origins": len(idx),
        "spacing_days": spacing,
        "horizon": source.horizon,
        "first_origin": hist[idx[0]][0] if idx else None,
        "last_origin": hist[idx[-1]][0] if idx else None,
        "models": {m: summarise(rs) for m, rs in rows.items()},
        # Block bootstrap: the seven steps from one origin share a weather
        # regime, so the resampling unit is the origin, not the day.
        "vs_baselines": {
            m: paired_bootstrap(
                {r["key"]: r["abs_err"] for r in rows["tabicl"]},
                {r["key"]: r["abs_err"] for r in rs},
                blocks={r["key"]: r["origin"] for r in rows["tabicl"] + rs},
            )
            for m, rs in rows.items()
            if m != "tabicl"
        }
        if "tabicl" in rows
        else {},
        "by_horizon": {
            m: {
                str(h): summarise([r for r in rs if r["horizon"] == h])
                for h in range(1, source.horizon + 1)
            }
            for m, rs in rows.items()
        },
    }
    path = os.path.join(ROOT, "data", source.id, "backtest.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    print(f"  → {path}")
    return out
