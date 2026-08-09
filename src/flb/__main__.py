"""`python -m flb <command>` — the only entry point. The Action calls nothing else."""

from __future__ import annotations

import argparse
import sys

from . import backtest as BT
from . import leaderboard as LB
from . import pipeline as P
from .sources import SOURCES, get_source


def _targets(arg: str | None):
    return [get_source(arg)] if arg else list(SOURCES.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="flb", description=__doc__)
    ap.add_argument("command", choices=["scrape", "forecast", "score", "build", "bootstrap",
                                        "backtest", "tick", "sources"])
    ap.add_argument("--source", help="restrict to one source id")
    ap.add_argument("--models", help="comma-separated model subset for `forecast`")
    ap.add_argument("--years", type=int, default=3, help="bootstrap depth (default 3)")
    ap.add_argument("--origins", type=int, default=12, help="backtest origins (default 12)")
    a = ap.parse_args(argv)

    if a.command == "sources":
        for s in SOURCES.values():
            print(f"{s.id:16} {s.title}  [{s.cadence}, h={s.horizon}, season={s.season}]")
        return 0

    only = a.models.split(",") if a.models else None

    for s in _targets(a.source):
        print(f"[{s.id}] {a.command}")
        if a.command == "bootstrap":
            print(f"  +{P.scrape(s, backfill_years=a.years)} observations")
        elif a.command == "scrape":
            print(f"  +{P.scrape(s)} observations")
        elif a.command == "forecast":
            print(f"  +{P.forecast(s, only)} forecast rows")
        elif a.command == "score":
            print(f"  +{P.score(s)} scored rows")
        elif a.command == "backtest":
            BT.run(s, origins=a.origins)
        elif a.command == "tick":
            print(f"  +{P.scrape(s)} observations")
            print(f"  +{P.score(s)} scored rows")
            print(f"  +{P.forecast(s, only)} forecast rows")

    if a.command in ("build", "tick", "backtest", "score", "bootstrap"):
        print(f"wrote {LB.build()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
