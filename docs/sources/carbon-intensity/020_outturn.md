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

Unlike the forecast archive, this data is **permanently re-fetchable**, which
changes how much care it warrants: a mistake here is a truncate and a reload.

## Endpoint

```
GET https://api.carbonintensity.org.uk/intensity/{from}/{to}
```

| Parameter | Type | Notes |
|---|---|---|
| `from` | path | `YYYY-MM-DDTHH:MMZ` |
| `to` | path | Same format. Window appears to cap at **30 days** |

30 days is confirmed to work and is what the poller chunks on; a year needs 13
requests. **31 days has not been tested**, so whether the API errors or
truncates silently beyond 30 is unconfirmed. Silent truncation is the dangerous
case, so this is worth establishing if the chunk size is ever raised.

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

**`actual` appears roughly 15 minutes after a period closes.** Whether it is
subsequently revised is not documented and is being measured from this table's
own history.

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

`sql/init/002_carbon_intensity_outturn.sql`

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

`actual` is nullable because unsettled periods legitimately have none.
`forecast_final` is nullable too, deliberately: a `NOT NULL` would reject the
row and destroy the evidence that the source published a null. That expectation
belongs in a dbt test on the staging model, not a constraint on raw.

## Ingestion

`ingestion/carbon_intensity/outturn_poller.py`, scheduled daily by the
`gridskew_carbon_intensity_outturn` DAG.

**One function, window taken from the database.** `data_checker` reads the
existing min and max `period_start`; an empty or short table triggers a 365 day
backfill, otherwise a 7 day look-back. Backfill and catch-up are the same code
path with different dates, which is what stops them drifting apart.

The 7 day look-back exists to pick up revisions. Airflow's own `catchup` would
fetch each day exactly once and never look back, which is why it is off.

## Traps

**Chunk boundaries overlap by one period.** Each chunk starts where the last
ended, and the API returns the period containing that instant. With
`retrieved_at` constant across a run, that is a guaranteed primary key
violation, so the insert carries `ON CONFLICT DO NOTHING`.

This only bites when a window starts exactly on a half-hour, which is precisely
when scheduled runs fire. A manual run starting mid-period will not reproduce
it.

**Every downstream read must deduplicate.** Append-only plus repeated polls
means one period can have several rows. Use `DISTINCT ON (period_start)` with
`ORDER BY period_start, retrieved_at DESC` to take the latest.

## Findings from this table

The hour-zero asymmetry test ran against a year of it on 2026-08-19. Median of
`actual - forecast_final` was -1 gCO2/kWh over 17,522 periods, with actual above
forecast 47.5% of the time excluding ties. See the README's mechanism section.
