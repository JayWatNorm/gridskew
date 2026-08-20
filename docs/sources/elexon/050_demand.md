# Demand forecast and outturn

**Not yet ingested.** Planned for Phase 1, step 3 of the thesis.

Several related datasets, documented together because they share a shape.

## In plain terms

**How much electricity the country was expected to use, and how much it
actually used.**

This matters because it is a **competing explanation**. If carbon intensity
comes in above forecast, one reason is that generators fell short and gas
filled the gap. Another, entirely different reason is that the country simply
used more electricity than expected, so more had to be generated.

Without demand data you cannot separate those two stories, and the thesis would
be claiming a mechanism it had not isolated. This is the honest-limitations
dataset.

---

## The datasets

Definitions below are quoted from the API's own endpoint descriptions rather
than paraphrased, because the difference between the two families is precisely
what makes them easy to misuse.

| Dataset | Meaning |
|---|---|
| `NDF` | National Demand forecast. **Includes** transmission losses. **Excludes** interconnector flows, and demand from station transformers and pumped storage units |
| `TSDF` | Transmission System Demand forecast. **Includes** interconnector flows, and demand from station transformers and pumped storage units |
| `INDO` | Initial National Demand outturn. What `NDF` forecast |
| `ITSDO` | Initial Transmission System Demand outturn. What `TSDF` forecast |

Both forecasts are expressed as an **average MW value for each settlement
period**, based on historically metered generation output for Great Britain,
and are received daily from NGESO showing values for the day ahead.

`NDF` and `TSDF` pair with `INDO` and `ITSDO` respectively. **Do not mix a
forecast from one family with an outturn from the other**: the inclusions
differ, so the gap between them is definitional, not error.

### Longer-horizon variants

| Dataset | Horizon |
|---|---|
| `NDFD` / `TSDFD` | Day-ahead to a few days |
| `NDFW` / `TSDFW` | Week-ahead and beyond |

Useful later if forecast horizon matters for demand the way it does for carbon.

## Endpoints and their limits

**These have documented maximum ranges, unlike PN and B1610.**

**All of these filter on publish time, not on `from`/`to` datetimes.** The
stream variants of NDF and TSDF take `publishDateTimeFrom` / `publishDateTimeTo`
(optional), unlike PN and B1610 whose streams take `from`/`to`. Omit the
parameters and you get the latest published forecast.

**Corrected 2026-08-20: every documented cap sits on the base endpoint only.**
A sweep of the spec found no `/stream` endpoint carrying a cap sentence — for
these datasets or any other. The developer portal describes stream endpoints as
having no restrictions on data return. Whether that holds in practice for large
publish windows is untested.

| Endpoint | Documented max range | Notes |
|---|---|---|
| `/datasets/NDF` | **1 day** | Cap on the base endpoint. `/stream`: none documented |
| `/datasets/TSDF` | **1 day** | As above. Optional `boundary` parameter on the stream |
| `/datasets/NDFD` | 92 days | `/stream`: none documented |
| `/datasets/TSDFD` | 92 days | As above |
| `/datasets/NDFW` | 366 days | As above |
| `/datasets/TSDFW` | 366 days | As above |
| `/datasets/INDO` | not documented | `publishDateTimeFrom` / `To`. No stream variant |
| `/datasets/ITSDO` | not documented | As above |

Since the parameters are publish times, the base endpoint's 1 day cap
presumably applies to the **publish window**, though the description does not
say so explicitly. Through the base endpoint, **a year of NDF history is
roughly 365 requests**. Through the stream, with no documented cap, it may be
far fewer — worth one test with a multi-day publish window before building the
S8 ingestion, since the difference is 365 requests against a handful.

`INDO` and `ITSDO` did not appear with stream variants.

### Observed live, 2026-08-20

A one-hour publish window on `/datasets/NDF` returned **two complete forecast
vintages**, published at 10:16 and 10:49, each covering roughly the next two
days (~85 settlement periods). See `tests/fixtures/elexon/ndf_truncated.json`.

So NDF publishes roughly every 30 minutes, each publication re-forecasts the
whole horizon, and it has the same horizon-resolved structure as the carbon
intensity forecast.

`INDO` and `ITSDO` returned one row per settlement period, published within
about 30 minutes of the period ending, with `INDO` below `ITSDO` as the
definitions imply. Both base endpoints wrap rows in the `{"data": [...]}`
envelope.

## Response fields

Same shape across all of them:

| Field | Type | Notes |
|---|---|---|
| `dataset` | `str` | `NDF`, `TSDF`, `INDO` or `ITSDO` |
| `demand` | `int` | MW, averaged over the settlement period |
| `publishTime` | `str` | When this figure was published |
| `startTime` | `str` | Period start |
| `settlementDate` | `str` | Local time date |
| `settlementPeriod` | `int` | 1 to 50 |
| `boundary` | `str` | On `NDF` and `TSDF`; `NDF` returns `"N"` (national) on every observed row, see the fixture. Absent from `INDO` and `ITSDO` |

`INDO` and `ITSDO` are described as **updated at 15 minute intervals**.

## The archive question: answered

Every one of these carries a `publishTime`, and without publish-time filters
you get the latest published forecast, the same serving pattern as the Carbon
Intensity API.

The question that mattered was whether earlier versions stay retrievable or
are overwritten. **Answered live on 2026-08-20: retrievable.** A single
publish-time window returned two distinct forecast vintages side by side, so
Elexon keeps forecast history and serves it on request.

Demand forecast error is therefore reconstructable from the API at any time,
and **no self-built archive is needed** for it, unlike the carbon intensity
forecast.

One narrower question remains: **how far back publish history is retained.**
Two vintages an hour apart prove recent history exists; they do not prove a
year of it does. Worth one query with a publish window from a year ago before
Phase 1 leans on deep history.

## Proposed tables

One table per dataset rather than a combined table with a `dataset` column.
They have different definitions and different revision behaviour, and separate
tables make a wrong join visible instead of silent.
