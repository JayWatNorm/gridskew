"""Fail when a scheduled dbt job could replace a relation.

dbt-postgres drops dependent views when it replaces a table or a view
(DROP ... CASCADE). Scheduled jobs therefore only write rows, and releases
change structure. This reads the nightly DAG's default commands and dbt's
manifest, and reports every command, model or seed that would break that rule.
It also reports an incremental model that the nightly DAG's missing-table guard
does not name: the first scheduled run would build that table in full.

The BM-unit DAG builds its dbt commands inside its task, so they are not read
here.

Run after `dbt parse`, from the repository root, with PYTHONPATH set to it:
    python tests/adhoc/check_scheduled_set.py dbt/target/manifest.json
"""

import json
import sys
from pathlib import Path

from pytest import MonkeyPatch

from tests.fakes import load_dag_file

FULL_REFRESH_FLAGS = ("--full-refresh", "-f")
SELECTOR_SYNTAX = ("+", ":", ",", "*", "@")
INCREMENTAL_MODELS_ONLY = {"method": "config.materialized", "value": "incremental"}


def scheduled_set_problems(manifest, scheduled_commands):
    """Return one sentence for each way a scheduled job could replace a relation."""

    problems = []
    for command in scheduled_commands:
        problems += _command_problems(command, manifest)
    for node_id, node in sorted(manifest["nodes"].items()):
        if _is_model(node, "incremental"):
            problems += _incremental_model_problems(node_id, node)
        if _is_model(node, "table"):
            problems += _table_model_problems(node_id, manifest)
        if node["resource_type"] == "seed":
            problems += _seed_problems(node_id, node)
    return problems


def unguarded_incremental_models(manifest, guarded_tables):
    """Return one sentence for each incremental model the nightly guard omits."""

    problems = []
    for node_id, node in sorted(manifest["nodes"].items()):
        if not _is_model(node, "incremental"):
            continue
        if node["name"] not in guarded_tables:
            problems.append(
                f"{node_id}: the nightly DAG's PERIOD_TABLES does not name this "
                "incremental model, so a scheduled run would build it in full "
                "when the table is missing"
            )
    return problems


def nightly_default_commands():
    """The dbt commands a scheduled, unparameterised nightly run executes."""

    return _nightly_dag()["dbt_commands"](full_refresh=False)


def nightly_guarded_tables():
    """The tables a scheduled nightly run requires before it writes anything."""

    return _nightly_dag()["PERIOD_TABLES"]


def _nightly_dag():
    monkeypatch = MonkeyPatch()
    try:
        return load_dag_file(monkeypatch, "gridskew__dbt_nightly_dag.py")
    finally:
        monkeypatch.undo()


def _command_problems(command, manifest):
    described = "dbt " + " ".join(command)
    problems = []
    for flag in FULL_REFRESH_FLAGS:
        if flag in command:
            problems.append(f"{described}: a scheduled command must not pass {flag}")

    dbt_verb = command[0]
    if dbt_verb == "build":
        problems.append(f"{described}: a scheduled build recreates views")
    if dbt_verb == "run":
        problems += _scheduled_run_problems(described, command, manifest)
    return problems


def _scheduled_run_problems(described, command, manifest):
    """Two shapes can be read: named models, or the incremental-models selector."""

    if _names_one_selector(command):
        return _run_selector_problems(described, command[2], manifest)
    if not _is_readable_run(command):
        return [
            f"{described}: a scheduled run must be 'run --select <model names>' "
            "or 'run --selector <name>', or the models it writes cannot be checked"
        ]

    problems = []
    for selection in command[2:]:
        if selection in FULL_REFRESH_FLAGS:
            continue
        problems += _selection_problems(described, selection, manifest)
    return problems


def _names_one_selector(command):
    return len(command) == 3 and command[1] == "--selector"


def _run_selector_problems(described, selector_name, manifest):
    selector = manifest["selectors"].get(selector_name)
    if selector is None:
        return [f"{described}: no selector is named '{selector_name}'"]
    if selector["definition"] != INCREMENTAL_MODELS_ONLY:
        return [
            f"{described}: selector '{selector_name}' must select "
            "config.materialized:incremental and nothing else, or the models it "
            "writes cannot be checked"
        ]
    return []


def _is_readable_run(command):
    if len(command) < 3 or command[1] != "--select":
        return False
    for argument in command[2:]:
        if argument.startswith("-") and argument not in FULL_REFRESH_FLAGS:
            return False
    return True


def _selection_problems(described, selection, manifest):
    for syntax in SELECTOR_SYNTAX:
        if syntax in selection:
            return [
                f"{described}: '{selection}' is not a plain model name, so the "
                "models it writes cannot be checked"
            ]

    model = _model_named(selection, manifest)
    if model is None:
        return [f"{described}: no model is named '{selection}'"]
    if _is_model(model, "view"):
        return [
            f"{described}: '{selection}' is a view, and a scheduled run of a "
            "view drops the views that depend on it"
        ]
    return []


def _model_named(name, manifest):
    for node in manifest["nodes"].values():
        if node["resource_type"] == "model" and node["name"] == name:
            return node
    return None


def _is_model(node, materialized):
    if node["resource_type"] != "model":
        return False
    return node["config"].get("materialized") == materialized


def _incremental_model_problems(node_id, node):
    problems = []
    config = node["config"]
    if config.get("on_schema_change") != "fail":
        problems.append(
            f"{node_id}: an incremental model must set on_schema_change='fail', "
            "or a column change recreates the table on a scheduled run"
        )
    if config.get("full_refresh") is True:
        problems.append(
            f"{node_id}: full_refresh is true, so every scheduled run replaces "
            "the table"
        )
    if config.get("full_refresh") is False:
        problems.append(
            f"{node_id}: full_refresh is false, so a release cannot rebuild the table"
        )
    return problems


def _table_model_problems(node_id, manifest):
    problems = []
    for view_id in _view_descendants(node_id, manifest):
        problems.append(
            f"{node_id}: a table is replaced on every run, which drops its "
            f"dependent view {view_id}"
        )
    return problems


def _view_descendants(node_id, manifest):
    """Every view below the node, at any depth."""

    views = []
    visited = set()
    to_visit = list(manifest["child_map"].get(node_id, []))
    while to_visit:
        child_id = to_visit.pop()
        if child_id in visited:
            continue
        visited.add(child_id)
        to_visit += manifest["child_map"].get(child_id, [])

        # Exposures and other non-relation children are not under "nodes".
        child = manifest["nodes"].get(child_id)
        if child is None:
            continue
        if _is_model(child, "view"):
            views.append(child_id)
    return sorted(views)


def _seed_problems(node_id, node):
    if node["config"].get("full_refresh") is True:
        return [
            f"{node_id}: full_refresh is true, so every scheduled seed replaces "
            "the table"
        ]
    return []


def main():
    manifest_path = Path(sys.argv[1])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = scheduled_set_problems(manifest, nightly_default_commands())
    problems += unguarded_incremental_models(manifest, nightly_guarded_tables())
    for problem in problems:
        print(problem)
    if problems:
        raise SystemExit(1)
    print(
        "Scheduled dbt set: no nightly command, model or seed replaces a relation, "
        "and the nightly guard names every incremental model"
    )


if __name__ == "__main__":
    main()
