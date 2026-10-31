import csv
import io
import json
import re
import statistics
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from scripts.readme_charts import (
    FUEL_GROUP_COLOURS,
    FUEL_GROUP_SCHEME,
    end_name_heights,
    write_charts,
)

FIXTURES = Path(__file__).parent / "fixtures" / "results"
REPOSITORY = Path(__file__).parent.parent
PAGE = REPOSITORY / "site" / "index.html"
PAGES_WORKFLOW = REPOSITORY / ".github" / "workflows" / "pages.yml"
PINNED_JSDELIVR_FILE = re.compile(
    r"https://cdn\.jsdelivr\.net/npm/(?P<package>[a-z][a-z-]*)@\d+\.\d+\.\d+/\S+"
)
VEGA_EMBED_CALL = re.compile(r'vegaEmbed\(\s*"[^"]+",\s*"(?P<spec>[^"]+)"')
DISPLAYED_FUEL_GROUP = re.compile(
    r'datum\.fuel_group == \\"(?P<code>[^"\\]*)\\" \? \\"(?P<label>[^"\\]*)\\"'
)
FOUR_OR_MORE_DECIMALS = re.compile(r"\d\.\d{4,}")
WRAPPED_LINES = re.compile(r'\[(?:"(?:[^"\\]|\\.)*"(?:, )?)+\]')
FUEL_GROUP_COLOUR_PAIR = re.compile(r'\["(#[0-9a-f]{6})", "(#[0-9a-f]{6})"\]')
FUEL_GROUP_DISPLAY_NAMES = {
    "": "All fuel groups",
    "bioenergy": "Bioenergy",
    "coal": "Coal",
    "gas_turbine": "Gas",
    "hydro": "Hydro",
    "interconnector": "Interconnector",
    "nuclear": "Nuclear",
    "oil": "Oil",
    "other": "Other",
    "pumped_storage": "Pumped storage",
    "unclassified": "Unclassified",
    "wind": "Wind",
}

BAND_FROM_17_AUGUST_2026 = {"date": 17, "month": 8, "utc": True, "year": 2026}
ENDS_12_SEPTEMBER_2026 = {"date": 12, "month": 9, "utc": True, "year": 2026}

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

# Hand-made saved outputs for the reads the fixture folder does not hold.
# Q3's groups come in an order the chart keeps; coal is a closed unit the
# chart leaves out; gas has no Q22 row. Q12's order differs from the
# chart's (largest gap first); interconnectors are left out. Q2's unit types
# sum to round figures, with E beside T and C beside V.
NEW_SAVED_OUTPUTS = {
    "q3_metered_vs_notified.csv": [
        "fuel_group,unit_periods,units,first_date,last_date,median_deviation_mwh,"
        "q1_deviation_mwh,q3_deviation_mwh,abs_metered_mwh,abs_pn_mwh",
        "wind,400,20,2025-08-22,2026-09-26,0.300,-1.000,2.900,9000.0,9500.0",
        "coal,90,3,2025-08-22,2026-09-26,0.000,0.000,0.000,1.0,0.0",
        "nuclear,150,8,2025-08-22,2026-09-26,0.250,0.000,2.600,8000.0,8100.0",
        "gas_turbine,300,15,2025-08-22,2026-09-26,0.170,0.000,1.100,7000.0,6900.0",
    ],
    "q3_coverage.csv": [
        "section,breakdown,item,unit_periods,abs_metered_mwh,abs_pn_mwh",
        '1,all metered unit-periods,"mapped, fuel type known (the cohort)",'
        "1000,25000.0,",
        "3,cohort table by determinability,determinable,940,24001.0,24500.0",
    ],
    "q12_instructed_vs_residual.csv": [
        "fuel_group,unit_periods,instructed_periods,instructed_period_share,"
        "abs_deviation_mwh,abs_instructed_deviation_mwh,"
        "abs_unexplained_shortfall_mwh,instructed_share_of_abs_deviation",
        "nuclear,150,0,0.000000,450.0,0.0,450.0,0.000000",
        "wind,400,120,0.300000,2950.0,900.0,2100.0,0.300000",
        "interconnector,60,0,0.000000,50.0,0.0,50.0,0.000000",
        "gas_turbine,300,200,0.666667,1180.0,1000.0,200.0,0.833333",
    ],
    "q2_changes_by_unit_prefix.csv": [
        "unit_prefix,matched,changed,moved_mwh",
        "2,100,40,60000.000",
        "T,50,5,25500.000",
        "C,80,2,4000.000",
        "E,30,1,500.000",
        "V,20,3,10000.000",
    ],
}

