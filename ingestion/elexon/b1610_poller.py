import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


# fetching the data from the API
def fetch(from_date, to_date):
    response = requests.get(
        "https://data.elexon.co.uk/bmrs/api/v1/datasets/B1610/stream",
        params={
            "from": from_date.strftime("%Y-%m-%dT%H:%MZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%MZ"),
        },
        headers={
            "User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"
        },
        timeout=60,
    )
    response.raise_for_status()
    return json.loads(response.text, parse_float=Decimal)


# dag will be handling catchup and batches, default set to yesterday for manual runs
def run(conn, from_date=None, to_date=None):
    retrieved_at = datetime.now(timezone.utc)
    retrieved_at_day_start = retrieved_at.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # if either of the dates are missing, pass yesterday, also protects against accidently running 1 year of data.
    if to_date is None or from_date is None:
        from_date = retrieved_at_day_start - timedelta(days=15)
        to_date = retrieved_at_day_start - timedelta(days=14)
    result = fetch(from_date, to_date)
    par_result = parse(result, retrieved_at)
    load(par_result, conn)
    return


def parse(results, retrieved_at):
    rows = [
        (
            result["bmUnit"],
            result["nationalGridBmUnitId"],
            result["psrType"],
            date.fromisoformat(result["settlementDate"]),
            result["settlementPeriod"],
            datetime.strptime(result["halfHourEndTime"], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            ),
            result["settlementRunType"],
            result["quantity"],
            retrieved_at,
        )
        for result in results
    ]
    return rows


def load(results, conn):
    insert_sql = (
        "INSERT INTO raw.elexon_b1610 (bm_unit, national_grid_bm_unit_id, psr_type, "
        "settlement_date, settlement_period, half_hour_end_time, settlement_run_type,"
        "quantity, retrieved_at) VALUES %s ON CONFLICT (bm_unit,"
        "settlement_date, settlement_period, settlement_run_type) DO NOTHING"
    )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, results, page_size=1000)
    conn.commit()


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )

    try:
        run(conn)
    finally:
        conn.close()
