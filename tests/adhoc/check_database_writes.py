"""Read back by column name what the loaders and the quarantine write, and run
the B1610 coverage, REMIT quarantine and wind capture queries against a real
database.

The unit tests replace the database with mocks, so they cannot show which
column a value lands in, that a repeated load is ignored, or what a query
counts. Each read-back uses a second connection, so it also proves the
writer committed.

The wind capture checks run the completeness and quarantine queries in UTC,
Europe/London, Etc/GMT-14 and Etc/GMT+12 sessions on rows they roll back;
check that the warning test warn_wind_capture_days_short names exactly the
days the poller's rule names, on rows inside its range, counting back from
today and from 28 October 2026; and run the capture functions twice across
a failure, with HTTP faked, on days in 2099.

The REMIT re-read checks (remit_reread_checks.py) run first: the real
re-read and hourly run across an outage and restart, two runs across a
failure and a malformed time, with HTTP faked, and the warning test
warn_remit_stored_late on fixed evaluation days in the four session zones.

The quarantine resolution checks run the runbook's steps on the real table:
a REMIT row stops counting once its message is stored, with its resolution
columns still null; an unresolved row fails the hourly run until it is
marked resolved by id; a quarantined WINDFOR day is held, with no request,
until its row is marked resolved, and is then asked for and loaded; a row
with one resolution column set is refused.

The loader role checks need the role gridskew_loader_test provisioned by
sql/provision/002_loader_role.sql and 004_loader_grants.sql, as CI does
before this runs, and psql on the path. The verification script passes on
it; as the loader, inserts succeed and every delete, update, truncate and
direct lock is refused, while the lock function takes the SHARE lock on
both registry tables; a role that exists with an update right makes the
creation script fail; a stray table, column or PUBLIC grant makes the
verification fail; the grants script re-run changes nothing and stops
before any grant on a role with a wrong flag; and the owner keeps the
update the resolution step and a payload change need.

The loaders commit, so in the CI `test` job this runs after every step that
reads the fixtures, and deletes its own rows at the end.

Run from the repository root with the dbt environment set, for example:
    GRIDSKEW_DISPOSABLE_TEST=1 DBT_HOST=localhost DBT_DBNAME=gridskew_dev ...
    python -m tests.adhoc.check_database_writes
"""

import json
import os
import subprocess
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import psycopg
import requests
from psycopg import sql
from psycopg.types.json import Jsonb

from ingestion.carbon_intensity.forecast_poller import load as load_forecast
from ingestion.carbon_intensity.forecast_poller import parse as parse_forecast
from ingestion.carbon_intensity.outturn_poller import load as load_outturn
from ingestion.carbon_intensity.outturn_poller import parse as parse_outturn
from ingestion.database import insert_rows
from ingestion.elexon import (
    demand_backfill,
    qas_poller,
    remit_poller,
    wind_backfill,
    wind_poller,
)
from ingestion.elexon.b1610_poller import (
    decimal_json_dumps,
    recent_unit_count,
    stored_coverage,
)
from ingestion.elexon.b1610_poller import load as load_b1610
from ingestion.elexon.b1610_poller import parse as parse_b1610
from ingestion.elexon.boalf_poller import load as load_boalf
from ingestion.elexon.boalf_poller import parse as parse_boalf
from ingestion.elexon.demand_poller import load_indo, load_ndf, parse_indo, parse_ndf
from ingestion.elexon.pn_poller import load as load_pn
from ingestion.elexon.pn_poller import parse as parse_pn
from ingestion.elexon.qpn_poller import load as load_qpn
from ingestion.elexon.qpn_poller import parse as parse_qpn
from ingestion.elexon.remit_poller import load as load_remit
from ingestion.elexon.remit_poller import (
    newest_stored_publish_time,
    quarantined_row_count,
)
from ingestion.elexon.remit_poller import parse as parse_remit
from ingestion.elexon.settlement_calendar import expected_period_count
from ingestion.elexon.wind_poller import (
    load_fuelhh,
    load_windfor,
    parse_fuelhh,
    parse_windfor,
)
from ingestion.routing import ContractRejected, quarantine_rows
from tests.adhoc.check_analyses_compile import compile_analyses
from tests.adhoc.remit_reread_checks import run_remit_reread_checks

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
PROVISION_DIR = Path(__file__).resolve().parents[2] / "sql" / "provision"
# The loader role provisioned before this check runs, as CI does; the owner
# of every raw table; and the roles the provisioning checks create and drop.
LOADER_ROLE = "gridskew_loader_test"
OWNER_ROLE = "gridskew"
PRIOR_ROLE = "gridskew_loader_prior"
WRONG_FLAGS_ROLE = "gridskew_loader_wrong_flags"
INSUFFICIENT_PRIVILEGE = "42501"
# A capture time no fixture uses, so every row this check writes is its own.
CHECK_CAPTURE = datetime(2000, 1, 1, tzinfo=timezone.utc)
CHECK_CONTEXT = {"source": "ci_read_back"}
B1610_DATE = date(2025, 1, 10)
B1610_UNIT_PREFIX = "CI_RB_"
QAS_REJECT_DATE = date(2025, 1, 10)

# The columns in the order each parser builds its tuples.
PN_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "time_from",
    "time_to",
    "level_from",
    "level_to",
    "national_grid_bm_unit",
    "bm_unit",
    "retrieved_at",
)
BOALF_COLUMNS = (
    "national_grid_bm_unit",
    "bm_unit",
    "acceptance_number",
    "acceptance_time",
    "settlement_date",
    "settlement_period_from",
    "settlement_period_to",
    "time_from",
    "time_to",
    "level_from",
    "level_to",
    "so_flag",
    "deemed_bo_flag",
    "stor_flag",
    "rr_flag",
    "amendment_flag",
    "retrieved_at",
)
REMIT_COLUMNS = (
    "mrid",
    "revision_number",
    "publish_time",
    "created_time",
    "message_type",
    "event_type",
    "unavailability_type",
    "event_status",
    "asset_id",
    "affected_unit",
    "fuel_type",
    "normal_capacity",
    "available_capacity",
    "unavailable_capacity",
    "event_start_time",
    "event_end_time",
    "payload",
    "retrieved_at",
)
QAS_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "national_grid_bm_unit",
    "bm_unit",
    "bm_unit_applicable_balancing_services_volume",
    "retrieved_at",
)
NDF_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "boundary",
    "publish_time",
    "demand",
    "retrieved_at",
)
INDO_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "publish_time",
    "demand",
    "retrieved_at",
)
# A UTC publish day after the moved fixture rows' (19 August 2000).
HELD_PUBLISH_DAY = date(2000, 8, 20)
WINDFOR_COLUMNS = ("start_time", "publish_time", "generation", "retrieved_at")
FUELHH_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "fuel_type",
    "publish_time",
    "generation",
    "retrieved_at",
)
# A summer day's eight WINDFOR issue times, UTC. The last falls on the next
# London day.
WINDFOR_ISSUE_TIMES = (
    "03:30",
    "05:30",
    "08:30",
    "10:30",
    "12:30",
    "16:30",
    "19:30",
    "23:30",
)
B1610_COLUMNS = (
    "bm_unit",
    "national_grid_bm_unit_id",
    "psr_type",
    "settlement_date",
    "settlement_period",
    "half_hour_end_time",
    "settlement_run_type",
    "quantity",
    "retrieved_at",
)
LONDON = ZoneInfo("Europe/London")
# At any hour one of the last two is on another date than UTC.
SESSION_ZONES = ("UTC", "Europe/London", "Etc/GMT-14", "Etc/GMT+12")
WIND_WARNING_TEST = "warn_wind_capture_days_short"
# Up to nine WINDFOR issues of one day; the eighth falls on the next London
# day in summer.
CHECK_ISSUE_TIMES = WINDFOR_ISSUE_TIMES + ("01:30",)
WINDFOR_INSERT = (
    "INSERT INTO raw.elexon_windfor (start_time, publish_time, generation, "
    "retrieved_at) VALUES %s"
)
FUELHH_INSERT = (
    "INSERT INTO raw.elexon_fuelhh (settlement_date, settlement_period, "
    "start_time, fuel_type, publish_time, generation, retrieved_at) VALUES %s"
)
QUARANTINE_INSERT = (
    "INSERT INTO raw.endpoint_quarantine (dataset, retrieved_at, request_context, "
    "validation_errors, observed_fields, payload) VALUES %s"
)
# The year-2000 days of the completeness and quarantine query checks. The
# autumn clock change of 2000 was on 29 October: publish days 27 to 29
# October expect 74 points, and 29 October has 50 periods; 26 March had 46.
AUGUST_10_2000 = date(2000, 8, 10)
AUGUST_11_2000 = date(2000, 8, 11)
AUGUST_12_2000 = date(2000, 8, 12)
AUGUST_13_2000 = date(2000, 8, 13)
OCTOBER_27_2000 = date(2000, 10, 27)
OCTOBER_28_2000 = date(2000, 10, 28)
SPRING_CHANGE_2000 = date(2000, 3, 26)
AUTUMN_CHANGE_2000 = date(2000, 10, 29)
OCTOBER_30_2000 = date(2000, 10, 30)
# The two-run sequence asks for days in 2099; the out-of-range values are in
# 2100. No fixture or other check uses either year, and the end of the check
# deletes every wind row and quarantine row from 2099 on.
# The warning test, counted back from this day, checks 28 September to
# 26 October 2026: summer days, the 74-point publish days 23 to 25 October
# and the 50-period date 25 October, whatever day the check runs.
WARNING_DAY_AFTER_CLOCK_CHANGE = date(2026, 10, 28)
SEQUENCE_YEARS_FROM = date(2099, 1, 1)
FIRST_RUN_TARGET = date(2099, 6, 10)
SECOND_RUN_TARGET = date(2099, 6, 11)
OUT_OF_RANGE_DAY = date(2100, 1, 1)
# The WINDFOR publish day quarantined, resolved and loaded by the resolution
# check; no other check asks for it.
RESOLUTION_DAY = date(2099, 8, 1)
# The quarantined REMIT message the resolution check stores, at its own time.
RELOADED_MRID = "CI-RB-RELOADED"


