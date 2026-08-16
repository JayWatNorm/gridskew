# Decision log

Why things are the way they are. Each entry is written when the decision is
made, in the same pull request as the change it describes.

Format: context, decision, consequences, status.

---

## 001 — How the thesis is stated, pending the hour-zero asymmetry test

**Date:** 2026-08-16 · **Status:** 🟡 Open

### Context

The project's core claim is that carbon intensity forecast error skews
positive, because unplanned generation shortfalls are backfilled by gas. Before
any pipeline is built, that claim can be given a first, cheap test: take the
distribution of (actual − forecast) carbon intensity straight from the Carbon
Intensity API and look at its shape.

The test is weaker than the real measurement, because the API's stored
"forecast" is a late revision rather than the 48-hour-ahead value. It measures
skew in *near-term* error, which is a lower bound.

### Decision

Pending. This entry is completed once the test has been run.

### Consequences

- Skew present > the mechanism is supported and the README states it as the
  motivating hypothesis.
- Skew absent > the thesis is restated as a decomposition question rather than
  a directional claim. The pipeline and the modelling work are unaffected
  either way; only the framing changes.

---

## 002 — Separate development and production databases

**Date:** 2026-08-16 · **Status:** ✅ Accepted

### Context

The homelab runs two PostgreSQL containers, one for development and one for
production, each holding a database per project. A single database would have
been simpler.

### Decision

`gridskew_dev` on the development server and `gridskew_prod` on the production
server, each owned by a scoped `gridskew` role with its own password. The
superuser is used for provisioning only.

### Consequences

- Development work cannot reach production data by accident — the separation is
  structural, not a naming convention that has to be remembered.
- Data restores flow production → development, never the reverse.
- dbt gains a second target, which is what makes `--defer`, `state:modified`
  and Slim CI possible later. With one database those features have nothing to
  point at.
- Two sets of credentials to manage instead of one.

---

## 003 — The forecast poller runs on Airflow, not cron

**Date:** 2026-08-16 · **Status:** ✅ Accepted

### Context

The forecast archive collects data that cannot be re-fetched. The original plan
was to start it as a cron job on the homelab and migrate it to Airflow later,
on the grounds that cron would be running sooner.

The archive is a single task with no upstream and no downstream dependency, so
it uses none of Airflow's dependency handling.

### Decision

Deploy it as an Airflow DAG on the shared homelab instance from the start.

### Consequences

- Failures are visible in the Airflow UI and retried automatically, which
  matters more here than elsewhere because a missed poll cannot be backfilled.
- One orchestrator for this project rather than two, so there is one place to
  look when something has not run.
- Per-project Airflow **Connections** must be set up now rather than later.
  This is the fix the shared platform's own README flags as needed once a
  second project arrives.
- The first row lands hours later than it would have under cron. Accepted: a
  handful of lost polls against an archive measured in years.
- `catchup=False`, because a missed window genuinely cannot be caught up — the
  forecast that existed at 04:00 no longer exists anywhere.

---

## 004 — Private repository, pull requests by convention rather than enforcement

**Date:** 2026-08-16 · **Status:** ✅ Accepted

### Context

The working practice for this repository is that every change arrives as a pull
request, with CI required to pass. GitHub does not offer branch protection or
rulesets on private repositories under the free plan; it requires Pro or above.
The repository is private until the first phase is complete.

### Decision

Stay on the free plan. Keep the pull request workflow as a convention, and add
a local guard — `git config branch.main.pushRemote no_push` — so that an
accidental push to `main` fails on this machine. Enable real branch protection
when the repository is made public.

### Consequences

- A red CI check is information rather than a barrier. Nothing but discipline
  prevents a direct merge.
- The local guard is client-side and bypassable. It is there to catch
  absent-mindedness, not determined circumvention.
- No cost, and the workflow being practised is identical to the enforced one.

---

## 005 — Raw forecast archive: grain, key and what is not stored

**Date:** 2026-08-16 · **Status:** ✅ Accepted

### Context

Each poll of the 48-hour forecast returns roughly 96 half-hourly periods. The
point of the archive is to record how the forecast for a given period changed
as that period approached.

### Decision

- **Grain:** one row per settlement period, per poll.
- **Key:** `(retrieved_at, period_start)` — both values are given, neither is
  derived.
- **`retrieved_at` is captured once per poll**, in the ingestion code,
  immediately before the HTTP request, and written identically to every row in
  that batch.
- **The forecast horizon is not stored.** It is derived downstream as
  `period_start − retrieved_at`.
- **Append-only.** No updates, no deletes.
- The `actual` field is stored even though it is always null in this table,
  because the raw layer records what the source returned. Actual outturn is
  permanently re-fetchable and is ingested by a separate job.
- **Table Structure raw.carbon_intensity_forecast**
  forecast is not nullable as this is a required field for the job to fail
  intesity index is nullable as this is not a trusted field when bands change. 
### Consequences

- A poll is identifiable as a poll. If `retrieved_at` were generated per row,
  the batch would dissolve into 96 near-identical timestamps and the archive
  would lose the thing it exists to record.
- Airflow's logical date must **not** be used for `retrieved_at`. A retried
  task would then be stamped with its scheduled time rather than its actual
  execution time, corrupting every horizon calculation derived from it.
- Storage is roughly 250 MB per year. Downsampling will never be necessary,
  so every revision is kept indefinitely.
