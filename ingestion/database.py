"""Open the connection the loaders write through, and insert their rows."""

import psycopg

DEFAULT_PORT = 5432
VALUES_SLOT = "%s"


def connect(airflow_connection):
    """A psycopg connection to the database an Airflow connection names.

    Connection extras (such as `sslmode`) are not passed on; no GridSkew
    connection sets one.
    """
    return psycopg.connect(
        host=airflow_connection.host,
        port=airflow_connection.port or DEFAULT_PORT,
        dbname=airflow_connection.schema,
        user=airflow_connection.login,
        password=airflow_connection.password,
    )


def connect_to_gridskew_prod():
    """The gridskew_prod connection, as the DAGs open it.

    For the one-off backfills, which run in the Airflow container.
    """

    from airflow.sdk import BaseHook

    return connect(BaseHook.get_connection("gridskew_prod"))


def one_row_statement(insert_sql, column_count):
    """The statement with its one `VALUES %s` slot filled by one row's
    placeholders."""

    if insert_sql.count(VALUES_SLOT) != 1:
        raise ValueError(f"Insert SQL needs exactly one {VALUES_SLOT}: {insert_sql}")
    group = "(" + ", ".join([VALUES_SLOT] * column_count) + ")"
    return insert_sql.replace(VALUES_SLOT, group)


def insert_rows(cursor, insert_sql, rows):
    """Insert a list of rows through psycopg's `executemany`, which pipelines
    them. The caller owns the transaction: nothing here commits."""

    if not rows:
        return
    cursor.executemany(one_row_statement(insert_sql, len(rows[0])), rows)
