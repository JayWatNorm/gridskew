import json
import pathlib
from datetime import datetime, timedelta, timezone

from ingestion.carbon_intensity.forecast_poller import parse

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "fw48h_sample.json"

# Deliberately not on a period boundary, and distinct from every other value in
# the golden tuple, so that a positional swap cannot pass unnoticed.
RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)


def test_parse():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    parsed_results = parse(results, RETRIEVED_AT)

    # One row out per entry in: parse drops nothing and duplicates nothing.
    assert len(parsed_results) == len(results["data"])

    # Golden value. The expected side is written by hand from the fixture, not
    # derived from it, so it independently verifies both the string-to-datetime
    # conversion and the position of every column in the tuple.
    assert parsed_results[0] == (
        datetime(2026, 8, 17, 7, 30, tzinfo=timezone.utc),
        datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc),
        RETRIEVED_AT,
        228,
        None,
        "high",
    )

    # Rules that must hold for every row.
    for i, row in enumerate(parsed_results, start=1):
        assert row[1] - row[0] == timedelta(minutes=30), (
            f"row {i}: period is {row[1] - row[0]}, expected 30 minutes"
        )
        assert row[2] == RETRIEVED_AT, (
            f"row {i}: retrieved_at is {row[2]}, expected {RETRIEVED_AT}"
        )
        assert row[3] > 0, f"row {i}: forecast is {row[3]}"
        assert row[4] is None, f"row {i}: actual is {row[4]}, expected None"
        assert row[0].utcoffset() == timedelta(0), f"row {i}: {row[0]} is not UTC"

    # Mirrors PRIMARY KEY (period_start, retrieved_at). retrieved_at is constant
    # across a batch, so a repeated period_start is a guaranteed insert failure.
    period_starts = [row[0] for row in parsed_results]
    assert len(set(period_starts)) == len(period_starts), (
        "duplicate period_start in batch"
    )
