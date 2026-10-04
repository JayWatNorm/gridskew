"""Fail when a scheduled dbt job could replace a relation.

dbt-postgres drops dependent views when it replaces a table or a view
(DROP ... CASCADE). Scheduled jobs therefore only write rows, and releases
change structure. This reads the nightly DAG's default commands and dbt's
manifest, and reports every command, model or seed that would break that rule.

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


def nightly_default_commands():
    """The dbt commands a scheduled, unparameterised nightly run executes."""

    monkeypatch = MonkeyPatch()
    try:
        nightly = load_dag_file(monkeypatch, "gridskew__dbt_nightly_dag.py")
        return nightly["dbt_commands"](full_refresh=False)
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
    """Only `run --select <model names>` can be read; any other shape fails."""

    if not _is_readable_run(command):
        return [
            f"{described}: a scheduled run must be 'run --select <model names>', "
            "or the models it writes cannot be checked"
        ]

    problems = []
    for selection in command[2:]:
        if selection in FULL_REFRESH_FLAGS:
            continue
        problems += _selection_problems(described, selection, manifest)
    return problems


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
    for problem in problems:
        print(problem)
    if problems:
        raise SystemExit(1)
    print("Scheduled dbt set: no nightly command, model or seed replaces a relation")


if __name__ == "__main__":
    main()
