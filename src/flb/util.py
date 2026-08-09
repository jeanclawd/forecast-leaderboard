"""Tiny stdlib-only helpers: HTTP (JSON + multipart) and append-only CSV tables.

No third-party dependencies anywhere in this package. That is deliberate:
the whole pipeline has to run in a 20-second GitHub Actions job with
`actions/setup-python` and nothing else, and a template repo that needs a
lockfile is a template nobody forks.
"""

from __future__ import annotations

import csv
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

# api.tabicl.org's edge 403s the default `Python-urllib/3.x` User-Agent.
# Any conventional UA works. See the tabicl-faas notes.
USER_AGENT = "forecast-leaderboard/0.1 (+https://github.com/)"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def parse_day(s: str) -> date:
    return date.fromisoformat(s[:10])


def daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# --------------------------------------------------------------------------- HTTP


def get_json(url: str, params: dict[str, Any] | None = None, timeout: int = 60) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post_multipart(
    url: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
    timeout: int = 240,
) -> Any:
    """POST a multipart/form-data body built by hand (no `requests`)."""
    boundary = uuid.uuid4().hex
    body = bytearray()

    def part(header: str) -> None:
        body.extend(f"--{boundary}\r\n{header}\r\n\r\n".encode())

    for name, value in fields.items():
        part(f'Content-Disposition: form-data; name="{name}"')
        body.extend(value.encode())
        body.extend(b"\r\n")
    for name, (filename, blob) in files.items():
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        part(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}"
        )
        body.extend(blob)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # surface the server's message, not just 500
        raise RuntimeError(f"{url} -> {e.code}: {e.read()[:400].decode(errors='replace')}") from e


def rows_to_csv_bytes(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> bytes:
    out = [",".join(header)]
    out.extend(",".join("" if v is None else str(v) for v in r) for r in rows)
    return ("\n".join(out) + "\n").encode()


# --------------------------------------------------------------------------- CSV tables


def read_table(path: str) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_table(path: str, header: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(header), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def num(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v  # drop NaN
