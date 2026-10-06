"""S6 acceptance checks for the period models in a disposable PostgreSQL.

Loads small, controlled raw rows, runs the real dbt models and asserts the S6
plan revision 4 acceptance matrix: the incremental period tables recompute
only touched unit-periods and equal a full refresh; the fact views show
current registry evidence. It mutates the target database, so it refuses to
run unless explicitly pointed at a local disposable gridskew_dev.

Run from the repository root with the dbt environment set, for example:
    GRIDSKEW_DISPOSABLE_TEST=1 DBT_HOST=localhost DBT_DBNAME=gridskew_dev ...
    python -m tests.adhoc.check_s6_facts
"""

import os
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2 import errors

from tests.adhoc.load_ci_fixtures import run_ddl

PROJECT_DIR = Path(__file__).resolve().parents[2] / "dbt"
# The repository profile reads the guarded DBT_* variables; pinning it and the
# target stops an inherited DBT_PROFILES_DIR sending dbt to another database.
PROFILES_DIR = Path(__file__).resolve().parents[2] / "dbt_profiles"
B1610_PERIODS = "dbt_dev.int_elexon__b1610_period"
PN_PERIODS = "dbt_dev.int_elexon__pn_period_mwh"
GENERATION = "dbt_dev.fct_generation"
COMMITMENTS = "dbt_dev.fct_commitments"
LONDON = ZoneInfo("Europe/London")
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)
# A short margin makes the touched window exact for these fixtures: each run
# recomputes the keys captured within one hour of the stored watermark.
MARGIN_VARS = '{"capture_margin": "1 hour"}'

AUG10 = date(2026, 8, 10)
AUG11 = date(2026, 8, 11)
AUTUMN = date(2025, 10, 26)
SPRING = date(2026, 3, 29)


def disposable_connection():
    return psycopg2.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
        dbname=os.environ["DBT_DBNAME"],
    )


def dbt(*args, expect_success=True):
    result = subprocess.run(
        [
            "dbt",
            *args,
            "--vars",
            MARGIN_VARS,
            "--project-dir",
            str(PROJECT_DIR),
            "--profiles-dir",
            str(PROFILES_DIR),
            "--target",
            "dev",
        ],
        capture_output=True,
        text=True,
    )
    if (result.returncode == 0) != expect_success:
        print(result.stdout[-4000:], result.stderr[-2000:])
        raise AssertionError(f"dbt {' '.join(args)} returned {result.returncode}")
    return result


def fetch(conn, query, params=()):
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def execute(conn, query, params=()):
    with conn.cursor() as cursor:
        cursor.execute(query, params)


def exists(conn, relation):
    return fetch(conn, "SELECT to_regclass(%s)", (relation,))[0][0] is not None


def table_rows(conn, table):
    """Every column keyed by the first three (the unit-period key)."""
    return {row[:3]: row[3:] for row in fetch(conn, f"SELECT * FROM {table}")}


def row_versions(conn, table):
    """A rewritten row gets a new xmin; an untouched row keeps its own."""
    if not exists(conn, table):
        return {}
    return {
        row[:3]: row[-1] for row in fetch(conn, f"SELECT *, xmin::text FROM {table}")
    }


def rewritten(before, after):
    return {key for key, version in after.items() if before.get(key) != version}


def build(conn, fact, table, expected_rewrites=None, expect_success=True):
    """Build a fact and its upstream; optionally check which rows were rewritten."""
    before = row_versions(conn, table)
    dbt("build", "--select", f"+{fact}", expect_success=expect_success)
    if expected_rewrites is not None:
        changed = rewritten(before, row_versions(conn, table))
        assert changed == expected_rewrites, f"rewrote {sorted(changed)}"


