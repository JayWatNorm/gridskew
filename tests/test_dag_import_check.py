"""The DAG import check CI runs in the Airflow image."""

from tests.fakes import DAGS_DIRECTORY
from tests.integration.check_dag_import import dag_import_problems, declared_dag_ids

DECLARED = {
    "gridskew_elexon_pn_dag.py": ["gridskew_elexon_pn"],
    "gridskew_carbon_intensity_forecast_dag.py": ["gridskew_carbon_intensity"],
}
LOADED = {
    "gridskew_elexon_pn": "gridskew_elexon_pn_dag.py",
    "gridskew_carbon_intensity": "gridskew_carbon_intensity_forecast_dag.py",
}


def test_every_dag_loaded_and_no_error_is_no_problem():
    assert dag_import_problems(DECLARED, LOADED, {}) == []


def test_an_empty_folder_is_reported():
    assert dag_import_problems({}, {}, {}) == ["no DAG files found"]


def test_an_import_error_is_reported_with_its_file_and_message():
    import_errors = {
        "/opt/airflow/dags/gridskew/gridskew_elexon_pn_dag.py": (
            "ModuleNotFoundError: No module named 'airflow.decorators'"
        )
    }
    loaded = {"gridskew_carbon_intensity": "gridskew_carbon_intensity_forecast_dag.py"}

    problems = dag_import_problems(DECLARED, loaded, import_errors)

    assert problems == [
        "/opt/airflow/dags/gridskew/gridskew_elexon_pn_dag.py: "
        "ModuleNotFoundError: No module named 'airflow.decorators'",
        "gridskew_elexon_pn_dag.py: Airflow did not load gridskew_elexon_pn",
    ]


def test_a_file_airflow_did_not_parse_is_reported():
    loaded = {"gridskew_elexon_pn": "gridskew_elexon_pn_dag.py"}

    problems = dag_import_problems(DECLARED, loaded, {})

    assert problems == [
        "gridskew_carbon_intensity_forecast_dag.py: Airflow did not parse it "
        '(safe mode skips a file without "airflow" and "dag" in its text)',
        "gridskew_carbon_intensity_forecast_dag.py: Airflow did not load "
        "gridskew_carbon_intensity",
    ]


def test_a_parsed_file_whose_dag_was_not_loaded_is_reported():
    loaded = {
        "gridskew_elexon_pn": "gridskew_elexon_pn_dag.py",
        "gridskew_carbon": "gridskew_carbon_intensity_forecast_dag.py",
    }

    problems = dag_import_problems(DECLARED, loaded, {})

    assert problems == [
        "gridskew_carbon_intensity_forecast_dag.py: Airflow did not load "
        "gridskew_carbon_intensity",
        "gridskew_carbon: loaded, but no file in dags/ declares it",
    ]


def test_a_file_with_two_dag_ids_is_reported():
    declared = {"gridskew_elexon_pn_dag.py": ["gridskew_elexon_pn", "gridskew_pn"]}
    loaded = {"gridskew_elexon_pn": "gridskew_elexon_pn_dag.py"}

    problems = dag_import_problems(declared, loaded, {})

    assert problems == ["gridskew_elexon_pn_dag.py: declares 2 dag_id values, not one"]


def test_a_file_with_no_dag_id_is_reported():
    declared = {"gridskew_elexon_pn_dag.py": []}
    import_errors = {"/opt/airflow/dags/gridskew/gridskew_elexon_pn_dag.py": "x"}

    problems = dag_import_problems(declared, {}, import_errors)

    assert problems == [
        "/opt/airflow/dags/gridskew/gridskew_elexon_pn_dag.py: x",
        "gridskew_elexon_pn_dag.py: declares 0 dag_id values, not one",
    ]


def test_a_loaded_dag_no_file_declares_is_reported():
    loaded = {**LOADED, "example_bash": "example_bash_operator.py"}

    problems = dag_import_problems(DECLARED, loaded, {})

    assert problems == ["example_bash: loaded, but no file in dags/ declares it"]


def test_every_dag_file_in_the_repository_declares_one_dag_id():
    declared = declared_dag_ids(DAGS_DIRECTORY)

    assert declared
    for dag_ids in declared.values():
        assert len(dag_ids) == 1
    assert declared["gridskew_carbon_intensity_forecast_dag.py"] == [
        "gridskew_carbon_intensity"
    ]
