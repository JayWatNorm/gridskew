# Elexon Insights API

Elexon administers the Balancing and Settlement Code, the rulebook for how GB
electricity trades are settled. The Insights platform publishes the underlying
market data.

| | |
|---|---|
| Base URL | `https://data.elexon.co.uk/bmrs/api/v1` |
| Auth | None. All Insights APIs are public |
| Developer portal | https://developer.data.elexon.co.uk/ |
| Docs | https://bmrs.elexon.co.uk/api-documentation/introduction |
| Data browser | https://bmrs.elexon.co.uk/ |

The documentation site is JavaScript-rendered and cannot be fetched
programmatically. The **OpenAPI spec** is the useful artefact: it carries every
endpoint's parameters, response schema and worked examples, and is available in
JSON, YAML and WADL from the developer portal.

## How the GB electricity market works, briefly

You do not need to be an industry expert to use this data, but four ideas make
every dataset here make sense.

**1. Electricity has to balance, second by second.** What is generated must
equal what is used, continuously. Britain has a growing fleet of grid-scale
batteries and pumped storage, but they shift energy by hours rather than days,
so supply still has to be adjusted constantly to follow demand.

**2. Everything happens in half hours.** The market is settled in
**settlement periods**, normally 48 per day. Every dataset here is stamped with
a settlement date and period. Clock-change days have 46 or 50, which is why the
API accepts a period as high as 50 and why this is a recurring source of bugs.

**3. Generators declare, then deliver, and the operator fixes the difference.**

- Ahead of time, each generator says what it will produce (`PN`).
- If the operator needs more or less, it pays generators to change plan
  (`BOALF`). Gas plants are the usual source of short-notice extra output,
  because they can start quickly.
- Afterwards, meters record what actually happened (`B1610`).
- The cost of correcting the imbalance becomes a price (system prices).

**4. The numbers get revised for months afterwards.** Settlement is re-run
several times as better meter data arrives. A figure published today can change
later.

The run sequence is taken to be II, SF, R1, R2, R3, RF. **Only `II` and `R1`
have been observed directly**; the rest come from the project plan and are not
confirmed by the API, which documents `settlementRunType` as a free-text string
with no enumerated values.

That revision behaviour is why this project's raw layer never overwrites
anything.

A **BM Unit** is the thing all of this is measured against. Roughly a single
generating unit or a group of them, identified like `T_DRAXX-1`. The registry
at `/reference/bmunits/all` says which is which.

Unit counts quoted in these pages are approximate. To get the current figure:

```powershell
(Invoke-RestMethod "https://data.elexon.co.uk/bmrs/api/v1/reference/bmunits/all").Count
```

## Datasets planned

Phase 1 needs the first two. The rest support the decomposition in step 3 of
the thesis and can follow.

Numbered in build order, not alphabetically.

| Page | Dataset | In a phrase | Priority |
|---|---|---|---|
| [010_pn.md](010_pn.md) | `PN` | The promise | Phase 1, first |
| [015_qpn.md](015_qpn.md) | `QPN` | An internal process netted off the promise | Phase 1, first |
| [020_b1610.md](020_b1610.md) | `B1610` | The receipt | Phase 1, first |
| [030_remit.md](030_remit.md) | `REMIT` | The excuse note, planned or unplanned | Phase 1, step 3 |
| [040_boalf.md](040_boalf.md) | `BOALF` | The intervention | Phase 1, step 3 |
| [050_demand.md](050_demand.md) | `NDF` `TSDF` `INDO` `ITSDO` | What the country was expected to use, and did | Phase 1, step 3 |
| [060_system-prices.md](060_system-prices.md) | system prices | What it cost to fix the imbalance | Phase 1, step 3 |
| [070_bmunits.md](070_bmunits.md) | `/reference/bmunits/all` | The address book | Phase 1, S4 |
| [080_mels-mils.md](080_mels-mils.md) | `MELS` `MILS` | The headroom | Optional |

**Steps 1 and 3** refer to the thesis in the project README: find the
shortfalls, then explain them. Only `PN`, `QPN` and `B1610` are needed to find
them.

## Use the `/stream` variants

Every dataset has two endpoints taking different parameters:

| | Takes | Covers |
|---|---|---|
| `/datasets/PN` | `settlementDate` and `settlementPeriod`, both required | one half hour |
| `/datasets/PN/stream` | `from` and `to` datetimes | a range |

A year through the base endpoint would be 17,520 requests. Build against
`/stream`.

## Shared behaviour

**Three envelope shapes, all observed live.** Stream endpoints return a bare
array. Dataset base endpoints wrap rows in `{"data": [...]}`. The settlement
endpoints wrap them in `{"metadata": {...}, "data": [...]}`. A parse copied
between modules without checking which shape it faces will fail, or worse,
silently iterate the wrong thing.

**Not every stream takes `from`/`to`.** PN, B1610, BOALF, MELS and MILS do.
REMIT's stream requires `publishDateTimeFrom`/`To`, and the demand forecasts'
streams take the same publish-time pair optionally. Check per dataset.

**Repeating the `bmUnit` parameter is unverified.** The spec types it as an
array, but the tooling used for these checks collapsed repeated query keys, so
multi-unit filtering has not been observed working. Test before relying on it;
ingestion does not filter by unit anyway.

**Window caps vary by endpoint, are documented per endpoint, and sit on the
BASE endpoints only.** There is no single platform-wide rule, and — verified
against the full spec 2026-08-20 — **no `/stream` endpoint documents a cap at
all**. The portal describes streams as having no restrictions on data return.
Base-endpoint examples from the spec:

