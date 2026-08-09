"""Pluggable data sources.

A source is anything that can answer "what was the observed value of my target
for these past periods?".  It must be:

  * **public and keyless** (or key-in-secrets), so a forked repo works;
  * **revising-free enough** that yesterday's value does not silently change
    after we have scored a forecast against it (see `REVISION` notes below);
  * **periodic** on a fixed cadence, so "the next period" is well defined.

To add a source, subclass `Source`, implement `fetch()` (and optionally
`backfill()`), and register it in `SOURCES`. Everything downstream — models,
scoring, leaderboard — is source-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .util import get_json


@dataclass
class Observation:
    timestamp: str  # ISO period label, e.g. "2026-08-09"
    value: float


@dataclass
class Source:
    id: str
    title: str
    unit: str
    cadence: str = "daily"
    horizon: int = 7  # periods ahead to forecast each tick
    season: int = 365  # seasonal-naive lag, in periods
    context: int = 180  # periods of history handed to the forecaster
    notes: str = ""

    def fetch(self) -> list[Observation]:
        """Most recent settled observations (a handful of periods)."""
        raise NotImplementedError

    def backfill(self, start: date, end: date) -> list[Observation]:
        """Historical observations, for bootstrapping a new deployment."""
        return []


@dataclass
class OpenMeteoDaily(Source):
    """Daily weather aggregate from open-meteo (free, keyless, no signup).

    `fetch()` reads the *forecast* endpoint with `past_days`, which returns the
    best-available analysis for days already elapsed.  We deliberately drop
    today (still accumulating) and only ever trust days that are fully closed
    in the location's own timezone.

    REVISION: open-meteo's recent-past values come from a short-range analysis
    and can move by a few tenths once ERA5 lands weeks later. We therefore
    freeze the first observed value we ever saw for a period (see
    `pipeline.scrape`, which never overwrites an existing row) so that a score
    can't drift after the fact. The frozen value is the one a skeptic can find
    in git at scoring time.
    """

    latitude: float = 48.86
    longitude: float = 2.35
    timezone_name: str = "Europe/Paris"
    variable: str = "temperature_2m_mean"
    past_days: int = 5

    FORECAST_API = "https://api.open-meteo.com/v1/forecast"
    ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"

    def _params(self) -> dict:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "daily": self.variable,
            "timezone": self.timezone_name,
        }

    def _unpack(self, daily: dict) -> list[Observation]:
        return [
            Observation(t, float(v))
            for t, v in zip(daily["time"], daily[self.variable])
            if v is not None
        ]

    def fetch(self) -> list[Observation]:
        d = get_json(
            self.FORECAST_API,
            {**self._params(), "past_days": self.past_days, "forecast_days": 1},
        )["daily"]
        obs = self._unpack(d)
        # keep only fully-elapsed days: everything strictly before the last
        # element (which is "today" at the location) is closed.
        today = d["time"][-1]
        return [o for o in obs if o.timestamp < today]

    def backfill(self, start: date, end: date) -> list[Observation]:
        d = get_json(
            self.ARCHIVE_API,
            {
                **self._params(),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )["daily"]
        return self._unpack(d)


# --------------------------------------------------------------------------- registry

SOURCES: dict[str, Source] = {}


def register(s: Source) -> Source:
    SOURCES[s.id] = s
    return s


register(
    OpenMeteoDaily(
        id="paris-temp",
        title="Paris daily mean temperature",
        unit="°C",
        horizon=7,
        season=365,
        context=180,
        latitude=48.86,
        longitude=2.35,
        timezone_name="Europe/Paris",
        variable="temperature_2m_mean",
        notes="open-meteo forecast API, `temperature_2m_mean`, Europe/Paris days.",
    )
)

# Second source, same class, different geography — proves the interface is
# actually pluggable and gives the leaderboard a per-source dimension.
# Disabled by default to keep the daily API budget and diff size small;
# uncomment to enable.
# register(
#     OpenMeteoDaily(
#         id="reykjavik-temp", title="Reykjavík daily mean temperature", unit="°C",
#         latitude=64.15, longitude=-21.94, timezone_name="Atlantic/Reykjavik",
#     )
# )


def get_source(source_id: str) -> Source:
    try:
        return SOURCES[source_id]
    except KeyError:
        raise SystemExit(f"unknown source {source_id!r}; have {sorted(SOURCES)}") from None


def default_backfill_window(years: int = 2) -> tuple[date, date]:
    end = date.today() - timedelta(days=6)  # archive lags ~5 days
    return end.replace(year=end.year - years), end
