"""Read back by column name what the loaders and the quarantine write, and run
the B1610 coverage and REMIT quarantine queries against a real database.

The unit tests replace the database with mocks, so they cannot show which
column a value lands in, that a repeated load is ignored, or what a query
counts. Each read-back uses a second connection, so it also proves the
writer committed.

The loaders commit, so in the CI `test` job this runs after every step that
reads the fixtures, and deletes its own rows at the end.

Run from the repository root with the dbt environment set, for example:
    GRIDSKEW_DISPOSABLE_TEST=1 DBT_HOST=localhost DBT_DBNAME=gridskew_dev ...
    python -m tests.adhoc.check_database_writes
"""

import json
import os
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import psycopg

from ingestion.carbon_intensity.forecast_poller import load as load_forecast
from ingestion.carbon_intensity.forecast_poller import parse as parse_forecast
from ingestion.carbon_intensity.outturn_poller import load as load_outturn
from ingestion.carbon_intensity.outturn_poller import parse as parse_outturn
from ingestion.elexon import demand_backfill, qas_poller
from ingestion.elexon.b1610_poller import (
    decimal_json_dumps,
    recent_unit_count,
    stored_coverage,
)
from ingestion.elexon.b1610_poller import load as load_b1610
from ingestion.elexon.b1610_poller import parse as parse_b1610
from ingestion.elexon.boalf_poller import load as load_boalf
from ingestion.elexon.boalf_poller import parse as parse_boalf
from ingestion.elexon.demand_poller import load_indo, load_ndf, parse_indo, parse_ndf
from ingestion.elexon.pn_poller import load as load_pn
from ingestion.elexon.pn_poller import parse as parse_pn
from ingestion.elexon.qpn_poller import load as load_qpn
from ingestion.elexon.qpn_poller import parse as parse_qpn
from ingestion.elexon.remit_poller import load as load_remit
from ingestion.elexon.remit_poller import (
    newest_stored_publish_time,
    quarantined_row_count,
)
from ingestion.elexon.remit_poller import parse as parse_remit
from ingestion.routing import ContractRejected, quarantine_rows

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
# A capture time no fixture uses, so every row this check writes is its own.
CHECK_CAPTURE = datetime(2000, 1, 1, tzinfo=timezone.utc)
CHECK_CONTEXT = {"source": "ci_read_back"}
B1610_DATE = date(2025, 1, 10)
B1610_UNIT_PREFIX = "CI_RB_"
QAS_REJECT_DATE = date(2025, 1, 10)

# The columns in the order each parser builds its tuples.
PN_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "time_from",
    "time_to",
    "level_from",
    "level_to",
    "national_grid_bm_unit",
    "bm_unit",
    "retrieved_at",
)
BOALF_COLUMNS = (
    "national_grid_bm_unit",
    "bm_unit",
    "acceptance_number",
    "acceptance_time",
    "settlement_date",
    "settlement_period_from",
    "settlement_period_to",
    "time_from",
    "time_to",
    "level_from",
    "level_to",
    "so_flag",
    "deemed_bo_flag",
    "stor_flag",
    "rr_flag",
    "amendment_flag",
    "retrieved_at",
)
REMIT_COLUMNS = (
    "mrid",
    "revision_number",
    "publish_time",
    "created_time",
    "message_type",
    "event_type",
    "unavailability_type",
    "event_status",
    "asset_id",
    "affected_unit",
    "fuel_type",
    "normal_capacity",
    "available_capacity",
    "unavailable_capacity",
    "event_start_time",
    "event_end_time",
    "payload",
    "retrieved_at",
)
QAS_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "national_grid_bm_unit",
    "bm_unit",
    "bm_unit_applicable_balancing_services_volume",
    "retrieved_at",
)
NDF_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "boundary",
    "publish_time",
    "demand",
    "retrieved_at",
)
INDO_COLUMNS = (
    "settlement_date",
    "settlement_period",
    "start_time",
    "publish_time",
    "demand",
    "retrieved_at",
)
# A UTC publish day after the moved fixture rows' (19 August 2000).
HELD_PUBLISH_DAY = date(2000, 8, 20)
B1610_COLUMNS = (
    "bm_unit",
    "national_grid_bm_unit_id",
    "psr_type",
    "settlement_date",
    "settlement_period",
    "half_hour_end_time",
    "settlement_run_type",
    "quantity",
    "retrieved_at",
)


