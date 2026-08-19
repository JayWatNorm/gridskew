import logging
import os
from datetime import datetime, timedelta, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


# fetching the data from the API
def fetch(from_date, to_date):
    from_date = from_date.strftime("%Y-%m-%dT%H:%MZ")
    to_date = to_date.strftime("%Y-%m-%dT%H:%MZ")
    response = requests.get(
        f"https://api.carbonintensity.org.uk/intensity/{from_date}/{to_date}",
        headers={
            "User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


# def chunker(now_time, from_date, to_date,  rows, chunk_size=30):
# rows = rows + [[from_date, to_date]]
# gap = to_date - now_time
# if gap.days < 30:
# chunk_size = gap.days
# chunker(now_time, to_date, to_date + timedelta(days=chunk_size), rows, chunk_size)
# if chunk_size <= 0:
# return rows
# return(rows)


# doing a loop instead of recusion, although that was fun it wasnt the right way to do it.
def chunker(yest_date, from_date, to_date, chunk_size=30):
    rows = []
    while from_date < yest_date:
        to_date = min(to_date, yest_date)
        rows.append([from_date, to_date])
        from_date = to_date
        to_date = to_date + timedelta(days=chunk_size)
    return rows


# checks the backfill data for the last 365 days, and then checks the last 30 days of data every day
def data_checker(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(*), min(period_start) as first_window, max(period_start) as last_window FROM raw.carbon_intensity_outturn;"
        )
        lst_data = cursor.fetchall()
    return lst_data


# runs the backfill if not populated or the last 1-7 days ago worth of actual & final forecasts
def run(conn):
    chunker_run = False
    retrieved_at = datetime.now(timezone.utc)
    yest_date = datetime.now(timezone.utc) - timedelta(days=1)
    horizon_time = yest_date - timedelta(days=365)
    from_date = horizon_time
    to_date = horizon_time + timedelta(days=30)
    lstdata = data_checker(conn)
    from_7d = yest_date - timedelta(days=7)
    logger.info(
        "Data checker results: %s  rows from %s to %s",
        lstdata[0][0],
        lstdata[0][1],
        lstdata[0][2],
    )
    if lstdata[0][2] is None or lstdata[0][1] > horizon_time:
        chunker_run = True
    else:
        chunker_run = False
    if chunker_run:
        rows = chunker(yest_date, from_date, to_date)
        for row in rows:
            from_date = row[0]
            to_date = row[1]
            bf_resultset = fetch(from_date, to_date)
            bf_parsed_results = parse(bf_resultset, retrieved_at)
            load(bf_parsed_results, conn)
            logger.info(
                "Retrieved %s rows at %s",
                len(bf_parsed_results),
                datetime.now(timezone.utc).isoformat(),
            )
    else:
        resultset = fetch(from_7d, yest_date)
        parsed_results = parse(resultset, retrieved_at)
        load(parsed_results, conn)
        logger.info(
            "Retrieved %s rows at %s",
            len(parsed_results),
            datetime.now(timezone.utc).isoformat(),
        )


def parse(results, retrieved_at):
    rows = [
        (
            datetime.strptime(result["from"], "%Y-%m-%dT%H:%MZ").replace(
                tzinfo=timezone.utc
            ),
            datetime.strptime(result["to"], "%Y-%m-%dT%H:%MZ").replace(
                tzinfo=timezone.utc
            ),
            retrieved_at,
            result["intensity"]["actual"],
            result["intensity"]["forecast"],
            result["intensity"]["index"],
        )
        for result in results["data"]
    ]
    return rows


def load(results, conn):
    insert_sql = (
        "INSERT INTO raw.carbon_intensity_outturn (period_start, "
        "period_end, retrieved_at,  actual, forecast_final, intensity_index) VALUES %s ON CONFLICT (period_start, retrieved_at) DO NOTHING"
    )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, results)
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