def disposable_connection(time_zone=None):
    """A connection to the disposable database; psycopg leaves out a None
    option, so the session keeps the server's time zone unless one is given."""

    options = f"-c TimeZone={time_zone}" if time_zone else None
    return psycopg.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
        dbname=os.environ["DBT_DBNAME"],
        options=options,
    )


def fixture_rows(relative_path):
    return json.loads((FIXTURES_DIR / relative_path).read_text(encoding="utf-8"))


def read_one_row(reader, query, params):
    """Return one row as a dictionary keyed by column name."""

    with reader.cursor() as cursor:
        cursor.execute(query, params)
        (row,) = cursor.fetchall()
        column_names = [column.name for column in cursor.description]
    reader.commit()
    return dict(zip(column_names, row, strict=True))


def check_quarantine_evidence(writer, reader):
    finding = {
        "row": {"bmUnit": "T_CHECK-1", "intensity": {"quantity": Decimal("123.4560")}},
        "index": 2,
        "errors": [{"field": "quantity", "error": "check"}],
    }
    quarantine_rows(
        [finding],
        writer,
        "CI_READ_BACK",
        CHECK_CAPTURE,
        CHECK_CONTEXT,
        payload_dumps=decimal_json_dumps,
    )

    stored = read_one_row(
        reader,
        "SELECT dataset, retrieved_at, request_context, validation_errors, "
        "observed_fields, payload -> 'intensity' ->> 'quantity' AS quantity "
        "FROM raw.endpoint_quarantine WHERE dataset = 'CI_READ_BACK'",
        (),
    )
    assert stored["retrieved_at"] == CHECK_CAPTURE
    assert stored["request_context"] == CHECK_CONTEXT
    assert stored["validation_errors"] == {
        "source_index": 2,
        "errors": [{"field": "quantity", "error": "check"}],
    }
    assert stored["observed_fields"] == ["bmUnit", "intensity", "intensity.quantity"]
    # Exactly the digits given: a float on the way would drop the last zero.
    assert stored["quantity"] == "123.4560"


def run_provision_script(script_name, loader_role):
    """Run one provision script with psql as the administrator, as CI does."""

    command = [
        "psql",
        "-h",
        os.environ["DBT_HOST"],
        "-p",
        os.environ["DBT_PORT"],
        "-U",
        os.environ["DBT_USER"],
        "-d",
        os.environ["DBT_DBNAME"],
        "-v",
        "ON_ERROR_STOP=1",
        "-v",
        f"loader_role={loader_role}",
        "-v",
        "expect_login=false",
        "-f",
        str(PROVISION_DIR / f"{script_name}.sql"),
    ]
    environment = dict(os.environ, PGPASSWORD=os.environ["DBT_PASSWORD"])
    return subprocess.run(command, env=environment, capture_output=True, text=True)


def provision_script_passes(script_name, loader_role):
    return run_provision_script(script_name, loader_role).returncode == 0


def set_local_role(cursor, role):
    cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))


def rolled_back_as(conn, role, statements, params=()):
    """Run the statements as role in one transaction, then roll it back;
    returns the rows and the row count of the last statement."""

    try:
        with conn.cursor() as cursor:
            set_local_role(cursor, role)
            for statement in statements:
                cursor.execute(statement, params)
            rows = []
            if cursor.description:
                rows = cursor.fetchall()
            row_count = cursor.rowcount
    finally:
        conn.rollback()
    return rows, row_count


def refused_as(conn, role, statement, params=()):
    """The statement is refused for role with InsufficientPrivilege, 42501."""

    try:
        with conn.cursor() as cursor:
            set_local_role(cursor, role)
            cursor.execute(statement, params)
    except psycopg.Error as error:
        conn.rollback()
        assert error.sqlstate == INSUFFICIENT_PRIVILEGE, (statement, error)
        return
    conn.rollback()
    raise AssertionError(f"{role} was allowed: {statement}")


def run_as_administrator(conn, statement):
    with conn.cursor() as cursor:
        cursor.execute(statement)
    conn.commit()


def create_role(conn, role, options):
    run_as_administrator(
        conn, sql.SQL("CREATE ROLE {} " + options).format(sql.Identifier(role))
    )


def drop_role(conn, role):
    run_as_administrator(
        conn,
        sql.SQL("DROP OWNED BY {role}; DROP ROLE {role}").format(
            role=sql.Identifier(role)
        ),
    )


def grant(conn, privilege, relation, grantee):
    run_as_administrator(conn, sql.SQL(f"GRANT {privilege} ON {relation} TO {grantee}"))


def revoke(conn, privilege, relation, grantee):
    run_as_administrator(
        conn, sql.SQL(f"REVOKE {privilege} ON {relation} FROM {grantee}")
    )


def table_grants_of(reader, role):
    with reader.cursor() as cursor:
        cursor.execute(
            "SELECT table_schema, table_name, privilege_type "
            "FROM information_schema.role_table_grants WHERE grantee = %s "
            "ORDER BY table_schema, table_name, privilege_type",
            (role,),
        )
        grants = cursor.fetchall()
    reader.commit()
    return grants


def share_locks_held(cursor):
    cursor.execute(
        "SELECT relation::regclass::text FROM pg_locks "
        "WHERE pid = pg_backend_pid() AND mode = 'ShareLock' ORDER BY 1"
    )
    return [relation for (relation,) in cursor.fetchall()]


def check_loader_inserts_and_cannot_change_rows(writer):
    """As the loader: an insert and a repeated insert with ON CONFLICT DO
    NOTHING succeed, on a source table and on the quarantine (its identity
    column draws from the sequence); delete, update and truncate on both are
    refused with 42501, as is a direct SHARE lock on the registry."""

    windfor_insert = (
        "INSERT INTO raw.elexon_windfor (start_time, publish_time, generation, "
        "retrieved_at) VALUES ('2099-12-31 00:00+00', '2099-12-31 00:00+00', 1, %s)"
    )
    _rows, inserted = rolled_back_as(
        writer, LOADER_ROLE, [windfor_insert], (CHECK_CAPTURE,)
    )
    assert inserted == 1, inserted
    _rows, ignored = rolled_back_as(
        writer,
        LOADER_ROLE,
        [windfor_insert, windfor_insert + " ON CONFLICT DO NOTHING"],
        (CHECK_CAPTURE,),
    )
    assert ignored == 0, ignored
    _rows, quarantined = rolled_back_as(
        writer,
        LOADER_ROLE,
        [QUARANTINE_INSERT.replace("%s", "(%s, %s, %s, %s, %s, %s)")],
        quarantine_row_naming("CI_READ_BACK", CHECK_CONTEXT),
    )
    assert quarantined == 1, quarantined

    for table in ("raw.elexon_windfor", "raw.endpoint_quarantine"):
        refused_as(
            writer,
            LOADER_ROLE,
            f"DELETE FROM {table} WHERE retrieved_at = %s",
            (CHECK_CAPTURE,),
        )
        refused_as(
            writer,
            LOADER_ROLE,
            f"UPDATE {table} SET retrieved_at = retrieved_at WHERE retrieved_at = %s",
            (CHECK_CAPTURE,),
        )
        refused_as(writer, LOADER_ROLE, f"TRUNCATE {table}")
    refused_as(
        writer,
        LOADER_ROLE,
        "LOCK TABLE raw.elexon_bm_units_extracts, raw.elexon_bm_units IN SHARE MODE",
    )


