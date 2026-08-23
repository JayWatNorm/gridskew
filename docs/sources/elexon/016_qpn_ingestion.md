# QPN ingestion

How `raw.elexon_qpn` is loaded. For what the data means, see
[015_qpn.md](015_qpn.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: deployed, backfill complete 2026-08-23.** 366 runs covering
2025-08-22 to 2026-08-23, 41,533,137 rows, no failures.

| | |
|---|---|
| Module | `ingestion/elexon/qpn_poller.py` |
| Table | `raw.elexon_qpn` |
| DDL | `sql/init/004_elexon_qpn.sql` |
| Tests | `tests/test_elexon_qpn.py`, against `qpn_stream.json` |
| DAG | `dags/gridskew_elexon_qpn_dag.py` |
| Schedule | `@daily` |
| `start_date` | 2025-08-22 |
| `catchup` | **`True`** |

## Identical to PN in every respect that matters here

QPN shares an OpenAPI schema object with PN (`PhysicalNotificationData`), the
same parameters, the same grain and the same timestamp formats. **Everything in
[011_pn_ingestion.md](011_pn_ingestion.md) applies unchanged**: one day per
chunk, `catchup=True` a year back, `timeout=30`, `page_size=1000`,
`sleep(0.2)` on backfill.

## Volume, counted

**The assumption that QPN matched PN was close but not exact.** Counted across
the completed backfill, 2026-08-23:

| | QPN | PN |
|---|---|---|
| Rows | **41,533,137** | 46,190,258 |
| Rows per run | **113,479** | 126,203 |
| Distinct units | **2,429** | 2,552 |
| On disk | **7,020 MB** | 7,780 MB |
| Density | 177 bytes/row | 177 bytes/row |

QPN runs about **10% smaller** than PN, and **123 units file a PN and never a
QPN** — the optional submission from the BSC glossary, visible in the data.

Two figures are worth more than the totals.

**Null `bm_unit` is 657,743 rows — identical to PN, to the row.** The same units
are missing an Elexon identifier in both datasets. That was a key design forced
by a constraint violation during the first load; it is now a measured property of
the source rather than an inference from one dataset to the other.

**18,300 rows are non-zero — 0.044%.** That works out at exactly **50 per run,
every run, for a year.** Zero variance. See [015_qpn.md](015_qpn.md), where that
number turns out to describe a single BM unit.

## Built as a separate module, deliberately

Decided 2026-08-21. A single parameterised module taking the dataset name would
be defensible — the API's own spec treats the two as one shape — but two files
were chosen so each reads start to finish with no indirection, and so divergence
later costs nothing.

**The accepted cost is that a parse bug has to be fixed twice.** The tests exist
partly to make that visible: both modules are tested against their own captured
fixture, so a fix applied to one and not the other shows up as a failure rather
than as quietly wrong data.

## Zero rows are normal

**99.96% of this table is zeros, and that is correct.** Units file even when
there is nothing to deduct, so a table that looks empty of signal is not a broken
poller. Anyone reviewing this table for the first time will assume otherwise,
which is why it is stated in the table comment as well as here.

**Submission is optional**, so a missing row means "this unit does not submit
QPN", not "the deduction is zero". Only an outer join is safe when combining with
PN.

## Pre-registered test: do QPNs restate?

Same as PN, same rule, same ladder — see
[011_pn_ingestion.md](011_pn_ingestion.md). QPN carries no revision marker
either, so `retrieved_at` is again the only instrument.

Run both experiments over the same four weeks so the results are comparable.

## The open question this table exists to answer

`015_qpn.md` carries it in full. Briefly: the BSC glossary states QPN is **not
used in Settlement** and that the deduction determines the level *the Dynamic
Data apply to*, while `B1610` is settlement metered volume. So whether
`PN - QPN` is the right promise to compare against B1610 is **unresolved**.

**Bounded rather than open, as of 2026-08-23.** A full year of data shows the
deduction can affect **one BM unit in the market**. Every other unit's shortfall
figures are identical under either interpretation, so no model is blocked. The
question still needs answering for that unit, and `B1610` answers it.

Ingesting QPN is what kept the question answerable. It was cheap, it shares
everything with PN, and skipping it would have foreclosed the analysis rather
than deferred it — the bound above could not have been established without a year
of the data already loaded.

## Result

*To be recorded here when the experiments conclude.*
