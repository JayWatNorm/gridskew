# Endpoint validation

Every ingested endpoint has an explicit contract checked before typed parsing.
The validator is offline and source-independent: callers provide decoded rows
and the matching contract.

Validation and quarantine cover PN, QPN, B1610, BOALF, REMIT, both Carbon
datasets and the BM-unit registry. Carbon forecast and outturn also have
period-completeness checks.

## Behavior

`validate_row(row, spec)` returns error and warning lists.
`validate_rows(rows, spec)` returns one indexed finding per source item without
changing the input.

| Condition | Finding |
|---|---|
| Required field missing | Error |
| Forbidden null | Error |
| Incompatible exact Python type | Error |
| Unexpected field | Warning |
| Optional field missing | Warning |

Elexon contracts are flat. Carbon contracts recurse through `intensity` and
report dotted paths such as `intensity.actual`. A non-dictionary row produces
an error finding and does not stop later rows from being checked.

Pollers validate the response envelope before row validation. Elexon expects a
non-empty list; Carbon expects a dictionary containing a non-empty `data` list.
Invalid envelopes fail without parsing, loading or quarantine. REMIT is the
exception: an empty list is a normal window in which nothing was published,
and the run continues (see
[REMIT ingestion](elexon/031_remit_ingestion.md)).

Carbon also checks semantic period coverage after compatible rows are loaded.
Forecast requires consecutive half-hour periods, the first of which contains
the request time. A forecast window shorter than 48 hours is stored and
logged as a warning without failing the task: the source feed has stopped
extending. Outturn requires consecutive periods covering the endpoint's
observed inclusive request boundaries. These rules do not assume a fixed
response size.

Each B1610 capture refuses a response before any write when a row holds
another settlement date or a period outside that date. After compatible rows
are loaded, it fails when a period is missing or another settlement run is
returned.

## Routing

Rows with no errors remain compatible, including warning-only rows. Warnings
are grouped by reason and field, with the affected count and at most five source
indexes. The BM-unit poller logs its warnings per row. REMIT leaves out a
field that has no value, so its poller does not log the missing-optional-field
warning.

Rejected rows are written to `raw.endpoint_quarantine` before compatible rows
are parsed and loaded. Evidence includes the dataset, capture time, request
context, validation errors, observed fields and complete payload. Carbon
observed fields include nested dotted paths.

Mixed responses retain both compatible data and rejected evidence before the
task fails. All-rejected responses skip typed parsing and loading. Outturn
applies this per request window: it continues after rejected or incomplete
windows and raises once after all requested windows. HTTP, envelope, parsing,
quarantine and database failures stop immediately. The exception is the Monday
re-fetch of outturn history: a failed history window is logged and counted as
incomplete, the remaining history windows are still attempted, and the run then
fails.

`process_rows` returns the rejected-row count. Each poller that calls it uses
that count to set its final task status after successful writes. Available
compatible data is therefore retained even when the task reports an incomplete
or invalid response.
REMIT counts from the table instead: every run fails while any `REMIT` row is
in `raw.endpoint_quarantine`, so a rejected message is not forgotten by the
next window. Recovery is in [REMIT ingestion](elexon/031_remit_ingestion.md).

The BM-unit registry uses a stricter complete-response rule. Any invalid row
blocks the entire extract. Rejected rows are quarantined, but compatible rows
from that response are not published. Its row/unit-count and key-loss checks
also block suspiciously small responses. See
[Elexon BM-unit ingestion](elexon/071_bmunits_ingestion.md).

## Components

| File | Responsibility |
|---|---|
| [`validation.py`](../../ingestion/validation.py) | Generic flat and nested contract checks |
| [`contracts.py`](../../ingestion/elexon/contracts.py) | PN, QPN, B1610, BOALF, REMIT and BM-unit contracts |
| [`contracts.py`](../../ingestion/carbon_intensity/contracts.py) | Forecast and outturn contracts |
| [`routing.py`](../../ingestion/routing.py) | Shared warning, quarantine and compatible-row routing |
| [`pn_poller.py`](../../ingestion/elexon/pn_poller.py) | PN response window, parser and loader |
| [`qpn_poller.py`](../../ingestion/elexon/qpn_poller.py) | QPN response window, parser and loader |
| [`b1610_poller.py`](../../ingestion/elexon/b1610_poller.py) | B1610 response window, settlement-date coverage check, parser, loader and Decimal encoder |
| [`boalf_poller.py`](../../ingestion/elexon/boalf_poller.py) | BOALF response window, parser and loader |
| [`remit_poller.py`](../../ingestion/elexon/remit_poller.py) | REMIT watermark windows, parser, loader and quarantine gate |
| [`bmunits_poller.py`](../../ingestion/elexon/bmunits_poller.py) | Complete registry gate, manifest and raw-row loader |
| [`completeness.py`](../../ingestion/carbon_intensity/completeness.py) | Carbon period-coverage checks |
| [`forecast_poller.py`](../../ingestion/carbon_intensity/forecast_poller.py) | Forecast envelope, parser, loader and 48-hour warning |
| [`outturn_poller.py`](../../ingestion/carbon_intensity/outturn_poller.py) | Outturn windows, history re-fetch, parser and loader |
| [`V006__endpoint_quarantine.sql`](../../sql/migrations/V006__endpoint_quarantine.sql) | Rejected-row table |

## Source-specific notes

Type checks use exact Python types, so booleans do not satisfy integer fields.
B1610 quantities and REMIT capacities accept integer and `Decimal` values. Both
pollers decode responses with `Decimal` and write payloads as JSON numbers,
never binary floats or strings.

Target-key duplicates handled by `ON CONFLICT DO NOTHING` are not validation
failures and do not enter quarantine.

## Tests and boundaries

```bash
python -m pytest
```

Fixture-backed tests cover contracts, response envelopes, parsers, loader
wiring and Carbon period completeness. Shared tests cover routing, warning
evidence and quarantine values. They make no live API or database calls, so
they do not exercise real PostgreSQL commits. CI covers that separately: it
loads the fixtures into a disposable PostgreSQL through the same routing and
loaders before the dbt build.
