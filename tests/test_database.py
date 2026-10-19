from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ingestion.database import DEFAULT_PORT, connect, insert_rows, one_row_statement


def airflow_connection(port):
    return SimpleNamespace(
        host="db-host",
        port=port,
        schema="gridskew_prod",
        login="ingest-user",
        password="ingest-password",
    )


def test_each_connection_field_reaches_its_psycopg_argument():
    with patch("ingestion.database.psycopg.connect") as psycopg_connect:
        connection = connect(airflow_connection(port=6543))

    psycopg_connect.assert_called_once_with(
        host="db-host",
        port=6543,
        dbname="gridskew_prod",
        user="ingest-user",
        password="ingest-password",
    )
    assert connection is psycopg_connect.return_value


def test_a_connection_without_a_port_uses_the_postgres_default():
    with patch("ingestion.database.psycopg.connect") as psycopg_connect:
        connect(airflow_connection(port=None))

    assert psycopg_connect.call_args.kwargs["port"] == DEFAULT_PORT


def test_the_values_slot_becomes_one_placeholder_per_column():
    statement = one_row_statement("INSERT INTO raw.t (a, b, c) VALUES %s", 3)

    assert statement == "INSERT INTO raw.t (a, b, c) VALUES (%s, %s, %s)"


def test_the_text_after_the_values_slot_is_kept():
    statement = one_row_statement(
        "INSERT INTO raw.t (a) VALUES %s ON CONFLICT (a) DO NOTHING", 1
    )

    assert statement == "INSERT INTO raw.t (a) VALUES (%s) ON CONFLICT (a) DO NOTHING"


def test_a_statement_without_one_values_slot_is_refused():
    with pytest.raises(ValueError, match="exactly one"):
        one_row_statement("INSERT INTO raw.t (a) VALUES (1)", 1)
    with pytest.raises(ValueError, match="exactly one"):
        one_row_statement("INSERT INTO raw.t (a, b) VALUES %s, %s", 1)


class ManyRecorder:
    """Stands in for a database cursor: records what reaches executemany."""

    def __init__(self):
        self.calls = []

    def executemany(self, statement, rows):
        self.calls.append((statement, list(rows)))


def test_every_row_reaches_executemany_once_in_order():
    cursor = ManyRecorder()
    rows = [(31, "third"), (12, "first"), (27, "second")]

    insert_rows(cursor, "INSERT INTO raw.t (a, b) VALUES %s", rows)

    assert cursor.calls == [
        (
            "INSERT INTO raw.t (a, b) VALUES (%s, %s)",
            [(31, "third"), (12, "first"), (27, "second")],
        )
    ]


def test_no_rows_send_nothing():
    cursor = ManyRecorder()

    insert_rows(cursor, "INSERT INTO raw.t (a) VALUES %s", [])

    assert cursor.calls == []
