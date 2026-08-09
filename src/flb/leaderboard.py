"""Rebuild `site/index.html` from the scored CSVs. No JS, no CDN, no build step.

The page is a report, not a pitch: every table carries its own `n`, the
baselines sit in the same table as the model under test, and the headline
sentence is the sample-size caveat rather than a win claim.
"""

from __future__ import annotations

import html
import json
import os
import subprocess
from collections import defaultdict

from .metrics import enough_data, paired_bootstrap, summarise
from .models import BASELINES
from .pipeline import ROOT, paths, read_table
from .sources import SOURCES
from .util import num, utcnow, iso

SITE = os.path.join(ROOT, "site", "index.html")

CSS = """
:root{--bg:#fff;--fg:#16181d;--mut:#606770;--line:#e3e6ea;--accent:#1c4fd8;--warn:#8a5a00;--warnbg:#fff8e6}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e8eaed;--mut:#9aa0a6;--line:#272b33;--accent:#7aa2ff;--warn:#f0c674;--warnbg:#241f10}}
*{box-sizing:border-box}
body{margin:0 auto;padding:2rem 1.25rem 5rem;max-width:60rem;background:var(--bg);color:var(--fg);
     font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
h1{font-size:1.6rem;margin:0 0 .25rem} h2{font-size:1.15rem;margin:2.5rem 0 .5rem}
h3{font-size:.95rem;margin:1.5rem 0 .35rem;color:var(--mut);font-weight:600}
a{color:var(--accent)} code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85em}
pre{background:rgba(128,128,128,.10);padding:.85rem 1rem;border-radius:8px;overflow-x:auto}
.sub{color:var(--mut);margin:0 0 1.5rem}
.note{background:var(--warnbg);border-left:3px solid var(--warn);padding:.7rem .9rem;border-radius:0 6px 6px 0;margin:1rem 0}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;min-width:34rem;margin:.5rem 0 .25rem;font-variant-numeric:tabular-nums}
th,td{padding:.4rem .6rem;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{font-weight:600;color:var(--mut);font-size:.8rem;text-transform:uppercase;letter-spacing:.03em}
tr.best td{font-weight:700}
.tag{font-size:.72rem;padding:.1rem .45rem;border-radius:99px;border:1px solid var(--line);color:var(--mut);margin-left:.4rem}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--mut);font-size:.85rem}
"""


def _fmt(v, nd=3):
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return ""


