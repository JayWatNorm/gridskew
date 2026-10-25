# Carbon intensity forecast archive

NESO publishes a prediction of how dirty Britain's electricity will be over the
next two days, updated every half hour. Each update quietly replaces the last.

This table keeps **every version** polled for each settlement period since the
archive started, from roughly 48 hours ahead down to minutes before. It is the
difference between saving every weather forecast for next Tuesday as the week
goes on, and only ever seeing the one issued on Monday night. The sequence
exists nowhere else and cannot be reconstructed after the fact. This is the
dataset the project was built around.

The question it answers: as a given half hour got closer, did the prediction
drift in a consistent direction?

## Endpoint

```
GET https://api.carbonintensity.org.uk/intensity/{from}/fw48h
```

| Parameter | Type | Notes |
|---|---|---|
| `from` | path | `YYYY-MM-DDTHH:MMZ`. The poller passes the current UTC time |

Normally returns **96 or 97** half-hourly periods covering the next 48 hours:
97 for a request exactly on a half-hour boundary, 96 for one mid-period. When
the source feed stops extending, it returns fewer. The poller checks the periods
rather than a fixed count; see
[011_forecast_ingestion.md](011_forecast_ingestion.md).

## Response

```json
{
  "data": [
    {
      "from": "2026-08-19T14:00Z",
      "to": "2026-08-19T14:30Z",
      "intensity": {
        "forecast": 125,
        "actual": null,
        "index": "moderate"
      }
    }
  ]
}
```

| Field | Python type | Notes |
|---|---|---|
| `from` | `str` | Period start, UTC, no seconds |
| `to` | `str` | Period end, always 30 minutes later |
| `intensity.forecast` | `int` | gCO2/kWh. Always present |
| `intensity.actual` | `None` | Always null on this endpoint; the periods are in the future |
| `intensity.index` | `str` | `very low` to `very high` |

## Table

```sql
CREATE TABLE raw.carbon_intensity_forecast (
    period_start      TIMESTAMPTZ NOT NULL,
    period_end        TIMESTAMPTZ NOT NULL,
    retrieved_at      TIMESTAMPTZ NOT NULL,
    forecast          integer     NOT NULL,
    actual            integer,
    intensity_index   text,
    PRIMARY KEY (period_start, retrieved_at)
);
```

`sql/migrations/V001__carbon_intensity_forecast.sql`

**Grain:** one row per settlement period, per poll. Usually 96 or 97 rows every
30 minutes, roughly 250 MB per year.

**Key:** `(period_start, retrieved_at)`. Both values are given by the source or
the run; neither is derived. Together they say "this is what the model believed
about this period, at this moment".

**Horizon is not stored.** It is derived downstream as
`period_start - retrieved_at`. Storing it would duplicate information already
present and create a way for the two to disagree. The model
`int_carbon_forecast__revisions` derives it as `horizon_hours`.

**`actual` is always null here** and is stored anyway, because the raw layer
records what the source returned. Outturn is ingested separately into
`raw.carbon_intensity_outturn`.

## Ingestion

See [011_forecast_ingestion.md](011_forecast_ingestion.md).

## Traps

**`retrieved_at` is captured once per poll.** See
[011_forecast_ingestion.md](011_forecast_ingestion.md).

**Two columns are `integer` and three are `timestamptz`**, so a positional swap in
the parse tuple is accepted silently by Postgres. `tests/test_forecast_poller.py`
pins every column position with a golden value for exactly this reason.
