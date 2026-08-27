# Ingestion patterns

How this project decides to load a source, and why the pollers do not all look
alike. The per-dataset pages carry the settings; this page carries the reasoning.

Each dataset has two pages: `0N0_name.md` for what the data is and what the
columns mean, and `0N1_name_ingestion.md` for how it is loaded. Anything that
would otherwise be repeated across those ingestion pages lives here.

---

## The first question: is the source addressable in time?

Everything follows from this.

**Addressable** means a request for a past window returns that window. Elexon's
`from`/`to` endpoints are addressable: ask for last March and you get last March.

**Not addressable** means the endpoint always returns current state regardless of
what you ask. The Carbon Intensity forecast is not addressable — it re-runs its
model every 30 minutes and overwrites in place, so asking today what it forecast
last March returns the final revision, not the 48-hour-ahead figure.

| | Airflow setting | Backfill |
|---|---|---|
| Addressable | `catchup=True` | 365 runs, each fetching its own interval |
| Not addressable | `catchup=False` | Impossible. History does not exist; archive forwards |

**`catchup` is a property of the source, not a preference.** Setting
`catchup=True` on a non-addressable source runs 365 tasks that all fetch today's
data and write it 365 times.

### `start_date` must be a literal, never computed

Where `catchup=True`, `start_date` defines the history window — and it must be a
fixed date written into the file.

**Never `datetime.now() - timedelta(days=365)`.** A DAG module is re-parsed by
the scheduler roughly every thirty seconds, so a computed `start_date` produces a
different value on every parse. Since Airflow derives which intervals exist from
`start_date`, a moving anchor shifts the schedule underneath the scheduler: runs
are skipped or duplicated and the DAG's history stops being coherent.

This is the same rule as mutable default arguments — **the expression is
evaluated when the module executes**, and a DAG module executes constantly.

The consequence to accept: redeploying onto a fresh Airflow instance years later
re-backfills from that date. That is a documented property of the design, not a
bug, and the fix is to edit the literal deliberately rather than to compute it.

## The second question: load once, or keep re-checking?

Nearly every dataset in this domain revises, so "does it revise" does not
discriminate. Two things do.

**Is re-polling structural or exceptional?** Carbon intensity actuals arrive late
for *every* period, so re-polling is how the data completes — structural. A PN
restatement, if it happens at all, would affect a small minority of rows —
exceptional.

**What does re-polling cost?** This varies by three orders of magnitude:

| Source | Rows/day | Cost of a 7-day overlap |
|---|---|---|
| Carbon intensity outturn | 48 | 336 rows/day — free |
| Elexon PN | 126,203 | 883,421 rows/day — **~57 GB/year** |

At forty-eight rows a day nobody would separate the two jobs. At a hundred and
twenty-six thousand, the same design is ruinous.

Both PN figures are **counted, not estimated** — 126,203 rows per run at a
measured 177 bytes each. See
[elexon/011_pn_ingestion.md](elexon/011_pn_ingestion.md).

> **Load history once. Re-poll only where something is known to change, and only
> over the window where it changes.**

## Lag ladders, not rolling windows

Where re-polling is needed at depth, sample at fixed lags rather than dragging a
window behind the present.

A rolling 90-day window on PN would be 4.3 billion rows and 655 GB a year.
Sampling at 1, 8, 30 and 90 days reaches the same depth for 29 GB — the same
coverage at 4% of the cost, because a rolling window re-fetches every day in
between for no reason.

Ladders belong in configuration, not hard-coded, because the whole point is that
they change once evidence arrives about where revisions actually land.

## Probe before an expensive fetch

Where a dataset publishes its own revision marker, use it. B1610 carries
`settlementRunType` in every row, so detecting a restatement does not require
comparing 130,000 values — it requires seeing whether a new run type appeared.

| Approach | Rows fetched |
|---|---|
| Blind re-fetch of a day | 129,797 |
| Probe one BM unit, then decide | 48, plus the full day only if it changed |

**0.04% of the cost on the days nothing changed**, which will be most of them,
because settlement runs complete in waves rather than gradually.

The assumption to validate once: that settlement runs advance uniformly across
units. Probe three or four units on the same day and check they agree.

## Chunk size is bounded by memory, not by the API

Elexon imposes no window cap on the stream endpoints — PN returns 1, 7 and 18 day
windows with linear row counts and no truncation, up to about 626 MB in a single
response.

The constraint is what happens after the response arrives:

| Chunk | Rows | JSON | In Python |
|---|---|---|---|
| 1 day | 130k | 34 MB | ~0.1 GB |
| 7 days | 909k | 236 MB | ~0.8 GB |
| 30 days | 3.9M | 1 GB | ~3.6 GB |

**One day per chunk**, because an Airflow worker shares a homelab with Postgres
and everything else, and a poller that is OOM-killed part way through a backfill
is a worse failure than a few hundred extra requests.

A chunk boundary can split a settlement period's ramp segments across two
requests. That is harmless — each segment is its own row keyed on its start
instant, so both chunks contribute and the period is complete once both have
loaded. It does mean a query assuming a period's segments arrived together will
be wrong during a partial backfill.

## Politeness

Neither API publishes a rate limit, and neither returns rate-limit headers, so a
client cannot see how close it is to one. Backing off gracefully is impossible;
you would simply be blocked.

- **Scheduled daily runs**: one or two requests. No delay needed.
- **Backfills**: `time.sleep(0.2)` between requests. It costs seconds and removes
  a risk with no warning signal.
- **Identify yourself.** Both sets of terms prohibit concealing an application's
  identity. `gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)`.
- **Assert on row counts, not just status codes.** A truncated or throttled
  response can return 200 with a short body, which looks like a successful run
  that wrote nothing.

That last point is not theoretical. During reconnaissance a truncated response
was measured as though it were complete, and produced a volume estimate that was
wrong by a factor of ten.

## Why `retrieved_at` is in every primary key

The raw layer is append-only: nothing is updated, nothing is deleted. Every poll
writes a new generation of rows, and `retrieved_at` is what keeps them apart.

For most datasets this preserves a revision history the source also exposes
through its own marker. For PN and QPN it is the **only** revision axis that
exists — neither carries a marker of its own, unlike `settlementRunType` on
B1610, `revisionNumber` on REMIT or `notificationSequence` on MELS.

It also means backfill rows and daily rows need no flag to tell apart:

```sql
SELECT retrieved_at - time_from AS observation_lag FROM raw.elexon_pn;
```

A lag of three hundred days is a backfill; a lag of thirty hours is the daily
job. **Do not store what can be derived** — and note that a backfill produces a
*range* of lags, from a year down to nearly zero, so "backfill" is a property of
a run rather than of a row.

## The three patterns in use

| DAG | Setting | Why |
|---|---|---|
| Carbon intensity forecast | `catchup=False`, poll now | Source not addressable. History does not exist |
| Carbon intensity outturn | `catchup=False`, rolling 7-day window | Addressable, but actuals arrive late for every period. Volume so small the overlap is free |
| Elexon PN, QPN, B1610 | `catchup=True`, daily chunks, lag ladder | Addressable, high volume, so per-run intervals and targeted sweeps matter |

Three behaviours, three justifications. The inconsistency is deliberate, and the
reasoning is above rather than in anyone's memory.
