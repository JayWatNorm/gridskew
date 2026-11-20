"""The repository's dbt profile: a local run cannot reach production by default."""

from pathlib import Path

import jinja2
import pytest
import yaml

PROFILES = Path(__file__).resolve().parents[1] / "dbt_profiles" / "profiles.yml"
PRODUCTION_DATABASE = "gridskew_prod"
PRODUCTION_ENVIRONMENT = {
    "DBT_HOST": "192.0.2.10",
    "DBT_PORT": "5435",
    "DBT_USER": "gridskew_dbt",
    "DBT_PASSWORD": "secret",
}


def renderer_with(environment):
    """A Jinja environment whose `env_var` and `as_number` stand in for dbt's:
    a variable without a default that is not set stops the render, as it
    stops dbt."""

    def env_var(name, default=None):
        if name in environment:
            return environment[name]
        if default is None:
            raise KeyError(f"environment variable {name} is not set")
        return default

    def as_number(value):
        return int(value)

    jinja = jinja2.Environment(undefined=jinja2.StrictUndefined)
    jinja.globals["env_var"] = env_var
    jinja.filters["as_number"] = as_number
    return jinja


def gridskew_profile():
    """The profile's text values before rendering, as dbt reads the file."""

    profiles = yaml.load(PROFILES.read_text(encoding="utf-8"), yaml.BaseLoader)
    return profiles["gridskew"]


def rendered_target_name(environment):
    """The target dbt selects with only `environment` set and no --target."""

    jinja = renderer_with(environment)
    return jinja.from_string(gridskew_profile()["target"]).render()


def rendered_output(target, environment):
    """One output's connection settings as dbt renders them, each value on
    its own, with only `environment` set. Other outputs are not rendered, as
    dbt does not render them."""

    jinja = renderer_with(environment)
    output = {}
    for setting, text in gridskew_profile()["outputs"][target].items():
        rendered = jinja.from_string(text).render()
        if setting == "port":
            rendered = int(rendered)
        output[setting] = rendered
    return output


def test_the_default_target_is_dev():
    assert rendered_target_name({}) == "dev"


def test_dbt_target_prod_selects_the_prod_output():
    assert rendered_target_name({"DBT_TARGET": "prod"}) == "prod"


def test_dev_target_is_the_local_disposable_database_by_default():
    dev = rendered_output("dev", {})

    assert dev["host"] == "localhost"
    assert dev["port"] == 5432
    assert dev["dbname"] == "gridskew_dev"
    assert dev["schema"] == "dbt_dev"


def test_dev_target_stays_local_with_the_production_variables_set():
    dev = rendered_output("dev", PRODUCTION_ENVIRONMENT)

    assert dev["host"] == "localhost"
    assert dev["dbname"] != PRODUCTION_DATABASE


def test_dev_target_takes_its_port_database_and_schema_from_the_environment():
    dev = rendered_output(
        "dev",
        {"DBT_PORT": "55447", "DBT_DBNAME": "gridskew_scratch", "DBT_SCHEMA": "dbt_jw"},
    )

    assert dev["port"] == 55447
    assert dev["dbname"] == "gridskew_scratch"
    assert dev["schema"] == "dbt_jw"


def test_prod_target_is_the_production_database_on_the_given_host():
    prod = rendered_output("prod", PRODUCTION_ENVIRONMENT)

    assert prod["host"] == "192.0.2.10"
    assert prod["port"] == 5435
    assert prod["user"] == "gridskew_dbt"
    assert prod["dbname"] == PRODUCTION_DATABASE
    assert prod["schema"] == "dbt_dev"


def test_prod_target_needs_the_host_set():
    with pytest.raises(KeyError, match="DBT_HOST"):
        rendered_output("prod", {})
