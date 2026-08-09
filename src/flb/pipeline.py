"""The three git-committed steps: scrape → forecast → score.

Ordering is the whole point of the project, so it is enforced here and in the
workflow rather than left to convention:

    1. `scrape`   appends newly-settled observations. Never overwrites a value
                  it has already recorded (see the REVISION note in sources).
    2. `score`    joins *previously issued* forecasts to whatever truth now
                  exists. Only ever adds rows.
    3. `forecast` issues predictions for periods that do not exist yet, and is
                  committed **last and separately**, so the repository tree at
                  that commit provably contains no observation for the periods
                  the commit predicts.

Every table is append-only. Rewriting one is possible (it's a file in a repo
the owner controls) — that is exactly the limitation CREDIBILITY.md is honest
about, and the reason a gap in the daily cadence is itself evidence.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

from . import models as M
from .metrics import covered, pinball
from .sources import Source, get_source, SOURCES
from .util import iso, num, parse_day, read_table, utcnow, write_table

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "data")

OBS_COLS = ["timestamp", "value", "first_seen_at"]
FC_COLS = ["issued_at", "issued_day", "model", "target_ts", "horizon", "point", *M.QUANTILES]
SC_COLS = [
    "target_ts", "model", "issued_at", "issued_day", "horizon",
    "point", "actual", "abs_err", "sq_err", "pinball", "covered80",
]


def paths(source_id: str) -> dict[str, str]:
    d = os.path.join(DATA, source_id)
    return {
        "dir": d,
        "obs": os.path.join(d, "observations.csv"),
        "fc": os.path.join(d, "forecasts.csv"),
        "sc": os.path.join(d, "scores.csv"),
    }


def history(source_id: str) -> list[tuple[str, float]]:
    rows = read_table(paths(source_id)["obs"])
    out = [(r["timestamp"], num(r["value"])) for r in rows]
    return sorted([(t, v) for t, v in out if v is not None])


# --------------------------------------------------------------------------- 1. scrape


def scrape(source: Source, backfill_years: int = 0) -> int:
    p = paths(source.id)
    existing = {r["timestamp"]: r for r in read_table(p["obs"])}
    now = iso(utcnow())

    incoming = list(source.fetch())
    if backfill_years:
        end = date.today() - timedelta(days=6)
        start = end.replace(year=end.year - backfill_years)
        incoming = list(source.backfill(start, end)) + incoming

    added = 0
    for o in incoming:
        if o.timestamp in existing:
            continue  # freeze the first value we ever saw for this period
        existing[o.timestamp] = {
            "timestamp": o.timestamp,
            "value": f"{o.value:.4g}",
            "first_seen_at": now,
        }
        added += 1

    write_table(p["obs"], OBS_COLS, [existing[k] for k in sorted(existing)])
    return added


# --------------------------------------------------------------------------- 2. forecast


def future_periods(source: Source, hist: list[tuple[str, float]]) -> list[str]:
    last = parse_day(hist[-1][0])
    return [(last + timedelta(days=h)).isoformat() for h in range(1, source.horizon + 1)]


def forecast(source: Source, only: list[str] | None = None) -> int:
    p = paths(source.id)
    hist = history(source.id)
    if not hist:
        raise SystemExit(f"{source.id}: no observations yet — run `flb scrape` first")

    future = future_periods(source, hist)
    rows = read_table(p["fc"])
    today = utcnow().date().isoformat()
    seen = {(r["issued_day"], r["model"], r["target_ts"]) for r in rows}
    now = iso(utcnow())
    last_day = parse_day(hist[-1][0])

    names = only or list(M.MODELS)
    if "tabicl" in names:
        M.warm()

    added = 0
    for name in names:
        fn = M.MODELS[name]
        try:
            preds = fn(hist, future, source)
        except Exception as e:  # a flaky FaaS must not lose the baselines
            print(f"  ! {name}: {e}")
            continue
        for pr in preds:
            key = (today, name, pr.timestamp)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "issued_at": now,
                    "issued_day": today,
                    "model": name,
                    "target_ts": pr.timestamp,
                    "horizon": (parse_day(pr.timestamp) - last_day).days,
                    "point": f"{pr.point:.4f}",
                    **{q: f"{v:.4f}" for q, v in pr.quantiles.items()},
                }
            )
            added += 1
        print(f"  {name}: {len(preds)} step(s)")

    rows.sort(key=lambda r: (r["target_ts"], r["model"], r["issued_at"]))
    write_table(p["fc"], FC_COLS, rows)
    return added


# --------------------------------------------------------------------------- 3. score


def score(source: Source) -> int:
    p = paths(source.id)
    actual = {t: v for t, v in history(source.id)}
    scored = read_table(p["sc"])
    seen = {(r["issued_day"], r["model"], r["target_ts"]) for r in scored}

    added = 0
    for r in read_table(p["fc"]):
        key = (r["issued_day"], r["model"], r["target_ts"])
        if key in seen or r["target_ts"] not in actual:
            continue
        y = actual[r["target_ts"]]
        pt = num(r["point"])
        if pt is None:
            continue
        q = {k: num(r.get(k)) for k in M.QUANTILES if num(r.get(k)) is not None}
        scored.append(
            {
                **{k: r[k] for k in ("target_ts", "model", "issued_at", "issued_day", "horizon")},
                "point": f"{pt:.4f}",
                "actual": f"{y:.4f}",
                "abs_err": f"{abs(pt - y):.4f}",
                "sq_err": f"{(pt - y) ** 2:.4f}",
                "pinball": (lambda v: "" if v is None else f"{v:.4f}")(pinball(y, q)),
                "covered80": (lambda v: "" if v is None else str(v))(covered(y, q)),
            }
        )
        seen.add(key)
        added += 1

    scored.sort(key=lambda r: (r["target_ts"], r["model"], r["issued_day"]))
    write_table(p["sc"], SC_COLS, scored)
    return added


def all_sources() -> list[Source]:
    return list(SOURCES.values())
