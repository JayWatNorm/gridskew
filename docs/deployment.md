# Deploying to an external Airflow

This project does not run its own scheduler. The DAGs in `dags/` are written to
be deployed onto an Airflow instance that lives elsewhere and is shared with
other projects.

That constraint shapes everything below. If Airflow were bundled with this
repository, a `git pull` would deploy it and this page would not exist.

For why the pollers are written the way they are, see
[sources/ingestion-patterns.md](sources/ingestion-patterns.md). This page is
about getting them running somewhere.

## Two artefacts, two mechanisms

A deployment moves two different things, and they do not move the same way.

| Artefact | How it reaches Airflow | Effect of a `git pull` |
|---|---|---|
| `dags/*.py` | **copied** into the Airflow instance's DAG folder | none until copied again |
| `ingestion/` | **bind-mounted** from a checkout of this repository | takes effect on next task run |

**DAGs are copies; ingestion is mounted.** This is the single most important
thing on the page, because the failure it causes is silent: pull a DAG change,
see it in the repository, and watch Airflow keep running the old one.

The asymmetry is deliberate rather than accidental. A shared scheduler needs one
flat namespace of DAG files it owns, whereas ingestion code is the project's own
package and should not be duplicated. The trade-off is that the DAG file exists
in two places and they can drift.

**A DAG file must end in `.py`.** Airflow's file processor discovers by
extension, so a file saved without one is invisible — no error, no import
failure, simply absent from the UI.

## Sequence

**1. Apply the DDL to every database the DAG will write to.**

The files in `sql/init/` are applied by hand, per database. There is no
migration tool. Confirm the result rather than assuming it:

```sql
\d raw.elexon_pn
```

A key that does not match the DDL will not fail at deploy time. It fails on the
first real load, part way through an insert, which is a worse place to find out.

Check ownership at the same time — a table owned by an administrative role
rather than the application role will fail on write, again only at run time:

```sql
SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'raw';
```

**2. Commit the DAG into the Airflow repository, then pull on the host.**

The copy is made **on your development machine and committed**, not made by hand
on the server. Copy the file into `homelab-platform/dags/`, commit and push
there, then on the host:

```bash
cd ~/gridskew && git pull            # bind-mounted ingestion/
cd ~/homelab-platform && git pull    # the DAG copy
```

Both repositories, because the two artefacts travel separately.

Copying by hand on the server also works, but leaves a file Git has no record
of — which blocks the next pull and lets the deployed DAG drift from the
repository invisibly. Committing the copy keeps the deployed version reviewable.

The duplication itself is unavoidable in this model: the file exists in two
repositories and drifts if you edit one and forget the other. Git makes that
drift **visible**; it cannot prevent it.

**The server should only ever pull.** Worth enforcing rather than remembering:

```bash
cd ~/homelab-platform && git config pull.ff only
```

Any divergence then fails loudly instead of offering three ways to merge around
it — which is what happens if you ever commit on the server by mistake.

**3. Wait for the scheduler to pick it up.**

Two settings govern this:

| Setting | Default | What it controls |
|---|---|---|
| `dag_dir_list_interval` | 300s | how often the folder is rescanned for new files |
| `min_file_process_interval` | 30s | how often an individual file is re-parsed |

Files are parsed **independently, not as a batch**, and the processing queue is
not ordered alphabetically or by modification time. Two DAGs copied in the same
second routinely appear minutes apart and in arbitrary order. This is normal and
not a symptom of anything.

A file still missing after ten minutes is worth investigating:

```bash
airflow dags list-import-errors
```

**4. Leave the DAG paused and trigger one run manually.**

Verify the row count before allowing a schedule to run unattended. Expected
volumes are on each dataset's ingestion page.

**5. Unpause.**

## Backfills

The PN, QPN and B1610 II DAGs use `catchup=True`, so **unpausing one starts a
backfill**. Their `start_date` is a fixed literal that defines how much history
to load. The B1610 SF DAG uses `catchup=False` and runs forwards only.

Runs execute one at a time under `max_active_runs=1`. For a daily-chunked Elexon
DAG that works out at roughly 1.4 requests per minute, and a year of history
takes about four hours.

**Pausing mid-backfill is safe.** The in-flight run finishes, queued runs stop,
and unpausing resumes where it left off. Each run is addressed to a specific
interval, so nothing is lost and nothing is repeated.

**An `elexon` pool with one slot now enforces this** — created 2026-08-25. Every
Elexon task carries `@task(pool="elexon")`, so only one runs at a time across all
four DAGs regardless of schedule. Carbon intensity is deliberately excluded:
different API, tiny payloads, no reason to queue behind an Elexon backfill.

```bash
docker compose exec airflow airflow pools list
```

The constraint it protects is **worker memory**, not the API. Each poller
materialises its whole response in Python before inserting — B1610 alone is
~450,000 rows — and several at once on a single `LocalExecutor` is a plausible
OOM. An OOM mid-backfill kills a run that then has to be cleared by hand.

**A task pointing at a pool that does not exist will not run**, so create the
pool before deploying a DAG that references it.

### `start_date` is a deployment decision

It defines the history window, and it is a literal on purpose.

Standing a DAG up on a fresh Airflow instance later re-backfills from that same
date, so the window grows with elapsed time. Change the literal deliberately if
that is not wanted.

**Never compute it from `datetime.now()`.** `start_date` is the anchor Airflow
uses to work out which intervals exist. A DAG module is re-parsed roughly every
thirty seconds, so a computed value shifts the schedule underneath itself and
runs get skipped or duplicated. The reasoning is in
[sources/elexon/011_pn_ingestion.md](sources/elexon/011_pn_ingestion.md).

## A UTC schedule is not a settlement day

`@daily` produces intervals running midnight to midnight **UTC**. The GB
settlement day starts at midnight **British local time**, which under BST is
23:00 UTC the previous day.

So for roughly half the year each run collects periods 3 to 48 of one settlement
day plus periods 1 and 2 of the next. Consecutive runs are contiguous in
absolute time, so nothing is lost — but **one DAG run is not one settlement
day**, and any downstream model that assumes otherwise is wrong for half the
year.

The earliest day of a backfill is a partial for the same reason: the periods
before the first window opens have no run to collect them.

## Reference implementation

The instance this project currently deploys to is a single-container Airflow
with a `LocalExecutor`, shared between several projects, defined in a separate
`homelab-platform` repository. Each project contributes DAG files into one
shared `dags/` folder and has its own repository bind-mounted for code.

Run `docker compose` commands from `~/homelab-platform` so Compose resolves the
short **service** names from the compose file rather than the auto-generated
container names:

```bash
cd ~/homelab-platform
docker compose exec airflow airflow dags list-import-errors
docker compose exec postgres-prod psql -U gridskew -d gridskew_prod
```

`docker exec` works from anywhere but needs the full name —
`homelab-platform-airflow-1`, `homelab-platform-postgres-prod-1`.

Database credentials come from an Airflow Connection rather than the `.env` file
the pollers use when run directly, so nothing secret is copied anywhere. Each
project's environment variables on the shared container are namespaced by
project to avoid collision.

None of that is a requirement of this project. Any Airflow that can read the
`ingestion/` package and reach the database will run these DAGs.
