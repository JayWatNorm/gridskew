# QPN ingestion

How `raw.elexon_qpn` is loaded. For what the data means, see
[015_qpn.md](015_qpn.md). For the reasoning behind these patterns, see
[../ingestion-patterns.md](../ingestion-patterns.md).

**Status: not yet deployed.** Table exists, poller not started.

| | |
|---|---|
| Module | `ingestion/elexon/qpn_poller.py` |
| Table | `raw.elexon_qpn` |
| DDL | `sql/init/004_elexon_qpn.sql` |
| DAG | not yet written |
| Schedule | daily |
| `catchup` | **`True`** |

## Identical to PN in every respect that matters here

QPN shares an OpenAPI schema object with PN (`PhysicalNotificationData`), the
same parameters, the same grain and the same timestamp formats. **Everything in
[011_pn_ingestion.md](011_pn_ingestion.md) applies unchanged**: one day per
chunk, `catchup=True` a year back, `timeout=30`, `page_size=1000`,
`sleep(0.2)` on backfill.

**Volume is assumed to match PN — about 130,000 rows a day — but has not been
counted directly.** Worth measuring once during S1.1 rather than inheriting the
assumption, since QPN submission is optional and the set of units filing it may
be smaller.

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

Every QPN row observed for `T_DRAXX-1` carried `levelFrom` and `levelTo` of zero.
A table full of zeros is not a broken poller — it means units file even when
there is nothing to deduct.

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

Ingesting QPN is what keeps that question answerable. It is cheap, it shares
everything with PN, and skipping it would foreclose the analysis rather than
defer it.

## Result

*To be recorded here when the experiments conclude.*
