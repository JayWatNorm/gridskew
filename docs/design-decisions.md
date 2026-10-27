# Design decisions

The main choices that shape GridSkew, and why. Each one links to the page
that holds the detail.

## Capturing the data

**Every version is kept; nothing is overwritten.** The sources revise their
data, and the forecast is replaced every half-hour. Each capture is stored
as a new row with the time it was fetched; where the source labels its own
versions, as settlement runs do, reading the same version again adds
nothing. A revision is something to measure rather than something lost.
[Raw layer conventions](README.md#raw-layer-conventions)

**The forecast is polled every half-hour and can never be backfilled.** The
Carbon Intensity API only ever shows its latest forecast, so a missed poll
is a version gone for good.
[Forecast ingestion](sources/carbon-intensity/011_forecast_ingestion.md)

**Settlement runs are captured at fixed ages.** Elexon serves only the
settlement run currently in force for a date, so each run is fetched while
it is available: the interim run (II) at 14 days, the initial settlement run
(SF) at 35 and the final reconciliation run (RF) at 450. A run missed in its
window cannot be fetched later.
[B1610 ingestion](sources/elexon/021_b1610_ingestion.md)

**Some pollers decide what to fetch from what is already stored.** REMIT
asks for everything since its newest stored message; the outturn loads a
year into an empty table, then looks back seven days each run and fills any
older gap on Mondays. A run after an outage catches up by itself, and the
first load and the daily load use the same code.
[REMIT ingestion](sources/elexon/031_remit_ingestion.md),
[outturn ingestion](sources/carbon-intensity/021_outturn_ingestion.md)

**A row that breaks its source's rules is kept aside, not dropped.** It goes
to a quarantine table with the request and the full payload, so nothing is
lost silently and the cause can be fixed and the data reloaded.
[Endpoint validation](sources/endpoint-validation.md)

## Modelling

**Clock changes are handled by the database's own time zone rules.** A UK
settlement day has 46 or 50 half-hours when the clocks change. PostgreSQL's
`Europe/London` rules work these out for any year, so no dates are written
into the code.
[Settlement periods](../dbt/README.md#settlement-periods)

**Each night recomputes only the half-hours whose inputs have changed.** It
looks back three days from the newest data already processed and rebuilds,
from all of its rows, every half-hour with a new capture in that window.
The shortfall table also rebuilds every half-hour of a unit whose registry
details changed, and every half-hour of a date with a new ABSVD capture,
because ABSVD can change any unit on that date. This gives the same result
as rebuilding everything because:

- raw tables only ever gain rows, so a half-hour's inputs change only when a
  new row arrives;
- every input a half-hour depends on is one of those checked: its own
  captures, its unit's registry entry and its date's ABSVD;
- a scheduled load finishes within 20 minutes, far inside the three days.

Two simpler options were measured and rejected: comparing all history every
night took longer than rebuilding from scratch, and a fixed date window would
miss late settlement runs for old dates. A load made outside the scheduled
jobs is not covered by the rule, so it is followed by a full rebuild. The
automated tests check that the nightly method and a full rebuild give
identical tables.
[Period facts](../dbt/README.md#period-facts)

**Facts are views (saved queries) over stored tables, joined to the current
registry.** The heavy half-hour work is stored once; the unit details (fuel
type, mapping) are joined when the fact is read. A registry change therefore
shows at once without rewriting any history.
[BM-unit registry lineage](../dbt/README.md#bm-unit-registry-lineage)

**Shortfall compares expected output with metered output.** Expected output
is the unit's physical notification (PN), with the instructed level in its
place for the minutes an instruction was in force; shortfall is that minus
the metered energy. QPN is not subtracted: the Balancing and Settlement Code
does not use it in Settlement.
[QPN notes](sources/elexon/015_qpn.md)

**When instructions overlap, the latest one counts for the minutes it
covers.** A later acceptance (an instruction from the grid operator to
change output) replaces an earlier one only where they overlap, and the
notified level applies where no acceptance does. This is how the balancing
mechanism itself settles them.
[Shortfall](../dbt/README.md#shortfall)

**An outage notice's current version is its latest publication.** Under
REMIT a participant updates a notice by publishing it again; revision
numbers are the participant's own counter and do not always rise.
[REMIT](sources/elexon/030_remit.md)

## Answering the questions

**Each question's reading rule is fixed before its data is read, and each
result is read once.** A result cannot then be tuned after it has been
seen. Each one-look analysis states its rule at the top of its file, and
[research questions](research-questions.md) lists every rule with its
status. Q24's measure was amended after a first read of the data, so Q24 is
not called pre-registered; Q25's first measure and Q23's ABSVD sensitivity
also follow an exploration, and are labelled so.
[Carbon forecast trajectory](carbon-forecast-trajectory.md),
[one-look analyses](../dbt/README.md#one-look-analyses)

**Results tables are built by hand at a fixed cutoff.** The shortfall and
coverage results come from two tables rebuilt together when the results are
refreshed, each recording its cutoff. No scheduled job or full refresh
rebuilds them. The cutoff (`analysis_read_until`)
fixes which settlement periods count, not which data is read: a later build
reads later settlement runs and refreshed sources for the same periods, so
it can give different figures. Each build therefore runs at least 14 days
after its cutoff, when every period has its interim run (II), and saves with
the figures it reports its build time, its outputs and the inputs it read:
the latest capture time of each source, which is enough to rebuild them,
since every source keeps its captures. Where a source cannot be rebuilt as
it was, the record says so.
[Shortfall](../dbt/README.md#shortfall)

## Running it

**Scheduled jobs change data; releases change structure.** Rebuilding a
view also drops the views that depend on it, so the nightly job only updates
tables and runs tests, and views change only in a release.
[Period facts](../dbt/README.md#period-facts)

**A release pauses only what it changes.** The forecast is paused last, and
only when at least ten minutes remain before its next poll, so a release
rarely costs a forecast version.
[Deployment](deployment.md)

**The raw schema is versioned by migrations.** Each change is a numbered
file, applied once, so the record of what has run lives with the code.
[Schema migrations](schema-migrations.md)

**Two tables that cannot be fetched again are copied; the rest is rebuilt.**
The forecast archive and the unit registry's history are copied nightly.
Data the sources still serve is fetched again after a loss, and dbt rebuilds
everything derived from it. Settlement runs Elexon has since replaced are
not copied.
[Operations](operations.md#if-the-database-is-lost)

**Failures are found by a daily check, not alerts.** A one-person research
project checks the Airflow UI each morning; each DAG's first action on a
failure is in the runbook.
[Operations](operations.md)

**The platform repository owns deployment.** Airflow and PostgreSQL run on
a shared homelab platform kept in its own repository, which mounts this
project's code. GridSkew ships no deployment files of its own, so there is
one way it runs.
[Deployment](deployment.md)
