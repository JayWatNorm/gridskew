"""The User-Agent, timestamp helpers, JSON encoder and Carbon Intensity
envelope check the pollers share."""

from datetime import datetime, timezone

import simplejson

USER_AGENT = "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"


def utc_from_text_with_seconds(value):
    """Parse a source time such as 2026-08-21T14:00:00Z as UTC."""

    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def utc_from_text_to_the_minute(value):
    """Parse a source time such as 2026-08-21T14:00Z as UTC."""

    return datetime.strptime(value, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def utc_text_to_the_minute(value):
    """Write an aware time as UTC source text such as 2026-08-21T14:00Z.

    The time may be in any zone: a time read from the database comes back in
    the session's zone, which need not be UTC.
    """

    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def decimal_json_dumps(values):
    """Encode JSON without converting Decimal values, so numbers keep their
    published digits."""

    return simplejson.dumps(values, use_decimal=True)


def carbon_intensity_rows(response, dataset):
    """Return the rows of a valid Carbon Intensity response envelope.

    `dataset` ("forecast" or "outturn") names the API in the error message.
    """

    if not isinstance(response, dict):
        raise RuntimeError(
            f"Invalid response returned from Carbon Intensity {dataset} API: "
            "expected a non-empty data list"
        )

    rows = response.get("data")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            f"Invalid response returned from Carbon Intensity {dataset} API: "
            "expected a non-empty data list"
        )
    return rows