def disposable_connection(time_zone=None):
    """A connection to the disposable database; psycopg leaves out a None
    option, so the session keeps the server's time zone unless one is given."""

    options = f"-c TimeZone={time_zone}" if time_zone else None
    return psycopg.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
        dbname=os.environ["DBT_DBNAME"],
        options=options,
    )


def fixture_rows(relative_path):
    return json.loads((FIXTURES_DIR / relative_path).read_text(encoding="utf-8"))


def read_one_row(reader, query, params):
    """Return one row as a dictionary keyed by column name."""

    with reader.cursor() as cursor:
        cursor.execute(query, params)
        (row,) = cursor.fetchall()
        column_names = [column.name for column in cursor.description]
    reader.commit()
    return dict(zip(column_names, row, strict=True))


def check_quarantine_evidence(writer, reader):
    finding = {
        "row": {"bmUnit": "T_CHECK-1", "intensity": {"quantity": Decimal("123.4560")}},
        "index": 2,
        "errors": [{"field": "quantity", "error": "check"}],
    }
    quarantine_rows(
        [finding],
        writer,
        "CI_READ_BACK",
        CHECK_CAPTURE,
        CHECK_CONTEXT,
        payload_dumps=decimal_json_dumps,
    )

    stored = read_one_row(
        reader,
        "SELECT dataset, retrieved_at, request_context, validation_errors, "
        "observed_fields, payload -> 'intensity' ->> 'quantity' AS quantity "
        "FROM raw.endpoint_quarantine WHERE dataset = 'CI_READ_BACK'",
        (),
    )
    assert stored["retrieved_at"] == CHECK_CAPTURE
    assert stored["request_context"] == CHECK_CONTEXT
    assert stored["validation_errors"] == {
        "source_index": 2,
        "errors": [{"field": "quantity", "error": "check"}],
    }
    assert stored["observed_fields"] == ["bmUnit", "intensity", "intensity.quantity"]
    # Exactly the digits given: a float on the way would drop the last zero.
    assert stored["quantity"] == "123.4560"


def check_outturn_load(writer, reader):
    parsed_rows = parse_outturn(
        fixture_rows("carbon_intensity/outturn.json")["data"], CHECK_CAPTURE
    )
    load_outturn(parsed_rows, writer)
    # Adjacent windows share their boundary period: a repeat must be ignored.
    load_outturn(parsed_rows, writer)

    counted = read_one_row(
        reader,
        "SELECT count(*) AS stored FROM raw.carbon_intensity_outturn "
        "WHERE retrieved_at = %s",
        (CHECK_CAPTURE,),
    )
    assert counted["stored"] == len(parsed_rows) == 7
    stored = read_one_row(
        reader,
        "SELECT period_end, actual, forecast_final, intensity_index "
        "FROM raw.carbon_intensity_outturn "
        "WHERE retrieved_at = %s AND period_start = %s",
        (CHECK_CAPTURE, datetime(2026, 7, 31, 23, 30, tzinfo=timezone.utc)),
    )
    assert stored == {
        "period_end": datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        "actual": 166,
        "forecast_final": 156,
        "intensity_index": "moderate",
    }


def check_forecast_load(writer, reader):
    source_rows = fixture_rows("carbon_intensity/forecast_fw48h.json")["data"]
    load_forecast(parse_forecast(source_rows, CHECK_CAPTURE), writer)

    stored = read_one_row(
        reader,
        "SELECT period_end, forecast, actual, intensity_index "
        "FROM raw.carbon_intensity_forecast "
        "WHERE retrieved_at = %s AND period_start = %s",
        (CHECK_CAPTURE, datetime(2026, 8, 17, 7, 30, tzinfo=timezone.utc)),
    )
    assert stored == {
        "period_end": datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc),
        "forecast": 228,
        "actual": None,
        "intensity_index": "high",
    }


