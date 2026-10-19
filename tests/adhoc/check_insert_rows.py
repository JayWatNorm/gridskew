"""Load through the shared insert helper on a real database and read back.

The unit tests check the statement text; only a database shows that every
row lands, each value type binds to its column, a repeated load is ignored
and a failed row leaves nothing behind once the caller rolls back. Each
read-back uses a second connection, so it also proves what was committed.

Run from the repository root on the disposable CI database, for example:
    GRIDSKEW_DISPOSABLE_TEST=1 DBT_HOST=localhost DBT_DBNAME=gridskew_dev ...
    python -m tests.adhoc.check_insert_rows
"""

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from ingestion.database import insert_rows
from ingestion.elexon.bmunits_poller import SOURCE_FIELDS_AND_COLUMNS, load_extract

SCHEMA = "ci_insert_rows"
TABLE = f"{SCHEMA}.loaded"
INSERT_SQL = (
    f"INSERT INTO {TABLE} (row_id, label, amount, captured_at, settlement_date, "
    "evidence) VALUES %s ON CONFLICT (row_id) DO NOTHING"
)
FIRST_CAPTURE = datetime(2026, 10, 19, 20, 15, 30, 123456, tzinfo=timezone.utc)


def disposable_connection():
    return psycopg.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
        dbname=os.environ["DBT_DBNAME"],
    )


def create_table(writer):
    with writer.cursor() as cursor:
        cursor.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cursor.execute(f"CREATE SCHEMA {SCHEMA}")
        cursor.execute(
            f"CREATE TABLE {TABLE} ("
            "row_id integer PRIMARY KEY, label text NOT NULL, amount numeric, "
            "captured_at timestamptz NOT NULL, settlement_date date NOT NULL, "
            "evidence jsonb NOT NULL)"
        )
    writer.commit()


def drop_table(writer):
    with writer.cursor() as cursor:
        cursor.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    writer.commit()


def row_numbered(row_id, label_prefix="row"):
    """One row whose values all differ from every other row's (T9)."""

    amount = None
    if row_id % 7:
        amount = Decimal(row_id) / Decimal(1000)
    return (
        row_id,
        f"{label_prefix}-{row_id}",
        amount,
        FIRST_CAPTURE + timedelta(seconds=row_id),
        date(2026, 1, 1) + timedelta(days=row_id % 365),
        Jsonb({"row": row_id, "parts": [row_id, str(row_id)]}),
    )


def rows_numbered(first_id, count, label_prefix="row"):
    rows = []
    for row_id in range(first_id, first_id + count):
        rows.append(row_numbered(row_id, label_prefix))
    return rows


def stored_rows(reader, first_id, count):
    with reader.cursor() as cursor:
        cursor.execute(
            f"SELECT row_id, label, amount, captured_at, settlement_date, evidence "
            f"FROM {TABLE} WHERE row_id >= %s AND row_id < %s ORDER BY row_id",
            (first_id, first_id + count),
        )
        rows = cursor.fetchall()
    reader.commit()
    return rows


def as_stored(row):
    """The row as the database returns it: the JSON wrapper becomes its value."""

    row_id, label, amount, captured_at, settlement_date, evidence = row
    return (row_id, label, amount, captured_at, settlement_date, evidence.obj)


def check_every_row_lands_with_each_value_in_its_column(writer, reader):
    rows = rows_numbered(1, 2500)

    with writer.cursor() as cursor:
        insert_rows(cursor, INSERT_SQL, rows)
    writer.commit()

    expected = []
    for row in rows:
        expected.append(as_stored(row))
    assert stored_rows(reader, 1, 2500) == expected


def check_a_repeated_load_is_ignored(writer, reader):
    before = stored_rows(reader, 1, 2500)
    relabelled = rows_numbered(1, 2500, label_prefix="again")

    with writer.cursor() as cursor:
        insert_rows(cursor, INSERT_SQL, relabelled)
    writer.commit()

    assert stored_rows(reader, 1, 2500) == before


def check_a_failed_later_row_leaves_no_row(writer, reader):
    rows = rows_numbered(10001, 2500)
    broken_id, _label, amount, captured_at, settlement_date, evidence = rows[2100]
    rows[2100] = (broken_id, None, amount, captured_at, settlement_date, evidence)

    try:
        with writer.cursor() as cursor:
            insert_rows(cursor, INSERT_SQL, rows)
        writer.commit()
    except psycopg.errors.NotNullViolation:
        writer.rollback()
    else:
        raise AssertionError("A NULL label at row 2,100 was accepted")

    assert stored_rows(reader, 10001, 2500) == []


def bm_unit_row(source_index, unit_name):
    row = {}
    for source_field, _column in SOURCE_FIELDS_AND_COLUMNS:
        row[source_field] = None
    row["nationalGridBmUnit"] = unit_name
    row["creditQualifyingStatus"] = source_index % 2 == 0
    row["demandInProductionFlag"] = source_index % 3 == 0
    return row


def check_a_failed_bm_unit_row_leaves_neither_extract_nor_units(writer, reader):
    rows = []
    for source_index in range(1500):
        rows.append(bm_unit_row(source_index, f"CI-UNIT-{source_index}"))
    rows[1200] = bm_unit_row(1200, "")
    extract_id = uuid4()

    try:
        load_extract(writer, rows, FIRST_CAPTURE, 1500, extract_id=extract_id)
    except psycopg.errors.CheckViolation:
        pass
    else:
        raise AssertionError("A blank BM unit at row 1,200 was accepted")

    with reader.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM raw.elexon_bm_units_extracts WHERE extract_id = %s",
            (extract_id,),
        )
        (extracts,) = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM raw.elexon_bm_units WHERE extract_id = %s",
            (extract_id,),
        )
        (units,) = cursor.fetchone()
    reader.commit()
    assert (extracts, units) == (0, 0)


def check_no_rows_send_nothing(writer, reader):
    with writer.cursor() as cursor:
        insert_rows(cursor, INSERT_SQL, [])
    writer.commit()

    assert len(stored_rows(reader, 1, 100000)) == 2500


def main():
    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError("Insert checks require a local disposable gridskew_dev")

    writer = disposable_connection()
    reader = disposable_connection()
    try:
        create_table(writer)
        check_every_row_lands_with_each_value_in_its_column(writer, reader)
        check_a_repeated_load_is_ignored(writer, reader)
        check_a_failed_later_row_leaves_no_row(writer, reader)
        check_a_failed_bm_unit_row_leaves_neither_extract_nor_units(writer, reader)
        check_no_rows_send_nothing(writer, reader)
        print(
            "Insert helper: rows, types, repeats and rollback checked on the database"
        )
    finally:
        drop_table(writer)
        writer.close()
        reader.close()


if __name__ == "__main__":
    main()
