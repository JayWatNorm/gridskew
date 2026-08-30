from decimal import Decimal

PN_SPEC = {
    "dataset": {
        "type": str,
        "required": False,
        "nullable": False,
    },
    "settlementDate": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "settlementPeriod": {
        "type": int,
        "required": True,
        "nullable": False,
    },
    "timeFrom": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "timeTo": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "levelFrom": {
        "type": int,
        "required": True,
        "nullable": False,
    },
    "levelTo": {
        "type": int,
        "required": True,
        "nullable": False,
    },
    "nationalGridBmUnit": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "bmUnit": {
        "type": str,
        "required": True,
        "nullable": True,
    },
}

QPN_SPEC = {
    "dataset": {
        "type": str,
        "required": False,
        "nullable": False,
    },
    "settlementDate": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "settlementPeriod": {
        "type": int,
        "required": True,
        "nullable": False,
    },
    "timeFrom": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "timeTo": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "levelFrom": {
        "type": int,
        "required": True,
        "nullable": False,
    },
    "levelTo": {
        "type": int,
        "required": True,
        "nullable": False,
    },
    "nationalGridBmUnit": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "bmUnit": {
        "type": str,
        "required": True,
        "nullable": True,
    },
}

B1610_SPEC = {
    "dataset": {
        "type": str,
        "required": False,
        "nullable": False,
    },
    "psrType": {
        "type": str,
        "required": True,
        "nullable": True,
    },
    "bmUnit": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "nationalGridBmUnitId": {
        "type": str,
        "required": True,
        "nullable": True,
    },
    "settlementDate": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "settlementPeriod": {
        "type": int,
        "required": True,
        "nullable": False,
    },
    "halfHourEndTime": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "settlementRunType": {
        "type": str,
        "required": True,
        "nullable": False,
    },
    "quantity": {
        "type": (int, Decimal),
        "required": True,
        "nullable": False,
    },
}