def assert_equals_full_refresh(conn, fact, table):
    """The incremental table equals a full rebuild of the same data, and a full
    refresh selected with its descendants keeps the fact view."""
    incremental = table_rows(conn, table)
    dbt("build", "--select", f"+{fact}", "--full-refresh")
    assert table_rows(conn, table) == incremental, "incremental != full refresh"
    assert exists(conn, f"dbt_dev.{fact}"), f"{fact} view missing after refresh"


def assert_indexes(conn, table, key_columns):
    name = table.split(".")[1]
    definitions = [
        d
        for (d,) in fetch(
            conn,
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'dbt_dev' AND tablename = %s",
            (name,),
        )
    ]
    assert any("UNIQUE" in d and f"({key_columns})" in d for d in definitions), (
        f"unique key index missing on {table}: {definitions}"
    )
    assert any("(last_captured_at)" in d for d in definitions), (
        f"watermark index missing on {table}: {definitions}"
    )


def assert_duplicate_rejected(conn, table):
    row = fetch(conn, f"SELECT * FROM {table} LIMIT 1")[0]
    placeholders = ", ".join(["%s"] * len(row))
    try:
        execute(conn, f"INSERT INTO {table} VALUES ({placeholders})", row)
    except errors.UniqueViolation:
        return
    raise AssertionError(f"duplicate key accepted by {table}")


def insert_registry(conn, units, step):
    """Commit one complete registry extract; the latest extract is current."""
    extract_id = str(uuid4())
    execute(
        conn,
        "INSERT INTO raw.elexon_bm_units_extracts "
        "(extract_id, retrieved_at, row_count, unit_count) VALUES (%s, %s, %s, %s)",
        (
            extract_id,
            T0 + timedelta(minutes=step),
            len(units),
            len({ng for ng, _, _ in units}),
        ),
    )
    for index, (ng_unit, elexon_unit, fuel) in enumerate(units):
        execute(
            conn,
            "INSERT INTO raw.elexon_bm_units (extract_id, source_index, "
            "national_grid_bm_unit, elexon_bm_unit, fuel_type, bm_unit_type, "
            "production_or_consumption_flag, credit_qualifying_status, "
            "demand_in_production_flag) "
            "VALUES (%s, %s, %s, %s, %s, 'T', 'P', false, false)",
            (extract_id, index, ng_unit, elexon_unit, fuel),
        )


REGISTRY = [
    ("S6NG_A", "S6_A", "CCGT"),
    ("S6NG_C1", "S6_C", "WIND"),
    ("S6NG_C2", "S6_C", "WIND"),
    ("S6NG_D", "S6_D", "NUCLEAR"),
]


# ---------------------------------------------------------------- B1610


def insert_b1610(conn, rows):
    for bm_unit, ng_id, settlement_date, period, run, quantity, seen in rows:
        execute(
            conn,
            "INSERT INTO raw.elexon_b1610 (bm_unit, national_grid_bm_unit_id, "
            "psr_type, settlement_date, settlement_period, half_hour_end_time, "
            "settlement_run_type, quantity, retrieved_at) "
            "VALUES (%s, %s, 'Generation', %s, %s, %s, %s, %s, %s)",
            (
                bm_unit,
                ng_id,
                settlement_date,
                period,
                T0,  # Not used by S6; the period start comes from date/period.
                run,
                Decimal(quantity),
                seen,
            ),
        )


def generation(conn):
    rows = fetch(
        conn,
        "SELECT bm_unit, settlement_date, settlement_period, period_start_utc, "
        "first_quantity_mwh, first_run_code, latest_quantity_mwh, latest_run_code, "
        "mapping_status, mapped_national_grid_bm_unit, current_fuel_type "
        f"FROM {GENERATION}",
    )
    return {row[:3]: row[3:] for row in rows}