def check_loader_locks_the_registry_through_the_function(writer):
    """As the loader, raw.lock_bm_unit_registry() takes a SHARE lock on both
    registry tables, held by the session until it rolls back."""

    try:
        with writer.cursor() as cursor:
            set_local_role(cursor, LOADER_ROLE)
            cursor.execute("SELECT raw.lock_bm_unit_registry()")
            held = share_locks_held(cursor)
    finally:
        writer.rollback()
    assert held == ["raw.elexon_bm_units", "raw.elexon_bm_units_extracts"], held
    with writer.cursor() as cursor:
        released = share_locks_held(cursor)
    writer.rollback()
    assert released == [], released


def check_owner_keeps_the_update_right(writer):
    """As the owner, the resolution update and a payload update both succeed:
    the role change restrains the loader, not the owner, whose hand steps
    stay a convention."""

    resolution_update = (
        "UPDATE raw.endpoint_quarantine "
        "SET resolved_at = now(), resolved_by = 'owner: privilege check' "
        "WHERE dataset = 'CI_READ_BACK' AND resolved_at IS NULL"
    )
    _rows, resolved = rolled_back_as(writer, OWNER_ROLE, [resolution_update])
    assert resolved == 1, resolved
    payload_update = (
        "UPDATE raw.endpoint_quarantine SET payload = payload || '{\"changed\": true}' "
        "WHERE dataset = 'CI_READ_BACK'"
    )
    _rows, changed = rolled_back_as(writer, OWNER_ROLE, [payload_update])
    assert changed == 1, changed


def check_creation_refuses_a_role_that_exists(writer):
    """A role of the name that exists with an update right makes the creation
    script fail; dropped, creation, grants and verification succeed."""

    create_role(writer, PRIOR_ROLE, "NOLOGIN")
    grant(writer, "UPDATE", "raw.elexon_windfor", PRIOR_ROLE)
    try:
        assert not provision_script_passes("002_loader_role", PRIOR_ROLE)
    finally:
        drop_role(writer, PRIOR_ROLE)

    assert provision_script_passes("002_loader_role", PRIOR_ROLE)
    try:
        assert provision_script_passes("004_loader_grants", PRIOR_ROLE)
        assert provision_script_passes("003_verify_loader_role", PRIOR_ROLE)
    finally:
        drop_role(writer, PRIOR_ROLE)


def table_exists(reader, relation):
    found = read_one_row(
        reader, "SELECT to_regclass(%s) IS NOT NULL AS found", (relation,)
    )
    return found["found"]


def stray_grants_to_catch(reader):
    """A table grant, a column UPDATE grant, a column REFERENCES grant, a
    grant to PUBLIC and, where Flyway's history table exists, a column
    INSERT grant on it."""

    stray_grants = [
        ("UPDATE", "raw.elexon_windfor", LOADER_ROLE),
        ("UPDATE (payload)", "raw.endpoint_quarantine", LOADER_ROLE),
        ("REFERENCES (quarantine_id)", "raw.endpoint_quarantine", LOADER_ROLE),
        ("UPDATE", "raw.elexon_windfor", "PUBLIC"),
    ]
    if table_exists(reader, "raw.flyway_schema_history"):
        stray_grants.append(
            ("INSERT (installed_rank)", "raw.flyway_schema_history", LOADER_ROLE)
        )
    return stray_grants


def check_verification_catches_a_stray_grant(writer, reader):
    """Each stray grant makes the verification script fail; revoked, it
    passes again."""

    for privilege, relation, grantee in stray_grants_to_catch(reader):
        grant(writer, privilege, relation, grantee)
        try:
            verified = provision_script_passes("003_verify_loader_role", LOADER_ROLE)
        finally:
            revoke(writer, privilege, relation, grantee)
        assert not verified, f"a stray grant passed: {privilege} on {relation}"
    assert provision_script_passes("003_verify_loader_role", LOADER_ROLE)


def check_grants_rerun_changes_nothing(reader):
    before = table_grants_of(reader, LOADER_ROLE)
    assert provision_script_passes("004_loader_grants", LOADER_ROLE)
    assert table_grants_of(reader, LOADER_ROLE) == before
    assert provision_script_passes("003_verify_loader_role", LOADER_ROLE)


def check_grants_stop_on_a_role_with_a_wrong_flag(writer, reader):
    """The grants script refuses a role created with CREATEDB before it
    grants anything."""

    create_role(writer, WRONG_FLAGS_ROLE, "NOLOGIN CREATEDB")
    try:
        assert not provision_script_passes("004_loader_grants", WRONG_FLAGS_ROLE)
        assert table_grants_of(reader, WRONG_FLAGS_ROLE) == []
        usage = read_one_row(
            reader,
            "SELECT has_schema_privilege(%s, 'raw', 'USAGE') AS usage",
            (WRONG_FLAGS_ROLE,),
        )
        assert usage == {"usage": False}, usage
    finally:
        drop_role(writer, WRONG_FLAGS_ROLE)


def check_loader_role_privileges(writer, reader):
    """The provisioned loader role's effective rights, on the real tables."""

    assert provision_script_passes("003_verify_loader_role", LOADER_ROLE)
    check_loader_inserts_and_cannot_change_rows(writer)
    check_loader_locks_the_registry_through_the_function(writer)
    check_owner_keeps_the_update_right(writer)
    check_creation_refuses_a_role_that_exists(writer)
    check_verification_catches_a_stray_grant(writer, reader)
    check_grants_rerun_changes_nothing(reader)
    check_grants_stop_on_a_role_with_a_wrong_flag(writer, reader)


def check_outturn_load(writer, reader):
    parsed_rows = parse_outturn(
        fixture_rows("carbon_intensity/outturn.json")["data"], CHECK_CAPTURE
    )
    load_outturn(parsed_rows, writer)
    # Adjacent windows share their boundary period: a repeat must be ignored.
    load_outturn(parsed_rows, writer)

    counted = read_one_row(
        reader,
        "SELECT count(*) AS stored FROM raw.carbon_intensity_outturn "
        "WHERE retrieved_at = %s",
        (CHECK_CAPTURE,),
    )
    assert counted["stored"] == len(parsed_rows) == 7
    stored = read_one_row(
        reader,
        "SELECT period_end, actual, forecast_final, intensity_index "
        "FROM raw.carbon_intensity_outturn "
        "WHERE retrieved_at = %s AND period_start = %s",
        (CHECK_CAPTURE, datetime(2026, 7, 31, 23, 30, tzinfo=timezone.utc)),
    )
    assert stored == {
        "period_end": datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        "actual": 166,
        "forecast_final": 156,
        "intensity_index": "moderate",
    }


def check_forecast_load(writer, reader):
    source_rows = fixture_rows("carbon_intensity/forecast_fw48h.json")["data"]
    load_forecast(parse_forecast(source_rows, CHECK_CAPTURE), writer)

    stored = read_one_row(
        reader,
        "SELECT period_end, forecast, actual, intensity_index "
        "FROM raw.carbon_intensity_forecast "
        "WHERE retrieved_at = %s AND period_start = %s",
        (CHECK_CAPTURE, datetime(2026, 8, 17, 7, 30, tzinfo=timezone.utc)),
    )
    assert stored == {
        "period_end": datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc),
        "forecast": 228,
        "actual": None,
        "intensity_index": "high",
    }


def unresolved_quarantine_ids(reader, dataset, names_the_row):
    """The runbook's listing: the dataset's unresolved rows in id order, kept
    to those names_the_row accepts by their request context and payload."""

    with reader.cursor() as cursor:
        cursor.execute(
            "select quarantine_id, dataset, retrieved_at, request_context, "
            "validation_errors, payload from raw.endpoint_quarantine "
            "where dataset = %s and resolved_at is null order by quarantine_id",
            (dataset,),
        )
        listed = cursor.fetchall()
    reader.commit()
    ids = []
    for quarantine_id, _dataset, _retrieved_at, context, _errors, payload in listed:
        if names_the_row(context, payload):
            ids.append(quarantine_id)
    return ids


def mark_resolved(writer, quarantine_ids, resolved_by):
    """The runbook's update, by the ids read first; returns the rows marked."""

    with writer.cursor() as cursor:
        cursor.execute(
            "update raw.endpoint_quarantine "
            "set resolved_at = now(), resolved_by = %s "
            "where quarantine_id = any(%s) and resolved_at is null",
            (resolved_by, quarantine_ids),
        )
        marked = cursor.rowcount
    writer.commit()
    return marked


