"""Guard the rule that scheduled dbt jobs never replace a relation."""

import pytest

from tests.adhoc.check_scheduled_set import (
    nightly_default_commands,
    nightly_guarded_tables,
    scheduled_set_problems,
    unguarded_incremental_models,
)

PERIOD_TABLE = "model.gridskew.int_period"
FACT_VIEW = "model.gridskew.fct_view"
MART_TABLE = "model.gridskew.mart_table"
SEED = "seed.gridskew.codes"
RUN_THE_PERIOD_TABLE = ["run", "--select", "int_period"]
RUN_THE_INCREMENTAL_MODELS = ["run", "--selector", "nightly_models"]


def model(materialized, **other_config):
    config = {"materialized": materialized, **other_config}
    return {"resource_type": "model", "name": "unused", "config": config}


def model_named(name, materialized, **other_config):
    named = model(materialized, **other_config)
    named["name"] = name
    return named


def seed(**config):
    return {"resource_type": "seed", "name": "codes", "config": config}


def manifest_with(nodes, child_map, selectors=None):
    if selectors is None:
        selectors = {}
    return {"nodes": nodes, "child_map": child_map, "selectors": selectors}


def selector(definition):
    return {"definition": definition}


def released_manifest():
    return manifest_with(
        nodes={
            PERIOD_TABLE: model_named(
                "int_period", "incremental", on_schema_change="fail"
            ),
            FACT_VIEW: model_named("fct_view", "view"),
            SEED: seed(),
        },
        child_map={PERIOD_TABLE: [FACT_VIEW]},
        selectors={
            "nightly_models": selector(
                {"method": "config.materialized", "value": "incremental"}
            ),
            "everything": selector({"method": "fqn", "value": "*"}),
        },
    )


def test_the_released_shape_has_no_problems():
    commands = [
        ["test", "--selector", "everything"],
        RUN_THE_INCREMENTAL_MODELS,
        RUN_THE_PERIOD_TABLE,
    ]

    assert scheduled_set_problems(released_manifest(), commands) == []


def test_the_nightly_dag_supplies_its_default_commands():
    assert RUN_THE_INCREMENTAL_MODELS in nightly_default_commands()


def test_the_nightly_dag_supplies_its_guarded_tables():
    assert nightly_guarded_tables() == (
        "int_elexon__b1610_period",
        "int_elexon__pn_period_mwh",
    )


def test_a_scheduled_run_by_selector_must_name_a_selector_that_exists():
    commands = [["run", "--selector", "renamed_away"]]

    (problem,) = scheduled_set_problems(released_manifest(), commands)

    assert "no selector is named 'renamed_away'" in problem


def test_a_scheduled_run_by_selector_must_select_incremental_models_only():
    commands = [["run", "--selector", "everything"]]

    (problem,) = scheduled_set_problems(released_manifest(), commands)

    assert "'everything' must select config.materialized:incremental" in problem


def test_the_nightly_guard_must_name_every_incremental_model():
    (problem,) = unguarded_incremental_models(released_manifest(), ("another_table",))

    assert PERIOD_TABLE in problem
    assert "PERIOD_TABLES does not name" in problem


def test_a_guard_that_names_every_incremental_model_has_no_problems():
    assert unguarded_incremental_models(released_manifest(), ("int_period",)) == []


def test_a_scheduled_run_must_not_select_a_view():
    commands = [["run", "--select", "int_period", "fct_view"]]

    (problem,) = scheduled_set_problems(released_manifest(), commands)

    assert "'fct_view' is a view" in problem


@pytest.mark.parametrize(
    "run_command",
    [
        pytest.param(["run"], id="no-selection"),
        pytest.param(["run", "-s", "fct_view"], id="short-option"),
        pytest.param(["run", "--select=fct_view"], id="joined-option"),
        pytest.param(
            ["run", "--select", "int_period", "--exclude", "int_period"],
            id="another-option",
        ),
        pytest.param(
            ["run", "--selector", "nightly_models", "--exclude", "int_period"],
            id="selector-with-another-option",
        ),
    ],
)
def test_a_scheduled_run_the_check_cannot_read_is_a_problem(run_command):
    (problem,) = scheduled_set_problems(released_manifest(), [run_command])

    assert "must be 'run --select <model names>'" in problem


def test_a_scheduled_run_must_select_plain_model_names():
    commands = [["run", "--select", "int_period+"]]

    (problem,) = scheduled_set_problems(released_manifest(), commands)

    assert "'int_period+' is not a plain model name" in problem


def test_a_scheduled_run_must_select_models_that_exist():
    commands = [["run", "--select", "renamed_away"]]

    (problem,) = scheduled_set_problems(released_manifest(), commands)

    assert "no model is named 'renamed_away'" in problem


@pytest.mark.parametrize("flag", ["--full-refresh", "-f"])
def test_a_scheduled_command_must_not_full_refresh(flag):
    commands = [[*RUN_THE_PERIOD_TABLE, flag]]

    (problem,) = scheduled_set_problems(released_manifest(), commands)

    assert f"must not pass {flag}" in problem


def test_a_scheduled_command_must_not_build():
    commands = [["build", "--select", "int_period"]]

    (problem,) = scheduled_set_problems(released_manifest(), commands)

    assert "a scheduled build recreates views" in problem


def test_an_incremental_model_must_fail_on_a_schema_change():
    manifest = manifest_with(
        nodes={PERIOD_TABLE: model("incremental", on_schema_change="ignore")},
        child_map={},
    )

    (problem,) = scheduled_set_problems(manifest, [])

    assert PERIOD_TABLE in problem
    assert "on_schema_change='fail'" in problem


def test_an_incremental_model_must_not_full_refresh_on_every_run():
    manifest = manifest_with(
        nodes={
            PERIOD_TABLE: model(
                "incremental", on_schema_change="fail", full_refresh=True
            )
        },
        child_map={},
    )

    (problem,) = scheduled_set_problems(manifest, [])

    assert PERIOD_TABLE in problem
    assert "full_refresh is true" in problem


def test_an_incremental_model_must_stay_rebuildable_by_a_release():
    manifest = manifest_with(
        nodes={
            PERIOD_TABLE: model(
                "incremental", on_schema_change="fail", full_refresh=False
            )
        },
        child_map={},
    )

    (problem,) = scheduled_set_problems(manifest, [])

    assert PERIOD_TABLE in problem
    assert "full_refresh is false" in problem


def test_a_seed_must_not_full_refresh_on_every_run():
    manifest = manifest_with(nodes={SEED: seed(full_refresh=True)}, child_map={})

    (problem,) = scheduled_set_problems(manifest, [])

    assert SEED in problem
    assert "full_refresh is true" in problem


def test_a_table_must_not_have_a_view_below_it_at_any_depth():
    middle_table = "model.gridskew.middle_table"
    manifest = manifest_with(
        nodes={
            MART_TABLE: model("table"),
            middle_table: model("table"),
            FACT_VIEW: model("view"),
        },
        child_map={MART_TABLE: [middle_table], middle_table: [FACT_VIEW]},
    )

    problems = scheduled_set_problems(manifest, [])

    assert problems == [
        f"{MART_TABLE}: a table is replaced on every run, which drops its "
        f"dependent view {FACT_VIEW}",
        f"{middle_table}: a table is replaced on every run, which drops its "
        f"dependent view {FACT_VIEW}",
    ]