B1610_ROWS = [
    # II -> SF -> R1: first is II, latest is R1.
    ("S6_A", None, AUG10, 1, "II", "48", T0),
    ("S6_A", None, AUG10, 1, "SF", "50", T0 + DAY),
    ("S6_A", None, AUG10, 1, "R1", "52", T0 + 2 * DAY),
    # SF captured before II: first captured is SF, latest is SF.
    ("S6_A", None, AUG10, 2, "SF", "10", T0),
    ("S6_A", None, AUG10, 2, "II", "9", T0 + DAY),
    # Same capture time: run order breaks the tie.
    ("S6_A", None, AUG10, 3, "II", "5", T0),
    ("S6_A", None, AUG10, 3, "SF", "6", T0),
    # Backfilled SF only; a late-visible R1 is added later.
    ("S6_A", None, AUG10, 4, "SF", "0", T0 + DAY),
    # Clock changes: autumn has periods 49-50, spring ends at 46.
    ("S6_A", None, AUTUMN, 49, "II", "1", T0),
    ("S6_A", None, AUTUMN, 50, "II", "1", T0),
    ("S6_A", None, SPRING, 46, "II", "1", T0),
    # Unmapped (negative kept), ambiguous and conflicting identifiers.
    ("S6_B", None, AUG10, 1, "II", "-3.5", T0),
    ("S6_C", None, AUG10, 1, "II", "1", T0),
    ("S6_D", "S6NG_WRONG", AUG10, 1, "II", "2", T0),
    # Delivered National Grid ID differs from the registry by letter case only.
    ("S6_A", "s6ng_a", AUG10, 5, "II", "4", T0),
]


