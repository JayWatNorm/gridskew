"""The dbt role's provision script grants read on every table dbt reads."""

import re
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).parent.parent
STAGING_MODELS = REPOSITORY / "dbt" / "models" / "staging"
DBT_ROLE_SCRIPT = REPOSITORY / "sql" / "provision" / "001_bmu_snapshot_schema.sql"
SELECT_GRANT = re.compile(r"GRANT SELECT ON TABLE(?P<tables>.*?)TO %I", re.DOTALL)
RAW_TABLE = re.compile(r"raw\.(\w+)")
SQL_COMMENT = re.compile(r"--[^\n]*")


def tables_dbt_reads():
    tables = set()
    for sources_file in sorted(STAGING_MODELS.rglob("*__sources.yml")):
        declared = yaml.safe_load(sources_file.read_text(encoding="utf-8"))
        for source in declared["sources"]:
            for table in source["tables"]:
                tables.add(table["name"])
    return tables


def tables_granted_to_the_dbt_role():
    script = SQL_COMMENT.sub("", DBT_ROLE_SCRIPT.read_text(encoding="utf-8"))
    tables = set()
    for grant in SELECT_GRANT.finditer(script):
        tables.update(RAW_TABLE.findall(grant.group("tables")))
    return tables


def test_dbt_reads_the_fourteen_source_tables():
    assert tables_dbt_reads() == {
        "carbon_intensity_forecast",
        "carbon_intensity_outturn",
        "elexon_pn",
        "elexon_qpn",
        "elexon_b1610",
        "elexon_bm_units_extracts",
        "elexon_bm_units",
        "elexon_boalf",
        "elexon_remit",
        "elexon_qas",
        "elexon_ndf",
        "elexon_indo",
        "elexon_windfor",
        "elexon_fuelhh",
    }


def test_the_dbt_role_is_granted_read_on_every_table_dbt_reads():
    assert tables_granted_to_the_dbt_role() == tables_dbt_reads()
