# BOALF, balancing acceptances

**Not yet ingested.** Planned for Phase 1, step 3 of the thesis.

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
| `settlementPeriodFrom`, `settlementPeriodTo` | no, did not filter when tested |
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

**None of the flag fields carry descriptions in the API spec.** The readings
below are industry convention and inference from the names, not documented
behaviour. Verify against live data before the analysis depends on any of them.
| `nationalGridBmUnit` | `str` | |
| `bmUnit` | `str` | |

## Things to know before modelling it

**Acceptances span settlement periods.** `settlementPeriodFrom` and
`settlementPeriodTo` can differ, so one row is not one half hour. Attributing
an acceptance to periods means splitting it.

**Like PN, levels are a ramp**, not a flat value.

**`soFlag` is believed to mark actions taken for system reasons** such as
network constraints or voltage, rather than to balance energy. If so, treating
them as energy balancing would misattribute the cause. **Not documented in the
API. Confirm before relying on it.**

**`amendmentFlag` looks like a revision axis**, given the `ORI` example value.
If so, take the latest per `acceptanceNumber`. Also unconfirmed.

**`from` and `to` filter on `timeFrom`** by default, inclusively. Supplying
`settlementPeriodFrom` or `settlementPeriodTo` switches them to filtering on
settlement **date**, with the time portion ignored. This is documented for
BOALF, unlike B1610.

## Proposed key

Not yet built. Likely `(acceptance_number, bm_unit, time_from, retrieved_at)`,
but `acceptanceNumber` uniqueness needs confirming against real data first.