def check_generation(conn):
    insert_registry(conn, REGISTRY, step=0)

    def build_b1610(expected_rewrites=None, expect_success=True):
        build(conn, "fct_generation", B1610_PERIODS, expected_rewrites, expect_success)

    # Existing empty target: the first build creates an empty table; the next
    # incremental run must still fill it (coalesce to -infinity).
    build_b1610()
    assert exists(conn, B1610_PERIODS) and not table_rows(conn, B1610_PERIODS)
    insert_b1610(conn, B1610_ROWS)
    build_b1610()
    facts = generation(conn)
    assert len(facts) == 11, f"expected 11 unit-periods, got {len(facts)}"
    assert_indexes(conn, B1610_PERIODS, "bm_unit, settlement_date, settlement_period")

    def row(unit, day, period):
        return facts[(unit, day, period)]

    assert row("S6_A", AUG10, 1)[1:5] == (Decimal(48), "II", Decimal(52), "R1")
    assert row("S6_A", AUG10, 2)[1:5] == (Decimal(10), "SF", Decimal(10), "SF")
    assert row("S6_A", AUG10, 3)[1:5] == (Decimal(5), "II", Decimal(6), "SF")
    assert row("S6_A", AUG10, 4)[1:5] == (Decimal(0), "SF", Decimal(0), "SF")
    assert row("S6_A", AUG10, 1)[5:] == ("mapped", "S6NG_A", "CCGT")
    assert row("S6_B", AUG10, 1)[1] == Decimal("-3.5")
    assert row("S6_B", AUG10, 1)[5:] == ("unmapped", None, None)
    assert row("S6_C", AUG10, 1)[5:] == ("ambiguous", None, None)
    assert row("S6_D", AUG10, 1)[5:] == ("conflict", None, None)
    assert row("S6_A", AUG10, 5)[5:] == ("mapped", "S6NG_A", "CCGT")
    assert row("S6_A", AUTUMN, 49)[0] == datetime(
        2025, 10, 26, 23, 0, tzinfo=timezone.utc
    )
    assert row("S6_A", AUTUMN, 50)[0] == datetime(
        2025, 10, 26, 23, 30, tzinfo=timezone.utc
    )
    assert row("S6_A", SPRING, 46)[0] == datetime(
        2026, 3, 29, 22, 30, tzinfo=timezone.utc
    )
    assert_equals_full_refresh(conn, "fct_generation", B1610_PERIODS)

    # Unchanged rerun: only keys captured within the margin of the watermark
    # (T0 + 2 days) are recomputed, to the same values.
    a1, a2, a3, a4 = (("S6_A", AUG10, p) for p in (1, 2, 3, 4))
    build_b1610(expected_rewrites={a1})
    assert generation(conn) == facts

    # Late-visible row older than the watermark but inside the margin.
    insert_b1610(
        conn, [("S6_A", None, AUG10, 4, "R1", "7", T0 + 2 * DAY - 30 * MINUTE)]
    )
    build_b1610(expected_rewrites={a1, a4})
    assert generation(conn)[a4][1:5] == (Decimal(0), "SF", Decimal(7), "R1")
    assert_equals_full_refresh(conn, "fct_generation", B1610_PERIODS)

    # Documented limit: a row older than the margin is not picked up by an
    # incremental run; a full refresh picks it up.
    insert_b1610(conn, [("S6_A", None, AUG10, 3, "R2", "8", T0)])
    build_b1610()
    assert generation(conn)[a3][3:5] == (Decimal(6), "SF")
    dbt("build", "--select", "+fct_generation", "--full-refresh")
    assert generation(conn)[a3][3:5] == (Decimal(8), "R2")

    # Normal arrivals: the window is measured from the watermark stored before
    # the build, so the previous window's key (a4) is recomputed as well.
    insert_b1610(
        conn,
        [
            ("S6_A", None, AUG10, 1, "R2", "53", T0 + 3 * DAY),
            ("S6_A", None, AUG10, 2, "R1", "11", T0 + 3 * DAY - 10 * MINUTE),
        ],
    )
    build_b1610(expected_rewrites={a1, a2, a4})
    facts = generation(conn)
    assert facts[a1][3:5] == (Decimal(53), "R2")
    assert facts[a2][3:5] == (Decimal(11), "R1")

    # Registry-only change: the fact view shows it at once; no period row is
    # rewritten, and the next build recomputes only the margin window.
    before = row_versions(conn, B1610_PERIODS)
    insert_registry(conn, [("S6NG_A", "S6_A", "NUCLEAR"), *REGISTRY[1:]], step=1)
    assert generation(conn)[a1][7] == "NUCLEAR"
    assert row_versions(conn, B1610_PERIODS) == before
    build_b1610(expected_rewrites={a1, a2})

    # A recent key missing from the table fails the nightly check; the next
    # build restores it.
    execute(
        conn,
        f"DELETE FROM {B1610_PERIODS} WHERE bm_unit = 'S6_A' "
        "AND settlement_date = %s AND settlement_period = 2",
        (AUG10,),
    )
    dbt(
        "test",
        "--select",
        "assert_b1610_period_recent_keys_present",
        expect_success=False,
    )
    build_b1610()
    assert a2 in generation(conn)

    # Unknown run code: the staging test fails and the table is unchanged. The
    # failed build dropped the dependent views (DROP ... CASCADE); a clean
    # build restores them.
    stored = table_rows(conn, B1610_PERIODS)
    insert_b1610(conn, [("S6_A", None, AUG11, 1, "XX", "1", T0 + 4 * DAY)])
    build_b1610(expect_success=False)
    assert table_rows(conn, B1610_PERIODS) == stored
    execute(conn, "DELETE FROM raw.elexon_b1610 WHERE settlement_run_type = 'XX'")
    build_b1610()
    assert table_rows(conn, B1610_PERIODS) == stored

    assert_duplicate_rejected(conn, B1610_PERIODS)
    assert_equals_full_refresh(conn, "fct_generation", B1610_PERIODS)
    print("B1610 period table and fct_generation: all acceptance checks passed")


# ---------------------------------------------------------------- PN


def period_start(settlement_date, period):
    """UTC start of a settlement period, stepping in elapsed time from local midnight."""
    midnight = datetime.combine(settlement_date, time(), tzinfo=LONDON)
    return midnight.astimezone(timezone.utc) + (period - 1) * timedelta(minutes=30)


