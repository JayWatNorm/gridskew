"""Exercise real dbt snapshot transitions in CI's disposable PostgreSQL."""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg2
from psycopg2 import errors, sql

from ingestion.elexon.bmunits_poller import locked_extract

PROJECT_DIR = Path(__file__).resolve().parents[2] / "dbt"
SNAPSHOT_TABLE = "dbt_dev_snapshots.snap_elexon__bm_units"


def dbt_snapshot(conn, expected_id):
    with locked_extract(conn, expected_id):
        subprocess.run(
            [
                "dbt",
                "snapshot",
                "--project-dir",
                str(PROJECT_DIR),
                "--select",
                "snap_elexon__bm_units",
            ],
            check=True,
        )


def latest_extract(cursor):
    cursor.execute(
        "SELECT extract_id FROM raw.elexon_bm_units_extracts "
        "ORDER BY retrieved_at DESC, extract_id DESC LIMIT 1"
    )
    return cursor.fetchone()[0]


def copy_extract(conn, source_id, step, *, missing_key=None, rename_key=None):
    new_id = uuid4()
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'raw' AND table_name = 'elexon_bm_units' "
            "AND column_name <> 'extract_id' ORDER BY ordinal_position"
        )
        columns = [row[0] for row in cursor.fetchall()]
        names = ", ".join(columns)
        condition = "" if missing_key is None else "AND national_grid_bm_unit <> %s"
        params = (
            (str(source_id),) if missing_key is None else (str(source_id), missing_key)
        )
        cursor.execute(
            "SELECT count(*), count(DISTINCT national_grid_bm_unit) "
            f"FROM raw.elexon_bm_units WHERE extract_id = %s {condition}",
            params,
        )
        row_count, unit_count = cursor.fetchone()
        cursor.execute(
            "INSERT INTO raw.elexon_bm_units_extracts "
            "(extract_id, retrieved_at, row_count, unit_count) "
            "VALUES (%s, %s, %s, %s)",
            (
                str(new_id),
                datetime.now(timezone.utc) + timedelta(minutes=step),
                row_count,
                unit_count,
            ),
        )
        cursor.execute(
            f"INSERT INTO raw.elexon_bm_units (extract_id, {names}) "
            f"SELECT %s, {names} FROM raw.elexon_bm_units "
            f"WHERE extract_id = %s {condition}",
            (str(new_id), *params),
        )
        if rename_key is not None:
            cursor.execute(
                "UPDATE raw.elexon_bm_units SET bm_unit_name = %s "
                "WHERE extract_id = %s AND national_grid_bm_unit = %s",
                ("CI changed name", str(new_id), rename_key),
            )
    conn.commit()
    return new_id


def assert_state(conn, total, current):
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT count(*), count(*) FILTER (WHERE dbt_valid_to IS NULL) "
            f"FROM {SNAPSHOT_TABLE}"
        )
        actual = cursor.fetchone()
    if actual != (total, current):
        raise AssertionError(f"snapshot rows {actual}; expected {(total, current)}")


