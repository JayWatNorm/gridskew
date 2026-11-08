# Operations

How GridSkew is kept running and what to do first when something fails.
Each step links to the page that holds the full procedure.

## Noticing a failure

Nothing sends an alert. Failed and skipped runs show in the Airflow UI,
which is checked each morning. The nightly dbt job also records every
model's and test's result in `dbt_dev.dbt_node_results`.

## When a DAG fails

| DAG | Runs (UTC) | Usual cause | First action |
|---|---|---|---|
| `gridskew_carbon_intensity` | :05 and :35 | The forecast API was down or returned an incomplete set | Do not clear the run: a later run can fetch only the current forecast, so the missed version is gone. [Forecast ingestion](sources/carbon-intensity/011_forecast_ingestion.md) |
| `gridskew_carbon_intensity_outturn` | 06:00 | An incomplete recent window or a rejected row | Usually none: each run fetches the last seven days again, and the Monday run fills older gaps. [Outturn ingestion](sources/carbon-intensity/021_outturn_ingestion.md#re-fetching-gaps-in-history) |
| `gridskew_elexon_b1610_II`, `_SF`, `_RF` | 00:00 | The settlement run is not fully published yet, or a later run has replaced it | Retries continue for about four days. After that, clear the task while the run is still in force (RF: any time). A replaced run cannot be fetched. [B1610 ingestion](sources/elexon/021_b1610_ingestion.md#one-settlement-date-per-run) |
| `gridskew_elexon_pn`, `_qpn`, `_boalf` | 00:00 | The API was unavailable; for BOALF, an empty response | Clear the task; for several past days, backfill (below). [PN ingestion](sources/elexon/011_pn_ingestion.md#backfill), [BOALF](sources/elexon/041_boalf_ingestion.md#an-empty-response-fails-the-run) |
| `gridskew_elexon_qas` | 00:00 | The API was unavailable | Retries continue for about four days; then clear the task. Each run captures the dates 14, 35 and 450 days before its interval, so a date missed at one age is captured at the next, and an Airflow backfill fetches those earlier dates, not the days it names. [QAS ingestion](sources/elexon/091_qas_ingestion.md#three-captures-of-every-date) |
| `gridskew_elexon_remit` | :15 | A row in quarantine, which keeps every run failing | Read the quarantined rows' `validation_errors` (below) and fix the contract or parser, then load again from the earliest `publishDateTimeFrom` in their `request_context`. [REMIT ingestion](sources/elexon/031_remit_ingestion.md#a-quarantined-row-keeps-the-run-failing) |
| `gridskew_elexon_remit_reread` | 04:40 | HTTP 429 or an unavailable API; a REMIT row in quarantine, which holds it with no request; a response the parser cannot read | Usually none: the next morning asks the seven days again. A quarantine hold ends when the hourly run's quarantine is dealt with; a hold of more than seven days leaves days unread, so backfill the runs whose ranges hold them (below). [REMIT re-read](sources/elexon/031_remit_ingestion.md#daily-re-read) |
| `gridskew_elexon_wind` | 06:00 | The API was unavailable, or a day is held in quarantine | Usually none: a target retries for up to ten hours, and each run's look-back asks again for the seven days before its target. A quarantined day fails its task each morning, with no request for it, until its quarantine rows are deleted ([wind ingestion](sources/elexon/101_wind_ingestion.md#daily-capture)). A day older than the look-back needs a backfill (below) |
| `gridskew_elexon_bmunits` | 00:00 | The registry capture or its dbt step failed | Inspect the source and quarantine evidence, fix the cause, then retry the DAG, or only its dbt task if the capture succeeded. [BM-unit recovery](sources/elexon/071_bmunits_ingestion.md#recovery) |
| `gridskew__dbt_freshness_dag` | :20 | A source is later than its threshold in the dbt source files | Look for that source's own failed run; freshness passes again once it catches up |
| `gridskew__dbt_nightly` | 02:30 | A model or test failed, or the run refused because a period table changed | For a failed model or test, read `dbt_dev.dbt_node_results`. A refusal happens before dbt runs, so its task log names the changed models or missing tables; trigger one full refresh. [dbt guide](../dbt/README.md#period-facts) |

## Running past days again

The PN, QPN, BOALF and B1610 DAGs fetch the day set by each run's data
interval (B1610 at its run's fixed offset). To fetch past days, run in the
scheduler service:

```bash
airflow backfill create --dag-id <dag id> --from-date <first day> --to-date <last day>
```

Add `--reprocess-behavior completed` to repeat a day that already has a
successful run. A manual trigger has no data interval and these DAGs refuse
it.

`gridskew_elexon_wind` targets the first day of each run's interval, and each
run's look-back asks again for the seven days before its target, so a
backfill is needed only for a day older than that. Backfill the runs whose
targets are the days wanted, with `--max-active-runs 1`; add `--dry-run` first
and check that it lists exactly those runs. Each backfilled run's look-back
asks again for every day before its target that is not complete, so a day
one run leaves short is asked again by the next run within minutes: backfill
only the runs the gap needs.

`gridskew_elexon_remit_reread` re-reads, in each run, the seven UTC publish
days up to its interval start's UTC date, so a day stays in range for seven
mornings. For an older day, backfill the runs whose ranges hold it, with
`--max-active-runs 1`; add `--dry-run` first and check that it lists exactly
those runs. A backfilled run asks nothing past the hourly run's newest
stored message, and makes no request while a REMIT row is in quarantine.

## The quarantine

A row that breaks its source's field contract is stored in
`raw.endpoint_quarantine` with the request that fetched it, the errors and
the full payload ([endpoint validation](sources/endpoint-validation.md#routing)):

```sql
select dataset, retrieved_at, validation_errors, request_context
from raw.endpoint_quarantine
order by quarantined_at desc
limit 20;
```

Validation inserts these rows, and no loader deletes them. The REMIT run
stops counting a row once its message is stored, but the wind pollers hold a
day for as long as any quarantine row names it, so releasing a wind day
means deleting its rows
([wind ingestion](sources/elexon/101_wind_ingestion.md#daily-capture)). The
one recovery so far, of 23 REMIT rows in October 2026, deleted them after
the messages were reloaded, so the database keeps no record of that
rejection. A change that marks a row resolved instead of deleting it is
under consideration.

## A missed settlement-run capture

Elexon serves only the settlement run currently in force for a date. Each of
the II, SF and RF captures retries for about four days and can be cleared
until a later run replaces its own; after that the run is gone. See
[B1610 ingestion](sources/elexon/021_b1610_ingestion.md#three-standing-rungs).

## If the database is lost

The capture jobs fetch again the source data the public APIs still serve,
and dbt rebuilds the derived models. Two things cannot be fetched again: the
carbon-intensity forecast archive and the history of the BM-unit registry.
They are copied nightly on the server, and a further copy is kept on a
separate machine. Superseded settlement runs are not copied and would be
lost.

To rebuild:

1. Apply the raw migrations ([schema migrations](schema-migrations.md)).
2. Provision the dbt schemas ([deployment](deployment.md)).
3. Restore the forecast archive and registry history from their copy.
4. Build the dbt project once, as the deployment page describes.
5. Let the capture DAGs catch up; backfill any long gap.
6. Trigger one full refresh of the nightly dbt job.

## Releases

See [deployment](deployment.md). The release procedure itself is in the
platform repository's GridSkew release runbook.
