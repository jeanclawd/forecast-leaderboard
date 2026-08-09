# What the git history proves — and what it doesn't

This project's only real claim is procedural:

> Every forecast on the leaderboard was written into the repository **before**
> the period it predicts had happened, and the score was computed later, from a
> value the repository did not contain at prediction time.

That is a claim about *ordering*, and ordering is the one thing a backtest can
never give you. It is worth being precise about how much of it a stranger can
actually check.

---

## 1. How a skeptic verifies a single forecast

Take any row of `data/<source>/scores.csv` — say model `tabicl`, target period
`D`, issued on day `I`.

```bash
git clone <repo> && cd forecast-leaderboard

# a) find every commit that touched the forecast table
git log --format='%H %cI %s' -- data/paris-temp/forecasts.csv

# b) the commit whose subject is "forecast: issued I…" — read the table AS OF that commit
git show <sha>:data/paris-temp/forecasts.csv | grep ",tabicl,D,"

# c) the load-bearing step: the SAME tree must contain no observation for D
git show <sha>:data/paris-temp/observations.csv | grep "^D,"
#    expected output: nothing at all

# d) and no score for D either
git show <sha>:data/paris-temp/scores.csv | grep "^D,"
#    expected output: nothing at all
```

If (b) prints a prediction and (c)/(d) print nothing, then at that point in the
repository's history the prediction existed and the outcome did not. The
workflow enforces this by committing the three pipeline stages **separately and
in the order observe → score → predict**, with the forecast commit last.

### Cross-checking against something the repo owner doesn't write

Commit metadata is authored on the runner; GitHub's own records are not.

```bash
# every workflow run, with GitHub's timestamp and the sha it produced
gh api repos/<owner>/<repo>/actions/runs \
  --jq '.workflow_runs[] | {head_sha, created_at, updated_at, conclusion, event}'

# the push events GitHub recorded for the repo
gh api repos/<owner>/<repo>/events --jq '.[] | select(.type=="PushEvent")
  | {created_at, sha: .payload.head}'
```

`created_at` here is stamped by GitHub, not by the committer. If a commit
claiming to be from 6 a.m. on the 9th only appears in a run created on the 11th,
that is visible.

---

## 2. Where the guarantee is weak

Be blunt about this; a leaderboard that oversells its own integrity has already
failed the test it is advertising.

**Commit timestamps are attacker-controlled.** `GIT_AUTHOR_DATE` and
`GIT_COMMITTER_DATE` are plain environment variables. The repo owner can write
any date into any commit. Timestamps alone prove nothing.

**History can be rewritten.** `git rebase` + `git push --force` can replace the
entire published history with a version in which every forecast was excellent.
Nothing in a repository you control can prevent this. The only defence is that
someone else already has a copy.

**GitHub's run logs are not permanent.** Actions logs default to 90 days
retention, and the repo owner can delete workflow runs. The `events` API keeps
roughly 90 days too. So the independent cross-check in §1 decays; it is strong
this month and gone next year.

**Selective deletion beats selective editing.** An owner does not need to fake a
forecast — it is enough to quietly stop the cron during a bad week, or to drop
rows that are about to be scored badly. Mitigations, all partial:
`forecasts.csv` is append-only and *every* row that matures gets scored; the
cadence is fixed, so missing `issued_day` values are visible; the leaderboard
prints `n` per model. A reader should check for gaps:

```bash
cut -d, -f2 data/paris-temp/forecasts.csv | sort -u   # one row per day, no holes?
```

**Self-hosted runners, or a `workflow_dispatch` re-run, blur the picture.**
A dispatched run at an arbitrary time looks much like a scheduled one; the
`event` field in the runs API distinguishes them, which is why the query above
selects it.

**None of this validates the *data source*.** open-meteo's recent-past values
are analysis output and can be revised. This repo freezes the first value it
ever observed for a period and never overwrites it, so a score cannot drift
after the fact — but that also means the frozen "truth" may differ slightly
from what open-meteo says today. That is a deliberate trade: reproducibility
over accuracy of the ground truth.

---

## 3. What would actually harden it

In rough order of effort-to-value:

1. **Let GitHub sign the commits.** Commits created through the Contents API
   (`PUT /repos/{o}/{r}/contents/{path}`) are signed by GitHub's `web-flow` key
   and marked *Verified*. Forging one then requires compromising GitHub, not
   just the repository. A `git push` from a runner, as this workflow does, is
   **not** signed — that is a real gap in the current v0.
2. **Anchor the commit hash in an append-only external log.** OpenTimestamps
   (`ots stamp`) writes a Bitcoin-backed proof that a given hash existed before
   a given block; Sigstore's Rekor is the same idea with a transparency log. The
   workflow has a commented-out `ots stamp` step. This is what converts "trust
   the owner" into "trust Bitcoin/Rekor", and it is the single highest-value
   addition.
3. **Publish to somewhere the owner can't rewrite.** Post each day's forecast to
   a channel with immutable history — an append-only S3 bucket with object lock,
   a mailing list archive, a Mastodon/Bluesky post, an IPFS pin. Then a rewrite
   contradicts a copy the owner doesn't hold.
4. **Invite mirrors.** A single third party running
   `git clone --mirror` on a cron makes force-push detectable. This is cheap and
   social rather than cryptographic, and for most audiences it is enough.
5. **Make the horizon longer than the response time.** A 7-day horizon means the
   owner would have to have decided to cheat a week in advance, every week,
   consistently. Short horizons are easier to game.

Until (1) and (2) are in place, the accurate statement is:

> *A reader who trusts GitHub's run records and has not seen evidence of a force
> push can confirm these forecasts predate their outcomes. A reader who assumes
> the repository owner is adversarial cannot — yet.*

---

## 4. The one thing this beats a backtest at

Worth stating plainly, because it is the actual argument for the whole design.

TabICL is a **pretrained** tabular foundation model. Any retrospective
evaluation — including the offline backtest shipped in this repo — has an
unfalsifiable confound: you cannot prove the model never saw that stretch of
history during pretraining, and for a public dataset like weather you should
assume it might have.

A forecast of a period that **did not exist when the model was trained** is
immune to that confound by construction. That is not a marginal improvement in
experimental hygiene; it is the difference between a claim that can be
challenged on leakage grounds and one that cannot.

Hence the two tables on the leaderboard page, deliberately never merged:

| table | n | leakage-proof | git-attested |
|---|---|---|---|
| live leaderboard | grows by `horizon` rows/model/day | yes | yes |
| offline backtest | large on day one | no | no |

Read the backtest for statistical power. Read the live table for whether the
result is real.
