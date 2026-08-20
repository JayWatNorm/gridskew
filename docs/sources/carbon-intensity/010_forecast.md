# Carbon intensity forecast archive

## In plain terms

NESO publishes a prediction of how dirty Britain's electricity will be over the
next two days, updated every half hour. Each update quietly replaces the last.

This table keeps **every version**. It is the difference between saving every
weather forecast for next Tuesday as the week goes on, and only ever seeing the
one issued on Monday night.

The interesting question it answers: as a given half hour got closer, did the
prediction drift in a consistent direction?

---

Every forecast the model has ever published for a given settlement period, from
roughly 48 hours ahead down to minutes before.

The source overwrites its forecast every 30 minutes, so this sequence exists
nowhere else and cannot be reconstructed after the fact. This is the dataset
the project was built around.

## Endpoint

```
GET https://api.carbonintensity.org.uk/intensity/{from}/fw48h
```

| Parameter | Type | Notes |
|---|---|---|
| `from` | path | `YYYY-MM-DDTHH:MMZ`. The poller passes the current UTC time |

Returns **96 or 97** half-hourly periods covering the next 48 hours. The count
varies: a request landing exactly on a half-hour boundary returns 97, one
mid-period returns 96. Scheduled runs fire at `:00` and `:30` and consistently
return 97.

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

`sql/init/001_carbon_intensity_forecast.sql`

**Grain:** one row per settlement period, per poll. About 97 rows every 30
minutes, roughly 250 MB per year.

**Key:** `(period_start, retrieved_at)`. Both values are given by the source or
the run; neither is derived. Together they say "this is what the model believed
about this period, at this moment".

**Horizon is not stored.** It is derived downstream as
`period_start - retrieved_at`. Storing it would duplicate information already
present and create a way for the two to disagree.

**`actual` is always null here** and is stored anyway, because the raw layer
records what the source returned. Outturn is ingested separately into
`raw.carbon_intensity_outturn`.

## Ingestion

`ingestion/carbon_intensity/forecast_poller.py`, scheduled by the
`gridskew_carbon_intensity` DAG every 30 minutes, writing to `gridskew_prod`.

`catchup=False` is load-bearing rather than cosmetic. A backfilled run cannot
retrieve the forecast that existed during its interval; it would fetch current
data and stamp it with a misleading `retrieved_at`.

## Traps

**`retrieved_at` must be captured once per poll**, before the HTTP request, and
written identically to every row in the batch. Generate it per row and the
batch dissolves into 97 near-identical timestamps, losing the thing the archive
exists to record.

**Two columns are `integer` and two are `timestamptz`**, so a positional swap in
the parse tuple is accepted silently by Postgres. `tests/test_forecast_poller.py`
pins every column position with a golden value for exactly this reason.

## Known history

The table was truncated once, deliberately, on 2026-08-17, twelve minutes after
the first row landed. It held three deployment-artefact polls and nothing of
analytical value. Append-only applies from the first scheduled poll after that,
and there is no second exception.
