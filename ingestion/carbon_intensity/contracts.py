"""Carbon Intensity source contracts: each field's type, presence and nullability."""

FORECAST_SPEC = {
    "from": {"type": str, "required": True, "nullable": False},
    "to": {"type": str, "required": True, "nullable": False},
    "intensity": {
        "type": dict,
        "required": True,
        "nullable": False,
        "fields": {
            "forecast": {"type": int, "required": True, "nullable": False},
            "actual": {"type": int, "required": True, "nullable": True},
            "index": {"type": str, "required": True, "nullable": False},
        },
    },
}


OUTTURN_SPEC = {
    "from": {"type": str, "required": True, "nullable": False},
    "to": {"type": str, "required": True, "nullable": False},
    "intensity": {
        "type": dict,
        "required": True,
        "nullable": False,
        "fields": {
            "forecast": {"type": int, "required": True, "nullable": False},
            "actual": {"type": int, "required": True, "nullable": False},
            "index": {"type": str, "required": True, "nullable": False},
        },
    },
}
