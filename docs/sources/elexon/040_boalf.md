# BOALF, balancing acceptances

How it is loaded: [041_boalf_ingestion.md](041_boalf_ingestion.md).

## In plain terms

**The intervention.** When the grid is not going to balance on its own, the
operator steps in and pays a generator to produce more or less than it planned.

Generators submit prices in advance saying what they would charge to move up
(an offer) or accept to move down (a bid). When the operator takes one of those
up, it is an **acceptance**, and that is what this dataset records.

This is where short-notice gas generation actually shows up. `PN` says what a
station intended; BOALF says the operator asked it to change; `B1610` says what
came out. Together they distinguish "a generator failed to deliver" from "the
operator told it to do something different".

Without BOALF you cannot tell those apart, which is why it belongs in the
decomposition even though it is not needed for step 1.

---

## Endpoint

```
GET https://data.elexon.co.uk/bmrs/api/v1/datasets/BOALF/stream
```

| Parameter | Required |
|---|---|
| `from`, `to` | yes |
| `settlementPeriodFrom`, `settlementPeriodTo` | no. Documented as switching `from`/`to` to settlement-date filtering; see below. Untested on this dataset — the no-effect observation was on B1610, not BOALF |
| `bmUnit` | no |

## Response fields

| Field | Type | Notes |
|---|---|---|
| `dataset` | `str` | Always `BOALF` |
| `settlementDate` | `str` | Local time date |
| `settlementPeriodFrom` | `int` | An acceptance can **span periods** |
| `settlementPeriodTo` | `int` | |
| `timeFrom` | `str` | |
| `timeTo` | `str` | |
| `levelFrom` | `int` | MW at the start of the accepted change |
| `levelTo` | `int` | MW at the end |
| `acceptanceNumber` | `int` | Identifier for the acceptance |
| `acceptanceTime` | `str` | When the operator issued it |
| `deemedBoFlag` | `bool` | Deemed bid-offer |
| `soFlag` | `bool` | System operator flag |
| `amendmentFlag` | `str` | `ORI` in the spec's example, suggesting original versus amended |
| `storFlag` | `bool` | Short Term Operating Reserve |
| `rrFlag` | `bool` | Replacement Reserve |
| `nationalGridBmUnit` | `str` | |
| `bmUnit` | `str` | |

**None of the flag fields carry descriptions in the API spec** — verified: no
field on this schema has one. The readings above are industry convention and
inference from the names, not documented behaviour.

## One day, counted

Market-wide request for the UTC day 2026-10-05, by
`tests/adhoc/boalf_checks.py`:

| | |
|---|---|
| Rows (ramp points) | **26,833** |
| Units | 364 |
| Acceptances, as `(nationalGridBmUnit, acceptanceNumber)` | 11,081 |
| Ramp points per acceptance | 1 to 5; two or three for 92% |
| Acceptance numbers held by more than one unit | **489 of 10,568** |
| Repeated `(nationalGridBmUnit, acceptanceNumber, timeFrom)` | 0 |
| Rows where the two settlement periods differ | 5,595 (20.9%) |
| Nulls in any field | 0 |
| `soFlag` | true on 6,346 rows, false on 20,487 |
| `amendmentFlag` | `ORI` on every row |
| `deemedBoFlag`, `rrFlag` | false on every row |
| `storFlag` | false on every row of this day; see below |

Six more days across the year (2025-08-22, 2025-10-26, 2025-12-25,
2026-01-15, 2026-03-29, 2026-06-21; 107,454 rows) hold no null, no repeated
ramp point, no ramp point without a duration and no `amendmentFlag` other
than `ORI`.

**`storFlag` is the same on every row of a day.** It is true on all 36,200
rows of 2026-03-29 and all 31,613 rows of 2026-03-28, and false on every row
of the other seven days. It does not single out individual acceptances.

## Things to know before modelling it

**A level is an absolute MW level**, the output the unit was instructed to
follow. It is not a change from the notified level.

**Like PN, levels are a ramp**, not a flat value.

**Acceptances span settlement periods.** `settlementPeriodFrom` and
`settlementPeriodTo` differ on a fifth of rows, so one row is not one half
hour. Attributing an acceptance to periods means splitting it.
`int_elexon__boa_by_period` does this.

**`acceptanceNumber` identifies an acceptance only together with the unit.**
Two units can hold the same number.

**Acceptances of one unit overlap in time.** A later acceptance replaces an
earlier one for the minutes they share. Summing energy across the acceptances
of a unit counts those minutes more than once; a model that needs one
instructed level per half hour must first choose the acceptance in force.

**`soFlag` is believed to mark actions taken for system reasons** such as
network constraints or voltage, rather than to balance energy. If so, treating
them as energy balancing would misattribute the cause. **Not documented in the
API. Confirm before relying on it.**

**`amendmentFlag` looks like a revision axis**, given that every observed
value is `ORI`. What an amended acceptance looks like is not known. A
warning-level dbt test on `stg_elexon__boalf` reports any other value except
null. `int_elexon__boa_by_period` keeps the latest capture of each ramp
point: an amendment that removed a ramp point or moved its start would leave
the old point in the view. Check how amendments arrive before the view feeds
an analysis.

**`from` and `to` filter on `timeFrom`, and both ends are included.** Two
consecutive daily requests therefore both return the ramp points that start
at the midnight between them: 12 points on 2026-10-06. Supplying
`settlementPeriodFrom` or `settlementPeriodTo` switches the filter to
settlement **date**, with the time portion ignored. This is documented for
BOALF, unlike B1610.

**An acceptance that crosses midnight is split across two daily requests.**
38 acceptances were in both the 2026-10-05 and the 2026-10-06 responses. Only
the midnight ramp point is in both; the points before it are in the first
response alone. A model must therefore choose the latest capture of each
**ramp point**, never of a whole acceptance.

## Key

```sql
PRIMARY KEY (national_grid_bm_unit, acceptance_number, time_from, retrieved_at)
```

The unit is part of the key because acceptance numbers repeat across units.
`retrieved_at` is part of the key because the rows carry no revision number,
as in PN.
