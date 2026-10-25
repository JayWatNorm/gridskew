import json
from decimal import Decimal

from ingestion.source_api import decimal_json_dumps


def test_decimals_keep_their_published_digits():
    payload = decimal_json_dumps({"availableCapacity": Decimal("49.920")})

    assert payload == '{"availableCapacity": 49.920}'
    assert json.loads(payload, parse_float=Decimal) == {
        "availableCapacity": Decimal("49.920")
    }