# Rows the newer charts read, added to the fixture files. Q23: a part month,
# the whole months the fixture lacks (with August 2026 and the provisional
# September there), wind thinnest at exactly 20 units, nuclear at 4, and
# coal; nuclear's October, April and June end in 50, so their labels show
# halves rounded up. Q5: an interconnector, which the chart leaves out. Q30:
# the other three correlations and their intervals, every value different.
ROWS_ADDED_TO_FIXTURES = {
    "q23_pn_accuracy_proxy.csv": [
        "1,wind,2025-08-01,unit-months,25,9000,0.500,4.000,9.900,14.000",
        "1,wind,2025-09-01,unit-months,20,30000,0.600,4.100,6.300,10.000",
        "1,wind,2025-10-01,unit-months,29,31000,0.700,4.200,7.000,11.000",
        "1,wind,2025-11-01,unit-months,30,31000,0.800,4.300,7.200,10.500",
        "1,wind,2025-12-01,unit-months,30,31000,0.900,4.400,7.900,12.000",
        "1,wind,2026-01-01,unit-months,30,31000,1.000,4.500,7.400,11.500",
        "1,wind,2026-02-01,unit-months,30,31000,1.100,4.600,7.600,10.800",
        "1,wind,2026-03-01,unit-months,30,31000,1.200,4.700,7.800,11.200",
        "1,wind,2026-04-01,unit-months,30,31000,1.300,4.800,6.900,10.200",
        "1,wind,2026-05-01,unit-months,30,31000,1.400,4.900,6.800,10.400",
        "1,wind,2026-06-01,unit-months,30,31000,1.500,5.000,7.300,11.100",
        "1,wind,2026-07-01,unit-months,30,31000,1.600,5.100,6.500,9.800",
        "1,nuclear,2025-09-01,unit-months,4,5000,0.800,3.000,0.900,4.000",
        "1,nuclear,2025-10-01,unit-months,4,5000,1.350,3.000,1.450,4.000",
        "1,nuclear,2025-11-01,unit-months,4,5000,1.000,3.000,1.100,4.000",
        "1,nuclear,2025-12-01,unit-months,4,5000,0.700,3.000,0.800,4.000",
        "1,nuclear,2026-01-01,unit-months,4,5000,1.700,3.000,1.800,4.000",
        "1,nuclear,2026-02-01,unit-months,4,5000,0.200,3.000,0.300,4.000",
        "1,nuclear,2026-03-01,unit-months,4,5000,0.400,3.000,0.500,4.000",
        "1,nuclear,2026-04-01,unit-months,4,5000,1.050,3.000,1.150,4.000",
        "1,nuclear,2026-05-01,unit-months,4,5000,1.300,3.000,1.400,4.000",
        "1,nuclear,2026-06-01,unit-months,4,5000,0.850,3.000,0.950,4.000",
        "1,nuclear,2026-07-01,unit-months,4,5000,1.200,3.000,1.300,4.000",
        "1,coal,2026-08-01,unit-months,2,2976,0.000,0.000,0.000,0.000",
    ],
    "q5_shortfall_with_known_outage.csv": [
        "2,interconnector,none,154,51.5,100.0,2025-08-22,2026-09-27,",
    ],
    "q30_wind_vs_carbon_error.csv": [
        '3,"rho_signed (secondary): wind error, outturn with cohort curtailment '
        'add-back, with error_api_final; expected direction negative",,500,,,,'
        "0.060000,",
        '3,"rho_signed (secondary): bootstrap 90% interval, low",,,,,,0.030000,',
        '3,"rho_signed (secondary): bootstrap 90% interval, high",,,,,,0.090000,',
        '4,"rho_abs (sensitivity): bootstrap 90% interval, low",,,,,,-0.070000,',
        '4,"rho_abs (sensitivity): bootstrap 90% interval, high",,,,,,-0.010000,',
        '4,"rho_signed (sensitivity): wind error, delivered outturn, '
        'with error_api_final",,500,,,,-0.150000,',
        '4,"rho_signed (sensitivity): bootstrap 90% interval, low",,,,,,-0.200000,',
        '4,"rho_signed (sensitivity): bootstrap 90% interval, high",,,,,,-0.120000,',
    ],
}

