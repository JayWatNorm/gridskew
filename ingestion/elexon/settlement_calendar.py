"""When a British settlement date starts in UTC, and how many periods it has."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
HALF_HOUR = timedelta(minutes=30)


def london_midnight_utc(settlement_date):
    """Return the UTC instant at which a British settlement date starts."""

    local_midnight = datetime(
        settlement_date.year,
        settlement_date.month,
        settlement_date.day,
        tzinfo=LONDON,
    )
    return local_midnight.astimezone(timezone.utc)


def expected_period_count(settlement_date):
    """Return 46, 48 or 50 settlement periods for a British date."""

    start = london_midnight_utc(settlement_date)
    end = london_midnight_utc(settlement_date + timedelta(days=1))
    return int((end - start) / HALF_HOUR)