| Base endpoint | Documented maximum range |
|---|---|
| `NDF`, `TSDF`, `INDGEN`, `INDDEM`, `MELNGC`, `IMBALNGC` | **1 day** |
| `AGWS`, `ATL`, `DATL`, `DGWS`, `OCNMFD` | 7 days |
| `AGPT` | 4 days |
| `NDFD`, `TSDFD` | 92 days |
| `NDFW`, `TSDFW` | 366 days |
| `IGCA`, `IGCPU` | 731 days |
| `PN`, `B1610`, `REMIT`, `BOALF`, `MELS`, `MILS` | **not documented** |

**Always check the endpoint description before assuming a range is safe**, and
treat an undocumented stream cap as untested rather than absent.

For the undocumented ones, a 92 day B1610 request returned the full range,
4,417 rows for one BM unit, with no truncation. Note that 92 days is also the
documented cap for `NDFD` and `TSDFD`, so **92 may be a platform default rather
than "no limit"**. Worth retesting at 100 days or more before relying on it.

**The boundary period is included.** 92 days is 4,416 half hours; the extra row
is the period containing the requested `from`. Chunks overlap by one, same as
the Carbon Intensity API.

**Chunk by day for all-unit pulls.** One day of B1610 across several hundred BM
units is on the order of 19,000 rows; a month would be over half a million.
Daily chunks also match the settlement day grain and sidestep the cap question
entirely.

**Do not filter by `bmUnit` in ingestion.** Pull every unit. There is no unit
list to maintain, no risk of missing one that appears mid-year, and raw records
what the source published. Scoping to physical generators belongs downstream,
joined against the BM unit registry.

**`settlementPeriodFrom` and `settlementPeriodTo` change what `from` and `to`
mean.** Straight from the BOALF and MELS descriptions:

> By default, the `from` and `to` parameters filter the data by time
> inclusively. If the `settlementPeriodFrom` or `settlementPeriodTo` parameters
> are provided, the corresponding `from` or `to` parameter **instead filters on
> settlement date**... when filtering via settlement date, `from`/`to` are
> treated as **dates only, with the time being ignored**.

So `2026-06-01T00:00Z` and `2026-06-01T11:11Z` are both read as the date
`2026-06-01` once a period parameter is present. That is a mode switch, not an
extra filter.

**It did not take effect on B1610 in testing.** Periods 1 to 1 across a 61 day
window returned all 48 periods per day. B1610's endpoint description does not
carry the paragraph above, unlike BOALF and MELS, so **the behaviour appears to
differ by dataset**. Test per dataset before relying on it.

**Row order is inconsistent between datasets.** PN returned descending by
settlement period, B1610 ascending. Sort downstream if it matters.

## Settlement dates and periods are LOCAL time

This is the trap most likely to produce silently wrong data.

```
settlementDate:   2026-08-01
settlementPeriod: 2
timeFrom:         2026-07-31T23:30:00Z
```

The settlement day starts at **midnight British local time**, which in BST is
23:00 UTC the previous day. A period stamped 1 August occurred on 31 July in
UTC.

**Never derive a settlement date from the date part of a UTC timestamp.** It is
wrong for an hour of every summer day, and it is why clock-change days have 46
and 50 settlement periods rather than 48.

## Timestamp formats differ between datasets

| Dataset | Field | Value | Note |
|---|---|---|---|
| PN | `timeFrom` | `2026-08-01T01:00:00Z` | seconds, `Z` |
| B1610 | `halfHourEndTime` | `2026-08-01T00:30:00` | seconds, **no `Z`, naive** |

Neither matches Carbon Intensity's `2026-08-01T01:00Z`. Each parse needs its
own format string, and B1610's needs a timezone attaching after parsing rather
than a suffix stripping.

## Everything has a revision axis

`settlementRunType` on B1610, `revisionNumber` on REMIT, `amendmentFlag` on
BOALF, `notificationSequence` on MELS and MILS.

The append-only raw layer with take-latest downstream is not a Carbon Intensity
quirk. It is the shape of this entire domain.

## Rate limits

Not documented anywhere in the spec or the developer portal.

### Tested 2026-08-20

50 sequential requests to `/datasets/PN/stream`, unthrottled, small window, one
BM unit.

| | Result |
|---|---|
| Status codes | **50 of 50 returned 200.** No 429, no errors |
| Rows returned | Constant at 3 throughout. No silent degradation |
| Latency | Median about 28 ms, no upward trend. Three spikes (86, 92, 288 ms) that returned immediately to baseline, so network noise rather than throttling |
| Rate achieved | Roughly **30 requests per second**, sustained |

**No rate-limit headers are returned.** No `X-RateLimit-Remaining`, no
`Retry-After`, nothing. A client cannot see how close it is to a limit, so
backing off gracefully is impossible; you would simply be blocked.

Response headers show `Cache-Control: no-store, must-revalidate, no-cache`, so
nothing is cacheable, and `Request-Context: appId=cid-v1:...`, indicating Azure
hosting with Application Insights.

### Policy that follows

**Nothing observed pushed back at 30 req/s across 50 requests.** But the test is
small: a year of PN at daily chunks is 365 requests, seven times larger, and the
terms state a limit exists and that applications making many calls may be
blocked.

So:

- **Daily scheduled jobs**: one or two requests. No delay needed.
- **Backfills**: use `time.sleep(0.2)` between requests. It turns a 365 request
  backfill from 12 seconds into 90, which costs nothing and removes a risk you
  have no way to monitor.
- **Assert on row counts, not just status codes.** `raise_for_status()` catches
  4xx and 5xx. With no rate-limit headers, a future throttle could arrive in a
  shape not seen here, and a 200 with an empty body would otherwise look like a
  successful run that wrote nothing.
- **Identify yourself.** The terms prohibit concealing an application's
  identity, and a descriptive `User-Agent` means an operator can contact you
  rather than simply blocking the address.
