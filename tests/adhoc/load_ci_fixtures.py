import json
import logging
import os
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

    # Use the same default fallback as the pollers, but respect standard env vars
    conn = psycopg2.connect(
        host=os.getenv("DBT_HOST", "localhost"),
        port=os.getenv("DBT_PORT", "5432"),
        dbname=os.getenv("DBT_DBNAME", "gridskew_dev"),
        user=os.getenv("DBT_USER", "postgres"),
        password=os.getenv("DBT_PASSWORD", "password"),
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