def resolution_columns(reader, quarantine_id):
    return read_one_row(
        reader,
        "select resolved_at, resolved_by from raw.endpoint_quarantine "
        "where quarantine_id = %s",
        (quarantine_id,),
    )


def check_a_half_resolved_row_is_refused(writer, quarantine_id):
    """The CHECK refuses resolved_at without resolved_by, and the reverse."""

    half_updates = (
        "update raw.endpoint_quarantine set resolved_at = now() "
        "where quarantine_id = %s",
        "update raw.endpoint_quarantine set resolved_by = 'owner: half' "
        "where quarantine_id = %s",
    )
    for half_update in half_updates:
        try:
            with writer.cursor() as cursor:
                cursor.execute(half_update, (quarantine_id,))
        except psycopg.errors.CheckViolation:
            writer.rollback()
            continue
        writer.rollback()
        raise AssertionError(f"a half-resolved row was accepted: {half_update}")


def has_mrid(mrid):
    def names_the_row(_context, payload):
        return payload["mrid"] == mrid

    return names_the_row


def run_the_hourly_remit_read_with_no_message(writer):
    """The real hourly run on the real tables, with Elexon serving nothing."""

    with (
        patch("ingestion.elexon.remit_poller.fetch", return_value=[]),
        patch("ingestion.elexon.remit_poller.time.sleep"),
    ):
        remit_poller.run(writer)


def check_remit_quarantine_count(writer, reader):
    """A quarantined message counts while it is unresolved and not stored.
    Once the same publication is stored it stops counting with its resolution
    columns still null; the hourly run fails for the one still counting until
    that row is listed and marked resolved by id, and then succeeds."""

    before = quarantined_row_count(writer)
    template = fixture_rows("elexon/remit_publications.json")[0]
    reloaded = deepcopy(template)
    reloaded["mrid"] = RELOADED_MRID
    given_up = deepcopy(template)
    given_up["mrid"] = "CI-RB-GIVEN-UP"
    findings = [
        {"row": reloaded, "index": 0, "errors": []},
        {"row": given_up, "index": 1, "errors": []},
    ]
    quarantine_rows(
        findings,
        writer,
        "REMIT",
        CHECK_CAPTURE,
        CHECK_CONTEXT,
        payload_dumps=decimal_json_dumps,
    )
    assert quarantined_row_count(writer) == before + 2

    load_remit(parse_remit([reloaded], datetime.now(timezone.utc)), writer)
    assert quarantined_row_count(writer) == before + 1
    (reloaded_id,) = unresolved_quarantine_ids(reader, "REMIT", has_mrid(RELOADED_MRID))
    assert resolution_columns(reader, reloaded_id) == {
        "resolved_at": None,
        "resolved_by": None,
    }

    try:
        run_the_hourly_remit_read_with_no_message(writer)
    except ContractRejected as error:
        assert f"{before + 1} REMIT rows are in quarantine" in str(error), error
    else:
        raise AssertionError("the hourly run succeeded with an unresolved row")

    (given_up_id,) = unresolved_quarantine_ids(
        reader, "REMIT", has_mrid("CI-RB-GIVEN-UP")
    )
    assert mark_resolved(writer, [given_up_id], "owner: check, message given up") == 1
    assert quarantined_row_count(writer) == before
    run_the_hourly_remit_read_with_no_message(writer)
    assert resolution_columns(reader, given_up_id)["resolved_by"] == (
        "owner: check, message given up"
    )


def stored_row_named(reader, table, columns, key_columns, parsed_row):
    """Read back the stored row with the parsed row's key, by column name."""

    expected = dict(zip(columns, parsed_row, strict=True))
    conditions = " AND ".join(f"{column} = %s" for column in key_columns)
    stored = read_one_row(
        reader,
        f"SELECT {', '.join(columns)} FROM {table} WHERE {conditions}",
        [expected[column] for column in key_columns],
    )
    return expected, stored


def check_elexon_load(
    writer,
    reader,
    table,
    source_rows,
    parse,
    load,
    columns,
    key_columns,
    unread=(),
):
    """Every value of every row reads back from the column of its name, and
    loading the same capture again adds nothing. Columns in unread are
    stored but not compared."""

    parsed_rows = parse(source_rows, CHECK_CAPTURE)
    load(parsed_rows, writer)
    load(parsed_rows, writer)

    expected = {}
    for parsed_row in parsed_rows:
        named = dict(zip(columns, parsed_row, strict=True))
        for column in unread:
            del named[column]
        # Within one load the first row of a repeated key is the one kept.
        expected.setdefault(tuple(named[column] for column in key_columns), named)
    read_columns = [column for column in columns if column not in unread]
    stored = {}
    with reader.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(read_columns)} FROM {table} WHERE retrieved_at = %s",
            (CHECK_CAPTURE,),
        )
        for row in cursor.fetchall():
            named = dict(zip(read_columns, row, strict=True))
            stored[tuple(named[column] for column in key_columns)] = named
    reader.commit()
    assert stored == expected, f"{table}: stored rows differ from the parsed rows"


def boalf_rows_with_distinct_flags():
    """The fixture rows and two more acceptances whose flags differ in every
    pair, so any two flag columns swapped in the insert read back wrong."""

    rows = fixture_rows("elexon/boalf_stream.json")
    first = rows[0]
    rows.append(
        dict(
            first,
            acceptanceNumber=990001,
            soFlag=True,
            deemedBoFlag=False,
            storFlag=True,
            rrFlag=False,
        )
    )
    rows.append(
        dict(
            first,
            acceptanceNumber=990002,
            soFlag=True,
            deemedBoFlag=True,
            storFlag=False,
            rrFlag=False,
        )
    )
    return rows


def remit_rows_under_new_ids():
    """The REMIT fixture with every mrid renamed: the fixture loader has
    stored the originals, and a stored publication is never loaded again."""

    text = (FIXTURES_DIR / "elexon/remit_publications.json").read_text(encoding="utf-8")
    rows = json.loads(text, parse_float=Decimal)
    for row in rows:
        row["mrid"] = f"CI-RB-{row['mrid']}"
    return rows


def check_remit_window_ignores_a_future_dated_message(writer):
    """A message published before it was captured moves the window; one
    dated after its capture never does (it would stop all fetching)."""

    captured = datetime(2100, 1, 1, tzinfo=timezone.utc)
    template = remit_rows_under_new_ids()[0]
    published_before_capture = dict(
        template, mrid="CI-RB-NEWEST-PAST", publishTime="2099-06-01T00:00:00Z"
    )
    dated_after_capture = dict(
        template, mrid="CI-RB-NEWEST-FUTURE", publishTime="2101-01-01T00:00:00Z"
    )
    load_remit(
        parse_remit([published_before_capture, dated_after_capture], captured), writer
    )

    assert newest_stored_publish_time(writer) == datetime(
        2099, 6, 1, tzinfo=timezone.utc
    )


def b1610_rows(settlement_date, run_type, unit_count, periods):
    """Source rows for units CI_RB_1..unit_count, one per period."""

    rows = []
    for unit in range(1, unit_count + 1):
        for period in periods:
            rows.append(
                {
                    "dataset": "B1610",
                    "psrType": "Generation",
                    "bmUnit": f"{B1610_UNIT_PREFIX}{unit}",
                    "nationalGridBmUnitId": f"CI-RB-{unit}",
                    "settlementDate": settlement_date.isoformat(),
                    "settlementPeriod": period,
                    "halfHourEndTime": f"{settlement_date.isoformat()}T00:30:00",
                    "settlementRunType": run_type,
                    "quantity": Decimal("1.5"),
                }
            )
    return rows


def check_b1610_coverage_and_unit_baseline(writer):
    day = timedelta(days=1)
    source_rows = [
        # The date being checked: four units, periods 1 and 2.
        *b1610_rows(B1610_DATE, "SF", 4, [1, 2]),
        # Another run on the same date: not this run's coverage.
        *b1610_rows(B1610_DATE, "R1", 6, [3]),
        # The seven earlier dates of the run: at most three units.
        *b1610_rows(B1610_DATE - day, "SF", 1, [4]),
        *b1610_rows(B1610_DATE - 7 * day, "SF", 3, [1]),
        # Outside the seven dates, or another run inside them: ignored.
        *b1610_rows(B1610_DATE - 8 * day, "SF", 5, [1]),
        *b1610_rows(B1610_DATE - 2 * day, "R1", 6, [1]),
    ]
    load_b1610(parse_b1610(source_rows, CHECK_CAPTURE), writer)

    row_count, periods, unit_count = stored_coverage(writer, B1610_DATE, "SF")
    assert (row_count, sorted(periods), unit_count) == (8, [1, 2], 4)
    assert recent_unit_count(writer, B1610_DATE, "SF") == 3
    assert recent_unit_count(writer, B1610_DATE - 8 * day, "SF") is None