def check_remit_quarantine_count(writer):
    """A quarantined message counts until the same publication is stored."""

    before = quarantined_row_count(writer)
    stored_message = fixture_rows("elexon/remit_publications.json")[0]
    never_stored = deepcopy(stored_message)
    never_stored["mrid"] = "CI-READ-BACK-NEVER-STORED"
    findings = [
        {"row": stored_message, "index": 0, "errors": []},
        {"row": never_stored, "index": 1, "errors": []},
    ]
    quarantine_rows(
        findings,
        writer,
        "REMIT",
        CHECK_CAPTURE,
        CHECK_CONTEXT,
        payload_dumps=decimal_json_dumps,
    )

    assert quarantined_row_count(writer) == before + 1


def stored_row_named(reader, table, columns, key_columns, parsed_row):
    """Read back the stored row with the parsed row's key, by column name."""

    expected = dict(zip(columns, parsed_row, strict=True))
    conditions = " AND ".join(f"{column} = %s" for column in key_columns)
    stored = read_one_row(
        reader,
        f"SELECT {', '.join(columns)} FROM {table} WHERE {conditions}",
        [expected[column] for column in key_columns],
    )
    return expected, stored


def check_elexon_load(
    writer,
    reader,
    table,
    source_rows,
    parse,
    load,
    columns,
    key_columns,
    unread=(),
):
    """Every value of every row reads back from the column of its name, and
    loading the same capture again adds nothing. Columns in unread are
    stored but not compared."""

    parsed_rows = parse(source_rows, CHECK_CAPTURE)
    load(parsed_rows, writer)
    load(parsed_rows, writer)

    expected = {}
    for parsed_row in parsed_rows:
        named = dict(zip(columns, parsed_row, strict=True))
        for column in unread:
            del named[column]
        # Within one load the first row of a repeated key is the one kept.
        expected.setdefault(tuple(named[column] for column in key_columns), named)
    read_columns = [column for column in columns if column not in unread]
    stored = {}
    with reader.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(read_columns)} FROM {table} WHERE retrieved_at = %s",
            (CHECK_CAPTURE,),
        )
        for row in cursor.fetchall():
            named = dict(zip(read_columns, row, strict=True))
            stored[tuple(named[column] for column in key_columns)] = named
    reader.commit()
    assert stored == expected, f"{table}: stored rows differ from the parsed rows"


def boalf_rows_with_distinct_flags():
    """The fixture rows and two more acceptances whose flags differ in every
    pair, so any two flag columns swapped in the insert read back wrong."""

    rows = fixture_rows("elexon/boalf_stream.json")
    first = rows[0]
    rows.append(
        dict(
            first,
            acceptanceNumber=990001,
            soFlag=True,
            deemedBoFlag=False,
            storFlag=True,
            rrFlag=False,
        )
    )
    rows.append(
        dict(
            first,
            acceptanceNumber=990002,
            soFlag=True,
            deemedBoFlag=True,
            storFlag=False,
            rrFlag=False,
        )
    )
    return rows


def remit_rows_under_new_ids():
    """The REMIT fixture with every mrid renamed: the fixture loader has
    stored the originals, and a stored publication is never loaded again."""

    text = (FIXTURES_DIR / "elexon/remit_publications.json").read_text(encoding="utf-8")
    rows = json.loads(text, parse_float=Decimal)
    for row in rows:
        row["mrid"] = f"CI-RB-{row['mrid']}"
    return rows


