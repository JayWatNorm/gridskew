"""The task bodies the daily Elexon DAGs share.

Each DAG imports its own poller and passes the poller's functions in: the
release controller reads a DAG file's imports to see which DAG runs which
poller.
"""

from ingestion.database import connect_to_gridskew_prod
from ingestion.routing import ContractRejected


def poll_into_gridskew_prod(poll, *arguments):
    """Call poll(conn, *arguments) on a new gridskew_prod connection, then close it.

    A contract rejection fails the task without a retry; any other error is
    left for Airflow to retry.
    """

    from airflow.sdk.exceptions import AirflowFailException

    conn = connect_to_gridskew_prod()
    try:
        poll(conn, *arguments)
    except ContractRejected as rejected:
        raise AirflowFailException(str(rejected)) from rejected
    finally:
        conn.close()


def capture_unless_replaced(
    settlement_date, run_type, *, replaced_by_a_later_run, capture_settlement_date
):
    """Capture one B1610 run for one settlement date, unless Elexon already
    serves a later run for that date."""

    from airflow.sdk.exceptions import AirflowFailException

    replacing_run = replaced_by_a_later_run(settlement_date, run_type)
    if replacing_run is not None:
        # Gone for good: fail now instead of retrying for days.
        raise AirflowFailException(
            f"{run_type} for {settlement_date} has been replaced by "
            f"{replacing_run} and can no longer be captured"
        )
    poll_into_gridskew_prod(capture_settlement_date, settlement_date, run_type)