def _table(headers, rows, best_row=None):
    h = "".join(f"<th>{html.escape(x)}</th>" for x in headers)
    body = []
    for i, r in enumerate(rows):
        cls = ' class="best"' if i == best_row else ""
        body.append(f"<tr{cls}>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return (
        f'<div class="scroll"><table><thead><tr>{h}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def _model_rows(scored: list[dict]) -> dict[str, list[dict]]:
    by = defaultdict(list)
    for r in scored:
        by[r["model"]].append(
            {
                "key": f"{r['issued_day']}|{r['target_ts']}",
                "issued_day": r["issued_day"],
                "target_ts": r["target_ts"],
                "horizon": int(r["horizon"]),
                "point": num(r["point"]),
                "actual": num(r["actual"]),
                "abs_err": num(r["abs_err"]),
                "sq_err": num(r["sq_err"]),
                "pinball": num(r.get("pinball")),
                "covered80": num(r.get("covered80")),
            }
        )
    return by


def _leaderboard_table(by_model: dict[str, list[dict]]) -> str:
    stats = {m: summarise(rs) for m, rs in by_model.items()}
    order = sorted(stats, key=lambda m: stats[m].get("mae", float("inf")))
    rows = []
    for m in order:
        s = stats[m]
        tag = "" if m == "tabicl" else ' <span class="tag">baseline</span>'
        rows.append(
            [
                f"<b>{html.escape(m)}</b>{tag}",
                s["n"],
                _fmt(s.get("mae")),
                _fmt(s.get("rmse")),
                _fmt(s.get("bias")),
                _fmt(s.get("pinball")),
                _fmt(s.get("coverage80"), 2) if "coverage80" in s else "—",
            ]
        )
    return _table(
        ["model", "n", "MAE", "RMSE", "bias", "pinball", "80% cov."], rows, best_row=0
    )


def _horizon_table(by_model: dict[str, list[dict]]) -> str:
    horizons = sorted({r["horizon"] for rs in by_model.values() for r in rs})
    rows = []
    for m in sorted(by_model):
        cells = [f"<b>{html.escape(m)}</b>"]
        for h in horizons:
            sub = [r for r in by_model[m] if r["horizon"] == h]
            cells.append(_fmt(summarise(sub).get("mae"), 2) if sub else "—")
        rows.append(cells)
    return _table(["model MAE by horizon"] + [f"h+{h}" for h in horizons], rows)


def _significance(by_model) -> str:
    if "tabicl" not in by_model:
        return "<p>No TabICL forecasts scored yet.</p>"
    tab = {r["key"]: r["abs_err"] for r in by_model["tabicl"]}
    blocks = {r["key"]: r["issued_day"] for rs in by_model.values() for r in rs}
    out = []
    for b in BASELINES:
        if b not in by_model:
            continue
        base = {r["key"]: r["abs_err"] for r in by_model[b]}
        res = paired_bootstrap(tab, base, blocks=blocks)
        if res is None:
            out.append([f"tabicl − {b}", "—", "—", "too few forecast origins to resample"])
            continue
        lo, hi = res["ci95"]
        verdict = (
            ("tabicl better" if res["delta_mae"] < 0 else f"{b} better")
            if res["significant"]
            else "not distinguishable"
        )
        out.append(
            [f"tabicl − {b}", res["n_pairs"], f"{res['delta_mae']:+.3f}",
             f"[{lo:+.3f}, {hi:+.3f}] &nbsp;{verdict}"]
        )
    return _table(["ΔMAE (negative = TabICL wins)", "pairs", "Δ", "95% bootstrap CI"], out)


def _open_forecasts(source_id: str) -> str:
    fc = read_table(paths(source_id)["fc"])
    sc = {(r["issued_day"], r["model"], r["target_ts"]) for r in read_table(paths(source_id)["sc"])}
    obs = {r["timestamp"] for r in read_table(paths(source_id)["obs"])}
    open_rows = [r for r in fc if r["target_ts"] not in obs]
    if not open_rows:
        return "<p>No open forecasts.</p>"
    latest = max(r["issued_day"] for r in open_rows)
    rows = []
    for r in sorted([x for x in open_rows if x["issued_day"] == latest],
                    key=lambda x: (x["target_ts"], x["model"])):
        rows.append([
            r["target_ts"], html.escape(r["model"]), f"h+{r['horizon']}",
            _fmt(num(r["point"]), 1),
            _fmt(num(r.get("0.1")), 1), _fmt(num(r.get("0.9")), 1),
        ])
    return (
        f"<p>Issued {html.escape(latest)}, outcome not yet observed — these are the rows "
        "a skeptic should check against git history before the truth lands.</p>"
        + _table(["target period", "model", "h", "point", "q10", "q90"], rows)
    )


def build() -> str:
    parts = [
        f"<title>Forecast leaderboard</title><style>{CSS}</style>",
        "<h1>An honest, git-scraped forecast leaderboard</h1>",
        '<p class="sub">Every forecast below was committed to git <em>before</em> the period '
        "it predicts existed. Scores are computed only after the observation arrives, and "
        "baselines are scored on exactly the same periods. "
        '<a href="../CREDIBILITY.md">What that does and does not prove →</a></p>',
    ]

    total_n = 0
    for sid, src in SOURCES.items():
        scored = read_table(paths(sid)["sc"])
        obs = read_table(paths(sid)["obs"])
        by_model = _model_rows(scored)
        total_n += len(scored)

        parts.append(f"<h2>{html.escape(src.title)} <span class='tag'>{html.escape(sid)}</span></h2>")
        parts.append(
            f'<p class="sub">{html.escape(src.notes)} · {len(obs)} observed periods '
            f"({html.escape(obs[0]['timestamp']) if obs else '—'} → "
            f"{html.escape(obs[-1]['timestamp']) if obs else '—'}) · horizon {src.horizon} "
            f"{src.cadence} periods · target unit {html.escape(src.unit)}</p>"
        )
        n_periods = len({(r["issued_day"], r["target_ts"]) for r in scored})
        n_origins = len({r["issued_day"] for r in scored})
        parts.append(
            "<div class=\"note\"><b>Sample size.</b> "
            f"{html.escape(enough_data(n_periods, n_origins))}</div>"
        )
        parts.append("<h3>Live leaderboard — git-attested forecasts only</h3>")
        parts.append(_leaderboard_table(by_model) if by_model else "<p>Nothing scored yet.</p>")
        if by_model:
            parts.append("<h3>Is the gap real?</h3>")
            parts.append(_significance(by_model))
            parts.append("<h3>Error by horizon</h3>")
            parts.append(_horizon_table(by_model))
        parts.append("<h3>Open forecasts (not yet scorable)</h3>")
        parts.append(_open_forecasts(sid))

        bt_path = os.path.join(ROOT, "data", sid, "backtest.json")
        if os.path.exists(bt_path):
            bt = json.load(open(bt_path))
            parts.append("<h3>Offline backtest <span class='tag'>NOT git-attested</span></h3>")
            parts.append(
                '<div class="note">These numbers come from a rolling-origin replay over '
                f"historical data ({html.escape(str(bt['origins']))} origins, "
                f"{html.escape(str(bt['first_origin']))} → {html.escape(str(bt['last_origin']))}). "
                "They have a much larger <i>n</i> — and a defect the live table does not have: "
                "TabICL is a pretrained model, and nothing here can rule out that this "
                "period was in its pretraining distribution. Only forecasts of periods that "
                "did not exist at pretraining time are leakage-proof by construction.</div>"
            )
            rows = []
            for m in sorted(bt["models"], key=lambda m: bt["models"][m].get("mae", 9e9)):
                s = bt["models"][m]
                rows.append([html.escape(m), s["n"], _fmt(s.get("mae")), _fmt(s.get("rmse")),
                             _fmt(s.get("pinball")),
                             _fmt(s.get("coverage80"), 2) if "coverage80" in s else "—"])
            parts.append(_table(["model", "n", "MAE", "RMSE", "pinball", "80% cov."], rows, 0))
            vb = [r for r in bt.get("vs_baselines", {}).items() if r[1]]
            if vb:
                parts.append(
                    _table(
                        ["ΔMAE (negative = TabICL wins)", "origins", "Δ", "95% block-bootstrap CI"],
                        [
                            [
                                f"tabicl − {html.escape(m)}",
                                res.get("blocks") or res["n_pairs"],
                                f"{res['delta_mae']:+.3f}",
                                f"[{res['ci95'][0]:+.3f}, {res['ci95'][1]:+.3f}] &nbsp;"
                                + (
                                    ("tabicl better" if res["delta_mae"] < 0 else f"{m} better")
                                    if res["significant"]
                                    else "not distinguishable"
                                ),
                            ]
                            for m, res in vb
                        ],
                    )
                )

    head = _git("log", "-1", "--format=%H|%cI")
    sha, when = (head.split("|") + ["", ""])[:2] if head else ("", "")
    parts.append("<h2>Verify it yourself</h2>")
    parts.append(
        "<pre>git clone &lt;this repo&gt; &amp;&amp; cd forecast-leaderboard\n"
        "# 1. find the commit that issued a forecast for some period D\n"
        "git log --oneline --follow -- data/paris-temp/forecasts.csv\n"
        "# 2. read the forecast as of that commit\n"
        "git show &lt;sha&gt;:data/paris-temp/forecasts.csv | grep ',D'\n"
        "# 3. show that D had no observation in that same tree\n"
        "git show &lt;sha&gt;:data/paris-temp/observations.csv | grep ',D'   # expect: nothing\n"
        "# 4. cross-check the commit against the Actions run that produced it\n"
        "gh api repos/&lt;owner&gt;/&lt;repo&gt;/actions/runs --jq '.workflow_runs[]"
        "|{head_sha,created_at,conclusion}'</pre>"
    )
    parts.append(
        f"<footer>Rebuilt {iso(utcnow())} · HEAD <code>{html.escape(sha[:10])}</code> "
        f"{html.escape(when)} · {total_n} scored forecast-periods total · "
        "forecasts by <a href='https://api.tabicl.org/docs'>TabICL FaaS</a>, "
        "data by <a href='https://open-meteo.com'>open-meteo</a>.</footer>"
    )

    os.makedirs(os.path.dirname(SITE), exist_ok=True)
    with open(SITE, "w") as f:
        f.write("\n".join(parts))
    return SITE
