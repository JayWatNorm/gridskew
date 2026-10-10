"""Guard that every daily Elexon capture keeps a full-day data interval.

Airflow 3 turns "@daily" into a timetable whose data interval has no length,
so a poller asked for that interval would fetch nothing and succeed. The
explicit timetable means the same on Airflow 2 and 3.
"""

import pytest
from fakes import DAG_SETTINGS, FakeCronDataIntervalTimetable, load_dag_file

DAILY_INTERVAL_DAGS = [
    pytest.param("gridskew_elexon_pn_dag.py", "gridskew_elexon_pn", id="PN"),
    pytest.param("gridskew_elexon_qpn_dag.py", "gridskew_elexon_qpn", id="QPN"),
    pytest.param("gridskew_elexon_boalf_dag.py", "gridskew_elexon_boalf", id="BOALF"),
    pytest.param(
        "gridskew_elexon_b1610_II_dag.py", "gridskew_elexon_b1610_II", id="II"
    ),
    pytest.param(
        "gridskew_elexon_b1610_SF_dag.py", "gridskew_elexon_b1610_SF", id="SF"
    ),
]


@pytest.mark.parametrize("file_name,dag_id", DAILY_INTERVAL_DAGS)
def test_daily_capture_declares_a_utc_midnight_data_interval(
    monkeypatch, file_name, dag_id
):
    load_dag_file(monkeypatch, file_name)

    schedule = DAG_SETTINGS[dag_id]["schedule"]

    assert isinstance(schedule, FakeCronDataIntervalTimetable)
    assert schedule.cron == "0 0 * * *"
    assert schedule.timezone == "UTC"