# Each chart CSV, worked by hand from the rows above and the fixtures.
EXPECTED_CHART_CSVS = {
    "q2_moved_energy_by_unit_type.csv": BMRS_LINE
    + "unit_type,label,moved_mwh,share_of_moved_pct\n"
    "Supplier units,Supplier units,60000.000,60.000000\n"
    "Transmission-connected and embedded units,"
    "Transmission-connected|and embedded units,26000.000,26.000000\n"
    "Other unit types,Other unit types,14000.000,14.000000\n",
    "q3_gap_from_plan_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,unit_periods,q1_deviation_mwh,median_deviation_mwh,"
    "q3_deviation_mwh,week_first_run_median_mwh,week_latest_run_median_mwh\n"
    "wind,400,-1.000,0.300,2.900,0.200,0.250\n"
    "nuclear,150,0.000,0.250,2.600,1.200,1.100\n"
    "gas_turbine,300,0.000,0.170,1.100,,\n",
    "q12_instructed_share_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,unit_periods,instructed_mwh,rest_mwh,parts_mwh,"
    "instructed_share_pct\n"
    "wind,400,900.0,2100.0,3000.0,30.0000\n"
    "gas_turbine,300,1000.0,200.0,1200.0,83.3333\n"
    "nuclear,150,0.0,450.0,450.0,0.0000\n",
    "q5_under_delivery_by_outage_notice.csv": BMRS_LINE
    + "fuel_group,planned_mwh,unplanned_mwh,no_notice_mwh,total_mwh,"
    "with_notice_pct\n"
    "wind,70.0,60.0,570.0,700.0,18.5714\n"
    "nuclear,180.0,90.0,30.0,300.0,90.0000\n",
    # Wind's 12 whole months, sorted: 6.3 6.5 6.8 6.9 7.0 7.2 | 7.3 7.4 7.5
    # 7.6 7.8 7.9, so the middle is 7.25; their 90th percentiles: 9.8 10.0
    # 10.2 10.4 10.5 10.8 | 11.0 11.1 11.2 11.5 12.0 12.0, so 10.9. Nuclear:
    # 0.3 0.5 0.8 0.9 0.95 1.1 | 1.15 1.2 1.3 1.4 1.45 1.8, so 1.125, and
    # no 90th percentile at 4 units.
    "q23_typical_gap_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,fewest_units,typical_pct,typical_label,one_in_ten_pct,"
    "one_in_ten_label\n"
    "wind,20,7.2500,7.3%,10.9000,10.9%\n"
    "nuclear,4,1.1250,1.1%,,\n",
    "q23_monthly_gap_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,month,month_name,gap_pct,gap_label,units,provisional,dashed,"
    "end_name_pct\n"
    "wind,2025-09-01,Sep 2025,6.300,6.3,20,no,no,\n"
    "wind,2025-10-01,Oct 2025,7.000,7.0,29,no,no,\n"
    "wind,2025-11-01,Nov 2025,7.200,7.2,30,no,no,\n"
    "wind,2025-12-01,Dec 2025,7.900,7.9,30,no,no,\n"
    "wind,2026-01-01,Jan 2026,7.400,7.4,30,no,no,\n"
    "wind,2026-02-01,Feb 2026,7.600,7.6,30,no,no,\n"
    "wind,2026-03-01,Mar 2026,7.800,7.8,30,no,no,\n"
    "wind,2026-04-01,Apr 2026,6.900,6.9,30,no,no,\n"
    "wind,2026-05-01,May 2026,6.800,6.8,30,no,no,\n"
    "wind,2026-06-01,Jun 2026,7.300,7.3,30,no,no,\n"
    "wind,2026-07-01,Jul 2026,6.500,6.5,30,no,no,\n"
    "wind,2026-08-01,Aug 2026,7.500,7.5,30,no,yes,\n"
    "wind,2026-09-01,Sep 2026,8.700,8.7,31,yes,yes,8.700\n"
    "nuclear,2025-09-01,Sep 2025,0.900,0.9,4,no,no,\n"
    "nuclear,2025-10-01,Oct 2025,1.450,1.5,4,no,no,\n"
    "nuclear,2025-11-01,Nov 2025,1.100,1.1,4,no,no,\n"
    "nuclear,2025-12-01,Dec 2025,0.800,0.8,4,no,no,\n"
    "nuclear,2026-01-01,Jan 2026,1.800,1.8,4,no,no,\n"
    "nuclear,2026-02-01,Feb 2026,0.300,0.3,4,no,no,\n"
    "nuclear,2026-03-01,Mar 2026,0.500,0.5,4,no,no,\n"
    "nuclear,2026-04-01,Apr 2026,1.150,1.2,4,no,no,\n"
    "nuclear,2026-05-01,May 2026,1.400,1.4,4,no,no,\n"
    "nuclear,2026-06-01,Jun 2026,0.950,1.0,4,no,no,\n"
    "nuclear,2026-07-01,Jul 2026,1.300,1.3,4,no,no,\n"
    "nuclear,2026-08-01,Aug 2026,1.200,1.2,4,no,yes,\n"
    "nuclear,2026-09-01,Sep 2026,2.300,2.3,4,yes,yes,2.300\n",
    "q24_flagged_share_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,acceptance_volume_mwh,flagged_pct,not_flagged_pct\n"
    "wind,9000.0,96.0,4.0\n"
    "gas_turbine,16000.0,35.0,65.0\n",
    "q25_short_instructions_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,instructions,short_joined_pct,short_on_its_own_pct,"
    "short_joined_as_first_read_pct\n"
    "other,4000,21.100000,77.250000,21.250000\n"
    "gas_turbine,1200,0.166667,2.500000,0.333333\n",
    "q28_shortfall_index_by_miss_fifth.csv": BMRS_AND_CARBON_LINE
    + "quintile,label,misses,half_hours,mean_shortfall_mwh,index,index_low,"
    "index_high\n"
    '1,Smallest misses|0–2 g,"Smallest misses, 0–2 g",100,500.000000,'
    "100.0000,,\n"
    "2,2–4 g,2–4 g,100,510.000000,102.0000,,\n"
    "3,4–7 g,4–7 g,100,520.000000,104.0000,,\n"
    "4,7–12 g,7–12 g,100,530.000000,106.0000,,\n"
    '5,Largest misses|12 g and up,"Largest misses, 12 g and up",100,'
    "550.000000,110.0000,101.0000,120.0000\n",
    "q30_rank_correlations.csv": BMRS_AND_CARBON_LINE + "label,measure,rho,low,high\n"
    "Size of misses|curtailment added back,"
    '"Size of the two misses, wind with curtailment added back",'
    "0.010000,-0.020000,0.040000\n"
    "Direction|curtailment added back,"
    '"Direction of the two misses, wind with curtailment added back",'
    "0.060000,0.030000,0.090000\n"
    "Size of misses|delivered wind,"
    '"Size of the two misses, delivered wind only",'
    "-0.040000,-0.070000,-0.010000\n"
    "Direction|delivered wind,"
    '"Direction of the two misses, delivered wind only",'
    "-0.150000,-0.200000,-0.120000\n",
}

WRITTEN_FILES = [
    "q12_instructed_share_by_fuel_group.csv",
    "q12_instructed_share_by_fuel_group.png",
    "q12_instructed_share_by_fuel_group.vl.json",
    "q12_instructed_vs_residual.csv",
    "q22_first_against_latest_run.csv",
    "q23_monthly_gap_by_fuel_group.csv",
    "q23_monthly_gap_by_fuel_group.png",
    "q23_monthly_gap_by_fuel_group.vl.json",
    "q23_pn_accuracy_proxy.csv",
    "q23_typical_gap_by_fuel_group.csv",
    "q23_typical_gap_by_fuel_group.png",
    "q23_typical_gap_by_fuel_group.vl.json",
    "q24_flagged_share_by_fuel_group.csv",
    "q24_flagged_share_by_fuel_group.png",
    "q24_flagged_share_by_fuel_group.vl.json",
    "q24_system_action_share.csv",
    "q25_instruction_fragmentation.csv",
    "q25_instruction_fragmentation_corrected.csv",
    "q25_short_instructions_by_fuel_group.csv",
    "q25_short_instructions_by_fuel_group.png",
    "q25_short_instructions_by_fuel_group.vl.json",
    "q28_shortfall_index_by_miss_fifth.csv",
    "q28_shortfall_index_by_miss_fifth.png",
    "q28_shortfall_index_by_miss_fifth.vl.json",
    "q29_error_by_month.png",
    "q29_error_by_month.vl.json",
    "q29_error_by_time_of_day.png",
    "q29_error_by_time_of_day.vl.json",
    "q29_final_forecast_error_by_month_and_block.csv",
    "q2_changes_by_unit_prefix.csv",
    "q2_moved_energy_by_unit_type.csv",
    "q2_moved_energy_by_unit_type.png",
    "q2_moved_energy_by_unit_type.vl.json",
    "q2_settlement_run_changes.csv",
    "q30_rank_correlations.csv",
    "q30_rank_correlations.png",
    "q30_rank_correlations.vl.json",
    "q30_wind_vs_carbon_error.csv",
    "q3_coverage.csv",
    "q3_gap_from_plan_by_fuel_group.csv",
    "q3_gap_from_plan_by_fuel_group.png",
    "q3_gap_from_plan_by_fuel_group.vl.json",
    "q3_metered_vs_notified.csv",
    "q5_shortfall_with_known_outage.csv",
    "q5_under_delivery_by_outage_notice.csv",
    "q5_under_delivery_by_outage_notice.png",
    "q5_under_delivery_by_outage_notice.vl.json",
    "q6_publication_lag.csv",
    "q8_q28_history.csv",
]


