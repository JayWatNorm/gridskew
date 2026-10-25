import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from ingestion.carbon_intensity.forecast_poller import parse
from ingestion.carbon_intensity.forecast_poller import run as run_poller
from ingestion.routing import ContractRejected

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


@pytest.mark.parametrize(
    "response",
    [
        pytest.param([], id="list"),
        pytest.param({}, id="missing-data"),
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
    with (
        patch(
            "ingestion.carbon_intensity.forecast_poller.fetch",
            return_value=payload,
        ) as mock_fetch,
        patch(
            "ingestion.carbon_intensity.forecast_poller.insert_rows"
        ) as mock_insert_rows,
    ):
        with pytest.raises(ValueError, match="first period"):
            run_poller(MagicMock())

    inserted_rows = mock_insert_rows.call_args.args[2]
    assert len(inserted_rows) == len(payload["data"])
    retrieved_at = inserted_rows[0][2]
    mock_fetch.assert_called_once_with(retrieved_at.strftime("%Y-%m-%dT%H:%MZ"))


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
        with pytest.raises(ContractRejected, match="Quarantined 1 row"):
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
    (warning,) = caplog.records
    assert warning.name == "ingestion.carbon_intensity.forecast_poller"
    assert warning.levelno == logging.WARNING
