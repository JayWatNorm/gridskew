# Test fixtures

Real API responses, captured verbatim unless marked truncated, grouped by
source to mirror `ingestion/` and `docs/sources/`. Hand-written fixtures encode
what you think an API returns; captured ones record what it actually returned,
which is the point.

## Validator test usage

[test_validation.py](../test_validation.py) loads `elexon/pn_stream.json` and
copies its first row for PN contract and mixed-batch tests. Deliberate key/value
changes are made in memory; the captured fixture file is unchanged. The empty-input
test uses an empty list instead. These tests need no live API or database access.
See [endpoint validation](../../docs/sources/endpoint-validation.md) for coverage
and the test command.

## carbon_intensity/

| File | Source | Captured | Notes |
|---|---|---|---|
| `forecast_fw48h.json` | `/intensity/2026-08-17T08:00Z/fw48h` | 2026-08-17 | 97 periods, all actuals null |
| `outturn.json` | `/intensity/2026-08-01T00:00Z/2026-08-01T03:00Z` | 2026-08-19 | 7 periods, settled |

## elexon/

| File | Source | Captured | Notes |
|---|---|---|---|
| `pn_stream.json` | `/datasets/PN/stream`, two windows, **5 units** | 2026-08-22 | **Composite** — see note below. 28 rows: five unit types, segments of 1 to 30 minutes, four multi-segment periods, five rows where `levelFrom != levelTo`, a negative level, three `nationalGridBmUnit` naming shapes. **Periods 32–35 are absent** |
| `qpn_stream.json` | `/datasets/QPN/stream`, two windows, **same 5 units as `pn_stream.json`** | 2026-08-22 | **Composite.** 24 rows, deliberately the same units and windows as the PN fixture so the two are directly comparable. **Every segment is 30 minutes** — QPN does not mirror PN's sub-period segmentation. Includes `T_WILCT-1` at −60 MW, the only unit with a non-zero QPN in the completed one-year backfill |
| `pn_stream_ramp.json` | `/datasets/PN/stream`, 1h, `T_DRAXX-1` | 2026-08-22 | **Multi-segment periods.** Drax starting up 2026-08-21: SP29 and SP30 each split into two segments, one of them a single minute. The test case for the planned PN ramp integration — naive period-endpoint integration is 47.6% wrong on SP29 |
| `b1610_stream.json` | `/datasets/B1610/stream`, 4h straddling a settlement-day boundary, **3 units** | 2026-08-23 | **Composite**, built by `tests/adhoc/b1610_capture_fixture.py`. 27 rows, two settlement dates, **periods 1 and 2 both present** — period 1 is the only period where `settlementDate` disagrees with the UTC date of `halfHourEndTime`. Naive `halfHourEndTime`, run type `II` only |
| `remit_stream.json` | `/datasets/REMIT/stream`, 2h publish window | 2026-08-20 | One mrid at revisions 4, 5, 6. `Unplanned` and `Dismissed` observed. No `outageProfile` field |
| `mels_stream.json` | `/datasets/MELS/stream`, 30min, `T_DRAXX-1` | 2026-08-20 | |
| `mils_stream.json` | `/datasets/MILS/stream`, 30min, `T_DRAXX-1` | 2026-08-20 | |
| `indo.json` | `/datasets/INDO`, 1h publish window | 2026-08-20 | Base endpoint: `{data: [...]}` envelope |
| `itsdo.json` | `/datasets/ITSDO`, 1h publish window | 2026-08-20 | Envelope |
| `ndf_truncated.json` | `/datasets/NDF`, 1h publish window | 2026-08-20 | **Truncated extract**: 6 rows of ~119, keeping two forecast vintages (10:16 and 10:49) and a local-date rollover row |
| `system_prices.json` | `/balancing/settlement/system-prices/2026-08-01/5` | 2026-08-20 | `{metadata, data}` envelope. 18-decimal numerics |
| `bmunits_truncated.json` | `/reference/bmunits/all` | 2026-08-20 | **Truncated extract**: first 10 of an unknown total. Includes null `fuelType`, null `eic`, and one unit with null `elexonBmUnit` |