def check_b1610_keeps_the_first_capture(writer, reader):
    """B1610 keeps one row per run: a later capture of it changes nothing."""

    source_rows = b1610_rows(B1610_DATE, "SF", 1, [1])
    later_capture = CHECK_CAPTURE + timedelta(days=1)
    load_b1610(parse_b1610(source_rows, later_capture), writer)

    (first_capture_row,) = parse_b1610(source_rows, CHECK_CAPTURE)
    expected, stored = stored_row_named(
        reader,
        "raw.elexon_b1610",
        B1610_COLUMNS,
        ("bm_unit", "settlement_date", "settlement_period", "settlement_run_type"),
        first_capture_row,
    )
    assert stored == expected, (stored, expected)


def demand_rows_published_in_2000(relative_path):
    """The fixture's rows with each publish time moved to 2000, so they share
    no key with the CI fixtures already loaded."""

    rows = deepcopy(fixture_rows(relative_path)["data"])
    for row in rows:
        row["publishTime"] = "2000" + row["publishTime"][4:]
    return rows


def check_a_held_publish_day_is_a_utc_day(writer):
    """48 NDF issues on one UTC day hold that day in a London session too,
    though in summer its last two issues fall on the next London day."""

    template = fixture_rows("elexon/ndf_truncated.json")["data"][0]
    first_issue = datetime(2000, 8, 20, 0, 16, tzinfo=timezone.utc)
    rows = []
    for issue in range(demand_backfill.ISSUES_PER_PUBLISH_DAY):
        row = dict(template)
        published = first_issue + issue * timedelta(minutes=30)
        row["publishTime"] = published.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(row)
    load_ndf(parse_ndf(rows, CHECK_CAPTURE), writer)

    london = disposable_connection("Europe/London")
    try:
        held = demand_backfill.held_days(
            london, "NDF", [HELD_PUBLISH_DAY, HELD_PUBLISH_DAY + timedelta(days=1)]
        )
    finally:
        london.close()
    assert held == {HELD_PUBLISH_DAY}, f"NDF days held in a London session: {held}"


def windfor_issues_of_a_summer_day():
    """Eight WINDFOR issues on 20 August 2000 (UTC), each with two hourly
    points, shaped as the API returns them."""

    rows = []
    for issue_number, issue_time in enumerate(WINDFOR_ISSUE_TIMES):
        for point_start in ("2000-08-20T21:00:00Z", "2000-08-20T22:00:00Z"):
            rows.append(
                {
                    "dataset": "WINDFOR",
                    "publishTime": f"2000-08-20T{issue_time}:00Z",
                    "startTime": point_start,
                    "generation": 3000 + 10 * issue_number + int(point_start[11:13]),
                }
            )
    return rows


def fuelhh_wind_periods_of_a_summer_day():
    """Periods 1 and 2 of 20 August 2000: they start on 19 August in UTC."""

    return [
        {
            "dataset": "FUELHH",
            "publishTime": "2000-08-19T23:30:00Z",
            "startTime": "2000-08-19T23:00:00Z",
            "settlementDate": "2000-08-20",
            "settlementPeriod": 1,
            "fuelType": "WIND",
            "generation": 2578,
        },
        {
            "dataset": "FUELHH",
            "publishTime": "2000-08-20T00:00:00Z",
            "startTime": "2000-08-19T23:30:00Z",
            "settlementDate": "2000-08-20",
            "settlementPeriod": 2,
            "fuelType": "WIND",
            "generation": 2541,
        },
    ]


def check_a_held_wind_publish_day_is_a_utc_day():
    """The eight WINDFOR issues already loaded hold their UTC day in a London
    session too, though in summer the last falls on the next London day."""

    london = disposable_connection("Europe/London")
    try:
        held = wind_backfill.held_windfor_days(
            london, [HELD_PUBLISH_DAY, HELD_PUBLISH_DAY + timedelta(days=1)]
        )
    finally:
        london.close()
    assert held == {HELD_PUBLISH_DAY}, f"WINDFOR days held in a London session: {held}"


def check_qas_captures(writer, reader):
    """Every QAS value reads back from its own column, and a second capture of
    the same date is kept beside the first."""

    source_rows = json.loads(
        (FIXTURES_DIR / "elexon/qas_volumes.json").read_text(encoding="utf-8"),
        parse_float=Decimal,
    )["data"]
    first = qas_poller.parse(source_rows, CHECK_CAPTURE)
    second = qas_poller.parse(source_rows, CHECK_CAPTURE + timedelta(days=1))
    qas_poller.load(first, writer)
    qas_poller.load(second, writer)

    expected = {}
    for parsed_row in [*first, *second]:
        named = dict(zip(QAS_COLUMNS, parsed_row, strict=True))
        expected[tuple(parsed_row[:3]) + (named["retrieved_at"],)] = named
    stored = {}
    with reader.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(QAS_COLUMNS)} FROM raw.elexon_qas "
            "WHERE retrieved_at IN (%s, %s)",
            (CHECK_CAPTURE, CHECK_CAPTURE + timedelta(days=1)),
        )
        for row in cursor.fetchall():
            named = dict(zip(QAS_COLUMNS, row, strict=True))
            stored[tuple(row[:3]) + (named["retrieved_at"],)] = named
    reader.commit()
    assert stored == expected, "raw.elexon_qas: stored rows differ from the parsed rows"


def check_qas_rejected_row_loads_nothing(writer, reader):
    """One rejected row beside Decimal volumes is quarantined with its exact
    digits, and no row of its date is loaded."""

    rows = []
    for period in range(1, 49):
        rows.append(
            {
                "settlementDate": QAS_REJECT_DATE.isoformat(),
                "settlementPeriod": period,
                "bmUnitApplicableBalancingServicesVolume": Decimal("0.250"),
                "nationalGridBmUnit": "CI_RB_QAS-1",
                "bmUnit": "T_CI_RB_QAS-1",
                "time": "2025-01-10T00:00:00Z",
            }
        )
    rows[6]["nationalGridBmUnit"] = None
    rows[6]["bmUnitApplicableBalancingServicesVolume"] = Decimal("-1.500")

    with patch.object(qas_poller, "fetch", return_value={"data": rows}):
        try:
            qas_poller.run(writer, QAS_REJECT_DATE)
        except ContractRejected:
            pass
        else:
            raise AssertionError("a rejected QAS row did not fail the capture")

    loaded = read_one_row(
        reader,
        "SELECT count(*) AS loaded FROM raw.elexon_qas WHERE settlement_date = %s",
        (QAS_REJECT_DATE,),
    )
    assert loaded["loaded"] == 0, loaded
    quarantined = read_one_row(
        reader,
        "SELECT payload ->> 'bmUnitApplicableBalancingServicesVolume' AS volume "
        "FROM raw.endpoint_quarantine "
        "WHERE dataset = 'QAS' AND request_context ->> 'settlementDate' = %s",
        (QAS_REJECT_DATE.isoformat(),),
    )
    assert quarantined["volume"] == "-1.500", quarantined


def uk_evening_in_utc(day):
    return datetime(day.year, day.month, day.day, 21, tzinfo=LONDON).astimezone(
        timezone.utc
    )


def windfor_day_rows(publish_day, points_per_issue):
    """WINDFOR rows as the API returns them: one issue per entry of
    points_per_issue, stamped at the summer issue times on the UTC publish
    day, each with that many hourly points from 21:00 UK time the day
    before."""

    issue_times = CHECK_ISSUE_TIMES[: len(points_per_issue)]
    first_point = uk_evening_in_utc(publish_day - timedelta(days=1))
    rows = []
    for issue_time, points in zip(issue_times, points_per_issue, strict=True):
        for hour in range(points):
            start = first_point + timedelta(hours=hour)
            rows.append(
                {
                    "dataset": "WINDFOR",
                    "publishTime": f"{publish_day.isoformat()}T{issue_time}:00Z",
                    "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "generation": 3000 + hour,
                }
            )
    return rows


