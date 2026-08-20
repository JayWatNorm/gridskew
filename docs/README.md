# Data source documentation

One page per dataset, grouped by source. Each page records what the API
returns, what the raw table looks like, and the traps found while building it.

Every dataset page opens with an **In plain terms** section. No industry
background is assumed.

Dataset pages are **numbered in build order**, not alphabetically. The README
in each folder is the source-level overview and stays unnumbered so GitHub
renders it on arrival.

```
docs/sources/
  carbon-intensity/
    README.md             API level: base URL, auth, licence, shared gotchas
    010_forecast.md       Every version of the forecast        [built]
    020_outturn.md        What actually happened               [built]
  elexon/
    README.md             API level, plus how the GB market works
    010_pn.md             The promise                          [planned]
    020_b1610.md          The receipt                          [planned]
    030_remit.md          The excuse note                      [planned]
    040_boalf.md          The intervention                     [planned]
    050_demand.md         Expected versus actual usage         [planned]
    060_system-prices.md  The cost of fixing the imbalance     [planned]
    070_bmunits.md        The address book                     [planned]
    080_mels-mils.md      The headroom                         [planned]
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

Each page covers:

- what the dataset is, in one paragraph
- the endpoint and its parameters
- the response shape, field by field
- the raw table DDL and why the key is what it is
- traps found in practice
