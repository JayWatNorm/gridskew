# Demand forecast and outturn

**NDF and INDO: a one-off back-fill of a year of history**, publish days
20 August 2025 to 17 August 2026; see
[051_demand_ingestion.md](051_demand_ingestion.md). `TSDF`, `ITSDO` and
the longer-horizon variants are not ingested.

Several related datasets, documented together because they share a shape.

## In plain terms

**How much electricity the country was expected to use, and how much it
actually used.**

This matters because it is a **competing explanation**. If carbon intensity
comes in above forecast, one reason is that generators fell short and gas
filled the gap. Another, entirely different reason is that the country simply
used more electricity than expected, so more had to be generated.

Without demand data the two cannot be separated. This is the control dataset
for that competing explanation.

---

## The datasets

Definitions follow the API's endpoint descriptions. The two families include
different flows, which is what makes them easy to misuse.

| Dataset | Meaning |
|---|---|
| `NDF` | National Demand forecast. **Includes** transmission losses. **Excludes** interconnector flows, and demand from station transformers and pumped storage units |
| `TSDF` | Transmission System Demand forecast. **Includes** interconnector flows, and demand from station transformers and pumped storage units |
| `INDO` | Initial National Demand outturn. Outturn on the `NDF` basis |
| `ITSDO` | Initial Transmission System Demand outturn. Outturn on the `TSDF` basis |

Both forecasts are an **average MW value for each settlement period**, based on
historically metered generation output for Great Britain. The API description
says they come daily from NGESO (now NESO) for the day ahead; observed
publications are more frequent and cover a longer horizon (see below).

`NDF` and `TSDF` pair with `INDO` and `ITSDO` respectively. **Do not mix a
forecast from one family with an outturn from the other**: the inclusions
differ, so the gap between them is definitional, not error.

### Longer-horizon variants

| Dataset | Horizon |
|---|---|
| `NDFD` / `TSDFD` | Day-ahead to a few days |
| `NDFW` / `TSDFW` | Week-ahead and beyond |

Relevant where forecast horizon matters for demand, as it does for carbon.

## Endpoints and their limits

**The forecast endpoints have documented maximum ranges, unlike PN and B1610.**

**All of these filter on publish time.** The `/stream` variants of NDF and TSDF
take optional `publishDateTimeFrom` / `publishDateTimeTo` rather than the
`from`/`to` used by PN and B1610. Omit them to get the latest published
forecast.

Every documented cap is on the base endpoint. The spec documents none for the
`/stream` endpoints, and the developer portal describes streams as having no
restrictions on data return.

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

The 1 day cap presumably applies to the **publish window**, since publish times
are the only time parameters. On that reading, **a year of NDF history through
the base endpoint is about 365 requests**. The stream has no documented cap.

### Observed live, 2026-08-20

A one-hour publish window on `/datasets/NDF` returned **two complete forecast
vintages**, published at 10:16 and 10:49, each covering roughly the next two
days (~85 settlement periods). See `tests/fixtures/elexon/ndf_truncated.json`.

In that window NDF published twice, 33 minutes apart. Each publication
re-forecasts the whole horizon, the same horizon-resolved structure as the
carbon intensity forecast.

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

## Forecast history

Every one of these carries a `publishTime`, and without publish-time filters
you get the latest published forecast, the same serving pattern as the Carbon
Intensity API.

**Earlier versions are retrievable.** On 2026-08-20 a single publish-time
window returned two distinct forecast vintages side by side, and on
2026-10-25 a request for publish day 2025-08-22 returned all 48 of that
day's NDF issues. A forecast-error study over that range needs no
self-built archive, unlike the carbon intensity forecast.

## Storage shape

These suit one table per dataset rather than a combined table with a `dataset`
column. They have different definitions and different revision behaviour, and
separate tables make a wrong join visible instead of silent.
