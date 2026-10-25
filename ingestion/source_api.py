"""The User-Agent, timestamp parsers and JSON encoder the pollers share."""

from datetime import datetime, timezone

import simplejson

USER_AGENT = "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"


def utc_from_text_with_seconds(value):
    """Parse a source time such as 2026-08-21T14:00:00Z as UTC."""

    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def utc_from_text_to_the_minute(value):
    """Parse a source time such as 2026-08-21T14:00Z as UTC."""

    return datetime.strptime(value, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def decimal_json_dumps(values):
    """Encode JSON without converting Decimal values, so numbers keep their
    published digits."""

    return simplejson.dumps(values, use_decimal=True)