def write_lines(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_lines(path, lines):
    with path.open("a", encoding="utf-8") as target:
        target.write("\n".join(lines) + "\n")


def saved_outputs_folder(folder):
    """A folder of the hand-made saved outputs, as the script reads them."""
    folder.mkdir()
    for fixture in FIXTURES.iterdir():
        (folder / fixture.name).write_bytes(fixture.read_bytes())
    for name, lines in NEW_SAVED_OUTPUTS.items():
        write_lines(folder / name, lines)
    for name, lines in ROWS_ADDED_TO_FIXTURES.items():
        add_lines(folder / name, lines)
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


def chart_csvs(folder):
    written = {}
    for name in EXPECTED_CHART_CSVS:
        written[name] = (folder / name).read_bytes().decode("utf-8")
    return written


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
        elif key == "as" and isinstance(value, str):
            derived.add(value)
        elif key == "as":
            derived.update(value)
        else:
            collect_fields(value, used, derived)


def written_specs(folder):
    specs = {}
    for spec_path in sorted(folder.glob("*.vl.json")):
        specs[spec_path.name] = json.loads(spec_path.read_text(encoding="utf-8"))
    return specs


def spec_data_urls(folder):
    data_urls = {}
    for name, spec in written_specs(folder).items():
        data_urls[name] = spec["data"]["url"]
    return data_urls


def fields_missing_from_their_csv(folder):
    """Per spec, the fields it reads that its CSV's header does not hold."""
    missing_fields = {}
    for name, spec in written_specs(folder).items():
        header = csv_header(folder / spec["data"]["url"].removeprefix("results/"))
        used = set()
        derived = set()
        collect_fields(spec, used, derived)
        missing = sorted(used - derived - set(header))
        if missing:
            missing_fields[name] = missing
    return missing_fields


def x_domains(node, domains):
    """Each distinct domain set on an x scale anywhere in a spec."""
    if isinstance(node, list):
        for item in node:
            x_domains(item, domains)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        x_domains(value, domains)
        if key != "x" or not isinstance(value, dict):
            continue
        domain = value.get("scale", {}).get("domain")
        if domain is not None and domain not in domains:
            domains.append(domain)


def x_domains_by_spec(folder):
    """Per spec that sets one, each domain of its x scales."""
    domains = {}
    for name, spec in written_specs(folder).items():
        spec_domains = []
        x_domains(spec, spec_domains)
        if spec_domains:
            domains[name] = spec_domains
    return domains


def x_reference_values(node, values):
    """Every fixed x or x2 position a spec draws a line or band at."""
    if isinstance(node, list):
        for item in node:
            x_reference_values(item, values)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        x_reference_values(value, values)
        if key in ("x", "x2") and isinstance(value, dict) and "datum" in value:
            values.append(value["datum"])


def nonzero_reference_lines(folder):
    """Per spec, its fixed x positions other than the zero line."""
    references = {}
    for name, spec in written_specs(folder).items():
        values = []
        x_reference_values(spec, values)
        kept = []
        for value in values:
            if value != 0:
                kept.append(value)
        if kept:
            references[name] = kept
    return references


def encoded_fields(node, fields):
    """Every field a spec's encodings read, in any layer."""
    if isinstance(node, list):
        for item in node:
            encoded_fields(item, fields)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key == "encoding":
            collect_fields(value, fields, set())
        else:
            encoded_fields(value, fields)


def specs_encoding_raw_fuel_groups(folder):
    """The specs whose encodings read the fuel group code itself."""
    names = []
    for name, spec in written_specs(folder).items():
        fields = set()
        encoded_fields(spec, fields)
        if "fuel_group" in fields:
            names.append(name)
    return names


def fuel_group_names_by_spec(folder):
    """Per spec that names fuel groups, each group with the name it shows."""
    names_shown = {}
    for name, spec in written_specs(folder).items():
        shown = dict(DISPLAYED_FUEL_GROUP.findall(json.dumps(spec)))
        if shown:
            names_shown[name] = shown
    return names_shown


def specs_not_written_sorted(folder):
    unsorted = []
    for spec_path in sorted(folder.glob("*.vl.json")):
        text = spec_path.read_text(encoding="utf-8")
        if text != json.dumps(json.loads(text), sort_keys=True, indent=2) + "\n":
            unsorted.append(spec_path.name)
    return unsorted


KEY_CHANNELS = ("color", "fill", "stroke", "shape")
# Layers drawn without a key entry, whose meaning the axis titles, or a
# word drawn on the band, give.
UNKEYED_LAYER_NAMES = ("zero line", "connector", "provisional band")
BAND_TYPES = ("nominal", "ordinal")


def mark_type(layer):
    mark = layer["mark"]
    if isinstance(mark, str):
        return mark
    return mark["type"]


def drawn_layers(node, layers):
    """Every layer that draws a mark, at any depth of a spec."""
    if isinstance(node, list):
        for item in node:
            drawn_layers(item, layers)
        return
    if not isinstance(node, dict):
        return
    if "mark" in node:
        layers.append(node)
    for key, value in node.items():
        if key != "mark":
            drawn_layers(value, layers)


def own_key_label(layer):
    """The entry in its chart's key that the layer names itself by, if any."""
    for step in layer.get("transform", []):
        if step.get("as") == "key":
            return json.loads(step["calculate"])
    return None


def shows_a_legend(definition):
    """A legend of none, or false, draws none."""
    return definition.get("legend", {}) not in (None, False)


def key_entries(layers):
    """Every entry the spec's keys show."""
    entries = set()
    for layer in layers:
        encoding = layer.get("encoding", {})
        for channel in KEY_CHANNELS:
            definition = encoding.get(channel, {})
            if "field" in definition and shows_a_legend(definition):
                entries.update(definition.get("scale", {}).get("domain", []))
    return entries


def text_fields(layers):
    """The fields the spec writes on the chart as text."""
    fields = set()
    for layer in layers:
        if mark_type(layer) == "text":
            text = layer.get("encoding", {}).get("text", {})
            if "field" in text:
                fields.add(text["field"])
    return fields


def named_on_the_chart(layer, written_as_text):
    """The fields the chart names beside the layer's marks: its text labels,
    and the layer's own row or column names."""
    named = set(written_as_text)
    encoding = layer.get("encoding", {})
    for position in ("x", "y"):
        definition = encoding.get(position, {})
        if definition.get("type") in BAND_TYPES and "field" in definition:
            named.add(definition["field"])
    return named


def has_key_entry(layer, entries, written_as_text):
    """Whether the chart says what the layer's marks are. A colour or shape
    drawn from a field needs that field's key, holding the layer's own key
    entry if it names one, or the field named beside the marks instead. A
    layer that names its key entry and has no key of its own needs the
    entry in the chart's key."""
    label = own_key_label(layer)
    encoding = layer.get("encoding", {})
    named = named_on_the_chart(layer, written_as_text)
    keyed = False
    labelled = False
    for channel in KEY_CHANNELS:
        definition = encoding.get(channel, {})
        if "field" not in definition:
            continue
        if not shows_a_legend(definition):
            if definition["field"] not in named:
                return False
            labelled = True
            continue
        domain = definition.get("scale", {}).get("domain", [])
        if label is not None and label not in domain:
            return False
        keyed = True
    if keyed:
        return True
    if label is not None:
        return label in entries
    return labelled


def marks_without_a_key(folder):
    """Per spec, the mark types it draws that neither its key nor a label
    on the chart explains; text labels and the named zero lines, connectors
    and provisional bands need none."""
    unkeyed = {}
    for name, spec in written_specs(folder).items():
        layers = []
        drawn_layers(spec, layers)
        entries = key_entries(layers)
        written_as_text = text_fields(layers)
        missing = []
        for layer in layers:
            if mark_type(layer) == "text":
                continue
            if layer.get("name") in UNKEYED_LAYER_NAMES:
                continue
            if not has_key_entry(layer, entries, written_as_text):
                missing.append(mark_type(layer))
        if missing:
            unkeyed[name] = missing
    return unkeyed


def specs_without_a_reading_line(folder):
    names = []
    for name, spec in written_specs(folder).items():
        if not spec["title"].get("subtitle"):
            names.append(name)
    return names


def wrapped_choices(expression):
    """Each wrapping of the text a box-width expression chooses between,
    narrowest box first."""
    choices = []
    for found in WRAPPED_LINES.finditer(expression):
        choices.append(json.loads(found.group(0)))
    return choices


def fills_each_line(lines, characters):
    """No line longer than the box holds, and none that could have taken
    the next line's first word."""
    for position, line in enumerate(lines):
        if len(line) > characters:
            return False
        if position + 1 < len(lines):
            next_word = lines[position + 1].split(" ")[0]
            if len(line) + 1 + len(next_word) <= characters:
                return False
    return True


def reading_lines_not_filling_the_box(folder):
    """Per spec whose reading line is not a box-width signal wrapping it full
    at the narrowest and widest boxes, what it holds instead."""
    narrowest = int((340 - 28) / 5.7)
    widest = int((720 - 28) / 5.7)
    not_filling = {}
    for name, spec in written_specs(folder).items():
        subtitle = spec["title"]["subtitle"]
        if not isinstance(subtitle, dict) or "signal" not in subtitle:
            not_filling[name] = subtitle
            continue
        choices = wrapped_choices(subtitle["signal"])
        if len(choices) != 6:
            not_filling[name] = choices
            continue
        if not fills_each_line(choices[0], narrowest):
            not_filling[name] = choices[0]
        elif not fills_each_line(choices[-1], widest):
            not_filling[name] = choices[-1]
    return not_filling


def chart_csv_rows(folder, name):
    """A chart CSV's rows, after its attribution line."""
    text = (folder / name).read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(text.split("\n", 1)[1])))


