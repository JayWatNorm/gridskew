import logging
import os
from datetime import datetime, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


def fetch(timestamp):
    response = requests.get(
        f"https://api.carbonintensity.org.uk/intensity/{timestamp}/fw48h", timeout=10
    )
    response.raise_for_status()
    return response.json()


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
            result["intensity"]["forecast"],
            result["intensity"]["actual"],
            result["intensity"]["index"],
        )
        for result in results["data"]
    ]
    return rows


def load(results, conn):
    insert_sql = (
        "INSERT INTO raw.carbon_intensity_forecast (period_start, "
        "period_end, retrieved_at, forecast, actual, intensity_index) VALUES %s"
    )

    with conn.cursor() as cursor:
        execute_values(cursor, insert_sql, results)
    conn.commit()


def run(conn):
    retrieved_at = datetime.now(timezone.utc)
    timestamp = retrieved_at.strftime("%Y-%m-%dT%H:%MZ")
    resultset = fetch(timestamp)
    parsed_results = parse(resultset, retrieved_at)

    load(parsed_results, conn)
    logger.info(
        "Retrieved %s rows at %s", len(parsed_results), retrieved_at.isoformat()
    )


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