def insert_pn(conn, captures):
    """Each capture: (unit, bm_unit, date, period, seen, [(from, to, MW, MW)])."""
    for unit, bm_unit, settlement_date, period, seen, segments in captures:
        start = period_start(settlement_date, period)
        for minute_from, minute_to, level_from, level_to in segments:
            execute(
                conn,
                "INSERT INTO raw.elexon_pn (bm_unit, national_grid_bm_unit, "
                "settlement_date, settlement_period, time_from, time_to, "
                "level_from, level_to, retrieved_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    bm_unit,
                    unit,
                    settlement_date,
                    period,
                    start + minute_from * MINUTE,
                    start + minute_to * MINUTE,
                    level_from,
                    level_to,
                    seen,
                ),
            )


def commitments(conn):
    rows = fetch(
        conn,
        "SELECT national_grid_bm_unit, settlement_date, settlement_period, "
        "period_start_utc, pn_mwh, coverage_status, segment_count, pn_retrieved_at, "
        "mapping_status, mapped_elexon_bm_unit, current_fuel_type "
        f"FROM {COMMITMENTS}",
    )
    return {row[:3]: row[3:] for row in rows}


def close(actual, expected):
    """Numeric division rounds, so compare MWh to a tight tolerance."""
    return actual is not None and abs(actual - expected) < Decimal("1e-9")


def full_period(level):
    return [(0, 30, level, level)]


PN_CAPTURES = [
    # Two-segment ramp: 0->100 MW for 1 minute, then 100->106 MW for 29.
    ("S6NG_A", "S6_A", AUG10, 1, T0, [(0, 1, 0, 100), (1, 30, 100, 106)]),
    # Zero and negative MW are valid.
    ("S6NG_A", "S6_A", AUG10, 2, T0, full_period(0)),
    ("S6NG_A", None, AUG10, 3, T0, full_period(-60)),
    # Retry replaces two segments with one that leaves a gap: no old segment
    # is kept, so the period is incomplete with NULL MWh.
    ("S6NG_A", "S6_A", AUG10, 4, T0, [(0, 15, 10, 10), (15, 30, 10, 10)]),
    ("S6NG_A", "S6_A", AUG10, 4, T0 + DAY, [(0, 15, 20, 20)]),
    # Echo: the next poll only repeats the final segment; the full capture wins.
    ("S6NG_A", "S6_A", AUG10, 5, T0, [(0, 29, 827, 827), (29, 30, 827, 828)]),
    ("S6NG_A", "S6_A", AUG10, 5, T0 + DAY, [(29, 30, 827, 828)]),
    # Gap and overlap: incomplete.
    ("S6NG_A", "S6_A", AUG10, 6, T0, [(0, 10, 5, 5), (20, 30, 5, 5)]),
    ("S6NG_A", "S6_A", AUG10, 7, T0, [(0, 20, 5, 5), (10, 30, 5, 5)]),
    # Zero-duration segment and a segment beyond the period: invalid. Distinct
    # poll times keep their boundary segments off the neighbours' raw keys.
    ("S6NG_A", "S6_A", AUG10, 8, T0 + MINUTE, [(0, 30, 5, 5), (30, 30, 5, 5)]),
    ("S6NG_A", "S6_A", AUG10, 9, T0 + 2 * MINUTE, [(0, 30, 5, 5), (30, 35, 5, 5)]),
    # A later poll omits this unit: its older capture is used.
    ("S6NG_A", "S6_A", AUG10, 10, T0, full_period(2)),
    ("S6NG_Z", None, AUG10, 10, T0 + DAY, full_period(2)),
    # Clock changes: distinct UTC half-hours on 50- and 46-period days.
    ("S6NG_A", "S6_A", AUTUMN, 49, T0, full_period(2)),
    ("S6NG_A", "S6_A", AUTUMN, 50, T0, full_period(2)),
    ("S6NG_A", "S6_A", SPRING, 46, T0, full_period(2)),
    # Conflicting Elexon ID.
    ("S6NG_D", "S6_X", AUG10, 1, T0, full_period(2)),
    # One capture whose segments carry two different Elexon IDs: a conflict,
    # not the minimum of the two.
    ("S6NG_A", "S6_A", AUG10, 11, T0, [(0, 15, 3, 3)]),
    ("S6NG_A", "S6_Z", AUG10, 11, T0, [(15, 30, 3, 3)]),
    # Period 47 does not exist on the 46-period spring day: no start time, so
    # the period is invalid, never complete.
    ("S6NG_A", "S6_A", SPRING, 47, T0, full_period(2)),
]


