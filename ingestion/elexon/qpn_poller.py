"""Fetch, validate, quarantine and load Elexon Quiescent Physical Notifications."""

from ingestion.elexon import physical_notifications
from ingestion.elexon.contracts import QPN_SPEC

DATASET = "QPN"
TABLE = "raw.elexon_qpn"

parse = physical_notifications.parse


def fetch(from_date, to_date):
    """Fetch decoded QPN rows for a UTC datetime window."""

    return physical_notifications.fetch(DATASET, from_date, to_date)


def run(conn, from_date, to_date):
    """Validate and load one QPN window."""

    physical_notifications.run(
        conn, from_date, to_date, dataset=DATASET, spec=QPN_SPEC, load_rows=load
    )


def load(parsed_rows, conn):
    """Insert typed QPN rows, ignoring existing target keys, and commit."""

    physical_notifications.load(parsed_rows, conn, TABLE, DATASET)
