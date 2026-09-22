# BM Unit registry

**Not yet ingested.** Planned for Phase 1, S4.

## In plain terms

**The address book.**

Every other Elexon dataset identifies things by codes like `T_DRAXX-1`. On
their own those tell you little. This registry adds names, capacities,
operators and sometimes a fuel type. Those fields help identify a unit, but
fuel can be null and no single field proves that it is a physical generator.

Two jobs for this project:

- **Scoping.** The shortfall analysis starts with verified physical generators.
  Batteries, interconnectors and demand units need separate treatment.
  `fuelType`, `bmUnitType`, `fpnFlag`, capacities and unit identity are evidence
  for that decision; none is a complete generator filter on its own.
- **Labelling.** Turning `T_DRAXX-1` into "Drax unit 1, biomass, 645 MW" so
  results are readable when the label is supported by source data.

**It changes over time.** Units are commissioned, decommissioned, re-registered
and re-rated. A snapshot preserves changes from the start of collection. The
current endpoint alone cannot tell us a unit's fuel type before collection
began.

---

## Endpoint

```
GET https://data.elexon.co.uk/bmrs/api/v1/reference/bmunits/all
```

No parameters. Returns every registered unit in one response.

There is also `/reference/bmunits` for filtered lookups.

## Response fields

| Field | Type | Notes |
|---|---|---|
| `elexonBmUnit` | `str or null` | e.g. `T_DRAXX-1`. The identifier used across datasets; null has been observed |
| `nationalGridBmUnit` | `str` | e.g. `DRAXX-1` |
| `eic` | `str or null` | European identification code; frequently null in the observed sample |
| `bmUnitName` | `str` | Human-readable name |
| `bmUnitType` | `str or null` | Category of unit; null has been observed |
| `fuelType` | `str or null` | e.g. `CCGT`, `NUCLEAR`, `WIND`, `BIOMASS`; often null |
| `leadPartyName` | `str` | Operator |
| `leadPartyId` | `str` | |
| `demandCapacity` | `str or null` | MW. **Typed as string in the API** |
| `generationCapacity` | `str or null` | MW, also a string |
| `productionOrConsumptionFlag` | `str` | |
| `transmissionLossFactor` | `str` | |
| `fpnFlag` | `bool` | Whether the unit submits Final Physical Notifications |
| `creditQualifyingStatus` | `bool` | |
| `demandInProductionFlag` | `bool` | |
| `gspGroupId` | `str` | Grid Supply Point group, a regional identifier |
| `gspGroupName` | `str` | |
| `interconnectorId` | `str` | Populated for interconnectors rather than generators |
| `workingDayCreditAssessmentImportCapability` | `str` | Credit fields, not relevant here |
| `nonWorkingDayCreditAssessmentImportCapability` | `str` | |
| `workingDayCreditAssessmentExportCapability` | `str` | |
| `nonWorkingDayCreditAssessmentExportCapability` | `str` | |

**Capacities come back as strings**, not numbers. Cast in staging, and expect
nulls and blanks.

## Observed live, 2026-08-20

An alphabetical sample of 120 units, with the first 10 retained in
`tests/fixtures/elexon/bmunits_truncated.json`, establishes the following
properties:

- **`fuelType` was null on 112 of 120 units.** It is sparsely populated, at
  least across this alphabetical slice, which was dominated by supplier and
  virtual units. Scoping to physical generators **cannot rely on `fuelType`
  alone**. `bmUnitType`, identifier shape and `fpnFlag` help investigate a unit,
  but none establishes its role or fills a missing fuel. A REMIT fuel label
  needs a verified asset match and relevant date before use.
- **`bmUnitType` is a single-letter code**: `T`, `E`, `S`, `V`, `G` observed.
  The spec does not document what the letters mean. `T` appears to align with
  transmission-connected units and `S` with suppliers, but that is inference
  from the identifier prefixes, not documentation.
- **Even identity fields can be null.** One unit had null `elexonBmUnit` and
  null `bmUnitType`. `eic` is frequently null.
- The sample is not suitable for a market-wide unit count or null-rate
  estimate. Those require loading the complete response.

## Full-registry checkpoint, 2026-09-22

The complete `/reference/bmunits/all` response contained 3,088 units. Of
those, 588 had a non-null `fuelType` (19 distinct codes) and 2,500 had a null
value. This is a dated unit count, not the share of generation with known fuel.

For a bounded check, the project's 10–16 August 2026 `B1610` `SF` unit
aggregate contained 468 `T_` or `E_` units with positive metered energy.
Matching them to the 22 September registry found fuel labels for 348 units,
covering 98.46% of their positive MWh. The remaining 120 units represented
1.54% of those MWh; 100 had a null fuel and 20 were absent from the current
registry. `T_` and `E_` are only a candidate cohort: it includes storage and
interconnector units, and positive metering does not prove physical generation.
The aggregate does not contain date or run-type fields, so the stated `SF`
scope comes from the query used to create it. This current-registry match also
does not establish each unit's fuel in August. Keep unsupported fuels null
until dated evidence supports a label.

## Why a snapshot rather than a table

The registry is a **current-state** view. Ask it today and you get today's
answer, with no history.

The endpoint has no reliable updated-at field, so the planned dbt snapshot
will use the `check` strategy. It will compare descriptive attributes on each
poll and record a new version when one changes. A settlement-period join can
use a captured version only from the start of collection onwards; earlier
fuel history needs separate dated evidence. That is the S4 build.

## Relevance to the README's limitations

The README notes that Physical Notifications mean different things by unit type,
so the shortfall analysis will be scoped to physical generators first. The registry
provides candidate signals such as `bmUnitType` and `fpnFlag`, but the eligible
cohort needs documented unit-role evidence and an explicit unknown category.
