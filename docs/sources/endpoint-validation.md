# Endpoint validation

## Status

PN, QPN and B1610 have field contracts and a shared, offline row validator.
Twelve pytest tests cover PN contract rules and basic batch behaviour.
The pollers still use their existing fetch, parse and load paths; they do not
call the validator or write rejected rows to quarantine yet. This is a tested
foundation for integration, not an enabled production control.

## Components

| File | Responsibility |
|---|---|
| [contracts.py](../../ingestion/elexon/contracts.py) | Independent `PN_SPEC`, `QPN_SPEC` and `B1610_SPEC` definitions |
| [validation.py](../../ingestion/validation.py) | Source-independent checks over already-fetched Python dictionaries |
| [test_validation.py](../../tests/test_validation.py) | PN contract tests, mixed-batch findings and empty-input behaviour |
| [006_endpoint_quarantine.sql](../../sql/init/006_endpoint_quarantine.sql) | Fixed quarantine-table definition for the planned rejected-row path |

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

## Validation performed

Earlier offline checks exercised the three captured fixtures: 28 PN, 24 QPN and
27 B1610 rows. Additional checks covered missing keys, nullable values,
incompatible types, boolean rejection, B1610 integer/decimal handling,
unexpected fields, missing optional fields, mixed batches and empty input.

Those exploratory checks are separate from the repeatable pytest coverage in
[test_validation.py](../../tests/test_validation.py). Its 12 tests cover:

- A valid PN row and missing required fields, including required-but-nullable
  `bmUnit`.
- Forbidden nulls and permitted null `bmUnit` values.
- Rejection of string and boolean values for integer `settlementPeriod`.
- Unexpected fields and missing optional `dataset` as warning-only findings.
- A renamed field as a missing-required error plus an unexpected-field warning.
- Mixed valid/invalid batch findings: count, zero-based indexes, errors,
  warnings and references to the original input dictionaries.
- An empty input list returning an empty findings list.

The tests import `PN_SPEC` and use independent expected findings. Row-based
cases load the captured PN fixture and copy its first dictionary before any
deliberate changes; the empty-input test needs no fixture. They make no live
API calls or database connections. Checking original-row references is distinct
from proving that validation leaves all input values unchanged.

Run from the repository root with the development dependencies installed:

```bash
python -m pytest tests/test_validation.py -v
```

The full Python suite passed with **25 tests on 31 August 2026**, including these
12 validator tests. QPN/B1610-specific validator coverage and an explicit
input-non-mutation test remain follow-up work; the earlier exploratory checks
are not a substitute for those tests.

## Integration boundary

The intended poller flow is fetch, validate, route, parse and load. Rejected
source rows will be retained in `raw.endpoint_quarantine` with dataset, request
context, retrieval time and validation findings. Compatible rows can continue
to typed loading. Existing target-key duplicates remain handled by
`ON CONFLICT ... DO NOTHING`; they are not quarantine events.

The quarantine DDL and a standalone insert/commit/read-back check have been
verified in development. This does not yet prove the Python row-routing path.

Before live integration, the remaining work includes:

- QPN/B1610-specific validator tests, including B1610 integer/Decimal
  alternatives, and explicit input-non-mutation coverage.
- Tests and handling for unexpected response containers or non-dictionary
  rows. Current functions expect flat row dictionaries.
- Routing date-parsing failures and other row-specific parse failures.
- A shared quarantine writer and transaction tests that protect rejected rows
  when later processing fails.
- Numeric-preserving JSON serialisation for rejected B1610 rows.
- A decision on Airflow status after a mixed accepted/rejected run commits.
- Nested contracts for Carbon Intensity, after the PN path is proven.

Value ranges and model-quality rules remain downstream concerns. Database
schema-drift monitoring, automatic schema evolution and blanket retention of
successful response payloads are outside this feature.