def check_remit_window_ignores_a_future_dated_message(writer):
    """A message published before it was captured moves the window; one
    dated after its capture never does (it would stop all fetching)."""

    captured = datetime(2100, 1, 1, tzinfo=timezone.utc)
    template = remit_rows_under_new_ids()[0]
    published_before_capture = dict(
        template, mrid="CI-RB-NEWEST-PAST", publishTime="2099-06-01T00:00:00Z"
    )
    dated_after_capture = dict(
        template, mrid="CI-RB-NEWEST-FUTURE", publishTime="2101-01-01T00:00:00Z"
    )
    load_remit(
        parse_remit([published_before_capture, dated_after_capture], captured), writer
    )

    assert newest_stored_publish_time(writer) == datetime(
        2099, 6, 1, tzinfo=timezone.utc
    )


def b1610_rows(settlement_date, run_type, unit_count, periods):
    """Source rows for units CI_RB_1..unit_count, one per period."""

    rows = []
    for unit in range(1, unit_count + 1):
        for period in periods:
            rows.append(
                {
                    "dataset": "B1610",
                    "psrType": "Generation",
                    "bmUnit": f"{B1610_UNIT_PREFIX}{unit}",
                    "nationalGridBmUnitId": f"CI-RB-{unit}",
                    "settlementDate": settlement_date.isoformat(),
                    "settlementPeriod": period,
                    "halfHourEndTime": f"{settlement_date.isoformat()}T00:30:00",
                    "settlementRunType": run_type,
                    "quantity": Decimal("1.5"),
                }
            )
    return rows


def check_b1610_coverage_and_unit_baseline(writer):
    day = timedelta(days=1)
    source_rows = [
        # The date being checked: four units, periods 1 and 2.
        *b1610_rows(B1610_DATE, "SF", 4, [1, 2]),
        # Another run on the same date: not this run's coverage.
        *b1610_rows(B1610_DATE, "R1", 6, [3]),
        # The seven earlier dates of the run: at most three units.
        *b1610_rows(B1610_DATE - day, "SF", 1, [4]),
        *b1610_rows(B1610_DATE - 7 * day, "SF", 3, [1]),
        # Outside the seven dates, or another run inside them: ignored.
        *b1610_rows(B1610_DATE - 8 * day, "SF", 5, [1]),
        *b1610_rows(B1610_DATE - 2 * day, "R1", 6, [1]),
    ]
    load_b1610(parse_b1610(source_rows, CHECK_CAPTURE), writer)

    row_count, periods, unit_count = stored_coverage(writer, B1610_DATE, "SF")
    assert (row_count, sorted(periods), unit_count) == (8, [1, 2], 4)
    assert recent_unit_count(writer, B1610_DATE, "SF") == 3
    assert recent_unit_count(writer, B1610_DATE - 8 * day, "SF") is None


def check_b1610_keeps_the_first_capture(writer, reader):
    """B1610 keeps one row per run: a later capture of it changes nothing."""

    source_rows = b1610_rows(B1610_DATE, "SF", 1, [1])
    later_capture = CHECK_CAPTURE + timedelta(days=1)
    load_b1610(parse_b1610(source_rows, later_capture), writer)

    (first_capture_row,) = parse_b1610(source_rows, CHECK_CAPTURE)
    expected, stored = stored_row_named(
        reader,
        "raw.elexon_b1610",
        B1610_COLUMNS,
        ("bm_unit", "settlement_date", "settlement_period", "settlement_run_type"),
        first_capture_row,
    )
    assert stored == expected, (stored, expected)


def demand_rows_published_in_2000(relative_path):
    """The fixture's rows with each publish time moved to 2000, so they share
    no key with the CI fixtures already loaded."""

    rows = deepcopy(fixture_rows(relative_path)["data"])
    for row in rows:
        row["publishTime"] = "2000" + row["publishTime"][4:]
    return rows


