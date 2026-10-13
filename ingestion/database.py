"""Open the psycopg2 connection the loaders write through."""

import psycopg2

DEFAULT_PORT = 5432


def connect(airflow_connection):
    """A psycopg2 connection to the database an Airflow connection names.

    The loaders use psycopg2's `execute_values`. Airflow's `PostgresHook`
    returns psycopg 3 connections once SQLAlchemy 2 is installed (Airflow 3.2
    onwards), so the DAGs open their own connection from the same fields.
    Connection extras (such as `sslmode`) are not passed on; no GridSkew
    connection sets one.
    """
    return psycopg2.connect(
        host=airflow_connection.host,
        port=airflow_connection.port or DEFAULT_PORT,
        dbname=airflow_connection.schema,
        user=airflow_connection.login,
        password=airflow_connection.password,
    )