Truncated extracts preserve verbatim rows but not the full payload; do not use
them for row-count assertions against the API.

## The one composite fixture

`pn_stream.json` is **assembled from six single-unit requests over two windows**
on 2026-08-21 — five units across `16:30Z–18:30Z`, plus `T_DRAXX-1` again across
`12:30Z–14:30Z` for the start-up ramp. Every row is verbatim; the payload is not.

> **Settlement periods 32 to 35 are missing**, because the two source windows are
> two hours apart. Segments are contiguous *within* each period, which is the
> invariant worth testing. **A contiguity test across periods will fail** — that
> is the fixture, not the parser.

This is deliberate. A genuine market-wide response for that window is roughly
11,000 rows across ~2,700 units, which is far too large to commit. The selected
rows vary across the fields and segment shapes that the parser must preserve.

The five units were chosen for variety:

| Unit | Type | What it contributes |
|---|---|---|
| `T_DRAXX-1` | transmission, biomass | flat baseload, 660 MW, 30-minute segments, plus the start-up ramp |
| `T_ABRBO-1` | transmission, offshore wind | **15-minute segments**, two per period, varying levels 47 → 41 |
| `T_WILCT-1` | transmission | **the only negative level**, −14 MW in PN and −60 MW in QPN |
| `V__BADEL001` | virtual lead party | zeros, and a `nationalGridBmUnit` of `AG-ADL00B` that shares no stem with the Elexon id |
| `E_ABERDARE` | embedded | zeros, `ABERU-1` — a third naming shape |

**The `T_DRAXX-1` ramp rows are what make `levelFrom != levelTo` testable.** With
every row flat, a parse written as `result["levelFrom"], result["levelFrom"]` —
ignoring `levelTo` entirely — passes every assertion. Five rows differ, so it
cannot.

**Negative levels are covered** by `T_WILCT-1`, at −14 MW in the PN fixture and
−60 MW in the QPN one. Non-zero units are rare enough that picking candidates by
name does not work; this one came from scanning a full market-wide day.

That unit is also the entire `PN - QPN` question in one row: **−14 minus −60 is
+46**, so applying the deduction flips it from importing to exporting. See
`docs/sources/elexon/015_qpn.md`.

## The second composite fixture

`b1610_stream.json` is **assembled from one real four-hour request** straddling a
settlement-day boundary, keeping every row of three units chosen to cover the
traits the tests need:

| Unit | `nationalGridBmUnitId` | Quantity range | What it contributes |
|---|---|---|---|
| `T_CARR-1` | `CARR-1` | 109.500 to 199.300 | transmission, positive, real magnitude |
| `E__MDRX001` | `DRAXD-1` | −17.183 to −14.198 | embedded, **negative throughout** |
| `2__ACOUL004` | **null** | −3.886 to 4.222 | supplier, **null NG id**, crosses zero |

Three identifier shapes, both null and non-null National Grid ids, positive,
negative and zero quantities, and every value at three decimal places.

**Periods 1 and 2 are the point.** Period 1 ends at 23:30Z the previous day, so
its `settlementDate` is a day ahead of its `halfHourEndTime` date. Period 2 ends
at exactly 00:00Z on the settlement date, so they agree. A test asserting both is
the only way to prove local-versus-UTC handling rather than assume it — one alone
could pass by accident.

The selection is done by `tests/adhoc/b1610_capture_fixture.py`, which reports
trait coverage. **Do not hand-edit this file**; recapture it, or the fixture stops
recording what the API returned and starts recording what someone assumed.

Only run type `II` appears, and it cannot be otherwise: the API serves one run
per settlement day and discards superseded ones. So a parse that ignored
`settlementRunType` entirely would pass — the one gap the suite cannot close, and
noted in the test.

`pn_stream_ramp.json` is retained as the **verbatim single-request capture** of
those ramp rows. Its contents are now a subset of `pn_stream.json`; it is kept
because provenance matters and every other fixture here is a real single
response. Tests should use `pn_stream.json`.