def check_database_guards(conn, initial_id):
    """Exercise real writer blocking and dbt tests against deliberate bad state."""
    writer = psycopg2.connect(conn.dsn)
    try:
        with locked_extract(conn, initial_id):
            with writer.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '100ms'")
                cursor.execute("SELECT count(*) FROM raw.elexon_bm_units")
                assert cursor.fetchone()[0] > 0  # Readers remain available.
            for statement in (
                "UPDATE raw.elexon_bm_units SET eic = eic",
                "INSERT INTO raw.elexon_bm_units_extracts "
                "(extract_id, retrieved_at, row_count, unit_count) "
                f"VALUES ('{uuid4()}', now(), 1, 1)",
            ):
                with writer.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '100ms'")
                    try:
                        cursor.execute(statement)
                    except errors.LockNotAvailable:
                        writer.rollback()
                    else:
                        raise AssertionError("A writer bypassed the snapshot guard")
        with writer.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '100ms'")
            cursor.execute("UPDATE raw.elexon_bm_units SET eic = eic")
        writer.rollback()  # Same write works after guard release; retain fixtures.
    finally:
        writer.close()

    target = Path(os.getenv("DBT_TARGET_PATH", str(PROJECT_DIR / "target")))
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    tests = {
        node["name"]: node["compiled_code"]
        for node in manifest["nodes"].values()
        if node["resource_type"] == "test" and node.get("compiled_code")
    }
    try:
        with conn.cursor() as cursor:
            cursor.execute("TRUNCATE raw.elexon_bm_units, raw.elexon_bm_units_extracts")
            cursor.execute(tests["assert_bm_units_current_extract_count"])
            assert cursor.fetchall(), "Empty manifest must fail the dbt count test"
    finally:
        conn.rollback()

    staging = sql.Identifier("dbt_dev", "stg_elexon__bm_units")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_viewdef('dbt_dev.stg_elexon__bm_units', true)"
            )
            definition = cursor.fetchone()[0].rstrip().rstrip(";")
            cursor.execute(
                sql.SQL(
                    "CREATE OR REPLACE VIEW {} AS SELECT * FROM ({}) s WHERE false"
                ).format(staging, sql.SQL(definition))
            )
            cursor.execute(tests["assert_bm_units_staging_grain"])
            assert cursor.fetchall(), "Whole missing extracts must fail the grain test"
    finally:
        conn.rollback()

    try:
        with conn.cursor() as cursor:
            for raw, expected in [(" \t\n\r\f\v", None), ("\t15.400\n", 15.4)]:
                cursor.execute(
                    "UPDATE raw.elexon_bm_units SET generation_capacity = %s "
                    "WHERE extract_id = %s AND source_index = 0",
                    (raw, str(initial_id)),
                )
                cursor.execute(
                    "SELECT generation_capacity_mw FROM dbt_dev.stg_elexon__bm_units "
                    "WHERE extract_id = %s AND source_index = 0",
                    (str(initial_id),),
                )
                actual = cursor.fetchone()[0]
                assert (None if actual is None else float(actual)) == expected
    finally:
        conn.rollback()


def assert_key_history(conn, key):
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT bm_unit_name, dbt_valid_from, dbt_valid_to FROM {SNAPSHOT_TABLE} "
            "WHERE national_grid_bm_unit = %s ORDER BY dbt_valid_from",
            (key,),
        )
        rows = cursor.fetchall()
        assert len(rows) == 3
        assert rows[0][0] != "CI changed name"
        assert rows[1][0] == rows[2][0] == "CI changed name"
        assert rows[0][1] < rows[0][2] == rows[1][1]
        assert rows[1][1] < rows[1][2] < rows[2][1]
        assert rows[2][2] is None
        cursor.execute(
            f"SELECT national_grid_bm_unit FROM {SNAPSHOT_TABLE} "
            "WHERE national_grid_bm_unit <> %s GROUP BY national_grid_bm_unit "
            "HAVING count(*) <> 1 OR count(*) FILTER (WHERE dbt_valid_to IS NULL) <> 1",
            (key,),
        )
        assert cursor.fetchall() == [], "Unchanged keys must retain one current version"


def main():
    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError("Lifecycle checks require opt-in to local gridskew_dev")
    conn = psycopg2.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
        dbname=os.environ["DBT_DBNAME"],
    )
    try:
        with conn.cursor() as cursor:
            initial_id = latest_extract(cursor)
            cursor.execute(
                "SELECT national_grid_bm_unit FROM raw.elexon_bm_units "
                "WHERE extract_id = %s ORDER BY source_index LIMIT 1",
                (initial_id,),
            )
            changed_key = cursor.fetchone()[0]
            cursor.execute(
                "SELECT unit_count FROM raw.elexon_bm_units_extracts "
                "WHERE extract_id = %s",
                (initial_id,),
            )
            initial_count = cursor.fetchone()[0]
        conn.commit()

        check_database_guards(conn, initial_id)
        assert_state(conn, initial_count, initial_count)
        unchanged_id = copy_extract(conn, initial_id, 1)
        dbt_snapshot(conn, unchanged_id)
        assert_state(conn, initial_count, initial_count)

        changed_id = copy_extract(conn, unchanged_id, 2, rename_key=changed_key)
        dbt_snapshot(conn, changed_id)
        assert_state(conn, initial_count + 1, initial_count)

        missing_id = copy_extract(conn, changed_id, 3, missing_key=changed_key)
        dbt_snapshot(conn, missing_id)
        assert_state(conn, initial_count + 1, initial_count - 1)

        reappeared_id = copy_extract(conn, changed_id, 4)
        dbt_snapshot(conn, reappeared_id)
        assert_state(conn, initial_count + 2, initial_count)
        assert_key_history(conn, changed_key)
        print("BM-unit locks, failure gates, numeric casts and exact history passed")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
