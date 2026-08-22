import json
import pathlib
from datetime import date, datetime, timedelta, timezone

from ingestion.elexon.pn_poller import parse

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "elexon" / "pn_stream.json"

# Deliberately not on a period boundary, and distinct from every other value in
# the golden tuple, so that a positional swap cannot pass unnoticed.
RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)


def test_parse():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    parsed_results = parse(results, RETRIEVED_AT)

    # One row out per entry in: parse drops nothing and duplicates nothing.
    assert len(parsed_results) == len(results)
    # results json hasnt been cleared
    assert len(results) > 0

    # Golden value. The expected side is written by hand from the fixture, not
    # derived from it, so it independently verifies both the string-to-datetime
    # conversion and the position of every column in the tuple.
    assert parsed_results[18] == (
        date(2026, 8, 21),
        31,
        datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc),
        250,
        474,
        "DRAXX-1",
        "T_DRAXX-1",
        RETRIEVED_AT,
    )

    # Rules that must hold for every row.
    for i, row in enumerate(parsed_results, start=1):
        assert row[2] < row[3]  # ends after starts
        assert row[8] == RETRIEVED_AT, (
            f"row {i}: retrieved_at is {row[2]}, expected {RETRIEVED_AT}"
        )
        assert (row[1] >= 1) & (row[1] <= 50)  # settlement peroids are valid
        assert type(row[0]) is date  # typecheck
        assert type(row[2]) is datetime  # typecheck
        assert type(row[3]) is datetime  # typecheck
        assert row[2].utcoffset() == timedelta(0)
        assert row[3].utcoffset() == timedelta(0)

    # no duplicates
    key = [(row[6], row[2]) for row in parsed_results]
    assert len(set(key)) == len(key), "duplicate key in batch"
    # ramp still exists
    ramp = [(row[4] - row[5]) for row in parsed_results]
    assert len(set(ramp)) > 1, "ramp still presnet"

    # more than one BM unit
    assert len({row[6] for row in parsed_results}) > 1
