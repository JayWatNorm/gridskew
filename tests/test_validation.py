import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.carbon_intensity.contracts import FORECAST_SPEC, OUTTURN_SPEC
from ingestion.elexon.contracts import B1610_SPEC, PN_SPEC, QPN_SPEC
from ingestion.validation import validate_row, validate_rows

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def load_rows(relative_path, *, data_envelope=False, preserve_decimals=False):
    fixture_text = (FIXTURE_ROOT / relative_path).read_text(encoding="utf-8")
    if preserve_decimals:
        payload = json.loads(fixture_text, parse_float=Decimal)
    else:
        payload = json.loads(fixture_text)
    return payload["data"] if data_envelope else payload


def assert_fixture_matches_contract(
    relative_path,
    spec,
    *,
    data_envelope=False,
    preserve_decimals=False,
):
    rows = load_rows(
        relative_path,
        data_envelope=data_envelope,
        preserve_decimals=preserve_decimals,
    )

    findings = validate_rows(rows, spec)

    assert findings
    assert all(finding["errors"] == [] for finding in findings)
    assert all(finding["warnings"] == [] for finding in findings)


@pytest.fixture
def pn_row():
    return load_rows("elexon/pn_stream.json")[0].copy()


@pytest.fixture
def forecast_row():
    return deepcopy(
        load_rows("carbon_intensity/forecast_fw48h.json", data_envelope=True)[0]
    )


def test_validate_row_accepts_a_valid_row(pn_row):
    errors, warnings = validate_row(pn_row, PN_SPEC)

    assert errors == []
    assert warnings == []


def test_validate_row_reports_a_missing_required_field(pn_row):
    del pn_row["settlementPeriod"]

    errors, warnings = validate_row(pn_row, PN_SPEC)

    assert errors == ["Missing required field: settlementPeriod"]
    assert warnings == []


def test_validate_row_rejects_a_forbidden_null(pn_row):
    pn_row["settlementPeriod"] = None

    errors, warnings = validate_row(pn_row, PN_SPEC)

    assert errors == ["Null not permitted: settlementPeriod"]
    assert warnings == []


def test_validate_row_accepts_an_allowed_null(pn_row):
    pn_row["bmUnit"] = None

    errors, warnings = validate_row(pn_row, PN_SPEC)

    assert errors == []
    assert warnings == []


@pytest.mark.parametrize(
    "invalid_value",
    [pytest.param("39", id="string"), pytest.param(True, id="boolean")],
)
def test_validate_row_rejects_an_incorrect_exact_type(pn_row, invalid_value):
    pn_row["settlementPeriod"] = invalid_value

    errors, warnings = validate_row(pn_row, PN_SPEC)

    assert errors == ["Invalid data type detected: settlementPeriod"]
    assert warnings == []


def test_validate_row_warns_about_an_unexpected_field(pn_row):
    pn_row["newPublisherField"] = "example"

    errors, warnings = validate_row(pn_row, PN_SPEC)

    assert errors == []
    assert warnings == ["Unexpected field: newPublisherField"]


def test_validate_row_warns_about_a_missing_optional_field(pn_row):
    del pn_row["dataset"]

    errors, warnings = validate_row(pn_row, PN_SPEC)

    assert errors == []
    assert warnings == ["Optional field is missing: dataset"]


def test_validate_row_reports_both_sides_of_a_renamed_field(pn_row):
    pn_row["settlementPeriodNumber"] = pn_row.pop("settlementPeriod")

    errors, warnings = validate_row(pn_row, PN_SPEC)

    assert errors == ["Missing required field: settlementPeriod"]
    assert warnings == ["Unexpected field: settlementPeriodNumber"]


def test_validate_row_reports_a_missing_nested_field(forecast_row):
    del forecast_row["intensity"]["actual"]

    errors, warnings = validate_row(forecast_row, FORECAST_SPEC)

    assert errors == ["Missing required field: intensity.actual"]
    assert warnings == []


def test_validate_row_reports_an_unexpected_nested_field(forecast_row):
    forecast_row["intensity"]["publisherStatus"] = "provisional"

    errors, warnings = validate_row(forecast_row, FORECAST_SPEC)

    assert errors == []
    assert warnings == ["Unexpected field: intensity.publisherStatus"]


def test_validate_row_rejects_a_non_dictionary_nested_value(forecast_row):
    forecast_row["intensity"] = "not a dictionary"

    errors, warnings = validate_row(forecast_row, FORECAST_SPEC)

    assert errors == ["Invalid data type detected: intensity"]
    assert warnings == []


def test_validate_rows_keeps_checking_after_a_non_dictionary_item(pn_row):
    findings = validate_rows([pn_row, None, pn_row.copy()], PN_SPEC)

    assert [finding["index"] for finding in findings] == [0, 1, 2]
    assert findings[0]["errors"] == []
    assert findings[1]["row"] is None
    assert findings[1]["errors"] == ["Invalid row type: expected dictionary"]
    assert findings[2]["errors"] == []


def test_validate_rows_does_not_change_nested_source_rows():
    rows = load_rows("carbon_intensity/outturn.json", data_envelope=True)
    original_rows = deepcopy(rows)

    validate_rows(rows, OUTTURN_SPEC)

    assert rows == original_rows


def test_pn_fixture_matches_its_contract():
    assert_fixture_matches_contract("elexon/pn_stream.json", PN_SPEC)


def test_qpn_fixture_matches_its_contract():
    assert_fixture_matches_contract("elexon/qpn_stream.json", QPN_SPEC)


def test_b1610_fixture_matches_its_contract():
    assert_fixture_matches_contract(
        "elexon/b1610_stream.json",
        B1610_SPEC,
        preserve_decimals=True,
    )


def test_forecast_fixture_matches_its_contract():
    assert_fixture_matches_contract(
        "carbon_intensity/forecast_fw48h.json",
        FORECAST_SPEC,
        data_envelope=True,
    )


def test_outturn_fixture_matches_its_contract():
    assert_fixture_matches_contract(
        "carbon_intensity/outturn.json",
        OUTTURN_SPEC,
        data_envelope=True,
    )


def test_b1610_contract_accepts_integer_and_decimal_quantities():
    row = load_rows(
        "elexon/b1610_stream.json",
        preserve_decimals=True,
    )[1].copy()

    decimal_errors, _ = validate_row(row, B1610_SPEC)
    row["quantity"] = 1
    integer_errors, _ = validate_row(row, B1610_SPEC)

    assert decimal_errors == []
    assert integer_errors == []


def test_b1610_contract_rejects_a_binary_float_quantity():
    row = load_rows(
        "elexon/b1610_stream.json",
        preserve_decimals=True,
    )[1].copy()
    row["quantity"] = 1.5

    errors, warnings = validate_row(row, B1610_SPEC)

    assert errors == ["Invalid data type detected: quantity"]
    assert warnings == []


def test_forecast_allows_null_actual_but_outturn_rejects_it(forecast_row):
    forecast_errors, _ = validate_row(forecast_row, FORECAST_SPEC)
    outturn_errors, _ = validate_row(forecast_row, OUTTURN_SPEC)

    assert forecast_errors == []
    assert outturn_errors == ["Null not permitted: intensity.actual"]
