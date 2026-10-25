import pytest

from tests.fakes import DECORATED_FUNCTIONS, FakeAirflowFailException, load_dag_file

INTERVAL_DAGS = [
    ("gridskew_elexon_b1610_II", "poll_b1610_II"),
    ("gridskew_elexon_b1610_SF", "poll_b1610_SF"),
    ("gridskew_elexon_b1610_RF", "poll_b1610_RF"),
    ("gridskew_elexon_pn", "poll_pn"),
    ("gridskew_elexon_qpn", "poll_qpn"),
    ("gridskew_elexon_boalf", "poll_boalf"),
    ("gridskew_elexon_qas", "capture_14_days"),
    ("gridskew_elexon_qas", "capture_35_days"),
    ("gridskew_elexon_qas", "capture_450_days"),
]


@pytest.mark.parametrize("dag_id, task_name", INTERVAL_DAGS)
def test_a_run_without_a_data_interval_fails_without_a_retry(
    monkeypatch, dag_id, task_name
):
    load_dag_file(monkeypatch, f"{dag_id}_dag.py")
    run_dag_body = DECORATED_FUNCTIONS[dag_id]
    run_dag_body()
    capture = DECORATED_FUNCTIONS[task_name]

    with pytest.raises(FakeAirflowFailException, match="No data interval"):
        capture(data_interval_start=None)
