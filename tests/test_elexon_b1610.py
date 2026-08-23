import json
import pathlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from ingestion.elexon.b1610_poller import parse

FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "elexon" / "b1610_stream.json"
)

# the golden tuple, so that a positional swap cannot pass unnoticed.
RETRIEVED_AT = datetime(2026, 8, 17, 7, 47, 13, tzinfo=timezone.utc)


def test_parse():
    results = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"), parse_float=Decimal)

    parsed_results = parse(results, RETRIEVED_AT)

    # Golden value. The expected side is written by hand from the fixture, not
    # derived from it, so it independently verifies both the string-to-datetime
    # conversion and the position of every column in the tuple.
    assert parsed_results[10] == (
        "E__MDRX001",
        "DRAXD-1",
        "Generation",
        date(2026, 8, 9),
        2,
        datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc),
        "II",  # this does nothing really.
        Decimal("-15.069"),
        RETRIEVED_AT,
    )

    # set tests
    # One row out per entry in: parse drops nothing and duplicates nothing.
    assert len(parsed_results) == len(results)
    # results json hasnt been cleared
    assert len(results) > 0
    # no duplicates date peroid runtype
    key = [(row[0], row[3], row[4], row[6]) for row in parsed_results]
    assert len(set(key)) == len(key), "duplicate key in batch"
    # more than one BM unit
    assert len({row[0] for row in parsed_results}) > 1

    # fixture doesn't contain the case they check
    assert any(row[1] is None for row in parsed_results)
    assert any(row[1] is not None for row in parsed_results)
    assert any(row[7] < 0 for row in parsed_results)
    assert any(row[7] == 0 for row in parsed_results)
    assert any(row[4] == 1 for row in parsed_results)
    assert len({row[3] for row in parsed_results}) > 1

    # Rules that must hold for every row.
    for row in parsed_results:
        assert type(row[3]) is date  # typecheck
        assert type(row[5]) is datetime  # typecheck
        assert row[5].utcoffset() == timedelta(0)  # tz-aware check
        assert row[0] is not None  # not null check on bm unit
        assert (row[4] >= 1) & (row[4] <= 50)  # settlement peroids are valid
        assert row[8] == RETRIEVED_AT

        # local time check for settlement dates against half hour end times
        if row[4] == 2:
            assert row[3] == row[5].date()
        if row[4] == 1:
            assert row[3] == row[5].date() + timedelta(days=1)

        # decimal type and dp check
        assert not isinstance(row[7], float)
        assert -row[7].as_tuple().exponent == 3
