import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from scripts.readme_charts import write_charts

FIXTURES = Path(__file__).parent / "fixtures" / "results"
REPOSITORY = Path(__file__).parent.parent
PAGE = REPOSITORY / "site" / "index.html"
PAGES_WORKFLOW = REPOSITORY / ".github" / "workflows" / "pages.yml"
PINNED_JSDELIVR_FILE = re.compile(
    r"https://cdn\.jsdelivr\.net/npm/(?P<package>[a-z][a-z-]*)@\d+\.\d+\.\d+/\S+"
)
VEGA_EMBED_CALL = re.compile(r'vegaEmbed\(\s*"[^"]+",\s*"(?P<spec>[^"]+)"')

DELIVERY_HEADER = (
    "fuel_group,unit_periods,units,first_date,last_date,median_deviation_mwh,"
    "p25_deviation_mwh,p75_deviation_mwh,abs_metered_mwh,abs_pn_mwh,"
    "instructed_periods,instructed_period_share,abs_deviation_mwh,"
    "abs_instructed_deviation_mwh,abs_unexplained_shortfall_mwh,"
    "instructed_share_of_abs_deviation,unit_periods_by_run,analysis_read_until"
)

# The charts order the groups by metered energy (nuclear, gas, wind, oil)
# and by deviation (wind, gas, nuclear, oil); the input order matches
# neither. Oil has no deviation, so its instructed share is undefined.
DELIVERY_ROWS = [
    "wind,400,20,2026-08-22,2026-10-10,-1.250,-4.000,0.500,9000.0,9500.0,"
    '120,0.300000,3000.0,900.0,2100.0,0.300000,"{""SF"": 400}",'
    "2026-10-25 00:00:00+00",
    "nuclear,150,8,2026-08-22,2026-10-11,-0.200,-0.900,0.100,40000.0,40500.0,"
    '10,0.066667,500.0,50.0,450.0,0.100000,"{""SF"": 150}",'
    "2026-10-25 00:00:00+00",
    "gas_turbine,300,15,2026-08-23,2026-10-11,0.125,-0.750,1.000,25000.0,"
    '25100.0,200,0.666667,1200.0,1000.0,200.0,0.833333,"{""R1"": 100, ""SF"": 200}",'
    "2026-10-25 00:00:00+00",
    "oil,50,5,2026-08-24,2026-10-09,0.000,0.000,0.000,100.0,100.0,"
    '0,0.000000,0.0,0.0,0.0,,"{""SF"": 50}",2026-10-25 00:00:00+00',
]

EXPECTED_Q3_CSV = (
    "# Contains BMRS data © Elexon Limited copyright and database right 2026.\n"
    "fuel_group,unit_periods,first_date,last_date,p25_deviation_mwh,"
    "median_deviation_mwh,p75_deviation_mwh,analysis_read_until\n"
    "nuclear,150,2026-08-22,2026-10-11,-0.900,-0.200,0.100,2026-10-25 00:00:00+00\n"
    "gas_turbine,300,2026-08-23,2026-10-11,-0.750,0.125,1.000,2026-10-25 00:00:00+00\n"
    "wind,400,2026-08-22,2026-10-10,-4.000,-1.250,0.500,2026-10-25 00:00:00+00\n"
    "oil,50,2026-08-24,2026-10-09,0.000,0.000,0.000,2026-10-25 00:00:00+00\n"
)

BMRS_LINE = "# Contains BMRS data © Elexon Limited copyright and database right 2026.\n"
CARBON_LINE = (
    "# Carbon intensity data: NESO Carbon Intensity API, CC BY 4.0, "
    "modified: aggregated by GridSkew.\n"
)
BMRS_AND_CARBON_LINE = (
    "# Contains BMRS data © Elexon Limited copyright and database right 2026. "
    "Carbon intensity data: NESO Carbon Intensity API, CC BY 4.0, "
    "modified: aggregated by GridSkew.\n"
)

EXPECTED_Q12_CSV = (
    "# Contains BMRS data © Elexon Limited copyright and database right 2026.\n"
    "fuel_group,unit_periods,first_date,last_date,abs_instructed_deviation_mwh,"
    "abs_unexplained_shortfall_mwh,instructed_share_of_abs_deviation,"
    "analysis_read_until\n"
    "wind,400,2026-08-22,2026-10-10,900.0,2100.0,0.300000,2026-10-25 00:00:00+00\n"
    "gas_turbine,300,2026-08-23,2026-10-11,1000.0,200.0,0.833333,2026-10-25 00:00:00+00\n"
    "nuclear,150,2026-08-22,2026-10-11,50.0,450.0,0.100000,2026-10-25 00:00:00+00\n"
    "oil,50,2026-08-24,2026-10-09,0.0,0.0,,2026-10-25 00:00:00+00\n"
)


