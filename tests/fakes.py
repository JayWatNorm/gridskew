"""Stand-ins the tests use in place of Airflow and a database connection."""

import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

DAGS_DIRECTORY = Path(__file__).resolve().parents[1] / "dags"


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


def replace_with_do_nothing(**settings):
    """Stand in for @dag(...) and @task(...), so loading a DAG file runs no task.

    The settings of each @dag(...) are kept in DAG_SETTINGS by dag_id, so a
    test can read a schedule without Airflow. Each decorated function is kept
    in DECORATED_FUNCTIONS by name, so a test can run a DAG's body (which
    defines its tasks) and then call a task's body.
    """

    if "dag_id" in settings:
        DAG_SETTINGS[settings["dag_id"]] = settings

    def replace(function):
        DECORATED_FUNCTIONS[function.__name__] = function
        return do_nothing

    return replace


def do_nothing():
    return None


class FakeCronDataIntervalTimetable:
    """Stand in for Airflow's CronDataIntervalTimetable; keeps its arguments."""

    def __init__(self, cron, timezone):
        self.cron = cron
        self.timezone = timezone


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