def check_commitments(conn):
    def build_pn(expected_rewrites=None):
        build(conn, "fct_commitments", PN_PERIODS, expected_rewrites)

    # Existing empty target, then arrivals.
    build_pn()
    assert exists(conn, PN_PERIODS) and not table_rows(conn, PN_PERIODS)
    insert_pn(conn, PN_CAPTURES)
    build_pn()
    facts = commitments(conn)
    assert len(facts) == 17, f"expected 17 unit-periods, got {len(facts)}"
    assert_indexes(
        conn, PN_PERIODS, "national_grid_bm_unit, settlement_date, settlement_period"
    )

    def row(unit, day, period):
        return facts[(unit, day, period)]

    # (period_start, pn_mwh, status, segment_count, pn_retrieved_at, ...)
    assert close(row("S6NG_A", AUG10, 1)[1], Decimal(50) + Decimal(37) / 60)
    assert row("S6NG_A", AUG10, 1)[2] == "complete"
    assert row("S6NG_A", AUG10, 2)[1:3] == (Decimal(0), "complete")
    assert row("S6NG_A", AUG10, 3)[1:3] == (Decimal(-30), "complete")
    assert row("S6NG_A", AUG10, 4)[1:5] == (None, "incomplete", 1, T0 + DAY)
    assert close(
        row("S6NG_A", AUG10, 5)[1], Decimal(827) * 29 / 60 + Decimal("827.5") / 60
    )
    assert row("S6NG_A", AUG10, 5)[2:5] == ("complete", 2, T0)
    assert row("S6NG_A", AUG10, 6)[1:3] == (None, "incomplete")
    assert row("S6NG_A", AUG10, 7)[1:3] == (None, "incomplete")
    assert row("S6NG_A", AUG10, 8)[1:3] == (None, "invalid")
    assert row("S6NG_A", AUG10, 9)[1:3] == (None, "invalid")
    assert row("S6NG_A", AUG10, 10)[4] == T0
    assert row("S6NG_A", AUTUMN, 49)[0] == datetime(
        2025, 10, 26, 23, 0, tzinfo=timezone.utc
    )
    assert row("S6NG_A", AUTUMN, 50)[0] == datetime(
        2025, 10, 26, 23, 30, tzinfo=timezone.utc
    )
    assert row("S6NG_A", SPRING, 46)[0] == datetime(
        2026, 3, 29, 22, 30, tzinfo=timezone.utc
    )
    assert row("S6NG_A", AUG10, 3)[5:7] == ("mapped", "S6_A")
    assert row("S6NG_Z", AUG10, 10)[5:] == ("unmapped", None, None)
    assert row("S6NG_D", AUG10, 1)[5:] == ("conflict", None, None)
    assert row("S6NG_A", AUG10, 11)[5:] == ("conflict", None, None)
    assert row("S6NG_A", SPRING, 47)[:3] == (None, None, "invalid")

    # The echo capture advances last_captured_at without changing the choice.
    p5 = ("S6NG_A", AUG10, 5)
    assert table_rows(conn, PN_PERIODS)[p5][-1] == T0 + DAY
    assert_equals_full_refresh(conn, "fct_commitments", PN_PERIODS)

    # Unchanged rerun: only keys captured within the margin of the watermark
    # (T0 + 1 day: the retry, the echo and unit Z) are recomputed.
    p2, p3, p4 = (("S6NG_A", AUG10, p) for p in (2, 3, 4))
    build_pn(expected_rewrites={p4, p5, ("S6NG_Z", AUG10, 10)})
    assert commitments(conn) == facts

    # New captures with new values. The window is measured from the watermark
    # stored before the build, so the previous window's keys are recomputed too.
    insert_pn(
        conn,
        [
            ("S6NG_A", "S6_A", AUG10, 2, T0 + 2 * DAY, full_period(4)),
            ("S6NG_A", "S6_A", AUG10, 3, T0 + 2 * DAY - 10 * MINUTE, full_period(-40)),
        ],
    )
    build_pn(expected_rewrites={p2, p3, p4, p5, ("S6NG_Z", AUG10, 10)})
    facts = commitments(conn)
    assert facts[p2][1:3] == (Decimal(2), "complete")
    assert facts[p3][1:3] == (Decimal(-20), "complete")

    # Registry-only change: shown at once, with no rewrite.
    before = row_versions(conn, PN_PERIODS)
    insert_registry(conn, [("S6NG_A", "S6_A", "CCGT"), *REGISTRY[1:]], step=2)
    assert commitments(conn)[p2][7] == "CCGT"
    assert row_versions(conn, PN_PERIODS) == before

    # A recent key missing from the table fails the nightly check; the next
    # build restores it.
    execute(
        conn,
        f"DELETE FROM {PN_PERIODS} WHERE national_grid_bm_unit = 'S6NG_A' "
        "AND settlement_date = %s AND settlement_period = 3",
        (AUG10,),
    )
    dbt(
        "test", "--select", "assert_pn_period_recent_keys_present", expect_success=False
    )
    build_pn()
    assert p3 in commitments(conn)

    assert_duplicate_rejected(conn, PN_PERIODS)
    assert_equals_full_refresh(conn, "fct_commitments", PN_PERIODS)
    print("PN period table and fct_commitments: all acceptance checks passed")


