"""Compile every dbt analysis and run each one against a disposable PostgreSQL.

No dbt build runs the analyses, so a renamed column would break one without
anyone noticing until it is next read. This compiles them all and runs each
as `select * from (<compiled sql>) as analysis limit 0`: PostgreSQL resolves
every relation and column without reading a row. It refuses to run unless
explicitly pointed at a local disposable gridskew_dev that holds the CI
fixtures and a full `dbt build`.

Run from the repository root with the dbt environment set, for example:
    GRIDSKEW_DISPOSABLE_TEST=1 DBT_HOST=localhost DBT_DBNAME=gridskew_dev ...
    python -m tests.adhoc.check_analyses_compile
"""

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg

PROJECT_DIR = Path(__file__).resolve().parents[2] / "dbt"
# The repository profile reads the guarded DBT_* variables; pinning it and the
# target stops an inherited DBT_PROFILES_DIR sending dbt to another database.
PROFILES_DIR = Path(__file__).resolve().parents[2] / "dbt_profiles"


def disposable_connection():
    return psycopg.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
        dbname=os.environ["DBT_DBNAME"],
    )


def compile_analyses(dbt_vars=None, also_compiled=()):
    """The manifest of a compile of every analysis, with dbt_vars if given.

    also_compiled names models whose compiled SQL is wanted too. A separate
    target folder leaves dbt/target, which later checks read, as the last
    full build wrote it.
    """

    command = [
        "dbt",
        "compile",
        "--select",
        "resource_type:analysis",
        *also_compiled,
        "--project-dir",
        str(PROJECT_DIR),
        "--profiles-dir",
        str(PROFILES_DIR),
        "--target",
        "dev",
    ]
    if dbt_vars is not None:
        command += ["--vars", json.dumps(dbt_vars)]
    with TemporaryDirectory(prefix="analyses-") as target_dir:
        result = subprocess.run(
            command + ["--target-path", target_dir],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stdout[-4000:], result.stderr[-2000:])
            raise SystemExit("dbt could not compile the analyses")
        manifest_path = Path(target_dir) / "manifest.json"
        return json.loads(manifest_path.read_text(encoding="utf-8"))


def compiled_analyses():
    """Each analysis's compiled SQL by name."""

    manifest = compile_analyses()
    analyses = {}
    for node in manifest["nodes"].values():
        if node["resource_type"] == "analysis":
            analyses[node["name"]] = node["compiled_code"]
    return analyses


def without_rows(compiled_sql):
    """The query as a subquery that returns its columns and no row.

    The closing bracket goes on its own line, so a trailing comment in the
    analysis cannot swallow it.
    """

    query = compiled_sql.rstrip().rstrip(";")
    return f"select * from (\n{query}\n) as analysis limit 0"


def database_error(conn, query):
    """PostgreSQL's message for the query, or None when it runs."""

    try:
        with conn.cursor() as cursor:
            cursor.execute(query)
    except psycopg.Error as error:
        return str(error).strip()
    finally:
        conn.rollback()
    return None


def main():
    if (
        os.getenv("GRIDSKEW_DISPOSABLE_TEST") != "1"
        or os.getenv("DBT_HOST") not in {"localhost", "127.0.0.1"}
        or os.getenv("DBT_DBNAME") != "gridskew_dev"
    ):
        raise RuntimeError(
            "Analysis checks require opt-in to a local disposable gridskew_dev"
        )
    analyses = compiled_analyses()
    if not analyses:
        raise SystemExit("dbt compiled no analyses")

    failed = []
    conn = disposable_connection()
    try:
        for name in sorted(analyses):
            error = database_error(conn, without_rows(analyses[name]))
            if error is None:
                print(f"{name}: runs")
                continue
            failed.append(name)
            print(f"{name}: {error}")
    finally:
        conn.close()

    if failed:
        raise SystemExit(f"{len(failed)} of {len(analyses)} analyses fail: {failed}")
    print(f"Analyses: all {len(analyses)} compile and run")


if __name__ == "__main__":
    main()