def check_a_held_publish_day_is_a_utc_day(writer):
    """48 NDF issues on one UTC day hold that day in a London session too,
    though in summer its last two issues fall on the next London day."""

    template = fixture_rows("elexon/ndf_truncated.json")["data"][0]
    first_issue = datetime(2000, 8, 20, 0, 16, tzinfo=timezone.utc)
    rows = []
    for issue in range(demand_backfill.ISSUES_PER_PUBLISH_DAY):
        row = dict(template)
        published = first_issue + issue * timedelta(minutes=30)
        row["publishTime"] = published.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(row)
    load_ndf(parse_ndf(rows, CHECK_CAPTURE), writer)

    london = disposable_connection("Europe/London")
    try:
        held = demand_backfill.held_days(
            london, "NDF", [HELD_PUBLISH_DAY, HELD_PUBLISH_DAY + timedelta(days=1)]
        )
    finally:
        london.close()
    assert held == {HELD_PUBLISH_DAY}, f"NDF days held in a London session: {held}"


def check_qas_captures(writer, reader):
    """Every QAS value reads back from its own column, and a second capture of
    the same date is kept beside the first."""

    source_rows = json.loads(
        (FIXTURES_DIR / "elexon/qas_volumes.json").read_text(encoding="utf-8"),
        parse_float=Decimal,
    )["data"]
    first = qas_poller.parse(source_rows, CHECK_CAPTURE)
    second = qas_poller.parse(source_rows, CHECK_CAPTURE + timedelta(days=1))
    qas_poller.load(first, writer)
    qas_poller.load(second, writer)

    expected = {}
    for parsed_row in [*first, *second]:
        named = dict(zip(QAS_COLUMNS, parsed_row, strict=True))
        expected[tuple(parsed_row[:3]) + (named["retrieved_at"],)] = named
    stored = {}
    with reader.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(QAS_COLUMNS)} FROM raw.elexon_qas "
            "WHERE retrieved_at IN (%s, %s)",
            (CHECK_CAPTURE, CHECK_CAPTURE + timedelta(days=1)),
        )
        for row in cursor.fetchall():
            named = dict(zip(QAS_COLUMNS, row, strict=True))
            stored[tuple(row[:3]) + (named["retrieved_at"],)] = named
    reader.commit()
    assert stored == expected, "raw.elexon_qas: stored rows differ from the parsed rows"


def check_qas_rejected_row_loads_nothing(writer, reader):
    """One rejected row beside Decimal volumes is quarantined with its exact
    digits, and no row of its date is loaded."""

    rows = []
    for period in range(1, 49):
        rows.append(
            {
                "settlementDate": QAS_REJECT_DATE.isoformat(),
                "settlementPeriod": period,
                "bmUnitApplicableBalancingServicesVolume": Decimal("0.250"),
                "nationalGridBmUnit": "CI_RB_QAS-1",
                "bmUnit": "T_CI_RB_QAS-1",
                "time": "2025-01-10T00:00:00Z",
            }
        )
    rows[6]["nationalGridBmUnit"] = None
    rows[6]["bmUnitApplicableBalancingServicesVolume"] = Decimal("-1.500")

    with patch.object(qas_poller, "fetch", return_value={"data": rows}):
        try:
            qas_poller.run(writer, QAS_REJECT_DATE)
        except ContractRejected:
            pass
        else:
            raise AssertionError("a rejected QAS row did not fail the capture")

    loaded = read_one_row(
        reader,
        "SELECT count(*) AS loaded FROM raw.elexon_qas WHERE settlement_date = %s",
        (QAS_REJECT_DATE,),
    )
    assert loaded["loaded"] == 0, loaded
    quarantined = read_one_row(
        reader,
        "SELECT payload ->> 'bmUnitApplicableBalancingServicesVolume' AS volume "
        "FROM raw.endpoint_quarantine "
        "WHERE dataset = 'QAS' AND request_context ->> 'settlementDate' = %s",
        (QAS_REJECT_DATE.isoformat(),),
    )
    assert quarantined["volume"] == "-1.500", quarantined


