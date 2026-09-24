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


# The full registry has many rows with no Elexon identifier and nullable
# descriptive fields. National Grid identity is present on every observed row.
BM_UNITS_SPEC = {
    "nationalGridBmUnit": {"type": str, "required": True, "nullable": False},
    "elexonBmUnit": {"type": str, "required": True, "nullable": True},
    "eic": {"type": str, "required": True, "nullable": True},
    "fuelType": {"type": str, "required": True, "nullable": True},
    "leadPartyName": {"type": str, "required": True, "nullable": True},
    "bmUnitType": {"type": str, "required": True, "nullable": True},
    "fpnFlag": {"type": bool, "required": True, "nullable": True},
    "bmUnitName": {"type": str, "required": True, "nullable": True},
    "leadPartyId": {"type": str, "required": True, "nullable": True},
    "demandCapacity": {"type": str, "required": True, "nullable": True},
    "generationCapacity": {"type": str, "required": True, "nullable": True},
    "productionOrConsumptionFlag": {
        "type": str,
        "required": True,
        "nullable": True,
    },
    "transmissionLossFactor": {"type": str, "required": True, "nullable": True},
    "workingDayCreditAssessmentImportCapability": {
        "type": str,
        "required": True,
        "nullable": True,
    },
    "nonWorkingDayCreditAssessmentImportCapability": {
        "type": str,
        "required": True,
        "nullable": True,
    },
    "workingDayCreditAssessmentExportCapability": {
        "type": str,
        "required": True,
        "nullable": True,
    },
    "nonWorkingDayCreditAssessmentExportCapability": {
        "type": str,
        "required": True,
        "nullable": True,
    },
    "creditQualifyingStatus": {"type": bool, "required": True, "nullable": False},
    "demandInProductionFlag": {"type": bool, "required": True, "nullable": False},
    "gspGroupId": {"type": str, "required": True, "nullable": True},
    "gspGroupName": {"type": str, "required": True, "nullable": True},
    "interconnectorId": {"type": str, "required": True, "nullable": True},
}
