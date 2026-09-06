# Endpoint validation

## Status

PN, QPN and B1610 have field contracts and a shared, offline row validator.
Nineteen validator tests cover PN contract rules and batch behaviour, input
non-mutation, QPN and B1610 fixture compatibility, and B1610 quantity types.
The local PN poller now validates every fetched row before parsing. It sends
compatible and warning-only rows to the typed load and rejected findings to a
fixed-table quarantine writer. Warning-only findings also produce grouped,
bounded JSON warning logs. Mixed responses commit both compatible rows and
quarantine evidence before the task fails. All-rejected responses commit the
quarantine evidence, skip typed parsing and loading, then fail. PN routing,
warning-evidence, writer and final-status tests are complete, but this branch is
not deployed. QPN and B1610 integration remains planned.

## Components

| File | Responsibility |
|---|---|
| [contracts.py](../../ingestion/elexon/contracts.py) | Independent `PN_SPEC`, `QPN_SPEC` and `B1610_SPEC` definitions |
| [validation.py](../../ingestion/validation.py) | Source-independent checks over already-fetched Python dictionaries |
| [test_validation.py](../../tests/test_validation.py) | Cross-source contract tests, mixed-batch findings and input non-mutation |
| [pn_poller.py](../../ingestion/elexon/pn_poller.py) | PN validation routing, grouped warning logs, typed loading and the current PN quarantine writer |
| [test_elexon_pn.py](../../tests/test_elexon_pn.py) | PN parsing, routing, warning-evidence and quarantine-writer boundary tests |
| [006_endpoint_quarantine.sql](../../sql/init/006_endpoint_quarantine.sql) | Fixed quarantine-table definition for rejected source rows |

The validator makes no HTTP requests, imports no poller and performs no database
writes. The caller supplies both the decoded data and the appropriate contract.

## Field contracts

Each contract contains nine field names. Each name maps to three settings:

- `type`: an allowed Python type or tuple of types.
- `required`: whether the key must be present.
- `nullable`: whether a present key may contain `None`.

A required, nullable field must exist but may contain `None`. This differs from
an optional field, whose absence does not produce an error. Contract order does
not need to match the response order; checks use field names.

PN and QPN require `bmUnit` but permit a null value. Their
`nationalGridBmUnit` must be present and non-null. B1610 requires a non-null
`bmUnit`; `nationalGridBmUnitId` and `psrType` are required but nullable.
All three recognise `dataset` as optional and non-null when present.

B1610 fetches decimal JSON numbers as `Decimal`, while integer JSON numbers
decode as `int`. Its `quantity` contract therefore accepts `(int, Decimal)`.
Type checks compare exact types: Python booleans do not satisfy integer fields.
Dates and timestamps remain strings here; the parser owns their conversion.
Locally generated `retrieved_at` is not a source field and is not in a contract.

## Function interfaces

`validate_row(row, spec)` returns `(errors, warnings)`, two lists of messages.

| Finding | Result |
|---|---|
| Required key absent | Error |
| Present value is null where null is forbidden | Error |
| Non-null value has an incompatible exact type | Error |
| Key is not in the contract | Warning |
| Optional key absent | Warning |

Missing keys and nulls are handled before type checks. The function does not
coerce values, drop fields or change the supplied row.

`run(results, spec)` checks every row and returns a list of findings:

```python
[
    {"index": 0, "row": original_row, "errors": [], "warnings": []},
    # One dictionary per input row.
]
```

Indexes are zero-based. Each `row` is a reference to the original dictionary,
not a copy. The output preserves input order, and an empty input list produces
an empty findings list. The function reports findings; it does not itself split
the data into load and quarantine batches.

A non-dictionary item produces an indexed error finding with the original item
and does not stop later rows from being validated. Malformed outer response
containers are not yet handled as a separate response-level failure.

## Validation performed

The repeatable pytest coverage in
[test_validation.py](../../tests/test_validation.py) exercises the three
captured fixtures: 28 PN, 24 QPN and 27 B1610 rows. Its 19 tests cover:

- A valid PN row and missing required fields, including required-but-nullable
  `bmUnit`.
- Forbidden nulls and permitted null `bmUnit` values.
- Rejection of string and boolean values for integer `settlementPeriod`.
- Unexpected fields and missing optional `dataset` as warning-only findings.
- A renamed field as a missing-required error plus an unexpected-field warning.
- Mixed valid/invalid batch findings: count, zero-based indexes, errors,
  warnings and references to the original input dictionaries.
- A non-dictionary item producing an error finding without preventing later
  valid rows from being checked.
- An empty input list returning an empty findings list.
- Source input remaining unchanged after batch validation.
- Complete QPN and B1610 fixtures matching their independent contracts.
- B1610 accepting integer and `Decimal` quantities while rejecting `float`.

The tests import the production `PN_SPEC`, `QPN_SPEC` and `B1610_SPEC` contracts
and use independent expected findings. Deliberate row changes happen only in
memory. They make no live API calls or database connections. Checking
original-row references is distinct from proving that validation leaves all
input values unchanged; both behaviours now have explicit coverage.

Run from the repository root with the development dependencies installed:

```bash
python -m pytest tests/test_validation.py -v
```

The full Python suite passed with **36 tests on 6 September 2026**, including
these 19 validator tests and the PN routing, warning-evidence and writer
coverage. Repository-wide Ruff lint and formatting checks also passed.

## Integration boundary

The local PN flow is fetch, validate, route, parse and load. Rejected source
rows are prepared for `raw.endpoint_quarantine` with the dataset, request
context, retrieval time, zero-based source index, validation errors, observed
field names and complete source payload. Compatible rows continue to typed
loading. Warning-only rows remain compatible and do not enter quarantine.
Existing target-key duplicates retain `ON CONFLICT ... DO NOTHING`; they are
not quarantine events.

After both routed writes complete, any rejected row makes the task fail. A
mixed response therefore retains its compatible rows and quarantine evidence
before raising. An all-rejected response retains its quarantine evidence,
does not call typed parsing or loading with an empty list, and then raises.

Warning-only findings produce one warning-level JSON log per `(reason, field)`
group. Each record contains the dataset, source retrieval time, request window,
severity, reason, affected field, complete affected-row count and up to five
zero-based source indexes. A row with multiple warnings contributes to each
matching group. The full count preserves impact while the fixed-size sample
keeps Airflow logs bounded. No warning table or duplicate error-summary table
is used.

The quarantine DDL and a standalone insert/commit/read-back check were verified
in development. The Python tests now prove PN routing and the values sent to the
writer with mocks; they do not constitute a live database integration test.

Before live integration, the remaining work includes:

- Handling malformed outer response containers as response-level failures.
- Routing date-parsing failures and other row-specific parse failures.
- Moving the PN writer to a shared quarantine module before wider poller use.
- Integrating QPN and B1610, including numeric-preserving JSON serialisation for
  rejected B1610 rows.
- Testing retry behaviour and persistence when later processing fails.
- Proving the five-index warning sample cap with a batch containing more than
  five matching warnings.
- Adding nested contracts for Carbon Intensity after the flat Elexon path is
  proven.

Value ranges and model-quality rules remain downstream concerns. Database
schema-drift monitoring, automatic schema evolution and blanket retention of
successful response payloads are outside this feature.