def chart_rows_by_group(folder, name):
    """Per fuel group, its row's values other than the group and units."""
    by_group = {}
    for row in chart_csv_rows(folder, name):
        values = dict(row)
        del values["fuel_group"]
        del values["fewest_units"]
        by_group[row["fuel_group"]] = values
    return by_group


def unlabelled_values(folder, name):
    """Rows of the summary chart whose drawn value has no label."""
    missing = []
    for row in chart_csv_rows(folder, name):
        if not row["typical_label"]:
            missing.append((row["fuel_group"], "typical"))
        if row["one_in_ten_pct"] and not row["one_in_ten_label"]:
            missing.append((row["fuel_group"], "one in ten"))
    return missing


def text_fields_drawn(folder, spec_name):
    """Each text layer's field and the field that places it along x."""
    layers = []
    drawn_layers(written_specs(folder)[spec_name], layers)
    drawn = []
    for layer in layers:
        encoding = layer.get("encoding", {})
        if mark_type(layer) == "text" and "field" in encoding.get("text", {}):
            drawn.append((encoding["text"]["field"], encoding["x"]["field"]))
    return drawn


def monthly_values(folder, picked):
    """The month-by-month chart's value and label for each (group, month)."""
    values = {}
    for row in chart_csv_rows(folder, "q23_monthly_gap_by_fuel_group.csv"):
        key = (row["fuel_group"], row["month"])
        if key in picked:
            values[key] = (row["gap_pct"], row["gap_label"])
    return values