def delete_check_rows(writer):
    with writer.cursor() as cursor:
        cursor.execute(
            "DELETE FROM raw.endpoint_quarantine WHERE request_context = %s::jsonb",
            (json.dumps(CHECK_CONTEXT),),
        )
        cursor.execute(
            "DELETE FROM raw.carbon_intensity_outturn WHERE retrieved_at = %s",
            (CHECK_CAPTURE,),
        )
        cursor.execute(
            "DELETE FROM raw.carbon_intensity_forecast WHERE retrieved_at = %s",
            (CHECK_CAPTURE,),
        )
        cursor.execute(
            "DELETE FROM raw.elexon_b1610 WHERE bm_unit LIKE %s",
            (f"{B1610_UNIT_PREFIX}%",),
        )
        for table in (
            "raw.elexon_pn",
            "raw.elexon_qpn",
            "raw.elexon_boalf",
            "raw.elexon_remit",
            "raw.elexon_ndf",
            "raw.elexon_indo",
        ):
            cursor.execute(
                f"DELETE FROM {table} WHERE retrieved_at = %s", (CHECK_CAPTURE,)
            )
        cursor.execute("DELETE FROM raw.elexon_remit WHERE mrid LIKE 'CI-RB-NEWEST-%'")
        cursor.execute(
            "DELETE FROM raw.elexon_qas WHERE retrieved_at IN (%s, %s)",
            (CHECK_CAPTURE, CHECK_CAPTURE + timedelta(days=1)),
        )
        cursor.execute(
            "DELETE FROM raw.endpoint_quarantine WHERE dataset = 'QAS' "
            "AND request_context ->> 'settlementDate' = %s",
            (QAS_REJECT_DATE.isoformat(),),
        )
    writer.commit()


def main():
    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError(
            "Database write checks require a local disposable gridskew_dev"
        )

    writer = disposable_connection()
    reader = disposable_connection()
    try:
        check_quarantine_evidence(writer, reader)
        check_outturn_load(writer, reader)
        check_forecast_load(writer, reader)
        check_remit_quarantine_count(writer)
        check_b1610_coverage_and_unit_baseline(writer)
        check_b1610_keeps_the_first_capture(writer, reader)
        check_remit_window_ignores_a_future_dated_message(writer)
        check_qas_captures(writer, reader)
        check_qas_rejected_row_loads_nothing(writer, reader)
        pn_key = ("national_grid_bm_unit", "time_from", "retrieved_at")
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_pn",
            fixture_rows("elexon/pn_stream.json"),
            parse_pn,
            load_pn,
            PN_COLUMNS,
            pn_key,
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_qpn",
            fixture_rows("elexon/qpn_stream.json"),
            parse_qpn,
            load_qpn,
            PN_COLUMNS,
            pn_key,
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_boalf",
            boalf_rows_with_distinct_flags(),
            parse_boalf,
            load_boalf,
            BOALF_COLUMNS,
            ("national_grid_bm_unit", "acceptance_number", "time_from", "retrieved_at"),
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_remit",
            remit_rows_under_new_ids(),
            parse_remit,
            load_remit,
            REMIT_COLUMNS,
            ("mrid", "revision_number", "publish_time", "created_time"),
            # The source row as jsonb, the one column of its type.
            unread=("payload",),
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_ndf",
            demand_rows_published_in_2000("elexon/ndf_truncated.json"),
            parse_ndf,
            load_ndf,
            NDF_COLUMNS,
            ("settlement_date", "settlement_period", "boundary", "publish_time"),
        )
        check_elexon_load(
            writer,
            reader,
            "raw.elexon_indo",
            demand_rows_published_in_2000("elexon/indo.json"),
            parse_indo,
            load_indo,
            INDO_COLUMNS,
            ("settlement_date", "settlement_period", "publish_time"),
        )
        check_a_held_publish_day_is_a_utc_day(writer)
    finally:
        writer.rollback()
        delete_check_rows(writer)
        writer.close()
        reader.close()
    print("Database writes: every value read back from its own column")


if __name__ == "__main__":
    main()
