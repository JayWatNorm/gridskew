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
