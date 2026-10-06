import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from fakes import single_spaced

from ingestion.carbon_intensity.contracts import FORECAST_SPEC
from ingestion.carbon_intensity.forecast_poller import load, parse
from ingestion.carbon_intensity.forecast_poller import run as run_poller

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "carbon_intensity" / "forecast_fw48h.json"
)
RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)


@pytest.fixture
def payload():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_parse_maps_a_source_row_to_the_database_tuple(payload):
    source_rows = payload["data"]

    parsed_rows = parse(source_rows, RETRIEVED_AT)

    assert len(parsed_rows) == len(source_rows)
    assert parsed_rows[0] == (
        datetime(2026, 8, 17, 7, 30, tzinfo=timezone.utc),
        datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc),
        RETRIEVED_AT,
        228,
        None,
        "high",
    )


def test_load_uses_the_expected_columns_and_commits():
    parsed_rows = [Mock(name="parsed_row")]
    conn = MagicMock()

    with patch(
        "ingestion.carbon_intensity.forecast_poller.execute_values"
    ) as mock_execute_values:
        load(parsed_rows, conn)

    _cursor, insert_sql, inserted_rows = mock_execute_values.call_args.args
    assert single_spaced(insert_sql) == (
        "INSERT INTO raw.carbon_intensity_forecast (period_start, period_end, "
        "retrieved_at, forecast, actual, intensity_index) "
        "VALUES %s"
    )
    assert inserted_rows == parsed_rows
    conn.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(None, id="none"),
        pytest.param([], id="list"),
        pytest.param({}, id="missing-data"),
        pytest.param({"data": {}}, id="data-not-a-list"),
        pytest.param({"data": []}, id="empty-data"),
    ],
)
def test_run_rejects_an_invalid_response_envelope(response):
    conn = Mock()

    with (
        patch(
            "ingestion.carbon_intensity.forecast_poller.fetch",
            return_value=response,
        ),
        patch(
            "ingestion.carbon_intensity.forecast_poller.process_rows"
        ) as mock_process_rows,
    ):
        with pytest.raises(RuntimeError, match="expected a non-empty data list"):
            run_poller(conn)

    mock_process_rows.assert_not_called()


def test_run_stores_rows_before_checking_completeness(payload):
    conn = Mock()
    events = []

    def record_storage(*args, **kwargs):
        events.append("stored")
        return 0

    def record_completeness_check(*args, **kwargs):
        events.append("checked")

    with (
        patch(
            "ingestion.carbon_intensity.forecast_poller.fetch",
            return_value=payload,
        ) as mock_fetch,
        patch(
            "ingestion.carbon_intensity.forecast_poller.process_rows",
            side_effect=record_storage,
        ) as mock_process_rows,
        patch(
            "ingestion.carbon_intensity.forecast_poller.validate_forecast_periods",
            side_effect=record_completeness_check,
        ) as mock_validate_completeness,
    ):
        run_poller(conn)

    retrieved_at = mock_process_rows.call_args.kwargs["retrieved_at"]
    timestamp = retrieved_at.strftime("%Y-%m-%dT%H:%MZ")
    mock_fetch.assert_called_once_with(timestamp)
    mock_process_rows.assert_called_once_with(
        payload["data"],
        spec=FORECAST_SPEC,
        dataset="CI_Forecast",
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={"timestamp": timestamp, "horizon_hours": 48},
        parse_rows=parse,
        load_rows=load,
    )
    mock_validate_completeness.assert_called_once_with(payload["data"], retrieved_at)
    assert events == ["stored", "checked"]


def test_run_fails_after_shared_routing_rejects_a_row(payload):
    conn = Mock()

    with (
        patch(
            "ingestion.carbon_intensity.forecast_poller.fetch",
            return_value=payload,
        ),
        patch(
            "ingestion.carbon_intensity.forecast_poller.process_rows",
            return_value=1,
        ),
        patch(
            "ingestion.carbon_intensity.forecast_poller.validate_forecast_periods"
        ) as mock_validate_completeness,
    ):
        with pytest.raises(RuntimeError, match="Quarantined 1 row"):
            run_poller(conn)

    mock_validate_completeness.assert_not_called()


def test_run_stores_a_short_window_once_and_succeeds_with_a_warning(payload, caplog):
    short_window = {"data": payload["data"][:80]}

    with (
        patch(
            "ingestion.carbon_intensity.forecast_poller.fetch",
            return_value=short_window,
        ),
        patch(
            "ingestion.carbon_intensity.forecast_poller.process_rows",
            return_value=0,
        ) as mock_process_rows,
        patch("ingestion.carbon_intensity.forecast_poller.validate_forecast_periods"),
        caplog.at_level("WARNING", logger="ingestion.carbon_intensity.forecast_poller"),
    ):
        run_poller(Mock())

    mock_process_rows.assert_called_once()
    assert "shorter than 48 hours: stored 80 rows" in caplog.text