DELIVERY_FILES = [
    "q12_instructed_share_by_fuel_group.csv",
    "q12_instructed_share_by_fuel_group.png",
    "q12_instructed_share_by_fuel_group.vl.json",
    "q3_deviation_by_fuel_group.csv",
    "q3_deviation_by_fuel_group.png",
    "q3_deviation_by_fuel_group.vl.json",
]

FILES_WITHOUT_DELIVERY = [
    "q22_first_against_latest_run.csv",
    "q23_absolute_deviation_by_month.png",
    "q23_absolute_deviation_by_month.vl.json",
    "q23_pn_accuracy_proxy.csv",
    "q24_system_action_share.csv",
    "q24_system_action_share_by_fuel_group.png",
    "q24_system_action_share_by_fuel_group.vl.json",
    "q25_instruction_fragmentation.csv",
    "q25_instruction_fragmentation_corrected.csv",
    "q25_short_acceptances_by_fuel_group.png",
    "q25_short_acceptances_by_fuel_group.vl.json",
    "q28_shortfall_by_error_quintile.png",
    "q28_shortfall_by_error_quintile.vl.json",
    "q29_error_by_month.png",
    "q29_error_by_month.vl.json",
    "q29_error_by_utc_block.png",
    "q29_error_by_utc_block.vl.json",
    "q29_final_forecast_error_by_month_and_block.csv",
    "q2_settlement_run_changes.csv",
    "q30_carbon_error_by_wind_miss_quintile.png",
    "q30_carbon_error_by_wind_miss_quintile.vl.json",
    "q30_wind_vs_carbon_error.csv",
    "q5_shortfall_by_outage_class.png",
    "q5_shortfall_by_outage_class.vl.json",
    "q5_shortfall_with_known_outage.csv",
    "q6_publication_lag.csv",
    "q6_publication_lag_by_class.png",
    "q6_publication_lag_by_class.vl.json",
    "q8_q28_history.csv",
]


