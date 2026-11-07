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
CAPTURE_CUTOFF_VAR = "rf_restatement_capture_cutoff"
# Without its offset a cutoff would be read in the session's time zone; with
# another offset it is not the UTC instant recorded for the read.
CUTOFFS_NOT_IN_UTC = ("2026-11-30 00:00:00", "2026-11-30 00:00:00+01")
# One-look analyses that refuse to compile without analysis_read_until. They
# are compiled and run here with this cutoff instead.
READ_UNTIL_VAR = "analysis_read_until"
NEEDS_READ_UNTIL = ("q26_instructed_and_absvd_share",)
CHECK_READ_UNTIL = "2026-09-30 22:30:00+00"


def disposable_connection():
    return psycopg.connect(
        host=os.environ["DBT_HOST"],
        port=os.environ["DBT_PORT"],
        user=os.environ["DBT_USER"],
        password=os.environ["DBT_PASSWORD"],
        dbname=os.environ["DBT_DBNAME"],
    )


def run_compile(
    dbt_vars,
    also_compiled,
    target_dir,
    selected=("resource_type:analysis",),
    excluded=(),
):
    command = [
        "dbt",
        "compile",
        "--select",
        *selected,
        *also_compiled,
        "--project-dir",
        str(PROJECT_DIR),
        "--profiles-dir",
        str(PROFILES_DIR),
        "--target",
        "dev",
        "--target-path",
        target_dir,
    ]
    if excluded:
        command += ["--exclude", *excluded]
    if dbt_vars is not None:
        command += ["--vars", json.dumps(dbt_vars)]
    return subprocess.run(command, capture_output=True, text=True)


def compile_analyses(
    dbt_vars=None, also_compiled=(), selected=("resource_type:analysis",), excluded=()
):
    """The manifest of a compile of every analysis, with dbt_vars if given.

    also_compiled names models whose compiled SQL is wanted too; selected and
    excluded narrow the analyses. A separate target folder leaves dbt/target,
    which later checks read, as the last full build wrote it.
    """

    with TemporaryDirectory(prefix="analyses-") as target_dir:
        result = run_compile(dbt_vars, also_compiled, target_dir, selected, excluded)
        if result.returncode != 0:
            print(result.stdout[-4000:], result.stderr[-2000:])
            raise SystemExit("dbt could not compile the analyses")
        manifest_path = Path(target_dir) / "manifest.json"
        return json.loads(manifest_path.read_text(encoding="utf-8"))


def cutoffs_compiled_anyway():
    """The capture cutoffs not in UTC that compile without dbt naming the var."""

    compiled_anyway = []
    for cutoff in CUTOFFS_NOT_IN_UTC:
        with TemporaryDirectory(prefix="analyses-") as target_dir:
            result = run_compile(
                {CAPTURE_CUTOFF_VAR: cutoff}, (), target_dir, excluded=NEEDS_READ_UNTIL
            )
        if result.returncode != 0 and CAPTURE_CUTOFF_VAR in result.stdout:
            continue
        compiled_anyway.append(cutoff)
    return compiled_anyway


def analyses_in(manifest):
    """Each analysis's compiled SQL by name, or None where it was not selected."""

    analyses = {}
    for node in manifest["nodes"].values():
        if node["resource_type"] == "analysis":
            analyses[node["name"]] = node.get("compiled_code")
    return analyses


def compiled_analyses():
    """Each analysis's compiled SQL by name: with the project's vars, and the
    ones that need a cutoff with CHECK_READ_UNTIL."""

    analyses = analyses_in(compile_analyses(excluded=NEEDS_READ_UNTIL))
    with_cutoff = analyses_in(
        compile_analyses({READ_UNTIL_VAR: CHECK_READ_UNTIL}, selected=NEEDS_READ_UNTIL)
    )
    for name in NEEDS_READ_UNTIL:
        analyses[name] = with_cutoff[name]
    not_compiled = []
    for name, compiled_sql in analyses.items():
        if compiled_sql is None:
            not_compiled.append(name)
    if not_compiled:
        raise SystemExit(f"dbt compiled no SQL for {sorted(not_compiled)}")
    return analyses


def compiled_without_read_until():
    """The analyses that need a cutoff but compile without dbt naming the var."""

    compiled_anyway = []
    for analysis in NEEDS_READ_UNTIL:
        with TemporaryDirectory(prefix="analyses-") as target_dir:
            result = run_compile(None, (), target_dir, selected=(analysis,))
        if result.returncode != 0 and READ_UNTIL_VAR in result.stdout:
            continue
        compiled_anyway.append(analysis)
    return compiled_anyway


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

    compiled_anyway = cutoffs_compiled_anyway()
    if compiled_anyway:
        raise SystemExit(
            f"{CAPTURE_CUTOFF_VAR} not in UTC compiled anyway: {compiled_anyway}"
        )
    print(f"Analyses: {CAPTURE_CUTOFF_VAR} not in UTC stops the compile")

    compiled_anyway = compiled_without_read_until()
    if compiled_anyway:
        raise SystemExit(f"compiled without {READ_UNTIL_VAR}: {compiled_anyway}")
    print(f"Analyses: without {READ_UNTIL_VAR} the compile stops: {NEEDS_READ_UNTIL}")


if __name__ == "__main__":
    main()
