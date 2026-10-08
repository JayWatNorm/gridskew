# REMIT, outage notices

**Ingestion built; not yet released.** See [031_remit_ingestion.md](031_remit_ingestion.md).

## In plain terms

**The outage notice.** When an outage or availability change qualifies as
inside information under REMIT, the market participant must publish it in a
timely and effective manner. The resulting notices record expected capacity
impact and duration. See [Ofgem's Article 4 guidance](https://www.ofgem.gov.uk/policy/publishing-inside-information-under-remit-article-4).

Crucially it also says whether the outage was **planned** or **unplanned**.

That single field is the falsifiable core of this project. The claim is that
*unplanned* shortfalls are what force gas plants to fill in at short notice,
pushing carbon intensity above forecast. Planned outages are known about days
ahead and should already be in the forecast.

REMIT stands for the Regulation on Wholesale Energy Markets Integrity and
Transparency, an EU regulation aimed at preventing market abuse. This endpoint
returns information participants publish to comply with **Article 4 of
Regulation (EU) 1227/2011**. The planned-versus-unplanned split is the field
used by this project.

**Caveat before building on it:** the API does not enumerate the values of
`unavailabilityType`. In the 5,497 messages published in September 2026 it is
`Planned` on 2,682, `Unplanned` on 2,699 and absent on 116. Every message
without it has `messageType` `OtherMarketInformation`.

---

## Endpoint

```
GET https://data.elexon.co.uk/bmrs/api/v1/datasets/REMIT/stream
    ?publishDateTimeFrom=...&publishDateTimeTo=...
```

**Both publish-time parameters are required on the stream variant.** Unlike PN
and B1610, REMIT's stream does not take `from`/`to`; it filters on when the
message was published, not when the outage occurs. The base `/datasets/REMIT`
takes the same parameters.

That distinction matters for ingestion: polling by publish window gives you
every new message and revision since the last poll, which is exactly the shape
an append-only feed wants.

## Response fields

| Field | Type | Notes |
|---|---|---|
| `dataset` | `str or null` | Always `REMIT` when present |
| `mrid` | `str or null` | Message identifier, stable across revisions |
| `revisionNumber` | `int` | **Messages are revised.** Not unique within an `mrid`: see [The key](#the-key) |
| `publishTime` | `str` | When this revision was published |
| `createdTime` | `str` | Creation time of this row. Later than `publishTime` on some rows |
| `messageType` | `str or null` | e.g. `UnavailabilitiesOfElectricityFacilities` |
| `messageHeading` | `str or null` | e.g. `Planned Unavailability of Generation Unit` |
| `eventType` | `str or null` | e.g. `Production unavailability` |
| **`unavailabilityType`** | `str or null` | The field that matters. `Planned` seen in the spec's example. **Values are not enumerated in the API**, so confirm the full set against live data before relying on a two-way split |
| `participantId` | `str or null` | Market participant |
| `registrationCode` | `str or null` | |
| `assetId` | `str or null` | e.g. `T_DIDCB5`, matches the BM unit identifier |
| `assetType` | `str or null` | e.g. `Production` |
| `affectedUnit` | `str or null` | e.g. `DIDCB5` |
| `affectedUnitEIC` | `str or null` | European identifier |
| `affectedArea` | `str or null` | |
| `biddingZone` | `str or null` | e.g. `10YGB----------A` |
| `fuelType` | `str or null` | e.g. `Fossil Gas` |
| `normalCapacity` | `float or null` | MW when fully available. Fractional (`49.920`) and negative (`-1.000`) values occur, so parse as a decimal |
| `availableCapacity` | `float or null` | MW still available during the outage |
| `unavailableCapacity` | `float or null` | MW lost |
| `eventStatus` | `str or null` | `Active`, `Dismissed` and `Inactive` observed. Not enumerated |
| `eventStartTime` | `str` | Outage start |
| `eventEndTime` | `str or null` | Outage end |
| `durationUncertainty` | `str or null` | Free text, e.g. `+- 1 day`. **Optional, often absent** |
| `cause` | `str or null` | Free text, e.g. `Other`, `Unknown` |
| `relatedInformation` | `str or null` | Free text. **Optional** |
| `outageProfile` | `list or null` | **Nested array, optional and often absent.** See below |

### The nested bit

```json
"outageProfile": [
  { "startTime": "...", "endTime": "...", "capacity": 436 }
]
```

An outage is not always flat. A unit might lose 400 MW for six hours then 200 MW
for another twelve, and `outageProfile` describes that shape.

**This does not flatten to one row.** Either store the profile as `jsonb` in
raw and unnest it downstream, or write two tables. Storing it as `jsonb` keeps
the raw layer faithful to what the source sent, which is the convention here.

## Why this is the awkward one

Three complications at once, which is why it is not the first dataset to build:

- **Event-shaped, not period-shaped.** Every other dataset is one row per
  settlement period. REMIT is one row per *event*, with a start and end that
  span many periods. Joining it to half-hourly data means expanding an interval.
- **Revised.** Store each publication append-only, then select the current
  one downstream when current state is required.
- **Nested.** The outage profile array.

That combination is what makes it the S7 build rather than an early one.

## The key

`(mrid, revisionNumber)` does not identify a row. In the 5,497 messages
published in September 2026 (`tests/adhoc/remit_checks.py`):

| Candidate key | Repeated |
|---|---|
| `mrid`, `revisionNumber` | 76 |
| `mrid`, `revisionNumber`, `publishTime` | 11 |
| `mrid`, `revisionNumber`, `publishTime`, `createdTime` | 0 |

The repeated rows are not copies. They differ in `eventEndTime`, in the
capacities, in `eventStatus` and, once in the days sampled, in the unit. A
key of the first two fields would keep one of them and drop the rest without
a sign. `raw.elexon_remit` therefore keys on all four fields.

`revisionNumber` also does not always rise with `publishTime`: within
September 2026 it falls at least once for 31 of 2,059 `mrid` values. A model
that needs the current state of a notice must choose its rule on that
evidence.

## Fields that are absent, not null

No field was null in 7,928 messages (September 2026 and seven other days back
to 2020). A field without a value is left out of the row. Thirteen fields
were on every row: `dataset`, `mrid`, `revisionNumber`, `publishTime`,
`createdTime`, `messageType`, `messageHeading`, `participantId`,
`registrationCode`, `assetId`, `eventStatus`, `eventStartTime` and `cause`.
`OtherMarketInformation` messages carry no `eventType`,
`unavailabilityType`, `affectedUnit`, `biddingZone` or capacity.
`outageProfile` is on about a quarter of rows and has up to 199 segments.

`eventEndTime` is on nearly every row but not all: an open-ended event has
none. Of the 107,941 messages published from 2024-08-22 to 2026-10-08, 23
have no `eventEndTime`, all of type `OtherMarketInformation`. The contract
treats the field as optional.

A whole day can pass without a message: none was published on 2026-04-25,
2026-04-26 or 2026-06-14.

## Observed live, 2026-08-20

A two-hour publish window (see `tests/fixtures/elexon/remit_stream.json`)
confirmed several things at once:

- **`unavailabilityType: "Unplanned"` observed.** With `Planned` in the spec's
  example, both expected values are now seen. The full value set is still not
  enumerated anywhere.
- **Revision in action.** One `mrid` appeared at revisions 4, 5 and 6 within an
  hour. Between revisions the `eventEndTime` moved earlier and `eventStatus`
  went from `Active` to `Dismissed`. Every revision remains retrievable, so the
  full history of an outage notice can be reconstructed.
- **`eventStatus: "Dismissed"` observed**, alongside `Active` and the spec's
  `Inactive`. Value set not enumerated.
- **Fields can be entirely absent.** The live rows carried no `outageProfile`
  and no `durationUncertainty` keys at all. A parse using `result["outageProfile"]`
  will raise `KeyError` on most messages; use `.get()` for the optional fields
  and know which those are.
- A gas unit (`T_ROCK-1`, 748 MW normal, 388 MW unavailable) reporting an
  unplanned outage is precisely the event class the thesis is about.

## Open questions

- Whether `unavailabilityType` and `eventStatus` take values other than the
  ones observed.
- Which row of a notice is current when revision numbers repeat or fall.
- Do `assetId` values always match BM unit identifiers cleanly, or is the join
  to `PN` and `B1610` dirty? (`E_LYNE2` against `affectedUnit: LNMTH-2`
  suggests not always.)
- Which fields are guaranteed present versus optional. Observed so far:
  `outageProfile`, `durationUncertainty` and `relatedInformation` are optional.
  The spec is blunter: it marks **every field nullable except
  `revisionNumber`, `publishTime`, `createdTime` and `eventStartTime`** —
  including `eventEndTime` and all three capacities. Parse defensively
  throughout, not just on the three observed absentees.
