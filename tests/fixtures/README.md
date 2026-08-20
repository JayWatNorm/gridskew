# Test fixtures

Real API responses, captured verbatim unless marked truncated, grouped by
source to mirror `ingestion/` and `docs/sources/`. Hand-written fixtures encode
what you think an API returns; captured ones record what it actually returned,
which is the point.

## carbon_intensity/

| File | Source | Captured | Notes |
|---|---|---|---|
| `forecast_fw48h.json` | `/intensity/2026-08-17T08:00Z/fw48h` | 2026-08-17 | 97 periods, all actuals null |
| `outturn.json` | `/intensity/2026-08-01T00:00Z/2026-08-01T03:00Z` | 2026-08-19 | 7 periods, settled |

## elexon/

| File | Source | Captured | Notes |
|---|---|---|---|
| `pn_stream.json` | `/datasets/PN/stream`, 1h, `T_DRAXX-1` | 2026-08-20 | Bare array. Descending period order as returned |
| `b1610_stream.json` | `/datasets/B1610/stream`, 1h, `T_DRAXX-1` | 2026-08-20 | Naive `halfHourEndTime`, run type `II` |
| `remit_stream.json` | `/datasets/REMIT/stream`, 2h publish window | 2026-08-20 | One mrid at revisions 4, 5, 6. `Unplanned` and `Dismissed` observed. No `outageProfile` field |
| `mels_stream.json` | `/datasets/MELS/stream`, 30min, `T_DRAXX-1` | 2026-08-20 | |
| `mils_stream.json` | `/datasets/MILS/stream`, 30min, `T_DRAXX-1` | 2026-08-20 | |
| `indo.json` | `/datasets/INDO`, 1h publish window | 2026-08-20 | Base endpoint: `{data: [...]}` envelope |
| `itsdo.json` | `/datasets/ITSDO`, 1h publish window | 2026-08-20 | Envelope |
| `ndf_truncated.json` | `/datasets/NDF`, 1h publish window | 2026-08-20 | **Truncated extract**: 6 rows of ~119, keeping two forecast vintages (10:16 and 10:49) and a local-date rollover row |
| `system_prices.json` | `/balancing/settlement/system-prices/2026-08-01/5` | 2026-08-20 | `{metadata, data}` envelope. 18-decimal numerics |
| `bmunits_truncated.json` | `/reference/bmunits/all` | 2026-08-20 | **Truncated extract**: first 10 of an unknown total. Includes null `fuelType`, null `eic`, and one unit with null `elexonBmUnit` |

Truncated extracts preserve verbatim rows but not the full payload; do not use
them for row-count assertions against the API.
