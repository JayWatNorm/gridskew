# Endpoint validation

Every ingested endpoint has an explicit contract checked before typed parsing.
The validator is offline and source-independent: callers provide decoded rows
and the matching contract.

**Deployment:** Runtime validation and quarantine are deployed for all five
ingested datasets. Carbon forecast and outturn completeness checks are also
deployed.

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
Invalid envelopes fail without parsing, loading or quarantine.

Carbon also checks semantic period coverage after compatible rows are loaded.
Forecast requires consecutive half-hour periods that cover at least 48 hours
from the first returned boundary and contain the request time in the first
period. Outturn requires consecutive periods covering the endpoint's observed
inclusive request boundaries. These rules do not assume a fixed response size.

## Routing

Rows with no errors remain compatible, including warning-only rows. Warnings
are grouped by reason and field, with the affected count and at most five source
indexes.

Rejected rows are written to `raw.endpoint_quarantine` before compatible rows
are parsed and loaded. Evidence includes the dataset, capture time, request
context, validation errors, observed fields and complete payload. Carbon
observed fields include nested dotted paths.

Mixed responses retain both compatible data and rejected evidence before the
task fails. All-rejected responses skip typed parsing and loading. Outturn
applies this per request window, continues after validation-rejected chunks and
raises once after all requested windows. HTTP, envelope, parsing, quarantine
and database failures still stop immediately.

`process_rows` returns the rejected-row count. Each poller uses that count to
set its final task status after successful writes. Available compatible data is
therefore retained even when the task reports an incomplete or invalid response.

## Components

| File | Responsibility |
|---|---|
| [`validation.py`](../../ingestion/validation.py) | Generic flat and nested contract checks |
| [`contracts.py`](../../ingestion/elexon/contracts.py) | PN, QPN and B1610 contracts |
| [`contracts.py`](../../ingestion/carbon_intensity/contracts.py) | Forecast and outturn contracts |
| [`routing.py`](../../ingestion/routing.py) | Shared warning, quarantine and compatible-row routing |
| [`pn_poller.py`](../../ingestion/elexon/pn_poller.py) | PN response window, parser and loader |
| [`qpn_poller.py`](../../ingestion/elexon/qpn_poller.py) | QPN response window, parser and loader |
| [`b1610_poller.py`](../../ingestion/elexon/b1610_poller.py) | B1610 response window, parser, loader and Decimal encoder |
| [`006_endpoint_quarantine.sql`](../../sql/init/006_endpoint_quarantine.sql) | Rejected-row table |

## Source-specific notes

Type checks use exact Python types, so booleans do not satisfy integer fields.
B1610 accepts integer and `Decimal` quantities. Its response and quarantine
serializers keep decimal values as JSON numbers without converting them to
binary floats or strings.

Target-key duplicates handled by `ON CONFLICT DO NOTHING` are not validation
failures and do not enter quarantine.

## Tests and boundaries

```bash
python -m pytest
```

Fixture-backed tests cover contracts, response envelopes, parsers, loader
wiring and Carbon period completeness. Shared tests cover routing, warning
evidence and quarantine values. They make no live API or database calls. Real
PostgreSQL commit/replay behavior remains a separate follow-up check.
