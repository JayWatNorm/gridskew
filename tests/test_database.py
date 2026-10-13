from types import SimpleNamespace
from unittest.mock import patch

from ingestion.database import DEFAULT_PORT, connect


def airflow_connection(port):
    return SimpleNamespace(
        host="db-host",
        port=port,
        schema="gridskew_prod",
        login="ingest-user",
        password="ingest-password",
    )


def test_each_connection_field_reaches_its_psycopg2_argument():
    with patch("ingestion.database.psycopg2.connect") as psycopg2_connect:
        connection = connect(airflow_connection(port=6543))

    psycopg2_connect.assert_called_once_with(
        host="db-host",
        port=6543,
        dbname="gridskew_prod",
        user="ingest-user",
        password="ingest-password",
    )
    assert connection is psycopg2_connect.return_value


def test_a_connection_without_a_port_uses_the_postgres_default():
    with patch("ingestion.database.psycopg2.connect") as psycopg2_connect:
        connect(airflow_connection(port=None))

    assert psycopg2_connect.call_args.kwargs["port"] == DEFAULT_PORT
