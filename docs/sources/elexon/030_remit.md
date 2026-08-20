# REMIT, outage notices

**Not yet ingested.** Planned for Phase 1, step 3 of the thesis.

## In plain terms

**The excuse note.** When a power station breaks down or goes offline for
maintenance, it is legally required to say so publicly, along with how much
capacity is lost and for how long.

Crucially it also says whether the outage was **planned** or **unplanned**.

That single field is the falsifiable core of this project. The claim is that
*unplanned* shortfalls are what force gas plants to fill in at short notice,
pushing carbon intensity above forecast. Planned outages are known about days
ahead and should already be in the forecast.

REMIT stands for the Regulation on Wholesale Energy Markets Integrity and
Transparency, an EU regulation aimed at preventing market abuse. This endpoint
returns information participants publish to comply with **Article 4 of
Regulation (EU) 1227/2011**. The name is not worth remembering; the planned
versus unplanned split is.

**Caveat before building on it:** the API does not enumerate the values of
`unavailabilityType`. Both `Planned` (spec example) and `Unplanned` (live,
2026-08-20) have been observed, so the two-way split is on reasonable ground,
but whether other values exist is unconfirmed.

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
| `dataset` | `str` | Always `REMIT` |
| `mrid` | `str` | Message identifier, stable across revisions |
| `revisionNumber` | `int` | **Messages are revised.** Take the latest per `mrid` |
| `publishTime` | `str` | When this revision was published |
| `createdTime` | `str` | When the message was created |
| `messageType` | `str` | e.g. `UnavailabilitiesOfElectricityFacilities` |
| `messageHeading` | `str` | e.g. `Planned Unavailability of Generation Unit` |
| `eventType` | `str` | e.g. `Production unavailability` |
| **`unavailabilityType`** | `str` | The field that matters. `Planned` seen in the spec's example. **Values are not enumerated in the API**, so confirm the full set against live data before relying on a two-way split |
| `participantId` | `str` | Market participant |
| `registrationCode` | `str` | |
| `assetId` | `str` | e.g. `T_DIDCB5`, matches the BM unit identifier |
| `assetType` | `str` | e.g. `Production` |
| `affectedUnit` | `str` | e.g. `DIDCB5` |
| `affectedUnitEIC` | `str` | European identifier |
| `affectedArea` | `str` | |
| `biddingZone` | `str` | e.g. `10YGB----------A` |
| `fuelType` | `str` | e.g. `Fossil Gas` |
| `normalCapacity` | `float` | MW when fully available. Spec type is `number`, not integer, so parse as float even though observed values are whole |
| `availableCapacity` | `float` | MW still available during the outage |
| `unavailableCapacity` | `float` | MW lost |
| `eventStatus` | `str` | `Active` and `Dismissed` observed live; `Inactive` in the spec. Not enumerated |
| `eventStartTime` | `str` | Outage start |
| `eventEndTime` | `str` | Outage end |
| `durationUncertainty` | `str` | Free text, e.g. `+- 1 day`. **Optional, often absent** |
| `cause` | `str` | Free text, e.g. `Other`, `Unknown` |
| `relatedInformation` | `str` | Free text. **Optional** |
| `outageProfile` | `list` | **Nested array, optional and often absent.** See below |

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
- **Revised.** `revisionNumber` against `mrid`, with no reliable updated-at, so
  a `check`-strategy snapshot rather than a timestamp one.
- **Nested.** The outage profile array.

That combination is what makes it the S7 build rather than an early one.

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

- Full value sets for `unavailabilityType` and `eventStatus`.
- Do `assetId` values always match BM unit identifiers cleanly, or is the join
  to `PN` and `B1610` dirty? (`E_LYNE2` against `affectedUnit: LNMTH-2`
  suggests not always.)
- Which fields are guaranteed present versus optional. Observed so far:
  `outageProfile`, `durationUncertainty` and `relatedInformation` are optional.
  The spec is blunter: it marks **every field nullable except
  `revisionNumber`, `publishTime`, `createdTime` and `eventStartTime`** —
  including `eventEndTime` and all three capacities. Parse defensively
  throughout, not just on the three observed absentees.
