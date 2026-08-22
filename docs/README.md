# Data source documentation

Grouped by source. Every dataset page opens with an **In plain terms** section;
no industry background is assumed.

## Two pages per dataset

**`0N0_name.md`** — what the data is, what the columns mean, and the traps.
**`0N1_name_ingestion.md`** — how it is loaded: schedule, chunking, backfill,
volumes, and any experiment running against it.

They change on different schedules. What `level_from` means will be true in five
years; the chunk size will not. Ingestion pages exist **only for datasets
actually being ingested** — the rest have one page until something reads them.

The reasoning that would otherwise be repeated across every ingestion page lives
once in [sources/ingestion-patterns.md](sources/ingestion-patterns.md).

Pages are **numbered in build order**, not alphabetically. Folder READMEs stay
unnumbered so GitHub renders them on arrival.

```
docs/sources/
  ingestion-patterns.md       Why the pollers differ. Read this first  [both sources]
  carbon-intensity/
    README.md                 API level: base URL, auth, licence, shared gotchas
    010_forecast.md           Every version of the forecast            [running]
    011_forecast_ingestion.md   every 30 min, catchup=False, no backfill possible
    020_outturn.md            What actually happened                   [running]
    021_outturn_ingestion.md    daily, 7-day look-back, 30-day chunks
  elexon/
    README.md                 API level, plus how the GB market works
    010_pn.md                 The promise                              [running]
    011_pn_ingestion.md         daily, catchup=True, 1-day chunks
    015_qpn.md                An internal process netted off it        [running]
    016_qpn_ingestion.md        as PN
    020_b1610.md              The receipt                              [table built]
    030_remit.md              The excuse note                          [planned]
    040_boalf.md              The intervention                         [planned]
    050_demand.md             Expected versus actual usage             [planned]
    060_system-prices.md      The cost of fixing the imbalance         [planned]
    070_bmunits.md            The address book                         [planned]
    080_mels-mils.md          The headroom                             [optional]
```

New to this domain? Start with **How the GB electricity market works, briefly**
in [sources/elexon/README.md](sources/elexon/README.md). Four ideas, and every
dataset here follows from them.

## Raw layer conventions

These hold for every table in the `raw` schema.

**Append only.** No updates, no deletes. A re-fetch lands as a new row rather
than overwriting, so revisions stay visible and every downstream read collapses
to one row per key.

**`retrieved_at` on every table.** Captured once per run, immediately before
the first HTTP request, and written identically to every row in that batch. It
is part of the primary key, which is what makes a batch identifiable as a batch
and a revision distinguishable from the original.

**Never Airflow's logical date.** A retried task would be stamped with its
scheduled time rather than its actual execution time, corrupting anything
derived from it.

**Raw records what the source returned.** Fields are stored even when they are
always null, names are kept close to the source, and value-level expectations
are tested in dbt rather than enforced as constraints. A `NOT NULL` on a value
column rejects the row and destroys the evidence that the source sent something
unexpected.

**Structural constraints only.** Primary keys, types, and not-null on identity
columns. Those define what a row is. Everything else is a belief, and beliefs
get tested.

## Reading a dataset page

**`0N0_name.md`** covers:

- what the dataset is, in plain terms
- the endpoint and its parameters
- the response shape, field by field
- the raw table DDL and why the key is what it is
- traps found in practice, and open questions

**`0N1_name_ingestion.md`** covers:

- module, table, DAG, schedule, `catchup` setting
- chunk size and why
- backfill approach, or why one is impossible
- measured volumes
- any pre-registered experiment running against the table, and its result

## A note on the numbers in these pages

Several figures in this documentation were **wrong by an order of magnitude**
before being measured directly, because they were inferred from response sizes
rather than counted. Where a page states a volume, it says whether the figure was
counted or estimated. Where it was corrected, the correction is left visible
rather than quietly overwritten.
