# Carbon intensity outturn

## In plain terms

What the carbon intensity **actually turned out to be**, once each half hour
had happened. The scorecard the forecasts get marked against.

The endpoint also hands back a forecast figure for the same period, but it is
the last-minute version, issued when the model already knew nearly everything.
Useful, but not the same thing as the 48-hour-ahead prediction, which is why
the column is called `forecast_final`.

Unlike the forecast archive, this can always be re-downloaded, so nothing here
is irreplaceable.

---

What carbon intensity actually was, per settlement period, alongside the
forecast as it stood at settlement time.

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
| `intensity.actual` | `int` or `None` | Null until the period has settled |
| `intensity.index` | `str` | Band at time of publication |

**`actual` appears roughly 15 minutes after a period closes.** The API does not
document whether it is revised later. Every poll is kept, so a revision that a
poll captures stays visible in this table's history; a revision made after the
7-day look-back is not captured.

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

The API's stored forecast for a past period is its **last revision**, made
minutes before the event when the model had already observed most of what it
was predicting. That is a different quantity from
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

## Ingestion

See [021_outturn_ingestion.md](021_outturn_ingestion.md).

## Traps

**Every downstream read must deduplicate.** Append-only plus repeated polls
means one period can have several rows. Use `DISTINCT ON (period_start)` with
`ORDER BY period_start, retrieved_at DESC` to take the latest.

## Findings from this table

The root README's first prediction, that actual intensity exceeds the forecast
more often than not, was tested against a year of this table on 2026-08-19.
Median of `actual - forecast_final` was -1 gCO2/kWh over 17,522 periods,
deduplicated with `DISTINCT ON`, with actual above forecast 47.5% of the time
excluding ties. See
[the mechanism being tested](../../../README.md#the-mechanism-being-tested).
