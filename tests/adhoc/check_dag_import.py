"""Import every DAG file with the real Airflow and fail on any import error.

CI runs this inside the Airflow image the platform deploys, with the
repository mounted where the platform mounts the GridSkew checkout. Airflow's
DagBag parses the files as the scheduler would; parsing touches no database,
so the image's default SQLite needs no setup. Every file must declare exactly
one dag_id and Airflow must load that DAG.

    python tests/adhoc/check_dag_import.py dags
"""

import re
import sys
from pathlib import Path

DAG_ID_KEYWORD = re.compile(r'dag_id="([^"]+)"')


def declared_dag_ids(dags_dir):
    """The dag_id values each DAG file declares, by file name."""

    declared = {}
    for dag_file in sorted(Path(dags_dir).glob("*.py")):
        declared[dag_file.name] = DAG_ID_KEYWORD.findall(
            dag_file.read_text(encoding="utf-8")
        )
    return declared


def dag_import_problems(declared_by_file, loaded_dag_ids, import_errors):
    """One sentence for each import error, each file without exactly one
    dag_id, each declared DAG Airflow did not load, and each loaded DAG no
    file declares."""

    problems = []
    for path, error in sorted(import_errors.items()):
        problems.append(f"{path}: {error}")
    declared_dag_ids_in_order = []
    for file_name, dag_ids in sorted(declared_by_file.items()):
        declared_dag_ids_in_order.extend(dag_ids)
        if len(dag_ids) != 1:
            problems.append(
                f"{file_name}: declares {len(dag_ids)} dag_id values, not one"
            )
            continue
        dag_id = dag_ids[0]
        if dag_id not in loaded_dag_ids:
            problems.append(f"{file_name}: Airflow did not load {dag_id}")
    for dag_id in sorted(loaded_dag_ids):
        if dag_id not in declared_dag_ids_in_order:
            problems.append(f"{dag_id}: loaded, but no file in dags/ declares it")
    return problems


def main(dags_dir):
    from airflow.models import DagBag

    bag = DagBag(dag_folder=dags_dir, include_examples=False)
    problems = dag_import_problems(
        declared_dag_ids(dags_dir), set(bag.dag_ids), bag.import_errors
    )
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"{len(bag.dag_ids)} DAGs imported: {sorted(bag.dag_ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
