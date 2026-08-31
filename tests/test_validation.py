import json
import pathlib

from ingestion.elexon.contracts import PN_SPEC
from ingestion.validation import run, validate_row

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "elexon" / "pn_stream.json"


def test_valid_pn_row():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == []
    assert warnings == []


def test_pn_missing_required_field():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    del row["settlementPeriod"]
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Missing required field: settlementPeriod"]
    assert warnings == []


def test_pn_rejects_null_settlement_period():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["settlementPeriod"] = None
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Null not permitted: settlementPeriod"]
    assert warnings == []


def test_pn_accepts_null_bm_unit():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["bmUnit"] = None
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == []
    assert warnings == []


def test_pn_requires_bm_unit_key():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    row = results[0].copy()
    del row["bmUnit"]
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Missing required field: bmUnit"]
    assert warnings == []


def test_pn_rejects_string_settlement_period():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["settlementPeriod"] = "39"
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Invalid data type detected: settlementPeriod"]
    assert warnings == []


def test_pn_rejects_boolean_settlement_period():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["settlementPeriod"] = True
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Invalid data type detected: settlementPeriod"]
    assert warnings == []


def test_pn_warns_on_unexpected_field():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    row = results[0].copy()
    row["newPublisherField"] = "example"
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == []
    assert warnings == ["Unexpected field: newPublisherField"]


def test_pn_detects_renamed_settlement_period():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["settlementPeriodNumber"] = row.pop("settlementPeriod")
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Missing required field: settlementPeriod"]
    assert warnings == ["Unexpected field: settlementPeriodNumber"]


def test_pn_warns_on_missing_dataset():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    del row["dataset"]
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == []
    assert warnings == ["Optional field is missing: dataset"]


def test_run_reports_valid_and_invalid_rows():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    valid_row = results[0].copy()
    invalid_row = results[0].copy()
    del invalid_row["settlementPeriod"]
    findings = run([valid_row, invalid_row], PN_SPEC)
    assert len(findings) == 2
    assert findings[0]["index"] == 0
    assert findings[1]["index"] == 1
    assert findings[0]["errors"] == []
    assert findings[0]["warnings"] == []
    assert findings[1]["errors"] == ["Missing required field: settlementPeriod"]
    assert findings[1]["warnings"] == []
    assert findings[1]["row"] is invalid_row
    assert findings[0]["row"] is valid_row


def test_run_returns_empty_findings_for_empty_input():
    findings = run([], PN_SPEC)
    assert findings == []
