"""Stand-ins the tests use in place of Airflow and a database connection, and
the helpers several test modules share."""

import runpy
import sys
from datetime import timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

DAGS_DIRECTORY = Path(__file__).resolve().parents[1] / "dags"
# An Elexon outage over a long weekend.
MULTI_DAY_OUTAGE = timedelta(days=3)
QUARANTINE_COLUMNS = (
    "dataset",
    "retrieved_at",
    "request_context",
    "validation_errors",
    "observed_fields",
    "payload",
    "quarantined_at",
)


def load_dag_file(monkeypatch, file_name, connections=None):
    """Run one DAG file without Airflow installed; return the names it defines.

    `connections` maps an Airflow connection name to the object that
    `BaseHook.get_connection(name)` returns.
    """

    if connections is None:
        connections = {}

    def get_connection(name):
        return connections[name]

    airflow = ModuleType("airflow")
    airflow.__path__ = []
    sdk = ModuleType("airflow.sdk")
    sdk.dag = replace_with_do_nothing
    sdk.task = replace_with_do_nothing
    sdk.BaseHook = SimpleNamespace(get_connection=get_connection)
    sdk.__path__ = []
    exceptions = ModuleType("airflow.sdk.exceptions")
    exceptions.AirflowFailException = FakeAirflowFailException
    exceptions.AirflowSkipException = FakeAirflowSkipException
    timetables = ModuleType("airflow.timetables")
    timetables.__path__ = []
    interval = ModuleType("airflow.timetables.interval")
    interval.CronDataIntervalTimetable = FakeCronDataIntervalTimetable

    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.sdk", sdk)
    monkeypatch.setitem(sys.modules, "airflow.sdk.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "airflow.timetables", timetables)
    monkeypatch.setitem(sys.modules, "airflow.timetables.interval", interval)
    return runpy.run_path(str(DAGS_DIRECTORY / file_name))


DAG_SETTINGS = {}
DECORATED_FUNCTIONS = {}


class FakeAirflowFailException(Exception):
    """Stands in for Airflow's exception that fails a task without a retry."""


class FakeAirflowSkipException(Exception):
    """Stands in for Airflow's exception that marks a task skipped."""


def replace_with_do_nothing(function=None, **settings):
    """Stand in for @dag(...) and @task(...), and for a bare @task, so loading a
    DAG file runs no task.

    The settings of each @dag(...) are kept in DAG_SETTINGS by dag_id, so a
    test can read a schedule without Airflow. Each decorated function is kept
    in DECORATED_FUNCTIONS by name, so a test can run a DAG's body (which
    defines its tasks) and then call a task's body.
    """

    if "dag_id" in settings:
        DAG_SETTINGS[settings["dag_id"]] = settings

    def replace(decorated):
        DECORATED_FUNCTIONS[decorated.__name__] = decorated
        return do_nothing

    if function is None:
        return replace
    return replace(function)


def do_nothing():
    return None


class FakeCronDataIntervalTimetable:
    """Stand in for Airflow's CronDataIntervalTimetable; keeps its arguments."""

    def __init__(self, cron, timezone):
        self.cron = cron
        self.timezone = timezone


def stop_the_utc_clock_at(monkeypatch, function, moment):
    """Make datetime.now(timezone.utc) in the DAG file return `moment` when
    `function` runs. A reading in any other time zone fails the test."""

    def now(tz=None):
        assert tz is timezone.utc, f"datetime.now({tz!r}) is not a UTC reading"
        return moment

    monkeypatch.setitem(function.__globals__, "datetime", SimpleNamespace(now=now))


def connection_returning(row):
    """Return a fake connection whose cursor's fetchone() gives `row`."""

    conn = MagicMock()
    cursor_of(conn).fetchone.return_value = row
    return conn


def connection_returning_in_turn(*rows):
    """Return a fake connection whose cursor's fetchone() gives each row in turn."""

    conn = MagicMock()
    cursor_of(conn).fetchone.side_effect = rows
    return conn


def cursor_of(conn):
    """Return the fake cursor that `with conn.cursor() as cursor:` yields."""

    return conn.cursor.return_value.__enter__.return_value


def rows_inserted_by_table(conn):
    """The rows the fake connection's cursor was given to insert, by table."""

    rows_by_table = {}
    for call in cursor_of(conn).executemany.call_args_list:
        statement, rows = call.args
        table = statement.split()[2]
        if table not in rows_by_table:
            rows_by_table[table] = []
        rows_by_table[table].extend(rows)
    return rows_by_table


def quarantined_records(conn):
    """The rows inserted into raw.endpoint_quarantine, each as a dictionary of
    column name to value."""

    records = []
    for row in rows_inserted_by_table(conn).get("raw.endpoint_quarantine", []):
        records.append(dict(zip(QUARANTINE_COLUMNS, row, strict=True)))
    return records


def waits_before_each_retry(retry_policy, jitter):
    """Airflow waits retry_delay * 2**(n-1) before retry n, capped at
    max_retry_delay; its jitter adds up to the same again before the cap."""

    retries = retry_policy["retries"]
    if not retry_policy.get("retry_exponential_backoff"):
        return [retry_policy["retry_delay"]] * retries

    waits = []
    wait = retry_policy["retry_delay"] * jitter
    for _retry in range(retries):
        waits.append(min(wait, retry_policy["max_retry_delay"]))
        wait = wait * 2
    return waits


def shortest_retry_span(retry_policy):
    return sum(waits_before_each_retry(retry_policy, jitter=1), timedelta())


def longest_retry_span(retry_policy):
    """Every wait at its longest, and every try running to its time limit."""

    tries = retry_policy["retries"] + 1
    waits = sum(waits_before_each_retry(retry_policy, jitter=2), timedelta())
    return waits + tries * retry_policy["execution_timeout"]
