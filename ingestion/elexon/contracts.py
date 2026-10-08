from decimal import Decimal

PN_SPEC = {
    "dataset": {"type": str, "required": False, "nullable": False},
    "settlementDate": {"type": str, "required": True, "nullable": False},
    "settlementPeriod": {"type": int, "required": True, "nullable": False},
    "timeFrom": {"type": str, "required": True, "nullable": False},
    "timeTo": {"type": str, "required": True, "nullable": False},
    "levelFrom": {"type": int, "required": True, "nullable": False},
    "levelTo": {"type": int, "required": True, "nullable": False},
    "nationalGridBmUnit": {"type": str, "required": True, "nullable": False},
    "bmUnit": {"type": str, "required": True, "nullable": True},
}

QPN_SPEC = {
    "dataset": {"type": str, "required": False, "nullable": False},
    "settlementDate": {"type": str, "required": True, "nullable": False},
    "settlementPeriod": {"type": int, "required": True, "nullable": False},
    "timeFrom": {"type": str, "required": True, "nullable": False},
    "timeTo": {"type": str, "required": True, "nullable": False},
    "levelFrom": {"type": int, "required": True, "nullable": False},
    "levelTo": {"type": int, "required": True, "nullable": False},
    "nationalGridBmUnit": {"type": str, "required": True, "nullable": False},
    "bmUnit": {"type": str, "required": True, "nullable": True},
}

BOALF_SPEC = {
    "dataset": {"type": str, "required": False, "nullable": False},
    "settlementDate": {"type": str, "required": True, "nullable": False},
    "settlementPeriodFrom": {"type": int, "required": True, "nullable": False},
    "settlementPeriodTo": {"type": int, "required": True, "nullable": False},
    "timeFrom": {"type": str, "required": True, "nullable": False},
    "timeTo": {"type": str, "required": True, "nullable": False},
    "levelFrom": {"type": int, "required": True, "nullable": False},
    "levelTo": {"type": int, "required": True, "nullable": False},
    "acceptanceNumber": {"type": int, "required": True, "nullable": False},
    "acceptanceTime": {"type": str, "required": True, "nullable": False},
    "deemedBoFlag": {"type": bool, "required": True, "nullable": False},
    "soFlag": {"type": bool, "required": True, "nullable": False},
    "amendmentFlag": {"type": str, "required": True, "nullable": True},
    "storFlag": {"type": bool, "required": True, "nullable": False},
    "rrFlag": {"type": bool, "required": True, "nullable": False},
    "nationalGridBmUnit": {"type": str, "required": True, "nullable": False},
    "bmUnit": {"type": str, "required": True, "nullable": True},
}

# The API marks only revisionNumber and the three times below as never null.
# mrid is not null here because it is part of the table key. A field is
# required when every observed row carried it.
REMIT_SPEC = {
    "dataset": {"type": str, "required": False, "nullable": False},
    "mrid": {"type": str, "required": True, "nullable": False},
    "revisionNumber": {"type": int, "required": True, "nullable": False},
    "publishTime": {"type": str, "required": True, "nullable": False},
    "createdTime": {"type": str, "required": True, "nullable": False},
    "messageType": {"type": str, "required": True, "nullable": True},
    "messageHeading": {"type": str, "required": True, "nullable": True},
    "eventType": {"type": str, "required": False, "nullable": True},
    "unavailabilityType": {"type": str, "required": False, "nullable": True},
    "participantId": {"type": str, "required": True, "nullable": True},
    "registrationCode": {"type": str, "required": True, "nullable": True},
    "assetId": {"type": str, "required": True, "nullable": True},
    "assetType": {"type": str, "required": False, "nullable": True},
    "affectedUnit": {"type": str, "required": False, "nullable": True},
    "affectedUnitEIC": {"type": str, "required": False, "nullable": True},
    "affectedArea": {"type": str, "required": False, "nullable": True},
    "biddingZone": {"type": str, "required": False, "nullable": True},
    "fuelType": {"type": str, "required": False, "nullable": True},
    "normalCapacity": {"type": (int, Decimal), "required": False, "nullable": True},
    "availableCapacity": {
        "type": (int, Decimal),
        "required": False,
        "nullable": True,
    },
    "unavailableCapacity": {
        "type": (int, Decimal),
        "required": False,
        "nullable": True,
    },
    "eventStatus": {"type": str, "required": True, "nullable": True},
    "eventStartTime": {"type": str, "required": True, "nullable": False},
    "eventEndTime": {"type": str, "required": True, "nullable": True},
    "durationUncertainty": {"type": str, "required": False, "nullable": True},
    "cause": {"type": str, "required": True, "nullable": True},
    "relatedInformation": {"type": str, "required": False, "nullable": True},
    "outageProfile": {"type": list, "required": False, "nullable": True},
}

B1610_SPEC = {
    "dataset": {"type": str, "required": False, "nullable": False},
    "psrType": {"type": str, "required": True, "nullable": True},
    "bmUnit": {"type": str, "required": True, "nullable": False},
    "nationalGridBmUnitId": {"type": str, "required": True, "nullable": True},
    "settlementDate": {"type": str, "required": True, "nullable": False},
    "settlementPeriod": {"type": int, "required": True, "nullable": False},
    "halfHourEndTime": {"type": str, "required": True, "nullable": False},
    "settlementRunType": {"type": str, "required": True, "nullable": False},
    "quantity": {"type": (int, Decimal), "required": True, "nullable": False},
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
