import json
import logging
import os
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2

from ingestion.carbon_intensity.contracts import FORECAST_SPEC, OUTTURN_SPEC
from ingestion.carbon_intensity.forecast_poller import load as load_forecast
from ingestion.carbon_intensity.forecast_poller import parse as parse_forecast
from ingestion.carbon_intensity.outturn_poller import load as load_outturn
from ingestion.carbon_intensity.outturn_poller import parse as parse_outturn
from ingestion.elexon.b1610_poller import load as load_b1610
from ingestion.elexon.b1610_poller import parse as parse_b1610
from ingestion.elexon.bmunits_poller import load_extract as load_bm_units
from ingestion.elexon.bmunits_poller import validate_extract as validate_bm_units
from ingestion.elexon.contracts import B1610_SPEC, PN_SPEC, QPN_SPEC
from ingestion.elexon.pn_poller import load as load_pn
from ingestion.elexon.pn_poller import parse as parse_pn
from ingestion.elexon.qpn_poller import load as load_qpn
from ingestion.elexon.qpn_poller import parse as parse_qpn
from ingestion.routing import process_rows

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def run_ddl(conn):
    """Run the initialization scripts to create the schemas and raw tables."""
    ddl_dir = Path(__file__).resolve().parent.parent.parent / "sql" / "init"
    with conn.cursor() as cursor:
        for file in sorted(ddl_dir.glob("*.sql")):
            cursor.execute(file.read_text(encoding="utf-8"))
    conn.commit()


def load_fixture(
    conn,
    rel_path,
    spec,
    dataset,
    parse_fn,
    load_fn,
    *,
    data_envelope=False,
    preserve_decimals=False,
):
    """Parse a JSON fixture and load it into the database using ingestion logic."""
    fixture_path = FIXTURES_DIR / rel_path
    if preserve_decimals:
        payload = json.loads(
            fixture_path.read_text(encoding="utf-8"), parse_float=Decimal
        )
    else:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    rows = payload["data"] if data_envelope else payload
    retrieved_at = datetime.now(timezone.utc)

    rejected_count = process_rows(
        rows,
        spec=spec,
        dataset=dataset,
        conn=conn,
        retrieved_at=retrieved_at,
        request_context={"source": "ci_fixture"},
        parse_rows=parse_fn,
        load_rows=load_fn,
    )
    if rejected_count:
        raise RuntimeError(
            f"Validation failed: {rejected_count} rows quarantined from {rel_path}"
        )


def main():
    logging.basicConfig(level=logging.INFO)

    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError("Fixtures require explicit opt-in to local gridskew_dev")

    conn = psycopg2.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        dbname=os.environ["DBT_DBNAME"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
    )

    try:
        run_ddl(conn)
        load_fixture(conn, "elexon/pn_stream.json", PN_SPEC, "PN", parse_pn, load_pn)
        load_fixture(
            conn, "elexon/qpn_stream.json", QPN_SPEC, "QPN", parse_qpn, load_qpn
        )
        load_fixture(
            conn,
            "elexon/b1610_stream.json",
            B1610_SPEC,
            "B1610",
            parse_b1610,
            load_b1610,
            preserve_decimals=True,
        )
        bm_units = json.loads(
            (FIXTURES_DIR / "elexon/bmunits_truncated.json").read_text(encoding="utf-8")
        )
        extra_eic = deepcopy(bm_units[1])
        extra_eic["eic"] = "48W00001ACHRW-1R"
        bm_units.insert(2, extra_eic)
        rejected, unit_count = validate_bm_units(bm_units)
        if rejected:
            raise RuntimeError("BM-unit CI fixture failed source validation")
        load_bm_units(conn, bm_units, datetime.now(timezone.utc), unit_count)
        load_fixture(
            conn,
            "carbon_intensity/forecast_fw48h.json",
            FORECAST_SPEC,
            "CARBON_FORECAST",
            parse_forecast,
            load_forecast,
            data_envelope=True,
        )
        load_fixture(
            conn,
            "carbon_intensity/outturn.json",
            OUTTURN_SPEC,
            "CARBON_OUTTURN",
            parse_outturn,
            load_outturn,
            data_envelope=True,
        )
        logging.info("All CI fixtures loaded successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