def typical_and_middle_of_months(folder):
    """Per fuel group, the summary chart's typical gap beside the middle of
    the whole months the month-by-month chart draws, both to 4 places."""
    whole_months = {}
    for row in chart_csv_rows(folder, "q23_monthly_gap_by_fuel_group.csv"):
        if row["provisional"] == "no":
            whole_months.setdefault(row["fuel_group"], []).append(
                Decimal(row["gap_pct"])
            )
    paired = {}
    for row in chart_csv_rows(folder, "q23_typical_gap_by_fuel_group.csv"):
        middle = statistics.median(whole_months[row["fuel_group"]])
        four_places = middle.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        paired[row["fuel_group"]] = (row["typical_pct"], str(four_places))
    return paired


def months_flagged(folder, column):
    """The months whose rows the column flags yes, for every fuel group
    alike; a month flagged for some groups only is shown with them."""
    flagged = {}
    groups = set()
    for row in chart_csv_rows(folder, "q23_monthly_gap_by_fuel_group.csv"):
        groups.add(row["fuel_group"])
        if row[column] == "yes":
            flagged.setdefault(row["month"], set()).add(row["fuel_group"])
    months = []
    for month, flagged_groups in sorted(flagged.items()):
        if flagged_groups == groups:
            months.append(month)
        else:
            months.append((month, sorted(flagged_groups)))
    return months


def q23_monthly_layers(folder):
    spec = written_specs(folder)["q23_monthly_gap_by_fuel_group.vl.json"]
    layers = []
    drawn_layers(spec, layers)
    return layers


def layer_filters(layer):
    filters = []
    for step in layer.get("transform", []):
        if "filter" in step:
            filters.append(step["filter"])
    return filters


def provisional_marks(folder):
    """How the month-by-month chart marks the provisional month: its band,
    the word drawn on it, and the filter of its hollow point."""
    marks = {}
    for layer in q23_monthly_layers(folder):
        mark = layer["mark"]
        if layer.get("name") == "provisional band":
            marks["band"] = layer["name"]
        text = layer.get("encoding", {}).get("text", {})
        if mark_type(layer) == "text" and "value" in text:
            marks["word"] = text["value"]
        if mark_type(layer) == "point" and mark.get("filled") is False:
            marks["hollow point"] = " and ".join(layer_filters(layer))
    return marks


def line_end_names(folder):
    """Per fuel group drawn, where its line's name sits; a group not named
    exactly once shows every place it is named, if any."""
    rows = chart_csv_rows(folder, "q23_monthly_gap_by_fuel_group.csv")
    places = {}
    for row in rows:
        places.setdefault(row["fuel_group"], [])
        if row["end_name_pct"]:
            places[row["fuel_group"]].append(row["end_name_pct"])
    named = {}
    for fuel_group, group_places in places.items():
        if len(group_places) == 1:
            named[fuel_group] = group_places[0]
        else:
            named[fuel_group] = group_places
    return named


def name_layers(folder):
    """The text layers that name a line, by the field they show and the
    height they sit at."""
    found = []
    for layer in q23_monthly_layers(folder):
        encoding = layer.get("encoding", {})
        if mark_type(layer) != "text" or "field" not in encoding.get("text", {}):
            continue
        found.append((encoding["text"]["field"], encoding["y"]["field"]))
    return found


def spec_descriptions(folder, names):
    descriptions = {}
    for name in names:
        descriptions[name] = written_specs(folder)[name]["description"]
    return descriptions


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    folder = tmp_path_factory.mktemp("charts")
    write_charts(saved_outputs_folder(folder / "saved"), folder / "out")
    return folder / "out"


