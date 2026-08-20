# BM Unit registry

**Not yet ingested.** Planned for Phase 1, S4.

## In plain terms

**The address book.**

Every other Elexon dataset identifies things by codes like `T_DRAXX-1`. On
their own those tell you nothing. This registry says what each one actually is:
which fuel it burns, how big it is, who operates it, and whether it is a
generator, a battery, or something stranger.

Two jobs for this project:

- **Scoping.** The analysis is limited to physical generators (gas, nuclear,
  wind) because batteries and aggregated demand-side units declare their
  intentions differently, which would make the shortfall calculation
  meaningless for them. `fuelType` and `bmUnitType` are how that filter is
  applied.
- **Labelling.** Turning `T_DRAXX-1` into "Drax unit 1, biomass, 645 MW" so
  results are readable.

**It changes over time.** Units are commissioned, decommissioned, re-registered
and re-rated. That is why it is a snapshot rather than a plain table: you need
to know what a unit's fuel type was *at the time*, not what it is today.

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
| `elexonBmUnit` | `str` | e.g. `T_DRAXX-1`. The identifier used across datasets |
| `nationalGridBmUnit` | `str` | e.g. `DRAXX-1` |
| `eic` | `str` | European identification code |
| `bmUnitName` | `str` | Human-readable name |
| `bmUnitType` | `str` | Category of unit |
| `fuelType` | `str` | e.g. gas, nuclear, wind, biomass |
| `leadPartyName` | `str` | Operator |
| `leadPartyId` | `str` | |
| `demandCapacity` | `str` | MW. **Typed as string in the API** |
| `generationCapacity` | `str` | MW, also a string |
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

A partial pull of the registry (120 units visible before the response was cut
off by tooling; see `tests/fixtures/elexon/bmunits_truncated.json` for the
first 10) changed the picture on two fields this project planned to lean on:

- **`fuelType` was null on 112 of 120 units.** It is sparsely populated, at
  least across this alphabetical slice, which was dominated by supplier and
  virtual units. Scoping to physical generators **cannot rely on `fuelType`
  alone**; it will need `bmUnitType`, the identifier prefix (`T_`, `E_`, `C_`,
  `V_`), `fpnFlag` and possibly REMIT's own `fuelType` in combination.
- **`bmUnitType` is a single-letter code**: `T`, `E`, `S`, `V`, `G` observed.
  The spec does not document what the letters mean. `T` appears to align with
  transmission-connected units and `S` with suppliers, but that is inference
  from the identifier prefixes, not documentation.
- **Even identity fields can be null.** One unit had null `elexonBmUnit` and
  null `bmUnitType`. `eic` is frequently null.
- The full unit count remains unestablished; the response is large enough that
  it needs proper handling rather than a browser peek.

## Why a snapshot rather than a table

The registry is a **current-state** view. Ask it today and you get today's
answer, with no history.

Ingesting it as a dbt snapshot on a timestamp strategy records what it said on
each run, so a join from a two-year-old settlement period picks up the fuel type
as it was recorded then, not as it is now. That is the S4 build.

## Relevance to the README's limitations

The README notes that Physical Notifications mean different things by unit type,
so the shortfall analysis is scoped to physical generators first. `bmUnitType`
and `fpnFlag` are the fields that make that scoping possible, and this registry
is where they come from.
