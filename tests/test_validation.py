import json
import pathlib
from copy import deepcopy
from decimal import Decimal

from ingestion.elexon.contracts import B1610_SPEC, PN_SPEC, QPN_SPEC
from ingestion.validation import run, validate_row

PN_FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "elexon" / "pn_stream.json"
)
QPN_FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "elexon" / "qpn_stream.json"
)
B1610_FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "elexon" / "b1610_stream.json"
)


def test_valid_pn_row():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == []
    assert warnings == []


def test_pn_run_reports_non_dictionary_row():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row_valid_1 = results[0].copy()
    row_valid_2 = results[1].copy()

    findings = run([row_valid_1, None, row_valid_2], PN_SPEC)
    assert len(findings) == 3
    assert findings[0]["errors"] == []
    assert findings[1]["index"] == 1
    assert findings[1]["row"] is None
    assert findings[1]["errors"] == ["Invalid row type: expected dictionary"]
    assert findings[1]["warnings"] == []
    assert findings[2]["errors"] == []


def test_pn_missing_required_field():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    del row["settlementPeriod"]
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Missing required field: settlementPeriod"]
    assert warnings == []


def test_pn_rejects_null_settlement_period():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["settlementPeriod"] = None
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Null not permitted: settlementPeriod"]
    assert warnings == []


def test_pn_accepts_null_bm_unit():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["bmUnit"] = None
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == []
    assert warnings == []


def test_pn_requires_bm_unit_key():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    row = results[0].copy()
    del row["bmUnit"]
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Missing required field: bmUnit"]
    assert warnings == []


def test_pn_rejects_string_settlement_period():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["settlementPeriod"] = "39"
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Invalid data type detected: settlementPeriod"]
    assert warnings == []


def test_pn_rejects_boolean_settlement_period():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["settlementPeriod"] = True
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Invalid data type detected: settlementPeriod"]
    assert warnings == []


def test_pn_warns_on_unexpected_field():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    row = results[0].copy()
    row["newPublisherField"] = "example"
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == []
    assert warnings == ["Unexpected field: newPublisherField"]


def test_pn_detects_renamed_settlement_period():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    row["settlementPeriodNumber"] = row.pop("settlementPeriod")
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == ["Missing required field: settlementPeriod"]
    assert warnings == ["Unexpected field: settlementPeriodNumber"]


def test_pn_warns_on_missing_dataset():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    row = results[0].copy()
    del row["dataset"]
    errors, warnings = validate_row(row, PN_SPEC)
    assert errors == []
    assert warnings == ["Optional field is missing: dataset"]


def test_pn_run_reports_valid_and_invalid_rows():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
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


def test_pn_run_returns_empty_findings_for_empty_input():
    findings = run([], PN_SPEC)
    assert findings == []


def test_pn_run_does_not_mutate_results():
    with open(PN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    original_results = deepcopy(results)
    run(results, PN_SPEC)
    assert results == original_results


# QPN Test, contract is identical to PN, but to test that the validation works for QPN as well


def test_qpn_fixture_matches_contract():
    with open(QPN_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    findings = run(results, QPN_SPEC)

    for finding in findings:
        assert finding["errors"] == []
        assert finding["warnings"] == []


def test_b1610_accepts_decimal_quantity():
    with open(B1610_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f, parse_float=Decimal)

    row = results[1].copy()
    assert type(row["quantity"]) is Decimal
    errors, warnings = validate_row(row, B1610_SPEC)
    assert errors == []
    assert warnings == []


def test_b1610_accepts_int_quantity():
    with open(B1610_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f, parse_float=Decimal)

    row = results[1].copy()
    row["quantity"] = 1
    assert type(row["quantity"]) is int
    errors, warnings = validate_row(row, B1610_SPEC)
    assert errors == []
    assert warnings == []


def test_b1610_rejects_float_quantity():
    with open(B1610_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f, parse_float=Decimal)

    row = results[1].copy()
    row["quantity"] = 1.5
    assert type(row["quantity"]) is float
    errors, warnings = validate_row(row, B1610_SPEC)
    assert errors == ["Invalid data type detected: quantity"]
    assert warnings == []


def test_b1610_fixture_matches_contract():
    with open(B1610_FIXTURE_PATH, "r", encoding="utf-8") as f:
        results = json.load(f, parse_float=Decimal)

    findings = run(results, B1610_SPEC)

    for finding in findings:
        assert finding["errors"] == []
        assert finding["warnings"] == []
