"""Guard what each daily Elexon DAG task calls, with which arguments and in which
order, without Airflow, a database or the network."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from ingestion.routing import ContractRejected
from tests.fakes import DECORATED_FUNCTIONS, FakeAirflowFailException, load_dag_file

INTERVAL_START = datetime(2026, 10, 24, tzinfo=timezone.utc)
INTERVAL_END = datetime(2026, 10, 25, tzinfo=timezone.utc)
INTERVAL = {"data_interval_start": INTERVAL_START, "data_interval_end": INTERVAL_END}
SETTLEMENT = {"data_interval_start": INTERVAL_START}
# The Airflow connection BaseHook returns for "gridskew_prod", and the
# psycopg.connect call it must become.
GRIDSKEW_PROD = SimpleNamespace(
    host="postgres-prod",
    port=5433,
    schema="gridskew_prod",
    login="gridskew_loader",
    password="not-a-real-password",
)
OPEN_GRIDSKEW_PROD = call.connect(
    host="postgres-prod",
    port=5433,
    dbname="gridskew_prod",
    user="gridskew_loader",
    password="not-a-real-password",
)
B1610_POLLER = "ingestion.elexon.b1610_poller"
CAPTURE = f"{B1610_POLLER}.capture_settlement_date"

INTERVAL_TASKS = [
    pytest.param(
        "gridskew_elexon_pn", "poll_pn", "ingestion.elexon.pn_poller.run", id="PN"
    ),
    pytest.param(
        "gridskew_elexon_qpn", "poll_qpn", "ingestion.elexon.qpn_poller.run", id="QPN"
    ),
    pytest.param(
        "gridskew_elexon_boalf",
        "poll_boalf",
        "ingestion.elexon.boalf_poller.run",
        id="BOALF",
    ),
]
# Each settlement date is INTERVAL_START less the DAG's lag: 14, 35, 450 days.
SETTLEMENT_TASKS = [
    pytest.param(
        "gridskew_elexon_b1610_II", "poll_b1610_II", "II", date(2026, 10, 10), id="II"
    ),
    pytest.param(
        "gridskew_elexon_b1610_SF", "poll_b1610_SF", "SF", date(2026, 9, 19), id="SF"
    ),
    pytest.param(
        "gridskew_elexon_b1610_RF", "poll_b1610_RF", "RF", date(2025, 7, 31), id="RF"
    ),
]
# Every daily task, the poller function it hands the connection to, and the
# context Airflow passes it.
DAILY_TASKS = [
    pytest.param(
        "gridskew_elexon_pn",
        "poll_pn",
        "ingestion.elexon.pn_poller.run",
        INTERVAL,
        id="PN",
    ),
    pytest.param(
        "gridskew_elexon_qpn",
        "poll_qpn",
        "ingestion.elexon.qpn_poller.run",
        INTERVAL,
        id="QPN",
    ),
    pytest.param(
        "gridskew_elexon_boalf",
        "poll_boalf",
        "ingestion.elexon.boalf_poller.run",
        INTERVAL,
        id="BOALF",
    ),
    pytest.param(
        "gridskew_elexon_b1610_II", "poll_b1610_II", CAPTURE, SETTLEMENT, id="II"
    ),
    pytest.param(
        "gridskew_elexon_b1610_SF", "poll_b1610_SF", CAPTURE, SETTLEMENT, id="SF"
    ),
    pytest.param(
        "gridskew_elexon_b1610_RF", "poll_b1610_RF", CAPTURE, SETTLEMENT, id="RF"
    ),
]


def refuse_network(*args, **kwargs):
    raise RuntimeError("A DAG task test tried to reach the network")


def refuse_database(*args, **kwargs):
    raise RuntimeError("A DAG task test tried to open a database connection")


@pytest.fixture(autouse=True)
def no_network_or_database(monkeypatch):
    """Fail, instead of calling Elexon or PostgreSQL, if a task reaches a real
    poller or connection."""

    monkeypatch.setattr("requests.sessions.Session.request", refuse_network)
    monkeypatch.setattr("psycopg.connect", refuse_database)


def boundary_calls(replacing_run=None):
    """One mock whose children stand in for psycopg.connect, the B1610 replaced
    check and the poller function; its mock_calls lists every call a task makes
    across those boundaries, in order."""

    calls = MagicMock()
    calls.replaced_by_a_later_run.return_value = replacing_run
    return calls


def connection_of(calls):
    """The connection the fake psycopg.connect returns."""

    return calls.connect.return_value


def run_task(monkeypatch, dag_id, task_name, poll_path, context, calls):
    """Run one task body with the boundaries replaced by children of `calls`.

    The fake psycopg.connect stands in only while the task body runs; the
    guard that refuses a real connection is back once this returns.
    """

    load_dag_file(monkeypatch, f"{dag_id}_dag.py", {"gridskew_prod": GRIDSKEW_PROD})
    DECORATED_FUNCTIONS[dag_id]()
    task_body = DECORATED_FUNCTIONS[task_name]
    with (
        patch("psycopg.connect", calls.connect),
        patch(f"{B1610_POLLER}.replaced_by_a_later_run", calls.replaced_by_a_later_run),
        patch(poll_path, calls.poll),
    ):
        task_body(**context)


@pytest.mark.parametrize("dag_id,task_name,poll_path", INTERVAL_TASKS)
def test_interval_task_polls_its_whole_interval_on_gridskew_prod_then_closes_it(
    monkeypatch, dag_id, task_name, poll_path
):
    calls = boundary_calls()

    run_task(monkeypatch, dag_id, task_name, poll_path, INTERVAL, calls)

    assert calls.mock_calls == [
        OPEN_GRIDSKEW_PROD,
        call.poll(connection_of(calls), INTERVAL_START, INTERVAL_END),
        call.connect().close(),
    ]


@pytest.mark.parametrize("dag_id,task_name,run_type,settlement_date", SETTLEMENT_TASKS)
def test_settlement_task_checks_its_run_is_in_force_then_captures_on_gridskew_prod(
    monkeypatch, dag_id, task_name, run_type, settlement_date
):
    calls = boundary_calls()

    run_task(monkeypatch, dag_id, task_name, CAPTURE, SETTLEMENT, calls)

    assert calls.mock_calls == [
        call.replaced_by_a_later_run(settlement_date, run_type),
        OPEN_GRIDSKEW_PROD,
        call.poll(connection_of(calls), settlement_date, run_type),
        call.connect().close(),
    ]


@pytest.mark.parametrize("dag_id,task_name,run_type,settlement_date", SETTLEMENT_TASKS)
def test_settlement_task_fails_at_once_without_a_connection_once_its_run_is_replaced(
    monkeypatch, dag_id, task_name, run_type, settlement_date
):
    calls = boundary_calls(replacing_run="DF")

    with pytest.raises(FakeAirflowFailException) as failure:
        run_task(monkeypatch, dag_id, task_name, CAPTURE, SETTLEMENT, calls)

    assert str(failure.value) == (
        f"{run_type} for {settlement_date.isoformat()} has been replaced by DF "
        "and can no longer be captured"
    )
    assert calls.mock_calls == [call.replaced_by_a_later_run(settlement_date, run_type)]


@pytest.mark.parametrize("dag_id,task_name,poll_path,context", DAILY_TASKS)
def test_a_contract_rejection_fails_the_task_without_a_retry(
    monkeypatch, dag_id, task_name, poll_path, context
):
    calls = boundary_calls()
    rejection = ContractRejected("3 rows quarantined")
    calls.poll.side_effect = rejection

    with pytest.raises(
        FakeAirflowFailException, match="^3 rows quarantined$"
    ) as failure:
        run_task(monkeypatch, dag_id, task_name, poll_path, context, calls)

    assert failure.value.__cause__ is rejection


@pytest.mark.parametrize("dag_id,task_name,poll_path,context", DAILY_TASKS)
def test_any_other_poll_error_is_left_for_airflow_to_retry(
    monkeypatch, dag_id, task_name, poll_path, context
):
    calls = boundary_calls()
    outage = requests.HTTPError("503 Server Error")
    calls.poll.side_effect = outage

    with pytest.raises(requests.HTTPError) as failure:
        run_task(monkeypatch, dag_id, task_name, poll_path, context, calls)

    assert failure.value is outage


@pytest.mark.parametrize("dag_id,task_name,poll_path,context", DAILY_TASKS)
def test_the_connection_is_closed_when_the_poll_fails(
    monkeypatch, dag_id, task_name, poll_path, context
):
    calls = boundary_calls()
    calls.poll.side_effect = requests.HTTPError("503 Server Error")

    with pytest.raises(requests.HTTPError):
        run_task(monkeypatch, dag_id, task_name, poll_path, context, calls)

    connection_of(calls).close.assert_called_once_with()
