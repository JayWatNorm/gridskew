import json
import pathlib
from datetime import datetime, timedelta, timezone
from itertools import pairwise

import pytest

from ingestion.carbon_intensity.outturn_poller import chunker, parse

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "outturn_sample.json"

# Deliberately not on a period boundary and distinct from every other value in
# the golden tuple, so a positional swap cannot pass unnoticed.
RETRIEVED_AT = datetime(2026, 8, 19, 21, 43, 17, tzinfo=timezone.utc)

# One year, chosen so the final chunk is short and the clamp is exercised.
WINDOW_START = datetime(2025, 8, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 20, tzinfo=timezone.utc)


@pytest.fixture
def payload():
    """The captured API response, loaded fresh for each test that asks for it."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def chunks():
    """A year split into 30 day windows."""
    return chunker(WINDOW_END, WINDOW_START, WINDOW_START + timedelta(days=30))


# chunker
def test_chunker_covers_the_whole_window(chunks):
    """The first chunk starts where asked, the last ends where asked."""
    assert chunks[0][0] == WINDOW_START
    assert chunks[-1][1] == WINDOW_END


def test_chunker_produces_expected_count(chunks):
    """365 days at 30 days per chunk is 12 full chunks plus a 5+ day remainder."""
    assert len(chunks) == 13


def test_chunker_never_exceeds_the_api_limit(chunks):
    """The API caps a single request at roughly 30 days."""
    for i, (start, end) in enumerate(chunks, start=1):
        assert end - start <= timedelta(days=30), (
            f"chunk {i} spans {end - start}, over the limit"
        )


def test_chunker_final_chunk_is_clamped(chunks):
    """Without min() the last chunk would run past the requested end."""
    assert chunks[-1][1] - chunks[-1][0] == timedelta(days=5)


def test_chunker_leaves_no_gaps(chunks):
    """Each chunk ends exactly where the next begins.

    A gap here loses data silently. An overlap only produces a duplicate, which
    the primary key catches, so this is the invariant that matters.
    """
    for i, (current, following) in enumerate(pairwise(chunks), start=1):
        assert current[1] == following[0], (
            f"gap between chunk {i} ending {current[1]} and chunk {i + 1} "
            f"starting {following[0]}"
        )


# parse
def test_parse_returns_one_row_per_entry(payload):
    """parse drops nothing and duplicates nothing."""
    assert len(parse(payload, RETRIEVED_AT)) == len(payload["data"])


def test_parse_golden_value(payload):
    """Known input, known output, written by hand from the fixture.

    The expected side is not derived from the payload, so this independently
    verifies the string to datetime conversion and the position of every column.
    It is the only assertion that can catch actual and forecast_final being
    swapped, since both are integers.
    """
    rows = parse(payload, RETRIEVED_AT)

    assert rows[0] == (
        datetime(2026, 7, 31, 23, 30, tzinfo=timezone.utc),
        datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        RETRIEVED_AT,
        166,
        156,
        "moderate",
    )


def test_parse_row_rules(payload):
    """Properties that must hold for every row, not just the first."""
    rows = parse(payload, RETRIEVED_AT)

    for i, row in enumerate(rows, start=1):
        assert row[1] - row[0] == timedelta(minutes=30), (
            f"row {i}: period is {row[1] - row[0]}, expected 30 minutes"
        )
        assert row[2] == RETRIEVED_AT, (
            f"row {i}: retrieved_at is {row[2]}, expected {RETRIEVED_AT}"
        )
        assert row[3] is not None, (
            f"row {i}: actual is None, but this fixture is a settled window"
        )
        assert row[4] > 0, f"row {i}: forecast_final is {row[4]}"
        assert row[0].utcoffset() == timedelta(0), f"row {i}: {row[0]} is not UTC"


def test_parse_no_duplicate_period_starts(payload):
    """Mirrors PRIMARY KEY (period_start, retrieved_at).

    retrieved_at is constant across a batch, so a repeated period_start would be
    a guaranteed unique violation on insert.
    """
    period_starts = [row[0] for row in parse(payload, RETRIEVED_AT)]
    assert len(set(period_starts)) == len(period_starts)
