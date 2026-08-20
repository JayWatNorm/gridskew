# MELS and MILS, export and import limits

**Not yet ingested.** Optional context for Phase 1.

## In plain terms

**The headroom.**

- **MELS**, Maximum Export Limit: the most a unit could produce if asked.
- **MILS**, Maximum Import Limit: the most it could consume.

Where `PN` says what a unit *intends* to do, these say what it *could* do. The
gap between them is spare capacity the operator can call on.

Why that is useful here: a generator running at 200 MW with a 600 MW limit has
plenty of headroom, so a shortfall elsewhere can be covered cheaply. A grid
where everything is already at its limit has nowhere to go but the expensive,
fast-starting plant. **Tight headroom is a scarcity signal**, and it may explain
why some shortfalls move carbon intensity more than others.

It also catches a specific case: if a unit's export limit drops to zero
mid-period, that is a breakdown showing up in the data before REMIT reports it.

---

## Endpoints

```
GET https://data.elexon.co.uk/bmrs/api/v1/datasets/MELS/stream?from=&to=
GET https://data.elexon.co.uk/bmrs/api/v1/datasets/MILS/stream?from=&to=
```

Same parameters as PN and B1610.

## Response fields

Identical shape for both:

| Field | Type | Notes |
|---|---|---|
| `dataset` | `str` | `MELS` or `MILS` |
| `settlementDate` | `str` | Local time date |
| `settlementPeriod` | `int` | 1 to 50 |
| `timeFrom` | `str` | |
| `timeTo` | `str` | |
| `levelFrom` | `int` | MW at `timeFrom` |
| `levelTo` | `int` | MW at `timeTo` |
| `notificationTime` | `str` | When the limit was submitted |
| `notificationSequence` | `int` | **Revision marker.** Limits get resubmitted |
| `nationalGridBmUnit` | `str` | |
| `bmUnit` | `str` | |

## Things to know

**Updated every 30 minutes**, and within 15 minutes of the end of the effective
settlement period, per the endpoint description.

**Same ramp structure as PN.** The description states that MELs are "submitted
as a series of MW values and associated times in UTC", so `levelFrom` and
`levelTo` describe a line rather than a flat value, and a unit can submit
several point pairs within one settlement period.

**`from` and `to` filter on time** by default. Supplying
`settlementPeriodFrom` or `settlementPeriodTo` switches the corresponding
parameter to filtering on settlement **date**, with the time portion ignored.
Documented for MELS and MILS, unlike B1610.

**`notificationSequence` is the revision axis.** A unit can resubmit its limits
during a period, for instance after a fault reduces what it can deliver. Take
the latest sequence per unit per time window, and note that the *sequence of
revisions is itself informative*: a limit revised sharply downward mid-period
is a breakdown in progress.

**Two datasets, one shape.** Worth a single parse function parameterised by
dataset name rather than two near-identical modules. This is the one place in
the Elexon set where sharing is clearly right.

## Priority

Lower than PN, B1610 and REMIT. It refines the explanation rather than
establishing it, so it belongs after step 1 of the thesis produces a result.