def fuelhh_date_rows(settlement_date, periods):
    """FUELHH WIND rows as the API returns them, one per period given, each
    published as its period ends. A period given twice is published again an
    hour later."""

    london_midnight = datetime(
        settlement_date.year, settlement_date.month, settlement_date.day, tzinfo=LONDON
    ).astimezone(timezone.utc)
    published_periods = set()
    rows = []
    for period in periods:
        start = london_midnight + (period - 1) * timedelta(minutes=30)
        published = start + timedelta(minutes=30)
        if period in published_periods:
            published += timedelta(hours=1)
        published_periods.add(period)
        rows.append(
            {
                "dataset": "FUELHH",
                "publishTime": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "settlementDate": settlement_date.isoformat(),
                "settlementPeriod": period,
                "fuelType": "WIND",
                "generation": 2000 + period,
            }
        )
    return rows


def whole_windfor_day(publish_day):
    points = wind_poller.expected_points_per_issue(publish_day)
    return windfor_day_rows(publish_day, [points] * 8)


def whole_fuelhh_date(settlement_date):
    return fuelhh_date_rows(
        settlement_date, range(1, expected_period_count(settlement_date) + 1)
    )


def periods(first, last):
    return list(range(first, last + 1))


def store_uncommitted(conn, windfor_rows, fuelhh_rows, stored_at):
    """Insert wind rows in the connection's open transaction; the caller rolls
    them back."""

    with conn.cursor() as cursor:
        insert_rows(cursor, WINDFOR_INSERT, parse_windfor(windfor_rows, stored_at))
        insert_rows(cursor, FUELHH_INSERT, parse_fuelhh(fuelhh_rows, stored_at))


def quarantine_row_naming(dataset, request_context):
    return (
        dataset,
        CHECK_CAPTURE,
        Jsonb(request_context),
        Jsonb({"source_index": 0, "errors": []}),
        Jsonb([]),
        Jsonb({}),
    )


def use_session_zone(conn, zone):
    with conn.cursor() as cursor:
        cursor.execute("SELECT set_config('TimeZone', %s, false)", (zone,))


def store_year_2000_wind_rows(conn):
    windfor_rows = [
        # Complete: its 23:30 issue is on 11 August in London.
        *windfor_day_rows(AUGUST_10_2000, [73] * 8),
        # The last issue one point short.
        *windfor_day_rows(AUGUST_11_2000, [73] * 7 + [72]),
        *windfor_day_rows(AUGUST_12_2000, [73] * 7),
        # A partial ninth issue.
        *windfor_day_rows(AUGUST_13_2000, [73] * 8 + [40]),
        # The span holds the clock change: 74 points are expected.
        *windfor_day_rows(OCTOBER_27_2000, [73] * 8),
        *windfor_day_rows(OCTOBER_28_2000, [74] * 8),
    ]
    fuelhh_rows = [
        *fuelhh_date_rows(SPRING_CHANGE_2000, periods(1, 46)),
        *fuelhh_date_rows(AUGUST_10_2000, periods(1, 48)),
        # Period 7 published twice.
        *fuelhh_date_rows(AUGUST_11_2000, periods(1, 48) + [7]),
        *fuelhh_date_rows(AUGUST_12_2000, periods(1, 47)),
        *fuelhh_date_rows(AUGUST_13_2000, periods(1, 47) + [49]),
        *fuelhh_date_rows(AUTUMN_CHANGE_2000, periods(1, 50)),
        *fuelhh_date_rows(OCTOBER_30_2000, [0] + periods(2, 48)),
    ]
    store_uncommitted(conn, windfor_rows, fuelhh_rows, CHECK_CAPTURE)
    quarantined = [
        quarantine_row_naming(
            "WINDFOR", {"publishDateTimeFrom": "2000-08-14T00:00:00+00:00"}
        ),
        # Read as a UTC instant: 23:30 UTC on 16 August.
        quarantine_row_naming(
            "WINDFOR", {"publishDateTimeFrom": "2000-08-17T00:30:00+01:00"}
        ),
        quarantine_row_naming("FUELHH", {"settlementDate": "2000-08-15"}),
        # Other datasets' rows naming a day hold nothing for the wind captures.
        quarantine_row_naming("QAS", {"settlementDate": "2000-08-14"}),
        quarantine_row_naming(
            "NDF", {"publishDateTimeFrom": "2000-08-15T00:00:00+00:00"}
        ),
    ]
    with conn.cursor() as cursor:
        insert_rows(cursor, QUARANTINE_INSERT, quarantined)


def check_wind_completeness_and_quarantine_queries():
    """The poller's completeness and quarantine queries on year-2000 rows name
    the same days in a UTC session as in three others. The rows are rolled
    back."""

    windfor_days = [
        AUGUST_10_2000,
        AUGUST_11_2000,
        AUGUST_12_2000,
        AUGUST_13_2000,
        OCTOBER_27_2000,
        OCTOBER_28_2000,
    ]
    fuelhh_dates = [
        SPRING_CHANGE_2000,
        AUGUST_10_2000,
        AUGUST_11_2000,
        AUGUST_12_2000,
        AUGUST_13_2000,
        AUTUMN_CHANGE_2000,
        OCTOBER_30_2000,
    ]
    quarantine_days = days_from(AUGUST_13_2000, date(2000, 8, 18))
    expected = {
        "WINDFOR complete": {AUGUST_10_2000, OCTOBER_28_2000},
        "FUELHH complete": {
            SPRING_CHANGE_2000,
            AUGUST_10_2000,
            AUGUST_11_2000,
            AUTUMN_CHANGE_2000,
        },
        "WINDFOR held": {date(2000, 8, 14), date(2000, 8, 16)},
        "FUELHH held": {date(2000, 8, 15)},
    }

    conn = disposable_connection()
    try:
        store_year_2000_wind_rows(conn)
        for zone in SESSION_ZONES:
            use_session_zone(conn, zone)
            found = {
                "WINDFOR complete": wind_poller.complete_days(
                    conn, "WINDFOR", windfor_days
                ),
                "FUELHH complete": wind_poller.complete_days(
                    conn, "FUELHH", fuelhh_dates
                ),
                "WINDFOR held": wind_poller.held_days(conn, "WINDFOR", quarantine_days),
                "FUELHH held": wind_poller.held_days(conn, "FUELHH", quarantine_days),
            }
            assert found == expected, f"wind queries in a {zone} session: {found}"
    finally:
        conn.rollback()
        conn.close()