def test_a_rerun_writes_identical_files(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    write_charts(saved, tmp_path / "first")
    write_charts(saved, tmp_path / "second")

    first = output_files(tmp_path / "first")
    assert sorted(first) == WRITTEN_FILES
    assert output_files(tmp_path / "second") == first


def test_a_saved_output_without_its_chart_rows_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    without_lines_containing(
        saved / "q8_q28_history.csv", "quintile of abs(error_api_final)"
    )

    with pytest.raises(ValueError, match="no row"):
        write_charts(saved, tmp_path / "out")


def test_each_chart_csv_holds_exactly_the_values_its_chart_draws(written):
    assert chart_csvs(written) == EXPECTED_CHART_CSVS


def test_titles_that_state_a_figure_take_it_from_the_saved_output(written):
    # Q5: planned 250, unplanned 140 and both 10 of 1,000 MWh. Q2: 100,000
    # MWh moved. Q23: wind's typical gap 7.25, its whole months 6.3 to 7.9;
    # nuclear's typical 1.125 and every whole month under 3.5.
    names = [
        "q5_under_delivery_by_outage_notice.vl.json",
        "q2_moved_energy_by_unit_type.vl.json",
        "q23_typical_gap_by_fuel_group.vl.json",
        "q23_monthly_gap_by_fuel_group.vl.json",
    ]
    assert spec_descriptions(written, names) == {
        "q5_under_delivery_by_outage_notice.vl.json": (
            "All units: 40% of under-delivery came with a published outage"
        ),
        "q2_moved_energy_by_unit_type.vl.json": (
            "Where the 100 GWh that moved came from"
        ),
        "q23_typical_gap_by_fuel_group.vl.json": (
            "With no NESO instruction, the typical wind farm missed its plan by "
            "7.3% of its capacity; no other type reached 3%"
        ),
        "q23_monthly_gap_by_fuel_group.vl.json": (
            "The typical wind farm missed its plan by 6.3 to 7.9% of its capacity "
            "in every whole month; every other type, by under 3.5%"
        ),
    }


def test_every_written_csv_starts_with_its_attribution(written):
    assert first_lines(written) == {
        "q12_instructed_share_by_fuel_group.csv": BMRS_LINE,
        "q12_instructed_vs_residual.csv": BMRS_LINE,
        "q22_first_against_latest_run.csv": BMRS_LINE,
        "q23_monthly_gap_by_fuel_group.csv": BMRS_LINE,
        "q23_pn_accuracy_proxy.csv": BMRS_LINE,
        "q23_typical_gap_by_fuel_group.csv": BMRS_LINE,
        "q24_flagged_share_by_fuel_group.csv": BMRS_LINE,
        "q24_system_action_share.csv": BMRS_LINE,
        "q25_instruction_fragmentation.csv": BMRS_LINE,
        "q25_instruction_fragmentation_corrected.csv": BMRS_LINE,
        "q25_short_instructions_by_fuel_group.csv": BMRS_LINE,
        "q28_shortfall_index_by_miss_fifth.csv": BMRS_AND_CARBON_LINE,
        "q29_final_forecast_error_by_month_and_block.csv": CARBON_LINE,
        "q2_changes_by_unit_prefix.csv": BMRS_LINE,
        "q2_moved_energy_by_unit_type.csv": BMRS_LINE,
        "q2_settlement_run_changes.csv": BMRS_LINE,
        "q30_rank_correlations.csv": BMRS_AND_CARBON_LINE,
        "q30_wind_vs_carbon_error.csv": BMRS_AND_CARBON_LINE,
        "q3_coverage.csv": BMRS_LINE,
        "q3_gap_from_plan_by_fuel_group.csv": BMRS_LINE,
        "q3_metered_vs_notified.csv": BMRS_LINE,
        "q5_shortfall_with_known_outage.csv": BMRS_LINE,
        "q5_under_delivery_by_outage_notice.csv": BMRS_LINE,
        "q6_publication_lag.csv": BMRS_LINE,
        "q8_q28_history.csv": BMRS_AND_CARBON_LINE,
    }


def test_a_saved_output_is_copied_unchanged_after_its_attribution(tmp_path):
    # The saved outputs are held with Windows line endings; the copy is LF.
    saved = saved_outputs_folder(tmp_path / "saved")
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
        "q23_monthly_gap_by_fuel_group.vl.json": (
            "results/q23_monthly_gap_by_fuel_group.csv"
        ),
        "q23_typical_gap_by_fuel_group.vl.json": (
            "results/q23_typical_gap_by_fuel_group.csv"
        ),
        "q24_flagged_share_by_fuel_group.vl.json": (
            "results/q24_flagged_share_by_fuel_group.csv"
        ),
        "q25_short_instructions_by_fuel_group.vl.json": (
            "results/q25_short_instructions_by_fuel_group.csv"
        ),
        "q28_shortfall_index_by_miss_fifth.vl.json": (
            "results/q28_shortfall_index_by_miss_fifth.csv"
        ),
        "q29_error_by_month.vl.json": (
            "results/q29_final_forecast_error_by_month_and_block.csv"
        ),
        "q29_error_by_time_of_day.vl.json": (
            "results/q29_final_forecast_error_by_month_and_block.csv"
        ),
        "q2_moved_energy_by_unit_type.vl.json": (
            "results/q2_moved_energy_by_unit_type.csv"
        ),
        "q30_rank_correlations.vl.json": "results/q30_rank_correlations.csv",
        "q3_gap_from_plan_by_fuel_group.vl.json": (
            "results/q3_gap_from_plan_by_fuel_group.csv"
        ),
        "q5_under_delivery_by_outage_notice.vl.json": (
            "results/q5_under_delivery_by_outage_notice.csv"
        ),
    }
    assert fields_missing_from_their_csv(written) == {}


def test_every_spec_is_written_with_sorted_keys_and_two_space_indents(written):
    assert specs_not_written_sorted(written) == []


def test_every_fixed_axis_spans_its_whole_scale(written):
    assert x_domains_by_spec(written) == {
        "q23_typical_gap_by_fuel_group.vl.json": [[0, 12]],
        "q24_flagged_share_by_fuel_group.vl.json": [[0, 100]],
        "q25_short_instructions_by_fuel_group.vl.json": [[0.01, 100]],
        "q28_shortfall_index_by_miss_fifth.vl.json": [[95, 130]],
        "q2_moved_energy_by_unit_type.vl.json": [[0, 100]],
        "q30_rank_correlations.vl.json": [[-0.3, 0.3]],
    }


def test_reference_lines_sit_where_the_rules_put_them(written):
    # Q28: supported at a ratio of 1.25; Q30: an absolute rank correlation
    # under about 0.2 was expected. Q23: the provisional band runs from
    # mid-August 2026 to just past September's point, where the time axis
    # ends, with its word at its left edge.
    assert nonzero_reference_lines(written) == {
        "q23_monthly_gap_by_fuel_group.vl.json": [
            BAND_FROM_17_AUGUST_2026,
            ENDS_12_SEPTEMBER_2026,
            BAND_FROM_17_AUGUST_2026,
        ],
        "q28_shortfall_index_by_miss_fifth.vl.json": [125, 125],
        "q30_rank_correlations.vl.json": [-0.2, 0.2],
    }


def test_every_mark_on_every_chart_has_an_entry_in_its_key(written):
    assert marks_without_a_key(written) == {}


def test_every_chart_says_how_to_read_it_under_its_title(written):
    assert specs_without_a_reading_line(written) == []


def test_every_reading_line_fills_the_chart_box_on_a_phone_and_a_desktop(written):
    assert reading_lines_not_filling_the_box(written) == {}


