# Carbon intensity outturn

What the carbon intensity **actually turned out to be** in each settlement
period, once it had happened. The scorecard the forecasts get marked against.

The endpoint also hands back a forecast figure for the same period, but it is
the last-minute version, issued when the model already knew nearly everything.
Useful, but not the same thing as the 48-hour-ahead prediction, which is why
the column is called `forecast_final`.

Unlike the forecast archive, this data can be fetched again, so missing windows
are re-fetched and a full rebuild remains possible.

## Endpoint

```
GET https://api.carbonintensity.org.uk/intensity/{from}/{to}
```

| Parameter | Type | Notes |
|---|---|---|
| `from` | path | `YYYY-MM-DDTHH:MMZ` |
| `to` | path | Same format. The poller requests at most **30 days** per call |

Requests of up to 30 days return complete data, so the poller splits a year into
13 requests. A response that ends before the requested window fails the
period-completeness check.

History is deep. January 2024 returns data with both forecast and actual
populated, and the API's own examples reach back to 2017.

## Response

```json
{
  "data": [
    {
      "from": "2026-08-01T00:00Z",
      "to": "2026-08-01T00:30Z",
      "intensity": {
        "forecast": 167,
        "actual": 165,
        "index": "moderate"
      }
    }
  ]
}
```

| Field | Python type | Notes |
|---|---|---|
| `from` | `str` | Period start |
| `to` | `str` | Period end |
| `intensity.forecast` | `int` | **The final revision**, not a horizon-resolved forecast |
| `intensity.actual` | `int` or `None` | Null before the actual is published; never null in this table |
| `intensity.index` | `str` | Band at time of publication |

**`actual` appears roughly 15 minutes after a period closes.** The API does not
document whether it is revised later. Every poll is kept, so a revision that a
poll captures stays visible in this table's history; a value revised and
replaced between two daily polls, or revised after the 7-day look-back, is
not captured. The first poll of a period comes 24.5 to 48 hours after it
starts. In the 2,883 half-hours from 1 September 2026 to early on 31 October
the stored actual changed in 40 (1.4%), each first at the second poll. That
measures when a value first changed, not whether it changed again.

## Table

```sql
CREATE TABLE raw.carbon_intensity_outturn (
    period_start      TIMESTAMPTZ NOT NULL,
    period_end        TIMESTAMPTZ NOT NULL,
    retrieved_at      TIMESTAMPTZ NOT NULL,
    actual            integer,
    forecast_final    integer,
    intensity_index   text,
    PRIMARY KEY (period_start, retrieved_at)
);
```

`sql/migrations/V002__carbon_intensity_outturn.sql`

**Note the column order differs from the forecast table.** Here `actual` comes
before `forecast_final`; there `forecast` comes before `actual`. Both pairs are
`integer`, so a parse written by copying the other module and not adjusting the
tuple order will corrupt data without raising.

### Why `forecast_final` and not `forecast`

The API keeps a **single stored forecast** for a past period, and NESO does
not document when it is made. That is a different quantity from
`raw.carbon_intensity_forecast.forecast`, which is horizon-resolved.

Two columns called `forecast` meaning different things is a join waiting to go
wrong, so the distinction is structural rather than a comment. The column
carries a `COMMENT ON COLUMN` saying so, which surfaces in `\d+` and in dbt
docs.

### Nullability

The raw `actual` and `forecast_final` columns remain nullable so PostgreSQL
cannot discard unexpected source evidence. The outturn contract requires
non-null `intensity.actual` and `intensity.forecast` because the poller requests
settled periods. A null value is retained in quarantine and the run fails.

### Impossible values

The dbt test `warn_outturn_actual_impossible`, run by the nightly job, warns
on every stored capture whose `actual` is zero or below. The API's own method
is not documented as able to give one, so the test reports it; the row is
kept. On 20 November 2026 the table held 48,861 rows over 21,951 half-hours;
one row, 0 g/kWh for 6 August 2026 11:00 UTC, had an actual of zero or
below, and the results page reports it.

## Ingestion

See [021_outturn_ingestion.md](021_outturn_ingestion.md).

## Traps

**Every downstream read must deduplicate.** Append-only plus repeated polls
means one period can have several rows. Use `DISTINCT ON (period_start)` with
`ORDER BY period_start, retrieved_at DESC` to take the latest.

## Results from this table

No saved result measures how often the actual exceeds the stored forecast.
The stored forecast's mean error by month and time of day is on the
[results page](https://jaywatnorm.github.io/gridskew/#q29); see
[the mechanism behind the question](../../../README.md#the-mechanism-behind-the-question).
