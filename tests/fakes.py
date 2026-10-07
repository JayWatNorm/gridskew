"""Stand-ins the tests use in place of Airflow and a database connection."""

import runpy
import sys
from datetime import datetime, timezone
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
    decorators = ModuleType("airflow.decorators")
    decorators.dag = replace_with_do_nothing
    decorators.task = replace_with_do_nothing
    hooks = ModuleType("airflow.hooks")
    hooks.__path__ = []
    base = ModuleType("airflow.hooks.base")
    base.BaseHook = SimpleNamespace(get_connection=get_connection)
    timetables = ModuleType("airflow.timetables")
    timetables.__path__ = []
    events = ModuleType("airflow.timetables.events")
    events.EventsTimetable = fake_events_timetable
    pendulum = ModuleType("pendulum")
    pendulum.datetime = utc_datetime

    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.decorators", decorators)
    monkeypatch.setitem(sys.modules, "airflow.hooks", hooks)
    monkeypatch.setitem(sys.modules, "airflow.hooks.base", base)
    monkeypatch.setitem(sys.modules, "airflow.timetables", timetables)
    monkeypatch.setitem(sys.modules, "airflow.timetables.events", events)
    monkeypatch.setitem(sys.modules, "pendulum", pendulum)
    return runpy.run_path(str(DAGS_DIRECTORY / file_name))


def replace_with_do_nothing(**_settings):
    """Stand in for @dag(...) and @task(...), so loading a DAG file runs no task."""

    def replace(_function):
        return do_nothing

    return replace


def do_nothing():
    return None


def fake_events_timetable(**_settings):
    return None


def utc_datetime(year, month, day, hour, tz):
    """Stand in for pendulum.datetime; a DAG must ask for UTC."""

    assert tz == "UTC"
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


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


def single_spaced(sql):
    return " ".join(sql.split())