def test_q23_typical_chart_takes_the_middle_of_the_12_whole_months(written):
    # Worked above EXPECTED_CHART_CSVS: wind 7.25 and 10.9; nuclear 1.125,
    # too few units for a 90th percentile.
    assert chart_rows_by_group(written, "q23_typical_gap_by_fuel_group.csv") == {
        "wind": {
            "typical_pct": "7.2500",
            "typical_label": "7.3%",
            "one_in_ten_pct": "10.9000",
            "one_in_ten_label": "10.9%",
        },
        "nuclear": {
            "typical_pct": "1.1250",
            "typical_label": "1.1%",
            "one_in_ten_pct": "",
            "one_in_ten_label": "",
        },
    }


def test_q23_typical_chart_prints_each_bars_values_at_its_end(written):
    # Every bar's value, and the pale line's where it is drawn.
    assert unlabelled_values(written, "q23_typical_gap_by_fuel_group.csv") == []
    assert text_fields_drawn(written, "q23_typical_gap_by_fuel_group.vl.json") == [
        ("typical_label", "typical_pct"),
        ("one_in_ten_label", "one_in_ten_pct"),
    ]


def test_q23_monthly_chart_draws_each_groups_typical_gap_in_its_month(written):
    # Wind and nuclear in two months each; nuclear's 1.450 and 0.950 show
    # their halves rounded up.
    picked = [
        ("wind", "2025-09-01"),
        ("wind", "2026-08-01"),
        ("nuclear", "2025-10-01"),
        ("nuclear", "2026-06-01"),
    ]
    assert monthly_values(written, picked) == {
        ("wind", "2025-09-01"): ("6.300", "6.3"),
        ("wind", "2026-08-01"): ("7.500", "7.5"),
        ("nuclear", "2025-10-01"): ("1.450", "1.5"),
        ("nuclear", "2026-06-01"): ("0.950", "1.0"),
    }


def test_q23_typical_gaps_are_the_middles_of_the_monthly_gaps_drawn(written):
    assert typical_and_middle_of_months(written) == {
        "wind": ("7.2500", "7.2500"),
        "nuclear": ("1.1250", "1.1250"),
    }


def test_q23_september_2026_is_drawn_provisional(written):
    assert months_flagged(written, "provisional") == ["2026-09-01"]
    assert months_flagged(written, "dashed") == ["2026-08-01", "2026-09-01"]
    assert provisional_marks(written) == {
        "band": "provisional band",
        "word": "provisional",
        "hollow point": "datum.provisional == 'yes'",
    }


def test_q23_every_line_is_named_at_its_end(written):
    assert line_end_names(written) == {"wind": "8.700", "nuclear": "2.300"}
    assert name_layers(written) == [("fuel_group_name", "end_name_pct")]


def test_q23_line_end_names_move_apart_only_as_far_as_they_must():
    # Gap 0.5, floor 0.2: 1.0 stays; 0.9 goes down to 0.5, then 0.1 to 0.0,
    # under the floor, so it rises to 0.2, 0.9's name to 0.7 and 1.0's to
    # 1.2. 5.0 is far from all of them.
    ends = {
        "a": Decimal("1.0"),
        "b": Decimal("0.9"),
        "c": Decimal("0.1"),
        "d": Decimal("5.0"),
    }

    assert end_name_heights(ends) == {
        "d": "5.000",
        "a": "1.200",
        "b": "0.700",
        "c": "0.200",
    }


def test_the_page_registers_the_fuel_group_colours_the_charts_name():
    page = PAGE.read_text(encoding="utf-8")

    assert f'vega.scheme("{FUEL_GROUP_SCHEME}", fuelGroupScheme())' in page
    assert FUEL_GROUP_COLOUR_PAIR.findall(page) == list(FUEL_GROUP_COLOURS.values())


def test_every_chart_shows_fuel_groups_by_their_display_names_only(written):
    assert specs_encoding_raw_fuel_groups(written) == []
    assert fuel_group_names_by_spec(written) == {
        "q12_instructed_share_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q23_monthly_gap_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q23_typical_gap_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q24_flagged_share_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q25_short_instructions_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q3_gap_from_plan_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q5_under_delivery_by_outage_notice.vl.json": FUEL_GROUP_DISPLAY_NAMES,
    }


class PageTags(HTMLParser):
    """Every start tag on the page, with its attributes."""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class PageText(HTMLParser):
    """The text a reader sees: everything outside script and style."""

    def __init__(self):
        super().__init__()
        self.hidden_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden_depth -= 1

    def handle_data(self, data):
        if self.hidden_depth == 0:
            self.parts.append(data)


def page_tags():
    parser = PageTags()
    parser.feed(PAGE.read_text(encoding="utf-8"))
    return parser.tags


def page_text():
    parser = PageText()
    parser.feed(PAGE.read_text(encoding="utf-8"))
    return " ".join(parser.parts)


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
        "results/q23_typical_gap_by_fuel_group.vl.json",
        "results/q23_monthly_gap_by_fuel_group.vl.json",
        "results/q3_gap_from_plan_by_fuel_group.vl.json",
        "results/q12_instructed_share_by_fuel_group.vl.json",
        "results/q5_under_delivery_by_outage_notice.vl.json",
        "results/q24_flagged_share_by_fuel_group.vl.json",
        "results/q25_short_instructions_by_fuel_group.vl.json",
        "results/q29_error_by_time_of_day.vl.json",
        "results/q29_error_by_month.vl.json",
        "results/q28_shortfall_index_by_miss_fifth.vl.json",
        "results/q30_rank_correlations.vl.json",
        "results/q2_moved_energy_by_unit_type.vl.json",
    ]
    assert not_shipped([*local_targets(page_tags()), *specs]) == []


def test_the_page_shows_no_figure_with_four_or_more_decimals():
    assert FOUR_OR_MORE_DECIMALS.findall(page_text()) == []


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
