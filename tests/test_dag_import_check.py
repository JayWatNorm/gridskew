"""The DAG import check CI runs in the Airflow image."""

from tests.adhoc.check_dag_import import dag_import_problems, declared_dag_ids
from tests.fakes import DAGS_DIRECTORY

DECLARED = {
    "gridskew_elexon_pn_dag.py": ["gridskew_elexon_pn"],
    "gridskew_carbon_intensity_forecast_dag.py": ["gridskew_carbon_intensity"],
}
LOADED = {"gridskew_elexon_pn", "gridskew_carbon_intensity"}


def test_every_dag_loaded_and_no_error_is_no_problem():
    assert dag_import_problems(DECLARED, LOADED, {}) == []


def test_an_import_error_is_reported_with_its_file_and_message():
    import_errors = {
        "/opt/airflow/dags/gridskew/gridskew_elexon_pn_dag.py": (
            "ModuleNotFoundError: No module named 'airflow.decorators'"
        )
    }

    problems = dag_import_problems(DECLARED, LOADED, import_errors)

    assert problems == [
        "/opt/airflow/dags/gridskew/gridskew_elexon_pn_dag.py: "
        "ModuleNotFoundError: No module named 'airflow.decorators'"
    ]


def test_a_declared_dag_airflow_did_not_load_is_reported():
    problems = dag_import_problems(DECLARED, {"gridskew_elexon_pn"}, {})

    assert problems == [
        "gridskew_carbon_intensity_forecast_dag.py: Airflow did not load "
        "gridskew_carbon_intensity"
    ]


def test_a_file_with_two_dag_ids_is_reported():
    declared = {"gridskew_elexon_pn_dag.py": ["gridskew_elexon_pn", "gridskew_pn"]}

    problems = dag_import_problems(declared, {"gridskew_elexon_pn"}, {})

    assert problems == ["gridskew_elexon_pn_dag.py: declares 2 dag_id values, not one"]


def test_a_file_with_no_dag_id_is_reported():
    declared = {"gridskew_elexon_pn_dag.py": []}

    problems = dag_import_problems(declared, set(), {})

    assert problems == ["gridskew_elexon_pn_dag.py: declares 0 dag_id values, not one"]


def test_a_loaded_dag_no_file_declares_is_reported():
    loaded = {"gridskew_elexon_pn", "gridskew_carbon_intensity", "example_bash"}

    problems = dag_import_problems(DECLARED, loaded, {})

    assert problems == ["example_bash: loaded, but no file in dags/ declares it"]


def test_every_dag_file_in_the_repository_declares_one_dag_id():
    declared = declared_dag_ids(DAGS_DIRECTORY)

    assert len(declared) == 15
    for dag_ids in declared.values():
        assert len(dag_ids) == 1
    assert declared["gridskew_carbon_intensity_forecast_dag.py"] == [
        "gridskew_carbon_intensity"
    ]