def write_delivery_csv(path, rows):
    path.write_text("\n".join([DELIVERY_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def saved_outputs_folder(folder):
    """A folder of the hand-made saved outputs, without the delivery result."""
    folder.mkdir()
    for fixture in FIXTURES.iterdir():
        (folder / fixture.name).write_bytes(fixture.read_bytes())
    return folder


def saved_results_folder(folder, delivery_rows=DELIVERY_ROWS):
    """A folder of saved outputs as the script reads them: the hand-made
    fixtures and the delivery result."""
    saved_outputs_folder(folder)
    write_delivery_csv(folder / "delivery_by_fuel_group.csv", delivery_rows)
    return folder


def without_lines_containing(path, fragment):
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if fragment not in line:
            kept.append(line)
    path.write_text("".join(kept), encoding="utf-8")


def output_files(folder):
    files = {}
    for path in sorted(folder.iterdir()):
        files[path.name] = path.read_bytes()
    return files


def first_lines(folder):
    lines = {}
    for path in sorted(folder.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as written_csv:
            lines[path.name] = written_csv.readline()
    return lines


def csv_header(path):
    lines = path.read_text(encoding="utf-8").split("\n")
    return lines[1].split(",")


def collect_fields(node, used, derived):
    """Every field a Vega-Lite spec reads, and every field its transforms
    create."""
    if isinstance(node, list):
        for item in node:
            collect_fields(item, used, derived)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key == "field" and isinstance(value, str):
            used.add(value)
        elif key == "fold":
            used.update(value)
        elif key == "parse":
            used.update(value)
        elif key == "as":
            derived.update(value)
        else:
            collect_fields(value, used, derived)


def spec_data_urls(folder):
    data_urls = {}
    for spec_path in sorted(folder.glob("*.vl.json")):
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        data_urls[spec_path.name] = spec["data"]["url"]
    return data_urls


def fields_missing_from_their_csv(folder):
    """Per spec, the fields it reads that its CSV's header does not hold."""
    missing_fields = {}
    for spec_path in sorted(folder.glob("*.vl.json")):
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        header = csv_header(folder / spec["data"]["url"].removeprefix("results/"))
        used = set()
        derived = set()
        collect_fields(spec, used, derived)
        missing = sorted(used - derived - set(header))
        if missing:
            missing_fields[spec_path.name] = missing
    return missing_fields


def specs_not_written_sorted(folder):
    unsorted = []
    for spec_path in sorted(folder.glob("*.vl.json")):
        text = spec_path.read_text(encoding="utf-8")
        if text != json.dumps(json.loads(text), sort_keys=True, indent=2) + "\n":
            unsorted.append(spec_path.name)
    return unsorted


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    folder = tmp_path_factory.mktemp("charts")
    write_charts(saved_results_folder(folder / "saved"), folder / "out")
    return folder / "out"


def test_a_rerun_writes_identical_files(tmp_path):
    saved = saved_results_folder(tmp_path / "saved")
    write_charts(saved, tmp_path / "first")
    write_charts(saved, tmp_path / "second")

    first = output_files(tmp_path / "first")
    assert sorted(first) == sorted([*DELIVERY_FILES, *FILES_WITHOUT_DELIVERY])
    assert output_files(tmp_path / "second") == first


def test_a_folder_without_the_delivery_result_writes_every_other_file(tmp_path):
    write_charts(saved_outputs_folder(tmp_path / "saved"), tmp_path / "out")

    assert sorted(output_files(tmp_path / "out")) == FILES_WITHOUT_DELIVERY


def test_a_saved_output_without_its_chart_rows_is_refused(tmp_path):
    saved = saved_results_folder(tmp_path / "saved")
    without_lines_containing(
        saved / "q8_q28_history.csv", "quintile of abs(error_api_final)"
    )

    with pytest.raises(ValueError, match="no row"):
        write_charts(saved, tmp_path / "out")


def test_each_chart_csv_holds_its_rows_in_reading_order_with_attribution(written):
    q3 = (written / "q3_deviation_by_fuel_group.csv").read_bytes()
    q12 = (written / "q12_instructed_share_by_fuel_group.csv").read_bytes()
    assert (q3, q12) == (
        EXPECTED_Q3_CSV.encode("utf-8"),
        EXPECTED_Q12_CSV.encode("utf-8"),
    )


def test_a_result_with_two_cutoffs_is_refused(tmp_path):
    second_read = DELIVERY_ROWS[2].replace(
        "2026-10-25 00:00:00+00", "2026-11-30 23:30:00+00"
    )
    saved = saved_results_folder(tmp_path / "saved", [DELIVERY_ROWS[0], second_read])

    with pytest.raises(ValueError, match="one cutoff"):
        write_charts(saved, tmp_path / "out")


def test_every_written_csv_starts_with_its_attribution(written):
    assert first_lines(written) == {
        "q12_instructed_share_by_fuel_group.csv": BMRS_LINE,
        "q22_first_against_latest_run.csv": BMRS_LINE,
        "q23_pn_accuracy_proxy.csv": BMRS_LINE,
        "q24_system_action_share.csv": BMRS_LINE,
        "q25_instruction_fragmentation.csv": BMRS_LINE,
        "q25_instruction_fragmentation_corrected.csv": BMRS_LINE,
        "q29_final_forecast_error_by_month_and_block.csv": CARBON_LINE,
        "q2_settlement_run_changes.csv": BMRS_LINE,
        "q30_wind_vs_carbon_error.csv": BMRS_AND_CARBON_LINE,
        "q3_deviation_by_fuel_group.csv": BMRS_LINE,
        "q5_shortfall_with_known_outage.csv": BMRS_LINE,
        "q6_publication_lag.csv": BMRS_LINE,
        "q8_q28_history.csv": BMRS_AND_CARBON_LINE,
    }


def test_a_saved_output_is_copied_unchanged_after_its_attribution(tmp_path):
    # The saved outputs are held with Windows line endings; the copy is LF.
    saved = saved_results_folder(tmp_path / "saved")
    q6_rows = (FIXTURES / "q6_publication_lag.csv").read_text(encoding="utf-8")
    (saved / "q6_publication_lag.csv").write_bytes(
        q6_rows.replace("\n", "\r\n").encode("utf-8")
    )
    write_charts(saved, tmp_path / "out")

    assert (tmp_path / "out" / "q6_publication_lag.csv").read_bytes() == (
        BMRS_LINE + q6_rows
    ).encode("utf-8")


def test_every_spec_reads_a_written_csv_that_holds_every_field_it_uses(written):
    assert spec_data_urls(written) == {
        "q12_instructed_share_by_fuel_group.vl.json": (
            "results/q12_instructed_share_by_fuel_group.csv"
        ),
        "q23_absolute_deviation_by_month.vl.json": "results/q23_pn_accuracy_proxy.csv",
        "q24_system_action_share_by_fuel_group.vl.json": (
            "results/q24_system_action_share.csv"
        ),
        "q25_short_acceptances_by_fuel_group.vl.json": (
            "results/q25_instruction_fragmentation.csv"
        ),
        "q28_shortfall_by_error_quintile.vl.json": "results/q8_q28_history.csv",
        "q29_error_by_month.vl.json": (
            "results/q29_final_forecast_error_by_month_and_block.csv"
        ),
        "q29_error_by_utc_block.vl.json": (
            "results/q29_final_forecast_error_by_month_and_block.csv"
        ),
        "q30_carbon_error_by_wind_miss_quintile.vl.json": (
            "results/q30_wind_vs_carbon_error.csv"
        ),
        "q3_deviation_by_fuel_group.vl.json": "results/q3_deviation_by_fuel_group.csv",
        "q5_shortfall_by_outage_class.vl.json": (
            "results/q5_shortfall_with_known_outage.csv"
        ),
        "q6_publication_lag_by_class.vl.json": "results/q6_publication_lag.csv",
    }
    assert fields_missing_from_their_csv(written) == {}


def test_every_spec_is_written_with_sorted_keys_and_two_space_indents(written):
    assert specs_not_written_sorted(written) == []


class PageTags(HTMLParser):
    """Every start tag on the page, with its attributes."""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def page_tags():
    parser = PageTags()
    parser.feed(PAGE.read_text(encoding="utf-8"))
    return parser.tags


def loaded_from_elsewhere(tag, attributes):
    """A script or stylesheet the browser fetches from another host."""
    target = attributes.get("src") or attributes.get("href") or ""
    if tag not in ("script", "link"):
        return None
    if not target.startswith(("http:", "https:", "//")):
        return None
    return target


def unpinned_resources(tags):
    """Scripts and stylesheets from another host that are not a pinned
    jsDelivr file with a sha384 integrity hash, loaded anonymously."""
    unpinned = []
    for tag, attributes in tags:
        target = loaded_from_elsewhere(tag, attributes)
        if target is None:
            continue
        pinned = PINNED_JSDELIVR_FILE.fullmatch(target)
        integrity = attributes.get("integrity") or ""
        anonymous = attributes.get("crossorigin") == "anonymous"
        if pinned and integrity.startswith("sha384-") and anonymous:
            continue
        unpinned.append(target)
    return unpinned


def external_script_packages(tags):
    packages = []
    for tag, attributes in tags:
        target = loaded_from_elsewhere(tag, attributes)
        pinned = PINNED_JSDELIVR_FILE.fullmatch(target or "")
        if tag == "script" and pinned:
            packages.append(pinned["package"])
    return packages


def shipped_path(target):
    """Where the Pages workflow takes a page-relative path from: site/,
    with results/ from docs/results/."""
    if target.startswith("results/"):
        return REPOSITORY / "docs" / target
    return REPOSITORY / "site" / target


def local_targets(tags):
    targets = []
    for _tag, attributes in tags:
        for name in ("src", "href"):
            target = attributes.get(name)
            if not target or target.startswith(("#", "http:", "https:", "//")):
                continue
            targets.append(target)
    return targets


def chart_specs_on_page():
    return VEGA_EMBED_CALL.findall(PAGE.read_text(encoding="utf-8"))


def jobs_with_their_own_permissions(workflow):
    names = []
    for name, job in workflow["jobs"].items():
        if "permissions" in job:
            names.append(name)
    return names


def not_shipped(targets):
    missing = []
    for target in targets:
        if not shipped_path(target).is_file():
            missing.append(target)
    return missing


def test_the_page_loads_scripts_only_from_pinned_jsdelivr_urls_with_integrity():
    tags = page_tags()

    assert unpinned_resources(tags) == []
    assert external_script_packages(tags) == ["vega", "vega-lite", "vega-embed"]


def test_every_local_link_and_chart_on_the_page_is_a_shipped_file():
    specs = chart_specs_on_page()

    assert specs == [
        "results/q5_shortfall_by_outage_class.vl.json",
        "results/q6_publication_lag_by_class.vl.json",
        "results/q23_absolute_deviation_by_month.vl.json",
        "results/q24_system_action_share_by_fuel_group.vl.json",
        "results/q25_short_acceptances_by_fuel_group.vl.json",
        "results/q28_shortfall_by_error_quintile.vl.json",
        "results/q29_error_by_month.vl.json",
        "results/q29_error_by_utc_block.vl.json",
        "results/q30_carbon_error_by_wind_miss_quintile.vl.json",
    ]
    assert not_shipped([*local_targets(page_tags()), *specs]) == []


def test_the_pages_workflow_runs_on_pushes_to_main_for_the_site_and_results():
    # BaseLoader keeps the key "on" a string; YAML 1.1 would read it as True.
    workflow = yaml.load(PAGES_WORKFLOW.read_text(encoding="utf-8"), yaml.BaseLoader)

    assert workflow["on"] == {
        "push": {"branches": ["main"], "paths": ["site/**", "docs/results/**"]},
        "workflow_dispatch": "",
    }


def test_the_pages_workflow_asks_for_exactly_its_three_permissions():
    workflow = yaml.load(PAGES_WORKFLOW.read_text(encoding="utf-8"), yaml.BaseLoader)

    assert workflow["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert jobs_with_their_own_permissions(workflow) == []