def database_utc_today(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT (now() AT TIME ZONE 'UTC')::date")
        (utc_today,) = cursor.fetchone()
    return utc_today


def compiled_wind_warning(capture_from, warn_today):
    """The warning test's SQL, compiled with wind_capture_from set, and
    wind_warn_today too unless warn_today is None (today's UTC date)."""

    dbt_vars = {"wind_capture_from": capture_from.isoformat()}
    if warn_today is not None:
        dbt_vars["wind_warn_today"] = warn_today.isoformat()
    manifest = compile_analyses(
        dbt_vars, also_compiled=(WIND_WARNING_TEST,), selected=()
    )
    for node in manifest["nodes"].values():
        if node["resource_type"] == "test" and node["name"] == WIND_WARNING_TEST:
            return node["compiled_code"]
    raise AssertionError(f"dbt did not compile {WIND_WARNING_TEST}")


def split_from_the_73_point_rule(expected_points):
    """Points per issue that the span rule and a constant 73 judge apart."""

    if expected_points > 73:
        return 73
    return 72


def store_rows_inside_the_warning_range(conn, utc_today):
    """Rows on days inside the warning's range, stored now: one day per rule
    condition, and the range's ends left empty. Days whose span or calendar
    holds a clock change get rows only when the range holds one."""

    def days_ago(count):
        return utc_today - timedelta(days=count)

    points = {}
    last_period = {}
    for count in (25, 24, 23, 22, 21, 19):
        points[count] = wind_poller.expected_points_per_issue(days_ago(count))
        last_period[count] = expected_period_count(days_ago(count))
    windfor_rows = [
        *windfor_day_rows(days_ago(25), [points[25]] * 8),
        *windfor_day_rows(days_ago(24), [points[24]] * 7),
        # The last issue one point short.
        *windfor_day_rows(days_ago(23), [points[23]] * 7 + [points[23] - 1]),
        # A partial ninth issue.
        *windfor_day_rows(days_ago(22), [points[22]] * 8 + [40]),
    ]
    fuelhh_rows = [
        *fuelhh_date_rows(days_ago(25), periods(1, last_period[25])),
        *fuelhh_date_rows(days_ago(24), periods(1, last_period[24] - 1)),
        # Period 7 published twice.
        *fuelhh_date_rows(days_ago(23), periods(1, last_period[23]) + [7]),
        # An out-of-range period in place of the last.
        *fuelhh_date_rows(days_ago(22), periods(1, last_period[22] - 1) + [51]),
        # A period 0 in place of the first.
        *fuelhh_date_rows(days_ago(21), [0] + periods(2, last_period[21])),
        *fuelhh_date_rows(days_ago(19), periods(2, last_period[19] + 1)),
    ]
    clock_change_days = []
    for count in range(17, 2, -1):
        day = days_ago(count)
        expected_points = wind_poller.expected_points_per_issue(day)
        if expected_points != 73:
            points = split_from_the_73_point_rule(expected_points)
            windfor_rows.extend(windfor_day_rows(day, [points] * 8))
            clock_change_days.append(f"WINDFOR {day}")
        if expected_period_count(day) != 48:
            fuelhh_rows.extend(fuelhh_date_rows(day, periods(1, 48)))
            clock_change_days.append(f"FUELHH {day}")
    store_uncommitted(conn, windfor_rows, fuelhh_rows, datetime.now(timezone.utc))
    return clock_change_days


def days_from(first, last):
    days = []
    day = first
    while day <= last:
        days.append(day)
        day += timedelta(days=1)
    return days


def days_the_poller_names(conn, range_days):
    """(dataset, day) for each day of the range the poller's rule finds not
    complete."""

    named = set()
    for dataset in ("WINDFOR", "FUELHH"):
        complete = wind_poller.complete_days(conn, dataset, range_days)
        for day in range_days:
            if day not in complete:
                named.add((dataset, day))
    return named


def days_the_warning_names(conn, warning_sql):
    named = set()
    with conn.cursor() as cursor:
        cursor.execute(warning_sql)
        for dataset, day, *_counts in cursor.fetchall():
            named.add((dataset, day))
    return named


def check_wind_warning_agrees_with_the_poller(warn_today=None):
    """The warning test lists exactly the days of its range the poller's rule
    finds not complete, in every session zone, with wind_capture_from before
    the range and inside it. The range counts back from warn_today, or from
    today's UTC date when it is None. The rows are rolled back."""

    conn = disposable_connection()
    try:
        utc_today = warn_today
        if utc_today is None:
            utc_today = database_utc_today(conn)
        capture_from_dates = [
            utc_today - timedelta(days=40),
            utc_today - timedelta(days=20),
        ]
        warnings = {}
        for capture_from in capture_from_dates:
            warnings[capture_from] = compiled_wind_warning(capture_from, warn_today)
        clock_change_days = store_rows_inside_the_warning_range(conn, utc_today)
        for capture_from, warning_sql in warnings.items():
            first_day = max(capture_from, utc_today - timedelta(days=30))
            range_days = days_from(first_day, utc_today - timedelta(days=2))
            for zone in SESSION_ZONES:
                use_session_zone(conn, zone)
                by_poller = days_the_poller_names(conn, range_days)
                by_warning = days_the_warning_names(conn, warning_sql)
                assert by_warning == by_poller, (
                    f"wind_capture_from {capture_from}, {zone} session: only the "
                    f"warning names {sorted(by_warning - by_poller)}; only the "
                    f"poller names {sorted(by_poller - by_warning)}"
                )
    finally:
        conn.rollback()
        conn.close()
    print(
        f"Wind warning: agrees with the poller on rows dated up to {utc_today}, "
        f"clock-change days in range: {clock_change_days or 'none'}"
    )


def unavailable_response():
    response = MagicMock()
    response.status_code = 503
    response.raise_for_status.side_effect = requests.HTTPError(
        "503 Service Unavailable", response=response
    )
    return response


def elexon_answering(replies, asked):
    """Stand in for Elexon's API: each request gets the reply scripted for
    its day, a body or None for HTTP 503, and the day is recorded in asked."""

    def get(url, params, headers, timeout):
        if url.endswith("/WINDFOR"):
            day = date.fromisoformat(params["publishDateTimeFrom"][:10])
        else:
            day = date.fromisoformat(params["settlementDateFrom"])
        asked.append(day)
        reply = replies[day]
        if reply is None:
            return unavailable_response()
        response = MagicMock()
        response.json.return_value = {"data": reply}
        return response

    return get


def run_capture(capture, conn, target, replies):
    """Run one capture task body with Elexon faked; return the days it asked
    and the error it raised, if any."""

    asked = []
    raised = None
    with (
        patch("ingestion.elexon.wind_poller.time.sleep"),
        patch(
            "ingestion.elexon.wind_poller.requests.get",
            side_effect=elexon_answering(replies, asked),
        ),
    ):
        try:
            capture(conn, target)
        except RuntimeError as error:
            raised = error
    return asked, raised


def june_2099(day_number):
    return date(2099, 6, day_number)


def load_windfor_rows(writer, rows):
    load_windfor(parse_windfor(rows, datetime.now(timezone.utc)), writer)


def load_fuelhh_rows(writer, rows):
    load_fuelhh(parse_fuelhh(rows, datetime.now(timezone.utc)), writer)


def windfor_out_of_range_rows():
    """One point of an issue stamped in 2100."""

    return windfor_day_rows(OUT_OF_RANGE_DAY, [1])


def fuelhh_out_of_range_rows():
    """A period of a date in 2100, and a period 51 of 5 June 2099, a date in
    the second run's look-back."""

    return fuelhh_date_rows(OUT_OF_RANGE_DAY, [1]) + fuelhh_date_rows(
        june_2099(5), [51]
    )


WIND_SEQUENCES = {
    "WINDFOR": SimpleNamespace(
        capture_target=wind_poller.capture_windfor_target,
        capture_look_back=wind_poller.capture_windfor_look_back,
        whole_day=whole_windfor_day,
        short_reply=windfor_day_rows(june_2099(9), [73] * 7),
        load_rows=load_windfor_rows,
        out_of_range_rows=windfor_out_of_range_rows,
        out_of_range_context={"publishDateTimeFrom": "2100-01-01T00:00:00+00:00"},
        also_asked_again=[],
    ),
    "FUELHH": SimpleNamespace(
        capture_target=wind_poller.capture_fuelhh_target,
        capture_look_back=wind_poller.capture_fuelhh_look_back,
        whole_day=whole_fuelhh_date,
        short_reply=fuelhh_date_rows(june_2099(9), periods(1, 20)),
        load_rows=load_fuelhh_rows,
        out_of_range_rows=fuelhh_out_of_range_rows,
        out_of_range_context={"settlementDate": "2100-01-01"},
        also_asked_again=[june_2099(5)],
    ),
}
OUT_OF_RANGE_FINDING = {
    "row": {},
    "index": 0,
    "errors": [{"field": "check", "error": "out of range"}],
}


def check_wind_capture_across_two_runs(writer):
    """P2 sequence faults on real tables, for each dataset. The first run's
    look-back gets a short reply, HTTP 503 and a whole day. Out-of-range
    values are then stored. The second run asks again for the short and the
    failed days, and for a date an out-of-range period left not complete,
    and for nothing else; its target task asks only its target."""

    for dataset, sequence in WIND_SEQUENCES.items():
        for day_number in (3, 4, 5, 6):
            sequence.load_rows(writer, sequence.whole_day(june_2099(day_number)))

        asked, raised = run_capture(
            sequence.capture_target,
            writer,
            FIRST_RUN_TARGET,
            {june_2099(10): sequence.whole_day(june_2099(10))},
        )
        assert (asked, raised) == ([june_2099(10)], None), (dataset, asked, raised)
        asked, raised = run_capture(
            sequence.capture_look_back,
            writer,
            FIRST_RUN_TARGET,
            {
                june_2099(9): sequence.short_reply,
                june_2099(8): None,
                june_2099(7): sequence.whole_day(june_2099(7)),
            },
        )
        assert asked == [june_2099(9), june_2099(8), june_2099(7)], (dataset, asked)
        assert "2099-06-08" in str(raised), (dataset, raised)
        assert not isinstance(raised, ContractRejected), (dataset, raised)

        sequence.load_rows(writer, sequence.out_of_range_rows())
        quarantine_rows(
            [OUT_OF_RANGE_FINDING],
            writer,
            dataset,
            datetime.now(timezone.utc),
            sequence.out_of_range_context,
        )

        asked, raised = run_capture(
            sequence.capture_target,
            writer,
            SECOND_RUN_TARGET,
            {june_2099(11): sequence.whole_day(june_2099(11))},
        )
        assert (asked, raised) == ([june_2099(11)], None), (dataset, asked, raised)
        asked_again = [june_2099(9), june_2099(8), *sequence.also_asked_again]
        replies = {}
        for day in asked_again:
            replies[day] = sequence.whole_day(day)
        asked, raised = run_capture(
            sequence.capture_look_back, writer, SECOND_RUN_TARGET, replies
        )
        assert (asked, raised) == (asked_again, None), (dataset, asked, raised)


def names_the_publish_day(publish_day):
    def names_the_row(context, _payload):
        return wind_poller.utc_day_of(context["publishDateTimeFrom"]) == publish_day

    return names_the_row


def check_a_resolved_wind_day_is_asked_for_again(writer, reader):
    """A WINDFOR day with a null generation is quarantined and then held,
    with no request, until its rows are listed and marked resolved by id;
    the next capture asks for the day and loads it, and the quarantine keeps
    the resolved row."""

    day = RESOLUTION_DAY
    reply_with_a_null = whole_windfor_day(day)
    reply_with_a_null[5]["generation"] = None
    asked, raised = run_capture(
        wind_poller.capture_windfor_target, writer, day, {day: reply_with_a_null}
    )
    assert asked == [day], asked
    assert isinstance(raised, ContractRejected), raised

    asked, raised = run_capture(
        wind_poller.capture_windfor_target, writer, day, {day: whole_windfor_day(day)}
    )
    assert asked == [], f"a held day was asked for: {asked}"
    assert isinstance(raised, ContractRejected), raised
    assert day.isoformat() in str(raised), raised

    (quarantine_id,) = unresolved_quarantine_ids(
        reader, "WINDFOR", names_the_publish_day(day)
    )
    check_a_half_resolved_row_is_refused(writer, quarantine_id)
    assert mark_resolved(writer, [quarantine_id], "owner: check, day reloaded") == 1
    assert (
        unresolved_quarantine_ids(reader, "WINDFOR", names_the_publish_day(day)) == []
    )

    asked, raised = run_capture(
        wind_poller.capture_windfor_target, writer, day, {day: whole_windfor_day(day)}
    )
    assert (asked, raised) == ([day], None), (asked, raised)
    window_start, window_end = wind_poller.utc_day_window(day)
    loaded = read_one_row(
        reader,
        "select count(*) as points from raw.elexon_windfor "
        "where publish_time >= %s and publish_time < %s",
        (window_start, window_end),
    )
    assert loaded["points"] == 584, loaded
    assert resolution_columns(reader, quarantine_id)["resolved_by"] == (
        "owner: check, day reloaded"
    )


def delete_wind_sequence_rows(cursor):
    first_instant = datetime(SEQUENCE_YEARS_FROM.year, 1, 1, tzinfo=timezone.utc)
    cursor.execute(
        "DELETE FROM raw.elexon_windfor WHERE publish_time >= %s", (first_instant,)
    )
    cursor.execute(
        "DELETE FROM raw.elexon_fuelhh WHERE settlement_date >= %s",
        (SEQUENCE_YEARS_FROM,),
    )
    cursor.execute(
        "DELETE FROM raw.endpoint_quarantine WHERE dataset IN ('WINDFOR', 'FUELHH') "
        "AND coalesce(request_context ->> 'publishDateTimeFrom', "
        "request_context ->> 'settlementDate') >= %s",
        (SEQUENCE_YEARS_FROM.isoformat(),),
    )


def delete_check_rows(writer):
    with writer.cursor() as cursor:
        delete_wind_sequence_rows(cursor)
        cursor.execute(
            "DELETE FROM raw.endpoint_quarantine WHERE request_context = %s::jsonb",
            (json.dumps(CHECK_CONTEXT),),
        )
        cursor.execute(
            "DELETE FROM raw.carbon_intensity_outturn WHERE retrieved_at = %s",
            (CHECK_CAPTURE,),
        )
        cursor.execute(
            "DELETE FROM raw.carbon_intensity_forecast WHERE retrieved_at = %s",
            (CHECK_CAPTURE,),
        )
        cursor.execute(
            "DELETE FROM raw.elexon_b1610 WHERE bm_unit LIKE %s",
            (f"{B1610_UNIT_PREFIX}%",),
        )
        for table in (
            "raw.elexon_pn",
            "raw.elexon_qpn",
            "raw.elexon_boalf",
            "raw.elexon_remit",
            "raw.elexon_ndf",
            "raw.elexon_indo",
            "raw.elexon_windfor",
            "raw.elexon_fuelhh",
        ):
            cursor.execute(
                f"DELETE FROM {table} WHERE retrieved_at = %s", (CHECK_CAPTURE,)
            )
        cursor.execute(
            "DELETE FROM raw.elexon_remit WHERE mrid LIKE 'CI-RB-NEWEST-%%' "
            "OR mrid = %s",
            (RELOADED_MRID,),
        )
        cursor.execute(
            "DELETE FROM raw.elexon_qas WHERE retrieved_at IN (%s, %s)",
            (CHECK_CAPTURE, CHECK_CAPTURE + timedelta(days=1)),
        )
        cursor.execute(
            "DELETE FROM raw.endpoint_quarantine WHERE dataset = 'QAS' "
            "AND request_context ->> 'settlementDate' = %s",
            (QAS_REJECT_DATE.isoformat(),),
        )
    writer.commit()


def main():
    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError(
            "Database write checks require a local disposable gridskew_dev"
        )

    writer = disposable_connection()
    reader = disposable_connection()
    try:
        # First: a REMIT row left in quarantine by a later check holds the re-read.
        run_remit_reread_checks(writer, disposable_connection)
        check_quarantine_evidence(writer, reader)
        check_loader_role_privileges(writer, reader)
        check_outturn_load(writer, reader)
        check_forecast_load(writer, reader)
        check_remit_quarantine_count(writer, reader)
        check_b1610_coverage_and_unit_baseline(writer)
        check_b1610_keeps_the_first_capture(writer, reader)
        check_remit_window_ignores_a_future_dated_message(writer)
        check_qas_captures(writer, reader)
        check_qas_rejected_row_loads_nothing(writer, reader)
        pn_key = ("national_grid_bm_unit", "time_from", "retrieved_at")
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_pn",
            fixture_rows("elexon/pn_stream.json"),
            parse_pn,
            load_pn,
            PN_COLUMNS,
            pn_key,
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_qpn",
            fixture_rows("elexon/qpn_stream.json"),
            parse_qpn,
            load_qpn,
            PN_COLUMNS,
            pn_key,
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_boalf",
            boalf_rows_with_distinct_flags(),
            parse_boalf,
            load_boalf,
            BOALF_COLUMNS,
            ("national_grid_bm_unit", "acceptance_number", "time_from", "retrieved_at"),
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_remit",
            remit_rows_under_new_ids(),
            parse_remit,
            load_remit,
            REMIT_COLUMNS,
            ("mrid", "revision_number", "publish_time", "created_time"),
            # The source row as jsonb, the one column of its type.
            unread=("payload",),
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_ndf",
            demand_rows_published_in_2000("elexon/ndf_truncated.json"),
            parse_ndf,
            load_ndf,
            NDF_COLUMNS,
            ("settlement_date", "settlement_period", "boundary", "publish_time"),
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_indo",
            demand_rows_published_in_2000("elexon/indo.json"),
            parse_indo,
            load_indo,
            INDO_COLUMNS,
            ("settlement_date", "settlement_period", "publish_time"),
        )
        check_a_held_publish_day_is_a_utc_day(writer)
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_windfor",
            windfor_issues_of_a_summer_day(),
            parse_windfor,
            load_windfor,
            WINDFOR_COLUMNS,
            ("start_time", "publish_time"),
        )
        check_a_held_wind_publish_day_is_a_utc_day()
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_fuelhh",
            fuelhh_wind_periods_of_a_summer_day(),
            parse_fuelhh,
            load_fuelhh,
            FUELHH_COLUMNS,
            ("settlement_date", "settlement_period", "fuel_type", "publish_time"),
        )
        check_wind_completeness_and_quarantine_queries()
        check_wind_warning_agrees_with_the_poller()
        check_wind_warning_agrees_with_the_poller(WARNING_DAY_AFTER_CLOCK_CHANGE)
        check_wind_capture_across_two_runs(writer)
        check_a_resolved_wind_day_is_asked_for_again(writer, reader)
    finally:
        writer.rollback()
        delete_check_rows(writer)
        writer.close()
        reader.close()
    print("Database writes: every value read back from its own column")
    print(
        "Wind capture: the queries agree in four session zones, and a second "
        "run asks again only for the days the first left not complete"
    )
    print(
        "Quarantine resolution: a row counts or holds only while unresolved, a "
        "resolved day is asked for again, and a half-resolved row is refused"
    )
    print(
        "Loader role: inserts and the lock function succeed, every delete, "
        "update, truncate and direct lock is refused, and the scripts refuse "
        "a reused role, a stray grant and a wrong flag"
    )


if __name__ == "__main__":
    main()
