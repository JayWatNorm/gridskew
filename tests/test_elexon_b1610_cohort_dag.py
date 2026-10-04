"""Guard the bounded cohort date mapping without running Airflow."""

from datetime import date, timedelta

import pytest
from fakes import load_dag_file


@pytest.fixture
def cohort_dag(monkeypatch):
    return load_dag_file(monkeypatch, "gridskew_elexon_b1610_cohort_dag.py")


def test_cohort_captures_are_exactly_seven_r1_dates(cohort_dag):
    assert cohort_dag["COHORT_CAPTURES"] == {
        date(2026, 10, 5): ("R1", date(2026, 8, 10)),
        date(2026, 10, 6): ("R1", date(2026, 8, 11)),
        date(2026, 10, 7): ("R1", date(2026, 8, 12)),
        date(2026, 10, 8): ("R1", date(2026, 8, 13)),
        date(2026, 10, 9): ("R1", date(2026, 8, 14)),
        date(2026, 10, 10): ("R1", date(2026, 8, 15)),
        date(2026, 10, 11): ("R1", date(2026, 8, 16)),
    }


def test_cohort_captures_match_the_fixed_settlement_offsets(cohort_dag):
    offsets = {"R1": timedelta(days=56), "RF": timedelta(days=430)}
    for capture_date, capture in cohort_dag["COHORT_CAPTURES"].items():
        run_type, settlement_date = capture
        assert capture_date - settlement_date == offsets[run_type]


def test_cohort_events_are_the_capture_dates_at_six_utc(cohort_dag):
    events = cohort_dag["COHORT_EVENTS"]
    capture_dates = sorted(cohort_dag["COHORT_CAPTURES"])

    assert len(events) == len(capture_dates)
    for event, capture_date in zip(events, capture_dates, strict=True):
        assert event.date() == capture_date
        assert event.hour == 6
        assert event.utcoffset() == timedelta(0)


def test_cohort_capture_refuses_an_unlisted_date(cohort_dag):
    assert cohort_dag["cohort_capture"](date(2026, 10, 5)) == ("R1", date(2026, 8, 10))
    with pytest.raises(RuntimeError, match="No B1610 cohort capture"):
        cohort_dag["cohort_capture"](date(2026, 10, 12))