def check_job_commands_keep_views(conn):
    """Scheduled jobs write data; only releases recreate views. Rebuilding a
    view drops its dependants (DROP ... CASCADE), so the nightly S6 commands and
    the registry job's checks must leave every view in place."""
    # Release: create every view once. (The earlier `build +fct_commitments`
    # rebuilt the registry view and so dropped fct_generation, which is the
    # hazard this check guards against.)
    dbt("seed", "--select", "elexon_fuel_codes")
    dbt("run", "--select", "dim_bm_unit", "fct_generation", "fct_commitments")
    views = (GENERATION, COMMITMENTS, "dbt_dev.dim_bm_unit")
    assert all(exists(conn, view) for view in views)
    # Nightly S6 job.
    dbt("test", "--select", "stg_elexon__b1610,test_type:generic")
    dbt("run", "--select", "int_elexon__b1610_period", "int_elexon__pn_period_mwh")
    dbt(
        "test",
        "--select",
        "int_elexon__b1610_period+",
        "int_elexon__pn_period_mwh+",
        "--exclude",
        "tag:full_population",
    )
    # Registry job checks (test, not build).
    dbt("test", "--select", "+dim_bm_unit")
    missing = [view for view in views if not exists(conn, view)]
    assert not missing, f"views dropped by scheduled commands: {missing}"
    print("Scheduled job commands: all views kept")


def main():
    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError(
            "S6 checks require opt-in to a local disposable gridskew_dev"
        )
    conn = disposable_connection()
    # Autocommit: an open read transaction would hold a lock on a model and
    # block dbt's table swap indefinitely.
    conn.autocommit = True
    try:
        run_ddl(conn)
        check_generation(conn)
        check_commitments(conn)
        check_job_commands_keep_views(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
