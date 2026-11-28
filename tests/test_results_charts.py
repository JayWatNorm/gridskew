import ast
import csv
import io
import json
import os
import re
import shutil
import statistics
import struct
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from scripts.results_charts import (
    BAND_FILL,
    CLEANER_COLOUR,
    COMPARISON_COLOUR,
    DIRTIER_COLOUR,
    FUEL_GROUP_COLOURS,
    FUEL_GROUP_SCHEME,
    INSIDE_LABEL_COLOUR,
    INSIDE_LABEL_STYLE,
    KEY_MARK_COLOUR,
    NEUTRAL_MARK_COLOUR,
    PALE_KEY_COLOUR,
    Q2_WEEK,
    Q28_REVERSE_FIFTH_ITEM,
    Q38_SCHEME,
    SPLIT_GREYS,
    SPLIT_THREE_SCHEME,
    SPLIT_TWO_SCHEME,
    UNIT_TYPE_BY_PREFIX,
    end_name_heights,
    output_date_range,
    output_month_range,
    write_charts,
)

# The colours a spec may write out; fuel-group colours and the split greys
# come from the page's schemes and are not among them.
SHARED_COLOURS = [
    BAND_FILL,
    CLEANER_COLOUR,
    COMPARISON_COLOUR,
    DIRTIER_COLOUR,
    INSIDE_LABEL_COLOUR,
    KEY_MARK_COLOUR,
    NEUTRAL_MARK_COLOUR,
    PALE_KEY_COLOUR,
]

FIXTURES = Path(__file__).parent / "fixtures" / "results"
REPOSITORY = Path(__file__).parent.parent
PAGE = REPOSITORY / "site" / "index.html"
QUESTIONS = REPOSITORY / "docs" / "research-questions.md"
FULL_MONTHS = {
    "Jan": "January",
    "Feb": "February",
    "Mar": "March",
    "Apr": "April",
    "May": "May",
    "Jun": "June",
    "Jul": "July",
    "Aug": "August",
    "Sep": "September",
    "Oct": "October",
    "Nov": "November",
    "Dec": "December",
}
PAGES_WORKFLOW = REPOSITORY / ".github" / "workflows" / "pages.yml"
PINNED_JSDELIVR_FILE = re.compile(
    r"https://cdn\.jsdelivr\.net/npm/(?P<package>[a-z][a-z-]*)@\d+\.\d+\.\d+/\S+"
)
VEGA_EMBED_CALL = re.compile(r'vegaEmbed\(\s*"[^"]+",\s*"(?P<spec>[^"]+)"')
VEGA_EMBED_TARGET = re.compile(r'vegaEmbed\(\s*"#(?P<box>[^"]+)",\s*"(?P<spec>[^"]+)"')
DISPLAYED_FUEL_GROUP = re.compile(
    r'datum\.fuel_group == \\"(?P<code>[^"\\]*)\\" \? \\"(?P<label>[^"\\]*)\\"'
)
FOUR_OR_MORE_DECIMALS = re.compile(r"\d\.\d{4,}")
WRAPPED_LINES = re.compile(r'\[(?:"(?:[^"\\]|\\.)*"(?:, )?)+\]')
FUEL_GROUP_COLOUR_PAIR = re.compile(r'\["(#[0-9a-f]{6})", "(#[0-9a-f]{6})"\]')
WRITTEN_COLOUR = re.compile(r"#[0-9a-fA-F]{6}\b|rgba\([^)]*\)")
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
ALIGNMENT_LINE = (
    "# Contains BMRS data © Elexon Limited copyright and database right 2026. "
    "Carbon intensity data: NESO Carbon Intensity API, CC BY 4.0, "
    "modified: aggregated by GridSkew. Historic generation mix: Supported by "
    "National Energy SO Open Data.\n"
)
OGL_LINE = (
    "# Contains public sector information licensed under the Open Government "
    "Licence v3.0.\n"
)
BMRS_AND_OGL_LINE = (
    "# Contains BMRS data © Elexon Limited copyright and database right 2026. "
    "Contains public sector information licensed under the Open Government "
    "Licence v3.0.\n"
)


def saved_context_label():
    """The row label the saved context files carry, as the saved demand
    count holds it below its attribution line."""
    text = (REPOSITORY / "docs" / "results" / "context_demand.csv").read_text(
        encoding="utf-8"
    )
    rows_below_attribution = text.split("\n", 1)[1]
    first_row = next(csv.DictReader(io.StringIO(rows_below_attribution)))
    return first_row["label"]


# Hand-made saved outputs for the reads the fixture folder does not hold.
# Q3's groups come in an order the chart keeps; coal is a closed unit the
# chart leaves out. Q12's order differs from the
# chart's (largest gap first); interconnectors are left out. Q2's unit types
# sum to round figures, with E beside T and C beside V.
# The saved context files' own row labels.
CONTEXT_LABEL = saved_context_label()
NOT_A_RESULT = CONTEXT_LABEL.removeprefix("context only: denominator, ")
DISTRIBUTION_LABEL = "context only: distribution, " + NOT_A_RESULT
BREAKDOWN_LABEL = "context only: breakdown, " + NOT_A_RESULT
COMPARISON_LABEL = "context only: comparison, " + NOT_A_RESULT
# Q23's chart months, for the planned-output context fixture.
Q23_FIXTURE_MONTHS = [
    "2025-09-01",
    "2025-10-01",
    "2025-11-01",
    "2025-12-01",
    "2026-01-01",
    "2026-02-01",
    "2026-03-01",
    "2026-04-01",
    "2026-05-01",
    "2026-06-01",
    "2026-07-01",
    "2026-08-01",
    "2026-09-01",
]


def planned_output_fixture_lines():
    """Wind plans 20.05% of capacity in the first month, one point more each
    month (halves round up to 20.1 and so on); nuclear 50% and up."""
    lines = [
        "fuel_group,settlement_month,units,half_hours,"
        "median_unit_planned_pct_of_capacity,p90_unit_planned_pct_of_capacity,"
        "pooled_planned_pct_of_capacity,label"
    ]
    for step, month in enumerate(Q23_FIXTURE_MONTHS):
        lines.append(
            f"wind,{month},20,1000,{20 + step}.050,40.000,25.000,{CONTEXT_LABEL}"
        )
        lines.append(
            f"nuclear,{month},4,200,{50 + step}.000,90.000,60.000,{CONTEXT_LABEL}"
        )
    return lines


Q37_FIXTURE_HEADER = (
    "section,settlement_month,wind_turn_down_band,fuel_group,item,half_hours,"
    "half_hours_with_flagged_wind_turn_down,half_hours_at_100_mw_or_more,"
    "half_hours_at_1_gw_or_more,half_hours_wind_not_measured,turned_up_mwh,"
    "flagged_turned_up_mwh,turned_down_mwh,flagged_turned_down_mwh,planned_mwh,"
    "flagged_turned_down_pct_of_planned,first_half_hour,last_half_hour,stretches,"
    "level_energy_mwh"
)


def turn_up_fixture_line(band, fuel_group, half_hours, turned_up_mwh):
    """One fuel group's row in one band of Q37's saved output; the three
    energies the chart does not draw are unlike any it does."""
    return (
        f"3,,{band},{fuel_group},energy turned up and down in the band's "
        f"half-hours,{half_hours},,,,,{turned_up_mwh},1.000,2.000,3.000,,,,,,"
    )


Q38_FIXTURE_HEADER = (
    "section,settlement_month,station,national_grid_bm_unit,notice_class,"
    "output_state,item,units,unit_half_hours,capacity_mwh,output_mwh,"
    "output_pct_of_capacity,gap_mwh,share_of_gap_pct,gap_below_highest_mwh,"
    "share_of_gap_below_highest_pct,above_capacity_mwh,drawn_mwh,"
    "registry_capacity_mw,highest_half_hour_mw,notice_normal_capacity_low_mw,"
    "notice_normal_capacity_high_mw,notice_publications,first_half_hour,"
    "last_half_hour"
)


def nuclear_month_fixture_line(month, capacity_mwh, output_mwh, gap_mwh):
    """One month's totals in Q38's saved output; its saved percentage is
    unlike the one the chart works out, so the chart cannot copy it."""
    return (
        f"1,{month},,,,,output against registered capacity,10,1440,{capacity_mwh},"
        f"{output_mwh},99.99,{gap_mwh},,5.0,,0.0,0.0,,,,,,"
        f"{month} 00:00:00+00:00,{month} 23:30:00+00:00"
    )


def nuclear_gap_fixture_line(month, notice_class, output_state, gap_mwh):
    """One notice class and generating state's gap in one month of Q38's
    saved output."""
    return (
        f"3,{month},,,{notice_class},{output_state},gap below registered capacity,"
        f"1,10,,,,{gap_mwh},1.00,2.0,3.00" + "," * 9
    )


NEW_SAVED_OUTPUTS = {
    # Q38, unlike the real output: three months. September's output is
    # 45.625% of its capacity and its gap with no notice 4.375%, halves
    # rounded up; its unplanned gap is split between generating and not
    # generating, and October's planned gap too, so the chart must add the
    # two.
    "q38_nuclear_output_and_outage_notices.csv": [
        Q38_FIXTURE_HEADER,
        nuclear_month_fixture_line("2025-09-01", "1000000.0", "456250.0", "543750.0"),
        nuclear_month_fixture_line("2025-10-01", "2000000.0", "1000000.0", "1000000.0"),
        nuclear_month_fixture_line("2025-11-01", "1000000.0", "800000.0", "200000.0"),
        "2,,,,,,output against registered capacity,10,4320,4000000.0,2256250.0,"
        "56.41,1743750.0,,1.0,,0.0,0.0,,,,,,2025-09-01 00:00:00+00:00,"
        "2025-11-01 23:30:00+00:00",
        nuclear_gap_fixture_line("2025-09-01", "planned", "not generating", "300000.0"),
        nuclear_gap_fixture_line("2025-09-01", "unplanned", "generating", "50000.0"),
        nuclear_gap_fixture_line(
            "2025-09-01", "unplanned", "not generating", "150000.0"
        ),
        nuclear_gap_fixture_line("2025-09-01", "none", "generating", "43750.0"),
        nuclear_gap_fixture_line("2025-10-01", "none", "generating", "100000.0"),
        nuclear_gap_fixture_line("2025-10-01", "planned", "generating", "200000.0"),
        nuclear_gap_fixture_line("2025-10-01", "planned", "not generating", "300000.0"),
        nuclear_gap_fixture_line(
            "2025-10-01", "unplanned", "not generating", "400000.0"
        ),
        nuclear_gap_fixture_line("2025-11-01", "none", "generating", "40000.0"),
        nuclear_gap_fixture_line("2025-11-01", "planned", "generating", "100000.0"),
        nuclear_gap_fixture_line("2025-11-01", "unplanned", "generating", "60000.0"),
        # Not read by the chart: the all-months split.
        "4,,,,planned,generating,gap below registered capacity,1,10,,,,1.0,,,,,,,,,,,,",
    ],
    # DESNZ's monthly figures, unlike the real ones: two of the output's
    # three months, the second provisional. 0.45625 TWh is 14.0625% above
    # 0.4, and 1.45625 over the two is 21.354...% above 1.2.
    "desnz_nuclear_supplied_by_month.csv": [
        "# Source line, as the saved file gives it.",
        "month,nuclear_twh,provisional",
        "2025-09,0.4000,no",
        "2025-10,0.8000,yes",
    ],
    # Q37, unlike the real output: two months, so its range is a span the
    # chart works out, and six bands. A half-hour's MWh times two is its
    # average MW: with no wind turned down, gas's 250,000 MWh over 1,000
    # half-hours is 500 MW and the rest's 18,775 MWh is 37.55 MW, a half
    # rounded up; the largest band's 1,250 half-hours show a thousands comma.
    "q37_wind_turn_down_and_turn_up.csv": [
        Q37_FIXTURE_HEADER,
        "1,2025-08-01,,wind,wind turned down,480,200,150,90,0,,,9000.000,8000.000,"
        "80000.000,10.00,2025-08-22 00:00:00+00:00,2025-08-31 22:30:00+00:00,,",
        "1,2026-09-01,,wind,wind turned down,1440,900,800,500,0,,,60000.000,"
        "50000.000,400000.000,12.50,2026-08-31 23:00:00+00:00,"
        "2026-09-30 22:30:00+00:00,,",
        turn_up_fixture_line("0 none", "gas_turbine", 1000, "250000.000"),
        turn_up_fixture_line("0 none", "hydro", 1000, "12500.000"),
        turn_up_fixture_line("0 none", "other", 1000, "6275.000"),
        turn_up_fixture_line("1 under 100 MW", "gas_turbine", 200, "60000.000"),
        turn_up_fixture_line("1 under 100 MW", "pumped_storage", 200, "4000.000"),
        turn_up_fixture_line(
            "2 100 MW to under 500 MW", "gas_turbine", 300, "105000.000"
        ),
        turn_up_fixture_line("2 100 MW to under 500 MW", "bioenergy", 300, "7500.000"),
        turn_up_fixture_line(
            "3 500 MW to under 1 GW", "gas_turbine", 250, "100000.000"
        ),
        turn_up_fixture_line("3 500 MW to under 1 GW", "other", 250, "7500.000"),
        turn_up_fixture_line("4 1 GW to under 2 GW", "gas_turbine", 400, "180000.000"),
        turn_up_fixture_line("4 1 GW to under 2 GW", "wind", 400, "14000.000"),
        turn_up_fixture_line("5 2 GW or more", "gas_turbine", 1250, "1250000.000"),
        turn_up_fixture_line(
            "5 2 GW or more", "no published fuel type", 1250, "62500.000"
        ),
        turn_up_fixture_line("5 2 GW or more", "hydro", 1250, "31250.000"),
        # Not a band of the chart: its half-hours are in no bar.
        turn_up_fixture_line(
            "6 wind turn-down not measured", "gas_turbine", 7, "7700.000"
        ),
    ],
    # Context counts, each unlike the real ones so a typed figure fails: 124.5
    # g/kWh and 21,350 MW are halves rounded up; wind's mean plan 18.25 MWh a
    # half-hour and its median 7.25; its 5,600 MWh planned makes Q5's 700 MWh
    # of under-delivery 12.5%, so 13%; gas's 12.35 TWh shows as 12.4.
    "context_carbon_intensity.csv": [
        "section,item,half_hours_in_window,half_hours_counted,"
        "mean_actual_g_per_kwh,median_actual_g_per_kwh,label",
        f"1,all half-hours,500,500,124.5,118.0,{CONTEXT_LABEL}",
    ],
    "context_demand.csv": [
        "section,item,half_hours_in_window,half_hours_with_demand,mean_demand_mw,"
        "mean_demand_mwh_per_half_hour,demand_twh,label",
        f'1,all half-hours,1920,1920,23064,11532,22.15,"{CONTEXT_LABEL}"',
        "2,settlement month 2025-08 (partial: 22 to 31),480,480,21350,10675,5.12,"
        f'"{CONTEXT_LABEL}"',
        f'2,settlement month 2025-09,1440,1440,23635,11817,17.02,"{CONTEXT_LABEL}"',
    ],
    "context_plan_per_unit_half_hour.csv": [
        "fuel_group,q3_unit_periods,q3_abs_pn_mwh,q3_mean_plan_mwh,"
        "q3_median_plan_mwh,q3_unit_periods_nonzero_plan,"
        "q3_median_nonzero_plan_mwh,q22_unit_periods,q22_mean_plan_mwh,"
        "q22_median_plan_mwh,q22_median_nonzero_plan_mwh,label",
        f"wind,400,5600.0,18.250,7.250,300,11.000,40,8.000,2.500,4.000,{CONTEXT_LABEL}",
        f"gas_turbine,300,12350000.0,40.000,0.000,60,200.000,30,49.000,0.000,198.000,"
        f"{CONTEXT_LABEL}",
        f"nuclear,150,8100.0,135.000,0.000,70,299.000,15,143.000,193.000,295.000,"
        f"{CONTEXT_LABEL}",
    ],
    "context_plan_pct_capacity.csv": planned_output_fixture_lines(),
    "context_q6_start_alignment.csv": [
        "section,item,notices,on_half_hour,on_whole_minute,label",
        f'1,coverage: 5 in the population,400,,,"{CONTEXT_LABEL}"',
        f'2,class (first publication): Unplanned (as read),300,180,290,"{CONTEXT_LABEL}"',
    ],
    # Counts-only context with its read time as a second comment line, which
    # the copy keeps.
    "context_q23_runs_by_month.csv": [
        "# Counts as held at a read-only query on 2 March 2027, 09:15 UTC.",
        "month,run_held,run_at_read,unit_periods,first_date,last_date",
        "2025-09,R3,R3,300,2025-09-01,2025-09-30",
        "2025-10,SF,II,40,2025-10-01,2025-10-03",
    ],
    "context_q28_runs_held.csv": [
        "# Counts as held at a read-only query on 2 March 2027, 09:16 UTC.",
        "month,run_held,cohort_unit_periods,first_date,last_date",
        "2025-09,(no metered value),7,2025-09-01,2025-09-30",
        "2025-09,R2,250,2025-09-01,2025-09-30",
    ],
    "context_q30_outturn_lateness.csv": [
        "# Counts as held at a read-only query on 2 March 2027, 09:17 UTC.",
        "section,breakdown,item,half_hours,zero_outturn,first_start_utc,last_start_utc",
        "1,lateness,over 2 h after the half-hour ends,5,2,"
        "2025-09-03 10:00:00+00:00,2025-09-03 12:00:00+00:00",
    ],
    "context_q28_wind_level.csv": [
        "# Counts as held at a read-only query on 2 March 2027, 09:18 UTC.",
        "measure,wind_fifth,measure_fifth,half_hours,label",
        f'under-delivery,2,5,40,"{BREAKDOWN_LABEL}"',
        f'signed carbon miss,4,1,35,"{BREAKDOWN_LABEL}"',
    ],
    "context_q30_wind_miss_sign.csv": [
        "# Counts as held at read-only queries on 2 March 2027, 09:19 to 09:20 UTC.",
        "month,half_hours,delivered_below,delivered_equal,delivered_above,"
        "delivered_below_by_over_1gw,delivered_above_by_over_1gw,add_back_below,"
        "add_back_above,add_back_below_by_over_1gw,add_back_above_by_over_1gw,"
        "no_wind_instruction,no_instruction_delivered_below,"
        "no_instruction_delivered_above,label",
        f'2025-09,100,70,1,29,30,2,45,54,9,11,20,13,6,"{BREAKDOWN_LABEL}"',
    ],
    "context_carbon_label_alignment.csv": [
        "# Measured alignment, not a documented convention. Computed 2 March 2027.",
        "section,comparison,group,offset_half_hours,common_rows,exactly_equal,"
        "within_5,mean_abs_diff,step_correlation,unit",
        "2,API actual against NESO historic generation mix CARBON_INTENSITY,all,1,"
        "500,30,250,12.50,0.7000,g/kWh",
    ],
    # Saved with a second comment line, the read's time and basis, which the
    # copy keeps below the attribution.
    "context_q6_lag_quartiles.csv": [
        "# Lag quartiles. Read 1 January 2026. Minutes.",
        "section,outage_class,notices,p25_lag_minutes,median_lag_minutes,"
        "p75_lag_minutes,p90_lag_minutes,label",
        f'1,Planned,300,-500.0,-100.0,-50.0,-2.0,"{DISTRIBUTION_LABEL}"',
    ],
    "context_q25_battery_named_units.csv": [
        "# Read 1 January 2026 on the read's data. Counts only.",
        "section,item,unit_class,acceptances,units,label",
        f'1,fuel type OTHER,battery by name or ID,900,9,"{CONTEXT_LABEL}"',
        f'2,fuel type OTHER,all classes,1000,12,"{CONTEXT_LABEL}"',
    ],
    # Read on the data held at each read, each with its basis as a second
    # comment line, which the copy keeps.
    "context_q5_drawing_power.csv": [
        "# Read 1 January 2026 on the data held at Q5's read.",
        "section,fuel_group,unit_periods,under_delivery_mwh,"
        "drawing_power_expected_at_or_below_zero_mwh,"
        "drawing_power_expected_at_or_below_zero_unit_periods,label",
        f'1,wind,400,700.0,35.0,12,"{BREAKDOWN_LABEL}"',
        f'2,,700,1000.0,90.0,40,"{BREAKDOWN_LABEL}"',
    ],
    "context_q6_publication_timing.csv": [
        "# Read 1 January 2026 on the REMIT rows held at Q6's read.",
        "section,outage_class,notices,before_start,over_1h_before_start,at_start,"
        "after_start,up_to_30min_after_start,over_1h_after_start,"
        "over_1d_after_start,label",
        f'1,Unplanned,300,140,70,2,158,120,25,4,"{DISTRIBUTION_LABEL}"',
    ],
    "context_cohort_unit_types.csv": [
        "# Read 1 January 2026 on the data held at each read.",
        "section,question,fuel_group,unit_type,unit_periods,units,label",
        f'1,Q3,,S,30,2,"{BREAKDOWN_LABEL}"',
        f'1,Q3,,all,940,40,"{BREAKDOWN_LABEL}"',
    ],
    "context_cohort_change.csv": [
        "# Read 1 January 2026 against the register of the read.",
        "section,item,detail,fuel_group,units,unit_periods,label",
        f'1,gained a fuel type,OCGT,gas_turbine,3,,"{BREAKDOWN_LABEL}"',
        f'2,as held at the read,,,40,900,"{BREAKDOWN_LABEL}"',
    ],
    "context_ii_zero_readings.csv": [
        "# Read 1 January 2026 on the data held at the reads.",
        "section,item,detail,fuel_group,unit_periods,zero_readings,units,label",
        "1,2025-09-01,first metered figure on the one-week run,gas_turbine,"
        f'500,20,2,"{BREAKDOWN_LABEL}"',
    ],
    "context_q23_one_week_and_later_runs.csv": [
        "# Read 1 January 2026 on Q23's population held at its read.",
        "section,fuel_group,settlement_month,unit_months,half_hours,"
        "median_absolute_pct_one_week,p90_absolute_pct_one_week,"
        "median_absolute_pct_latest,p90_absolute_pct_latest,"
        "median_absolute_pct_at_read,p90_absolute_pct_at_read,label",
        f'1,wind,2025-09-01,20,9000,8.100,12.000,7.900,11.000,8.000,11.500,"{BREAKDOWN_LABEL}"',
    ],
    "context_q25_cadl_check.csv": [
        "# Copied 1 January 2026 from the recorded checks.",
        "section,item,first_settlement_date,last_settlement_date,stack_responses,"
        "new_api_calls,acceptances_compared,agree,predicted_short_not_flagged,"
        "flagged_not_predicted,flagged_by_both,flagged_by_neither,label",
        "1,check,2025-09-01,2025-09-07,20,20,500,490,10,0,40,450,check",
    ],
    "q3_metered_vs_notified.csv": [
        "fuel_group,unit_periods,units,first_date,last_date,median_deviation_mwh,"
        "q1_deviation_mwh,q3_deviation_mwh,abs_metered_mwh,abs_pn_mwh",
        "wind,400,20,2025-08-22,2026-09-26,0.300,-1.000,2.900,9000.0,9500.0",
        "coal,90,3,2025-08-22,2026-09-26,0.000,0.000,0.000,1.0,0.0",
        "nuclear,150,8,2025-08-22,2026-09-26,0.250,0.000,2.600,7897.5,8100.0",
        "gas_turbine,300,15,2025-08-22,2026-09-26,0.170,0.000,1.100,6906.9,6900.0",
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
    # The three gas units: 23.4 GWh revised, 90% of the grid-connected 26 GWh,
    # 23.4% of the 100 GWh revised and 39% of their own 60 GWh.
    "q2_gas_units_revisions.csv": [
        "unit,moved_mwh,net_change_mwh,sf_abs_mwh",
        "gas unit A,15000.000,14000.000,10000.000",
        "gas unit B,5000.000,-4500.000,30000.000",
        "gas unit C,3400.000,-3000.000,20000.000",
    ],
    # The reverse cut: 225 MWh is 0.45 GW, a half rounded up; fifth 2's mean
    # rounds to zero from below, fifth 3's (0.05) is a half rounded up.
    "q28_reverse_exploratory.csv": [
        "section,line,item,fifth,half_hours,days,first_date,last_date,"
        "under_delivery_from_mwh,under_delivery_to_mwh,value,interval_low,"
        "interval_high,usable_draws,share_dirtier,share_equal,mean_abs_error,result",
        '1,1,"read 1 November 2026, every check matched Q28",,500,100,'
        "2025-09-01,2026-07-31,,,,,,,,,,matches Q28",
        f'2,1,"{Q28_REVERSE_FIFTH_ITEM}",1,100,,,,225.000000,500.000000,'
        "-3.000000,-4.000000,-2.000000,1000,0.300000,0.040000,9.000000,"
        "interval available",
        f'2,1,"{Q28_REVERSE_FIFTH_ITEM}",2,100,,,,500.100000,650.000000,'
        "-0.040000,-0.500000,0.400000,1000,0.400000,0.040000,9.000000,"
        "interval available",
        f'2,1,"{Q28_REVERSE_FIFTH_ITEM}",3,100,,,,650.100000,800.000000,'
        "0.050000,-0.300000,0.400000,1000,0.500000,0.040000,9.000000,"
        "interval available",
        f'2,1,"{Q28_REVERSE_FIFTH_ITEM}",4,100,,,,800.100000,1000.000000,'
        "1.240000,0.500000,2.000000,1000,0.500000,0.040000,9.000000,"
        "interval available",
        f'2,1,"{Q28_REVERSE_FIFTH_ITEM}",5,100,,,,1000.100000,3000.000000,'
        "2.000000,1.000000,3.000000,1000,0.600000,0.040000,9.000000,"
        "interval available",
        "2,2,all population half-hours,,500,,,,225.000000,3000.000000,0.050000,"
        ",,,0.460000,0.040000,9.000000,",
        '3,1,"difference, top fifth minus bottom fifth",,,,,,,,5.000000,'
        "4.000000,6.000000,1000,,,,interval available",
        '3,2,"least-squares slope, gCO2/kWh per GW",,500,,,,,,3.000000,'
        "2.000000,4.000000,1000,,,,interval available",
    ],
    # Wind under-delivery per GW, unlike the real output. Two ways of
    # allowing for planned output (section 4 line 4, section 5 line 13) for
    # the actual, the forecast and the miss, and three more ways for the
    # actual (section 3 line 4, section 5 lines 12 and 11); 6.25 and 5.05 are
    # halves rounded up, and -0.95 is a half rounded away from zero. A gas
    # row and a net-shortfall row stand on the same lines, and the forecast
    # and the miss have twenty-band rows: the chart must pass over all four.
    # The scale row (section 6) is not drawn.
    "q28_wind_under_delivery_cut.csv": [
        "section,line,item,measure,outcome,fifth,half_hours,days,first_date,"
        "last_date,range_from,range_to,value,interval_low,interval_high,"
        "usable_draws,share_dirtier,share_equal,mean_abs_error,result",
        "1,2,population,,,,500,100,2025-09-01,2026-07-31,,,,,,,,,,",
        "4,4,same cell,wind_positive,actual,,500,,,,,,6.250000,2.000000,9.000000,"
        "1000,,,,",
        "4,4,same cell,wind_positive,forecast,,500,,,,,,5.050000,1.000000,"
        "8.000000,1000,,,,",
        "4,4,same cell,wind_positive,signed_miss,,500,,,,,,1.200000,0.500000,"
        "2.000000,1000,,,,",
        "4,4,same cell,gas_positive,actual,,500,,,,,,0.500000,-1.000000,2.000000,"
        "1000,,,,",
        "4,4,same cell,wind_net,actual,,500,,,,,,3.000000,1.000000,5.000000,1000,,,,",
        "5,13,twentieths,wind_positive,actual,,500,,,,,,4.000000,-0.040000,"
        "8.500000,1000,,,,",
        "5,13,twentieths,wind_positive,forecast,,500,,,,,,-0.040000,-3.000000,"
        "3.000000,1000,,,,",
        "5,13,twentieths,wind_positive,signed_miss,,500,,,,,,1.450000,0.600000,"
        "2.400000,1000,,,,",
        "3,4,second variable,wind_positive,actual,,500,,,,,,2.500000,-1.000000,"
        "6.000000,1000,,,,",
        "5,12,fifths less means,wind_positive,actual,,500,,,,,,1.240000,-2.000000,"
        "4.000000,1000,,,,",
        "5,11,fifths,wind_positive,actual,,500,,,,,,-0.950000,-6.500000,5.000000,"
        "1000,,,,",
        "6,2,yardstick,,,,500,,,,,,12.345000,,,,,,,",
    ],
    # The reverse cut within time of day and month: copied as saved, with
    # its attribution; the page reads the committed output.
    "q28_reverse_strata_exploratory.csv": [
        "section,line,item,fifth,stratum,half_hours,days,first_date,last_date,"
        "under_delivery_from_mwh,under_delivery_to_mwh,value,interval_low,"
        "interval_high,usable_draws,mean_signed_error,cells,result",
        '1,2,"population: Q28\'s matched days and half-hours",,,500,100,'
        "2025-09-01,2026-07-31,,,,,,,,,matches Q28",
        '2,1,"UTC block of the half-hour\'s start: slope",,00:00-05:59,125,100,,,'
        "225.000000,3000.000000,1.500000,0.500000,2.500000,1000,0.100000,,"
        "interval available",
        '4,1,"within-cell slope",,,500,100,2025-09-01,2026-07-31,,,1.750000,'
        "1.250000,2.250000,1000,0.050000,8,interval available",
    ],
    # Q1 and Q10, each unlike the real outputs. Q1's splits: 2,000 half-hours
    # either way; a whole median, a negative half, a zero and a share that is
    # a half rounded up (61.25%); two bands hold no half-hours and one holds
    # a single half-hour, and the bands come in the saved output's
    # alphabetical order, not the chart's.
    "q1_forecast_drift_verdict.csv": [
        "complete_half_hours,first_half_hour,last_half_hour,half_hours_needed,"
        "population_is_reached,reported_verdict,median_drift,positive_share,"
        "as_published_half_hours,as_published_median_drift,"
        "as_published_positive_share,as_published_verdict,"
        "weekday_reweighted_median_drift,weekday_reweighted_positive_share,"
        "weekday_reweighted_verdict,part_not_seen_half_hours,"
        "part_not_seen_median_drift,part_not_seen_positive_share,"
        "part_not_seen_verdict",
        "2000,2026-08-20 00:00:00+00:00,2026-09-30 23:30:00+00:00,1900,True,"
        "no directional drift,1.0,0.520000,2400,1.0,0.510000,"
        "no directional drift,1.0,0.530000,no directional drift,1500,1.0,"
        "0.540000,no directional drift",
    ],
    "q1_forecast_drift_splits.csv": [
        "section,split,cell,half_hours,median_drift,positive_share",
        "1,time of day (UTC start),00:00-05:59,500,12.0,0.612500",
        "1,time of day (UTC start),06:00-11:59,480,-2.5,0.400000",
        "1,time of day (UTC start),12:00-17:59,520,0.0,0.500000",
        "1,time of day (UTC start),18:00-23:59,500,7.0,0.700000",
        "2,first-forecast band,high,90,30.5,0.900000",
        "2,first-forecast band,low,1300,3.0,0.550000",
        "2,first-forecast band,moderate,609,9.0,0.640000",
        "2,first-forecast band,unclassified,0,,",
        "2,first-forecast band,very high,1,41.0,1.000000",
        "2,first-forecast band,very low,0,,",
    ],
    "q1_forecast_drift_beside.csv": [
        "section,position,item,n,value,interval_low,interval_high,"
        "draws_with_value,detail",
        '2,1,"mean drift, gCO2/kWh",2000,1.250000,0.500000,2.000000,1000,',
    ],
    "context_q1_population.csv": [
        "section,breakdown,item,half_hours,first_half_hour,last_half_hour",
        "1,population,complete half-hours (1900 needed),2000,"
        "2026-08-20 00:00:00+00:00,2026-09-30 23:30:00+00:00",
    ],
    "q10_forecast_accuracy_by_lead_time.csv": [
        "section,item,horizon,half_hours,mean_absolute_error,bias",
        "2,accuracy on the common sample,about 24 hours,1950,20.500,4.250",
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
    # A first month, so Q24's range is a span the chart works out.
    "q24_system_action_share.csv": [
        "1,wind,2025-08-01,stretches with a volume,100,300,500.0,450.0,90.0,120.0,80.0",
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
    "Suppliers' customer accounts,Suppliers' customer accounts,60000.000,60.000000\n"
    "Transmission-connected and embedded units,"
    "Transmission-connected|and embedded units,26000.000,26.000000\n"
    "Other unit types,Other unit types,14000.000,14.000000\n",
    "q3_gap_from_plan_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,unit_periods,q1_deviation_mwh,median_deviation_mwh,"
    "q3_deviation_mwh,mean_plan_mwh\n"
    "wind,400,-1.000,0.300,2.900,18.250\n"
    "nuclear,150,0.000,0.250,2.600,135.000\n"
    "gas_turbine,300,0.000,0.170,1.100,40.000\n",
    # Metered against planned energy, one-way groups only (coal, with no
    # plan, is left out). Wind: 9,000 against 9,500 MWh, 5.2632% below.
    # Nuclear: 7,897.5 against 8,100, exactly 2.5% below, so 3% (halves
    # round away from zero). Gas: 6,906.9 against 6,900, 0.1% above, which
    # rounds to nothing and keeps its direction as "under 1%".
    "q3_energy_against_plan_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,unit_periods,metered_mwh,planned_mwh,against_plan_pct,label\n"
    "wind,400,9000.0,9500.0,-5.2632,5% below\n"
    "nuclear,150,7897.5,8100.0,-2.5000,3% below\n"
    "gas_turbine,300,6906.9,6900.0,0.1000,under 1% above\n",
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
    "end_name_pct,planned_label\n"
    "wind,2025-09-01,Sep 2025,6.300,6.3,20,no,no,,20.1\n"
    "wind,2025-10-01,Oct 2025,7.000,7.0,29,no,no,,21.1\n"
    "wind,2025-11-01,Nov 2025,7.200,7.2,30,no,no,,22.1\n"
    "wind,2025-12-01,Dec 2025,7.900,7.9,30,no,no,,23.1\n"
    "wind,2026-01-01,Jan 2026,7.400,7.4,30,no,no,,24.1\n"
    "wind,2026-02-01,Feb 2026,7.600,7.6,30,no,no,,25.1\n"
    "wind,2026-03-01,Mar 2026,7.800,7.8,30,no,no,,26.1\n"
    "wind,2026-04-01,Apr 2026,6.900,6.9,30,no,no,,27.1\n"
    "wind,2026-05-01,May 2026,6.800,6.8,30,no,no,,28.1\n"
    "wind,2026-06-01,Jun 2026,7.300,7.3,30,no,no,,29.1\n"
    "wind,2026-07-01,Jul 2026,6.500,6.5,30,no,no,,30.1\n"
    "wind,2026-08-01,Aug 2026,7.500,7.5,30,no,yes,,31.1\n"
    "wind,2026-09-01,Sep 2026,8.700,8.7,31,yes,yes,8.700,32.1\n"
    "nuclear,2025-09-01,Sep 2025,0.900,0.9,4,no,no,,50.0\n"
    "nuclear,2025-10-01,Oct 2025,1.450,1.5,4,no,no,,51.0\n"
    "nuclear,2025-11-01,Nov 2025,1.100,1.1,4,no,no,,52.0\n"
    "nuclear,2025-12-01,Dec 2025,0.800,0.8,4,no,no,,53.0\n"
    "nuclear,2026-01-01,Jan 2026,1.800,1.8,4,no,no,,54.0\n"
    "nuclear,2026-02-01,Feb 2026,0.300,0.3,4,no,no,,55.0\n"
    "nuclear,2026-03-01,Mar 2026,0.500,0.5,4,no,no,,56.0\n"
    "nuclear,2026-04-01,Apr 2026,1.150,1.2,4,no,no,,57.0\n"
    "nuclear,2026-05-01,May 2026,1.400,1.4,4,no,no,,58.0\n"
    "nuclear,2026-06-01,Jun 2026,0.950,1.0,4,no,no,,59.0\n"
    "nuclear,2026-07-01,Jul 2026,1.300,1.3,4,no,no,,60.0\n"
    "nuclear,2026-08-01,Aug 2026,1.200,1.2,4,no,yes,,61.0\n"
    "nuclear,2026-09-01,Sep 2026,2.300,2.3,4,yes,yes,2.300,62.0\n",
    "q24_flagged_share_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,acceptance_volume_mwh,flagged_pct,not_flagged_pct\n"
    "wind,9000.0,96.0,4.0\n"
    "gas_turbine,16000.0,35.0,65.0\n",
    # Worked above NEW_SAVED_OUTPUTS: each band's MWh times two over its
    # half-hours, gas and the rest.
    "q37_turn_up_by_wind_turn_down_band.csv": BMRS_LINE
    + "wind_turn_down_band,half_hours,gas_mw,other_mw,label\n"
    '0 none,1000,500.0,37.6,"None as a system action|1,000 half-hours"\n'
    "1 under 100 MW,200,600.0,40.0,Under 100 MW|200 half-hours\n"
    "2 100 MW to under 500 MW,300,700.0,50.0,"
    "100 MW to under 500 MW|300 half-hours\n"
    "3 500 MW to under 1 GW,250,800.0,60.0,500 MW to under 1 GW|250 half-hours\n"
    "4 1 GW to under 2 GW,400,900.0,70.0,1 GW to under 2 GW|400 half-hours\n"
    '5 2 GW or more,1250,2000.0,150.0,"2 GW or more|1,250 half-hours"\n',
    # Worked above NEW_SAVED_OUTPUTS: each month's output and gap per notice
    # class over its capacity, generating and not generating added.
    "q38_output_and_gap_by_month.csv": BMRS_LINE
    + "settlement_month,label,capacity_mwh,output_pct,planned_gap_pct,"
    "unplanned_gap_pct,no_notice_gap_pct\n"
    "2025-09-01,Sep 2025,1000000.0,45.63,30.00,20.00,4.38\n"
    "2025-10-01,Oct 2025,2000000.0,50.00,25.00,20.00,5.00\n"
    "2025-11-01,Nov 2025,1000000.0,80.00,10.00,6.00,4.00\n",
    # The months both give; November is not in DESNZ's fixture.
    "context_q38_desnz_monthly_nuclear.csv": BMRS_AND_OGL_LINE
    + "section,month,output_twh,desnz_twh,output_above_desnz_pct,"
    "desnz_provisional,label\n"
    f'1,2025-09,0.45625,0.4000,14.06,no,"{COMPARISON_LABEL}"\n'
    f'1,2025-10,1.0,0.8000,25.00,yes,"{COMPARISON_LABEL}"\n'
    f'2,,1.45625,1.2000,21.35,,"{COMPARISON_LABEL}"\n',
    "q25_short_instructions_by_fuel_group.csv": BMRS_LINE
    + "fuel_group,instructions,short_joined_pct,short_on_its_own_pct,"
    "short_joined_as_first_read_pct\n"
    "other,4000,21.100000,77.250000,21.250000\n"
    "gas_turbine,1200,0.166667,2.500000,0.333333\n",
    # The actual on its five ways, then the forecast and the miss on the
    # first.
    "q28_wind_shortfall_per_gw.csv": BMRS_AND_CARBON_LINE
    + "outcome,way,label,value,value_label,interval_low,interval_high\n"
    'actual,Month and time held as well,"Carbon actual|month, time, plan held",'
    "6.250000,+6.3,2.000000,9.000000\n"
    "actual,Another way for the plan,Carbon actual|twenty bands of plan,"
    "4.000000,+4.0,-0.040000,8.500000\n"
    "actual,Another way for the plan,Carbon actual|plan as second variable,"
    "2.500000,+2.5,-1.000000,6.000000\n"
    'actual,Another way for the plan,"Carbon actual|five bands, own means",'
    "1.240000,+1.2,-2.000000,4.000000\n"
    "actual,Another way for the plan,Carbon actual|five bands of plan,"
    "-0.950000,−1.0,-6.500000,5.000000\n"
    'forecast,Month and time held as well,"Stored forecast|month, time, plan held",'
    "5.050000,+5.1,1.000000,8.000000\n"
    'signed_miss,Month and time held as well,"Miss|month, time, plan held",'
    "1.200000,+1.2,0.500000,2.000000\n",
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
    # GW is MWh per half-hour x 2 / 1,000: 225 MWh is 0.45, so 0.5.
    "q28_reverse_carbon_miss_by_under_delivery_fifth.csv": BMRS_AND_CARBON_LINE
    + "fifth,label,under_delivery,half_hours,mean_error,mean_label,interval_low,"
    "interval_high\n"
    '1,Least under-delivery|0.5–1.0 GW,"Least under-delivery, 0.5–1.0 GW",100,'
    "-3.000000,−3.0,-4.000000,-2.000000\n"
    "2,1.0–1.3 GW,1.0–1.3 GW,100,-0.040000,0.0,-0.500000,0.400000\n"
    "3,1.3–1.6 GW,1.3–1.6 GW,100,0.050000,+0.1,-0.300000,0.400000\n"
    "4,1.6–2.0 GW,1.6–2.0 GW,100,1.240000,+1.2,0.500000,2.000000\n"
    '5,Most under-delivery|2.0–6.0 GW,"Most under-delivery, 2.0–6.0 GW",100,'
    "2.000000,+2.0,1.000000,3.000000\n",
    # 21,350 MW is 21.35 GW, a half rounded up.
    "q29_context_demand_by_month.csv": BMRS_LINE
    + "month,item,half_hours,mean_demand_gw,part_month\n"
    "2025-08,settlement month 2025-08 (partial: 22 to 31),480,21.4,yes\n"
    "2025-09,settlement month 2025-09,1440,23.6,no\n",
    # Cells in the chart's order, the two empty bands left out; 61.25% shows
    # as 61.3, and the minus is a true minus sign.
    "q1_median_drift_by_time_and_band.csv": CARBON_LINE
    + "split,cell,label,half_hours,median_drift,median_label,"
    "positive_share_pct\n"
    "time of day (UTC start),00:00-05:59,00:00–05:59|500 half-hours,500,12.0,"
    "+12,61.3\n"
    "time of day (UTC start),06:00-11:59,06:00–11:59|480 half-hours,480,-2.5,"
    "−2.5,40.0\n"
    "time of day (UTC start),12:00-17:59,12:00–17:59|520 half-hours,520,0.0,"
    "0,50.0\n"
    "time of day (UTC start),18:00-23:59,18:00–23:59|500 half-hours,500,7.0,"
    "+7,70.0\n"
    'first-forecast band,low,"Low band|1,300 half-hours",1300,3.0,+3,55.0\n'
    "first-forecast band,moderate,Moderate band|609 half-hours,609,9.0,+9,"
    "64.0\n"
    "first-forecast band,high,High band|90 half-hours,90,30.5,+30.5,90.0\n"
    "first-forecast band,very high,Very high band|1 half-hour,1,41.0,+41,"
    "100.0\n",
    "q30_rank_correlations.csv": BMRS_AND_CARBON_LINE + "label,measure,rho,low,high\n"
    "Size of misses|turned-down wind added back,"
    '"Size of the two misses, with turned-down wind added back",'
    "0.010000,-0.020000,0.040000\n"
    "Direction|turned-down wind added back,"
    '"Direction of the two misses, with turned-down wind added back",'
    "0.060000,0.030000,0.090000\n"
    "Size of misses|delivered wind,"
    '"Size of the two misses, delivered wind only",'
    "-0.040000,-0.070000,-0.010000\n"
    "Direction|delivered wind,"
    '"Direction of the two misses, delivered wind only",'
    "-0.150000,-0.200000,-0.120000\n",
}

WRITTEN_FILES = [
    "context_carbon_intensity.csv",
    "context_carbon_label_alignment.csv",
    "context_cohort_change.csv",
    "context_cohort_unit_types.csv",
    "context_demand.csv",
    "context_ii_zero_readings.csv",
    "context_plan_pct_capacity.csv",
    "context_plan_per_unit_half_hour.csv",
    "context_q1_population.csv",
    "context_q23_one_week_and_later_runs.csv",
    "context_q23_runs_by_month.csv",
    "context_q25_battery_named_units.csv",
    "context_q25_cadl_check.csv",
    "context_q28_runs_held.csv",
    "context_q28_wind_level.csv",
    "context_q30_outturn_lateness.csv",
    "context_q30_wind_miss_sign.csv",
    "context_q38_desnz_monthly_nuclear.csv",
    "context_q5_drawing_power.csv",
    "context_q6_lag_quartiles.csv",
    "context_q6_publication_timing.csv",
    "context_q6_start_alignment.csv",
    "desnz_nuclear_supplied_by_month.csv",
    "q10_forecast_accuracy_by_lead_time.csv",
    "q12_instructed_share_by_fuel_group.csv",
    "q12_instructed_share_by_fuel_group.png",
    "q12_instructed_share_by_fuel_group.vl.json",
    "q12_instructed_vs_residual.csv",
    "q1_forecast_drift_beside.csv",
    "q1_forecast_drift_splits.csv",
    "q1_forecast_drift_verdict.csv",
    "q1_median_drift_by_time_and_band.csv",
    "q1_median_drift_by_time_and_band.png",
    "q1_median_drift_by_time_and_band.vl.json",
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
    "q28_reverse_carbon_miss_by_under_delivery_fifth.csv",
    "q28_reverse_carbon_miss_by_under_delivery_fifth.png",
    "q28_reverse_carbon_miss_by_under_delivery_fifth.vl.json",
    "q28_reverse_exploratory.csv",
    "q28_reverse_strata_exploratory.csv",
    "q28_shortfall_index_by_miss_fifth.csv",
    "q28_shortfall_index_by_miss_fifth.png",
    "q28_shortfall_index_by_miss_fifth.vl.json",
    "q28_wind_shortfall_per_gw.csv",
    "q28_wind_shortfall_per_gw.png",
    "q28_wind_shortfall_per_gw.vl.json",
    "q28_wind_under_delivery_cut.csv",
    "q29_context_demand_by_month.csv",
    "q29_context_demand_by_month.png",
    "q29_context_demand_by_month.vl.json",
    "q29_error_by_month.png",
    "q29_error_by_month.vl.json",
    "q29_error_by_time_of_day.png",
    "q29_error_by_time_of_day.vl.json",
    "q29_final_forecast_error_by_month_and_block.csv",
    "q2_changes_by_unit_prefix.csv",
    "q2_gas_units_revisions.csv",
    "q2_moved_energy_by_unit_type.csv",
    "q2_moved_energy_by_unit_type.png",
    "q2_moved_energy_by_unit_type.vl.json",
    "q2_settlement_run_changes.csv",
    "q30_rank_correlations.csv",
    "q30_rank_correlations.png",
    "q30_rank_correlations.vl.json",
    "q30_wind_vs_carbon_error.csv",
    "q37_turn_up_by_wind_turn_down_band.csv",
    "q37_turn_up_by_wind_turn_down_band.png",
    "q37_turn_up_by_wind_turn_down_band.vl.json",
    "q37_wind_turn_down_and_turn_up.csv",
    "q38_nuclear_output_and_outage_notices.csv",
    "q38_output_and_gap_by_month.csv",
    "q38_output_and_gap_by_month.png",
    "q38_output_and_gap_by_month.vl.json",
    "q3_coverage.csv",
    "q3_energy_against_plan_by_fuel_group.csv",
    "q3_energy_against_plan_by_fuel_group.png",
    "q3_energy_against_plan_by_fuel_group.vl.json",
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


def prepend_text(path, text):
    path.write_text(text + path.read_text(encoding="utf-8"), encoding="utf-8")


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
    """The column names: the first line below the leading comment lines."""
    return header_of(path.read_text(encoding="utf-8"))


def header_of(csv_text):
    for line in csv_text.split("\n"):
        if not line.startswith("# "):
            return line.split(",")
    return []


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
    headers = {}
    for name, spec in written_specs(folder).items():
        headers[name] = csv_header(
            folder / spec["data"]["url"].removeprefix("results/")
        )
    return fields_missing_from(headers, folder)


def fields_missing_from(headers, folder):
    """Per spec in a folder, the fields it reads that the header given
    for it does not hold."""
    missing_fields = {}
    for name, spec in written_specs(folder).items():
        used = set()
        derived = set()
        collect_fields(spec, used, derived)
        missing = sorted(used - derived - set(headers[name]))
        if missing:
            missing_fields[name] = missing
    return missing_fields


NODE = shutil.which("node")
needs_node = pytest.mark.skipif(
    NODE is None and "CI" not in os.environ,
    reason="Node runs the page's own script; CI has it",
)


def page_function(name):
    """The source of a named top-level function in the page's script."""
    source = re.search(
        rf"^  function {name}\(.*?^  }}$",
        PAGE.read_text(encoding="utf-8"),
        re.DOTALL | re.MULTILINE,
    )
    assert source is not None, f"No two-space-indented function {name} in the page"
    return source.group(0)


def page_function_results(name, arguments):
    """What one of the page's functions returns for each argument, run
    by Node."""
    assert NODE is not None, "Node is needed to run the page's script"
    program = (
        "const given = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "const pageFunction = eval('(' + given.source + ')');"
        "const results = given.arguments.map((argument) => pageFunction(argument));"
        "process.stdout.write(JSON.stringify(results));"
    )
    finished = subprocess.run(
        [NODE, "-e", program],
        input=json.dumps({"source": page_function(name), "arguments": arguments}),
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(finished.stdout)


@needs_node
def test_the_page_gives_a_chart_the_rows_below_every_leading_comment_line():
    rows = "section,item\n1,a\n"

    assert page_function_results(
        "withoutLeadingComments",
        [
            rows,
            "# Attribution.\n" + rows,
            "# Attribution.\n# A note on the read.\n" + rows,
            "# Attribution.\n# A note.\n# Another.\nitem\n# not leading\n",
            "# Attribution, and nothing below it.",
        ],
    ) == [rows, rows, rows, "item\n# not leading\n", ""]


@needs_node
def test_the_page_gives_every_committed_chart_its_columns():
    # Q29's two charts read a saved output whose note is a second comment
    # line; the page once dropped only the first and drew them empty.
    results = REPOSITORY / "docs" / "results"
    names = []
    texts = []
    for name, data_url in spec_data_urls(results).items():
        names.append(name)
        texts.append(
            (results / data_url.removeprefix("results/")).read_text(encoding="utf-8")
        )
    headers = {}
    given = page_function_results("withoutLeadingComments", texts)
    for name, rows in zip(names, given, strict=True):
        headers[name] = rows.split("\n", 1)[0].split(",")

    assert len(names) == 18
    assert fields_missing_from(headers, results) == {}


def test_every_committed_charts_columns_are_below_its_comment_lines():
    # Runs without Node: a comment line the page would not drop, or a
    # header without a field its chart reads, fails here.
    results = REPOSITORY / "docs" / "results"

    assert fields_missing_from_their_csv(results) == {}


def test_a_csvs_header_is_its_first_line_below_the_leading_comment_lines():
    assert header_of("# Attribution.\n# A note on the read.\nsection,item\n1,a\n") == [
        "section",
        "item",
    ]


def test_every_chart_is_drawn_with_the_loader_that_drops_comment_lines():
    # An embed without these options, or options without this loader, reads
    # a file's first comment line as its header.
    page = PAGE.read_text(encoding="utf-8")

    assert "    loader: { ...fileLoader, load: loadBelowComments },\n" in page
    assert page.count("vegaEmbed(") == 19
    assert len(re.findall(r'vegaEmbed\("#[^"]+", "[^"]+", chartOptions\)', page)) == 19


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


def reversed_x_scales(node, found):
    """Whether any x scale in a spec runs right to left, or any x is sorted
    in descending order."""
    if isinstance(node, list):
        for item in node:
            reversed_x_scales(item, found)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        reversed_x_scales(value, found)
        if key != "x" or not isinstance(value, dict):
            continue
        scale = value.get("scale") or {}
        if scale.get("reverse") or value.get("sort") in ("descending", "-x"):
            found.append(value.get("field", "x"))


def specs_reading_right_to_left(folder):
    names = []
    for name, spec in written_specs(folder).items():
        found = []
        reversed_x_scales(spec, found)
        if found:
            names.append(name)
    return names


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
    entry in the chart's key. A layer whose colour and shape come from no
    field and that names no key entry is one series, named by the title and
    axes."""
    label = own_key_label(layer)
    encoding = layer.get("encoding", {})
    named = named_on_the_chart(layer, written_as_text)
    keyed = False
    labelled = False
    from_a_field = False
    for channel in KEY_CHANNELS:
        definition = encoding.get(channel, {})
        if "field" not in definition:
            continue
        from_a_field = True
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
    return labelled or not from_a_field


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


def titles_with_figures(folder, names):
    """The titles, of the specs named, that hold a digit."""
    with_figures = {}
    for name in names:
        description = written_specs(folder)[name]["description"]
        if any(character.isdigit() for character in description):
            with_figures[name] = description
    return with_figures


def marks_by_x_field(folder, spec_name):
    """Each drawn layer's mark and the field that places it along x, the
    named zero line and key left out."""
    layers = []
    drawn_layers(written_specs(folder)[spec_name], layers)
    marks = []
    for layer in layers:
        if layer.get("name") in ("zero line", "key"):
            continue
        marks.append((mark_type(layer), layer["encoding"]["x"]["field"]))
    return marks


def colours_not_shared(folder):
    """Per spec, any colour it writes out that is not one of the chart
    script's shared colour constants; fuel-group colours come from the
    page's scheme, never written out."""
    shared = set(SHARED_COLOURS)
    not_shared = {}
    for name, spec in written_specs(folder).items():
        found = set(WRITTEN_COLOUR.findall(json.dumps(spec))) - shared
        if found:
            not_shared[name] = sorted(found)
    return not_shared


def colour_channels(layer):
    channels = []
    for channel in ("color", "fill", "stroke"):
        definition = layer.get("encoding", {}).get(channel)
        if isinstance(definition, dict):
            channels.append(definition)
    return channels


def colours_on_fuel_group_rows(folder):
    """Per spec with fuel groups as rows, each drawn layer on those rows (not
    text, not a connector) and the field its colour comes from."""
    by_spec = {}
    for name, spec in written_specs(folder).items():
        layers = []
        drawn_layers(spec, layers)
        found = []
        for layer in layers:
            rows = layer.get("encoding", {}).get("y", {})
            if rows.get("field") != "fuel_group_name" or mark_type(layer) == "text":
                continue
            if layer.get("name") in UNKEYED_LAYER_NAMES:
                continue
            fields = []
            for definition in colour_channels(layer):
                fields.append(definition.get("field"))
            found.append((mark_type(layer), fields))
        if found:
            by_spec[name] = found
    return by_spec


def fuel_colours_not_from_the_scheme(folder):
    """Every colour drawn by fuel group whose scale is not the page's
    fuel-group scheme over every group in its fixed order."""
    scheme = {"expr": json.dumps(FUEL_GROUP_SCHEME)}
    names = []
    for fuel_group in FUEL_GROUP_COLOURS:
        names.append(FUEL_GROUP_DISPLAY_NAMES[fuel_group])
    wrong = []
    for name, spec in written_specs(folder).items():
        layers = []
        drawn_layers(spec, layers)
        for layer in layers:
            for definition in colour_channels(layer):
                if definition.get("field") != "fuel_group_name":
                    continue
                scale = definition.get("scale", {})
                if scale.get("scheme") != scheme or scale.get("domain") != names:
                    wrong.append(name)
    return sorted(set(wrong))


def inside_label_styles(folder):
    """Per spec that splits bars into parts, each text layer drawn inside a
    bar (right-aligned against a part's end) as its style and any colour it
    sets itself."""
    found = {}
    split = split_schemes(folder)
    for name, spec in written_specs(folder).items():
        if name not in split:
            continue
        layers = []
        drawn_layers(spec, layers)
        for layer in layers:
            mark = layer["mark"]
            if mark_type(layer) != "text" or not isinstance(mark, dict):
                continue
            if mark.get("align") != "right":
                continue
            found.setdefault(name, []).append((mark.get("style"), mark.get("color")))
    return found


def split_schemes(folder):
    """Per spec that splits bars into parts, the scheme its parts' colour
    names; a part colour given any other way shows as its scale."""
    found = {}
    for name, spec in written_specs(folder).items():
        layers = []
        drawn_layers(spec, layers)
        for layer in layers:
            for definition in colour_channels(layer):
                if definition.get("field") != "part_name":
                    continue
                scale = definition.get("scale", {})
                scheme = scale.get("scheme", {})
                if isinstance(scheme, dict) and "expr" in scheme:
                    found[name] = json.loads(scheme["expr"])
                else:
                    found[name] = scale
    return found


def diverging_colours(folder, spec_name):
    """The colours a Q29 chart's key gives dirtier and cleaner."""
    layers = []
    drawn_layers(written_specs(folder)[spec_name], layers)
    for layer in layers:
        for definition in colour_channels(layer):
            scale = definition.get("scale", {})
            domain = scale.get("domain", [])
            if "Dirtier than forecast" in domain:
                ranges = dict(zip(domain, scale["range"], strict=True))
                return (
                    ranges["Dirtier than forecast"],
                    ranges["Cleaner than forecast"],
                )
    return None


def x_axis_titles(folder, spec_name):
    """Every x-axis title line a spec writes, joined into one text."""
    layers = []
    drawn_layers(written_specs(folder)[spec_name], layers)
    lines = []
    for layer in layers:
        x = layer.get("encoding", {}).get("x", {})
        title = x.get("axis", {}).get("title") if isinstance(x, dict) else None
        if isinstance(title, list):
            lines.extend(title)
        elif isinstance(title, str):
            lines.append(title)
    return " ".join(lines)


def reading_line(folder, spec_name):
    """A spec's reading line as one line, from its widest wrapping."""
    subtitle = written_specs(folder)[spec_name]["title"]["subtitle"]
    return " ".join(wrapped_choices(subtitle["signal"])[-1])


def saved_output_rows(name):
    """A committed saved output's rows, after its attribution line and any
    comment line below it."""
    text = (REPOSITORY / "docs" / "results" / name).read_text(encoding="utf-8")
    lines = text.split("\n")
    while lines[0].startswith("#"):
        lines.pop(0)
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


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
    """Per fuel group, the summary chart's median gap beside the median of
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
    # MWh revised. Q23's titles state no figure: wind's median of monthly
    # medians (7.25) tops nuclear's (1.125), and wind tops every whole month.
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
            "Where the 100 GWh of revisions for 10–16 Aug 2026 came from"
        ),
        "q23_typical_gap_by_fuel_group.vl.json": (
            "With no NESO instruction, wind farms stray furthest from their plans"
        ),
        "q23_monthly_gap_by_fuel_group.vl.json": (
            "Wind stayed furthest from plan in every whole month"
        ),
    }


def test_every_written_csv_starts_with_its_attribution(written):
    assert first_lines(written) == {
        "context_carbon_intensity.csv": CARBON_LINE,
        "context_carbon_label_alignment.csv": ALIGNMENT_LINE,
        "context_cohort_change.csv": BMRS_LINE,
        "context_cohort_unit_types.csv": BMRS_LINE,
        "context_demand.csv": BMRS_LINE,
        "context_ii_zero_readings.csv": BMRS_LINE,
        "context_plan_pct_capacity.csv": BMRS_LINE,
        "context_plan_per_unit_half_hour.csv": BMRS_LINE,
        "context_q1_population.csv": CARBON_LINE,
        "context_q23_one_week_and_later_runs.csv": BMRS_LINE,
        "context_q23_runs_by_month.csv": BMRS_LINE,
        "context_q25_battery_named_units.csv": BMRS_LINE,
        "context_q25_cadl_check.csv": BMRS_LINE,
        "context_q28_runs_held.csv": BMRS_LINE,
        "context_q28_wind_level.csv": BMRS_AND_CARBON_LINE,
        "context_q30_outturn_lateness.csv": BMRS_LINE,
        "context_q30_wind_miss_sign.csv": BMRS_LINE,
        "context_q38_desnz_monthly_nuclear.csv": BMRS_AND_OGL_LINE,
        "context_q5_drawing_power.csv": BMRS_LINE,
        "context_q6_lag_quartiles.csv": BMRS_LINE,
        "context_q6_publication_timing.csv": BMRS_LINE,
        "context_q6_start_alignment.csv": BMRS_LINE,
        "desnz_nuclear_supplied_by_month.csv": OGL_LINE,
        "q10_forecast_accuracy_by_lead_time.csv": CARBON_LINE,
        "q12_instructed_share_by_fuel_group.csv": BMRS_LINE,
        "q12_instructed_vs_residual.csv": BMRS_LINE,
        "q1_forecast_drift_beside.csv": CARBON_LINE,
        "q1_forecast_drift_splits.csv": CARBON_LINE,
        "q1_forecast_drift_verdict.csv": CARBON_LINE,
        "q1_median_drift_by_time_and_band.csv": CARBON_LINE,
        "q22_first_against_latest_run.csv": BMRS_LINE,
        "q23_monthly_gap_by_fuel_group.csv": BMRS_LINE,
        "q23_pn_accuracy_proxy.csv": BMRS_LINE,
        "q23_typical_gap_by_fuel_group.csv": BMRS_LINE,
        "q24_flagged_share_by_fuel_group.csv": BMRS_LINE,
        "q24_system_action_share.csv": BMRS_LINE,
        "q25_instruction_fragmentation.csv": BMRS_LINE,
        "q25_instruction_fragmentation_corrected.csv": BMRS_LINE,
        "q25_short_instructions_by_fuel_group.csv": BMRS_LINE,
        "q28_reverse_carbon_miss_by_under_delivery_fifth.csv": BMRS_AND_CARBON_LINE,
        "q28_reverse_exploratory.csv": BMRS_AND_CARBON_LINE,
        "q28_reverse_strata_exploratory.csv": BMRS_AND_CARBON_LINE,
        "q28_shortfall_index_by_miss_fifth.csv": BMRS_AND_CARBON_LINE,
        "q28_wind_shortfall_per_gw.csv": BMRS_AND_CARBON_LINE,
        "q28_wind_under_delivery_cut.csv": BMRS_AND_CARBON_LINE,
        "q29_context_demand_by_month.csv": BMRS_LINE,
        "q29_final_forecast_error_by_month_and_block.csv": CARBON_LINE,
        "q2_changes_by_unit_prefix.csv": BMRS_LINE,
        "q2_gas_units_revisions.csv": BMRS_LINE,
        "q2_moved_energy_by_unit_type.csv": BMRS_LINE,
        "q2_settlement_run_changes.csv": BMRS_LINE,
        "q30_rank_correlations.csv": BMRS_AND_CARBON_LINE,
        "q30_wind_vs_carbon_error.csv": BMRS_AND_CARBON_LINE,
        "q37_turn_up_by_wind_turn_down_band.csv": BMRS_LINE,
        "q37_wind_turn_down_and_turn_up.csv": BMRS_LINE,
        "q38_nuclear_output_and_outage_notices.csv": BMRS_LINE,
        "q38_output_and_gap_by_month.csv": BMRS_LINE,
        "q3_coverage.csv": BMRS_LINE,
        "q3_energy_against_plan_by_fuel_group.csv": BMRS_LINE,
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


def test_a_saved_output_already_carrying_its_attribution_writes_the_same_files(
    tmp_path,
):
    # Q3's rows feed a chart, so the attribution must not reach the parsing,
    # and the copy must not double it.
    raw = saved_outputs_folder(tmp_path / "raw")
    attributed = saved_outputs_folder(tmp_path / "attributed")
    prepend_text(attributed / "q3_metered_vs_notified.csv", BMRS_LINE)
    write_charts(raw, tmp_path / "from_raw")
    write_charts(attributed, tmp_path / "from_attributed")

    assert output_files(tmp_path / "from_attributed") == output_files(
        tmp_path / "from_raw"
    )


def test_a_committed_context_file_keeps_its_second_comment_line(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    (saved / "context_q28_runs_held.csv").write_text(
        BMRS_LINE
        + "# Counts as held at a read-only query on 2 March 2027, 09:16 UTC.\n"
        + "month,run_held,cohort_unit_periods,first_date,last_date\n"
        + "2025-09,R2,250,2025-09-01,2025-09-30\n",
        encoding="utf-8",
    )
    write_charts(saved, tmp_path / "out")

    assert (tmp_path / "out" / "context_q28_runs_held.csv").read_bytes() == (
        "# Contains BMRS data © Elexon Limited copyright and database right 2026.\n"
        "# Counts as held at a read-only query on 2 March 2027, 09:16 UTC.\n"
        "month,run_held,cohort_unit_periods,first_date,last_date\n"
        "2025-09,R2,250,2025-09-01,2025-09-30\n"
    ).encode("utf-8")


def test_a_saved_output_carrying_another_attribution_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    prepend_text(
        saved / "q6_publication_lag.csv",
        "# Contains BMRS data © Elexon Limited copyright and database right 2025.\n",
    )

    with pytest.raises(ValueError, match="attribution"):
        write_charts(saved, tmp_path / "out")


def test_a_saved_output_carrying_its_attribution_below_another_comment_is_refused(
    tmp_path,
):
    saved = saved_outputs_folder(tmp_path / "saved")
    (saved / "context_q28_runs_held.csv").write_text(
        "# Counts as held at a read-only query on 2 March 2027, 09:16 UTC.\n"
        + BMRS_LINE
        + "month,run_held,cohort_unit_periods,first_date,last_date\n"
        + "2025-09,R2,250,2025-09-01,2025-09-30\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="attribution"):
        write_charts(saved, tmp_path / "out")


def test_a_saved_output_with_no_header_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    (saved / "q6_publication_lag.csv").write_text(BMRS_LINE + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no header"):
        write_charts(saved, tmp_path / "out")


def test_a_saved_output_with_a_comment_where_its_header_should_be_is_refused(
    tmp_path,
):
    saved = saved_outputs_folder(tmp_path / "saved")
    prepend_text(saved / "q6_publication_lag.csv", BMRS_LINE + "#note\n")

    with pytest.raises(ValueError, match="no header"):
        write_charts(saved, tmp_path / "out")


def test_a_saved_output_with_a_blank_line_above_its_attribution_is_refused(
    tmp_path,
):
    saved = saved_outputs_folder(tmp_path / "saved")
    prepend_text(saved / "q6_publication_lag.csv", "\n" + BMRS_LINE)

    with pytest.raises(ValueError, match="no header"):
        write_charts(saved, tmp_path / "out")


def test_every_spec_reads_a_written_csv_that_holds_every_field_it_uses(written):
    assert spec_data_urls(written) == {
        "q12_instructed_share_by_fuel_group.vl.json": (
            "results/q12_instructed_share_by_fuel_group.csv"
        ),
        "q1_median_drift_by_time_and_band.vl.json": (
            "results/q1_median_drift_by_time_and_band.csv"
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
        "q28_reverse_carbon_miss_by_under_delivery_fifth.vl.json": (
            "results/q28_reverse_carbon_miss_by_under_delivery_fifth.csv"
        ),
        "q28_shortfall_index_by_miss_fifth.vl.json": (
            "results/q28_shortfall_index_by_miss_fifth.csv"
        ),
        "q28_wind_shortfall_per_gw.vl.json": "results/q28_wind_shortfall_per_gw.csv",
        "q29_context_demand_by_month.vl.json": (
            "results/q29_context_demand_by_month.csv"
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
        "q37_turn_up_by_wind_turn_down_band.vl.json": (
            "results/q37_turn_up_by_wind_turn_down_band.csv"
        ),
        "q38_output_and_gap_by_month.vl.json": (
            "results/q38_output_and_gap_by_month.csv"
        ),
        "q3_energy_against_plan_by_fuel_group.vl.json": (
            "results/q3_energy_against_plan_by_fuel_group.csv"
        ),
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
        "q28_reverse_carbon_miss_by_under_delivery_fifth.vl.json": [[-5, 3]],
        "q28_wind_shortfall_per_gw.vl.json": [[-8, 16]],
        "q2_moved_energy_by_unit_type.vl.json": [[0, 100]],
        "q30_rank_correlations.vl.json": [[-0.3, 0.3]],
        "q38_output_and_gap_by_month.vl.json": [[0, 100]],
    }


def test_no_chart_reads_right_to_left(written):
    # Each chart's axis says which side is which ("left: below plan"); a
    # reversed scale would put every bar on the wrong side of those words.
    assert specs_reading_right_to_left(written) == []
    assert len(written_specs(written)) == 19


def test_reference_lines_sit_where_the_rules_put_them(written):
    # Q23: the provisional band runs from mid-August 2026 to just past
    # September's point, where the time axis ends, with its word at its left
    # edge. No chart draws a line for a threshold a result had to clear.
    assert nonzero_reference_lines(written) == {
        "q23_monthly_gap_by_fuel_group.vl.json": [
            BAND_FROM_17_AUGUST_2026,
            ENDS_12_SEPTEMBER_2026,
            BAND_FROM_17_AUGUST_2026,
        ],
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


def test_every_chart_colour_comes_from_the_shared_constants(written):
    assert colours_not_shared(written) == {}


def test_fuel_group_charts_colour_each_group_from_the_shared_scheme(written):
    # Colour by fuel group where it encodes nothing else; where it encodes
    # something else, the bars keep it and a chip beside each name shows the
    # group's colour.
    group = ["fuel_group_name"]
    part = ["part_name"]
    assert colours_on_fuel_group_rows(written) == {
        "q12_instructed_share_by_fuel_group.vl.json": [("bar", part), ("point", group)],
        "q23_typical_gap_by_fuel_group.vl.json": [("bar", group), ("bar", group)],
        "q24_flagged_share_by_fuel_group.vl.json": [("bar", part), ("point", group)],
        "q25_short_instructions_by_fuel_group.vl.json": [
            ("point", group),
            ("point", group),
        ],
        "q3_energy_against_plan_by_fuel_group.vl.json": [("bar", group)],
        "q3_gap_from_plan_by_fuel_group.vl.json": [("rule", group), ("point", group)],
        "q5_under_delivery_by_outage_notice.vl.json": [("bar", part), ("point", group)],
    }
    assert fuel_colours_not_from_the_scheme(written) == []


def test_bars_split_into_parts_take_the_page_split_greys(written):
    # Colour means fuel group; greys mean the split inside a bar.
    assert split_schemes(written) == {
        "q12_instructed_share_by_fuel_group.vl.json": SPLIT_TWO_SCHEME,
        "q24_flagged_share_by_fuel_group.vl.json": SPLIT_TWO_SCHEME,
        "q37_turn_up_by_wind_turn_down_band.vl.json": SPLIT_TWO_SCHEME,
        "q38_output_and_gap_by_month.vl.json": Q38_SCHEME,
        "q5_under_delivery_by_outage_notice.vl.json": SPLIT_THREE_SCHEME,
    }


def test_the_nuclear_chart_takes_nuclears_colour_then_the_three_split_greys():
    # The output part is nuclear's output, so it wears nuclear's colour; the
    # gap's three parts take the split greys as published outages' do,
    # planned strongest. The page registers the four for each mode.
    page = PAGE.read_text(encoding="utf-8")
    nuclear_position = list(FUEL_GROUP_COLOURS).index("nuclear")

    assert f"const nuclearColour = fuelGroupScheme()[{nuclear_position}];" in page
    assert (
        f'vega.scheme("{Q38_SCHEME}", [nuclearColour, '
        '...splitScheme(["strong", "mid", "soft"])]);' in page
    )


def test_both_q29_charts_share_one_diverging_pair_no_fuel_group_wears(written):
    pair = (DIRTIER_COLOUR, CLEANER_COLOUR)
    assert diverging_colours(written, "q29_error_by_time_of_day.vl.json") == pair
    assert diverging_colours(written, "q29_error_by_month.vl.json") == pair
    for light, dark in FUEL_GROUP_COLOURS.values():
        assert light not in pair
        assert dark not in pair


def test_each_energy_axis_and_title_carries_its_saved_output_range(written):
    # Q3's fixture covers 22 August 2025 to 26 September 2026, and Q12 reads
    # Q3's half-hours; Q5's rows run 22 August 2025 to 27 September 2026;
    # Q24's fixture holds August 2025 and September 2026, as settlement
    # months, and so does Q37's; Q38's runs September to November 2025; Q2's
    # week is its rule's.
    q3 = "22 Aug 2025 – 26 Sep 2026"
    assert q3 in x_axis_titles(written, "q3_gap_from_plan_by_fuel_group.vl.json")
    assert q3 in x_axis_titles(written, Q3_ENERGY_CHART)
    assert q3 in x_axis_titles(written, "q12_instructed_share_by_fuel_group.vl.json")
    assert "22 Aug 2025 – 27 Sep 2026" in x_axis_titles(
        written, "q5_under_delivery_by_outage_notice.vl.json"
    )
    assert "energy in TWh, settlement months Aug 2025 – Sep 2026." in reading_line(
        written, "q24_flagged_share_by_fuel_group.vl.json"
    )
    assert "half-hours, settlement months Aug 2025 – Sep 2026." in reading_line(
        written, "q37_turn_up_by_wind_turn_down_band.vl.json"
    )
    assert "half-hour. Settlement months Sep 2025 – Nov 2025." in reading_line(
        written, "q38_output_and_gap_by_month.vl.json"
    )
    assert (
        f"for {Q2_WEEK} came from"
        in (
            written_specs(written)["q2_moved_energy_by_unit_type.vl.json"][
                "description"
            ]
        )
    )


Q3_ENERGY_CHART = "q3_energy_against_plan_by_fuel_group.vl.json"
Q3_ENERGY_CSV = "q3_energy_against_plan_by_fuel_group.csv"


def with_dates_moved(path, moves):
    text = path.read_text(encoding="utf-8")
    for before, after in moves.items():
        text = text.replace(before, after)
    path.write_text(text, encoding="utf-8")


def test_each_energy_range_moves_with_its_saved_outputs_dates(tmp_path):
    # The fixtures hold the real outputs' dates, so a range typed in would
    # pass the test above; here each output's dates move a day, and every
    # range must move with them (Q12's with Q3's rows).
    saved = saved_outputs_folder(tmp_path / "saved")
    with_dates_moved(
        saved / "q3_metered_vs_notified.csv",
        {"2025-08-22": "2025-08-23", "2026-09-26": "2026-09-25"},
    )
    with_dates_moved(
        saved / "q5_shortfall_with_known_outage.csv",
        {"2025-08-22": "2025-08-21", "2026-09-27": "2026-09-28"},
    )
    write_charts(saved, tmp_path / "out")
    written_folder = tmp_path / "out"

    q3 = "23 Aug 2025 – 25 Sep 2026"
    assert q3 in x_axis_titles(written_folder, "q3_gap_from_plan_by_fuel_group.vl.json")
    assert q3 in x_axis_titles(written_folder, Q3_ENERGY_CHART)
    assert q3 in x_axis_titles(
        written_folder, "q12_instructed_share_by_fuel_group.vl.json"
    )
    assert "21 Aug 2025 – 28 Sep 2026" in x_axis_titles(
        written_folder, "q5_under_delivery_by_outage_notice.vl.json"
    )


def test_q2s_week_is_its_registered_population_and_q22s():
    # Q2's and Q22's outputs hold no dates, so the week is their rules'.
    first_day, rest = Q2_WEEK.split("–")
    last_day, month, year = rest.split(" ")
    week = f"{first_day} to {last_day} {FULL_MONTHS[month]} {year}"
    q2_population = f"**Population.** Settlement dates {week}, every BM unit"
    q22_run = f"run of settlement dates {week}, side by side"
    questions = QUESTIONS.read_text(encoding="utf-8")

    assert q2_population in questions
    assert q22_run in questions


HAND_RUN_QUERIES = {
    "q2": "q2_settlement_run_changes.sql",
    "q22": "q22_first_against_latest_run.sql",
}


def test_q2_and_q22_link_the_queries_run_by_hand_beside_their_outputs():
    # Neither query is a dbt analysis; each is kept as run, and its header
    # names the saved output it produced.
    page = PAGE.read_text(encoding="utf-8")
    questions = QUESTIONS.read_text(encoding="utf-8")

    for question_id, query in HAND_RUN_QUERIES.items():
        link = f'<a href="results/{query}">query as run</a>'
        output = query.removesuffix(".sql") + ".csv"
        header = (REPOSITORY / "docs" / "results" / query).read_text(encoding="utf-8")
        header = header.split("\n\n", 1)[0]
        assert link in article_text(page, "q3" if question_id == "q22" else "q2")
        assert f"[{query}](results/{query})" in questions
        assert output in header
        assert "the query is not in this repository" not in page
    assert "Q2's and Q22's are not in this repository" not in page


def q24_data_start(page):
    """The first day of Q24's read, from its How we measured it entry."""
    article = page.split('<article id="q24">', 1)[1].split("</article>", 1)[0]
    found = re.search(r"<dt>Data</dt>\s*<dd>(\d{1,2} [A-Z][a-z]+ \d{4}) to ", article)
    return found.group(1)


def test_q24s_part_month_starts_on_its_reads_first_day():
    # Q24's output holds months only; its first month is a part month, and
    # the day it starts is the one its read records.
    page = PAGE.read_text(encoding="utf-8")
    q24 = output_month_range(saved_output_rows("q24_system_action_share.csv"))
    first_month, first_year = q24.split(" – ")[0].split(" ")
    start = q24_data_start(page)

    assert start.endswith(f" {FULL_MONTHS[first_month]} {first_year}")
    assert (
        f"<dd>Settlement months {q24}; the first is a part month, from {start}. "
        "Units with" in fold_outs(page, "q24")
    )


def test_q28_says_which_way_it_compares():
    # Sorting half-hours by the size of the carbon miss cannot give its
    # direction; the page says so in view, and the fold-outs keep the
    # measure's wording.
    page = PAGE.read_text(encoding="utf-8")
    shown = in_view(page, "q28")
    folded = fold_outs(page, "q28")

    assert (
        "<h3>Do the carbon forecast's misses go with units falling short of "
        "their plans?</h3>" in shown
    )
    assert VISIBLE_LIMITS["q28"] in shown
    assert (
        "Half-hours are sorted by how far the forecast missed, not which way, so "
        "this cannot say whether under-delivery makes the forecast too clean or "
        "too dirty, or by how much.</dd>" in folded
    )
    assert (
        '"Yes" if the fifth of half-hours with the biggest carbon misses carries '
        "at least 25% more under-delivery than the fifth with the smallest." in folded
    )


def signed_tenths(value):
    """A saved value to one decimal, halves up, with its sign, as the page
    states it."""
    rounded = Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if rounded > 0:
        return f"+{rounded}"
    return str(rounded)


WIND_CHART = "q28_wind_shortfall_per_gw.vl.json"
WIND_OUTPUT = "q28_wind_under_delivery_cut.csv"
# Words for how a question was set up or judged, which no chart shows.
CHART_FRAMING_WORDS = re.compile(
    r"registered|set in advance|before the read|before reading|unplanned look|"
    r"exploratory|needed for|expected|not tested|is tested|would mean yes|"
    r"\bQ\d+",
    re.IGNORECASE,
)


def spec_texts(node, texts):
    """Every piece of fixed text a spec shows: titles, reading lines, axis
    titles, key entries and labels."""
    if isinstance(node, str):
        texts.append(node)
        return
    if isinstance(node, list):
        for item in node:
            spec_texts(item, texts)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key in ("title", "subtitle", "description", "domain", "signal", "text"):
            spec_texts(value, texts)
        elif key in ("calculate", "value") and isinstance(value, str):
            texts.append(value)
        elif not isinstance(value, str):
            spec_texts(value, texts)


def charts_showing_framing_words(folder):
    showing = {}
    for name, spec in written_specs(folder).items():
        texts = []
        spec_texts(spec, texts)
        found = CHART_FRAMING_WORDS.findall(" ".join(texts))
        if found:
            showing[name] = found
    return showing


def chart_script_strings_naming_a_question():
    """Every string the chart script holds, docstrings aside, that names a
    question by its number: the PNG titles and reading lines are drawn from
    these strings, and no test reads a PNG's pixels."""
    script = (REPOSITORY / "scripts" / "results_charts.py").read_text(encoding="utf-8")
    tree = ast.parse(script)
    docstrings = set()
    for node in ast.walk(tree):
        documented = isinstance(node, (ast.Module, ast.FunctionDef))
        if documented and ast.get_docstring(node) is not None:
            docstrings.add(id(node.body[0].value))
    naming = []
    for node in ast.walk(tree):
        is_text = isinstance(node, ast.Constant) and isinstance(node.value, str)
        if not is_text or id(node) in docstrings:
            continue
        if question_ids_in(node.value):
            naming.append(node.value)
    return naming


def test_no_text_the_chart_script_draws_names_a_question_by_number():
    assert chart_script_strings_naming_a_question() == []


PUBLISHED_WIND_COMMENTS = (
    "# One status row omitted; carbon, demand and registry cut 2026-10-26 "
    "11:21:39 UTC; shortfall and wind level as held 24 November 2026; seed "
    "20261025.\n"
    "# Row labels reworded for publication; every figure is as read.\n"
)
INTERNAL_LABELS = re.compile(r"HEADLINE|pre-regist|EXPLORATORY|Q\d|reverse cut")


def test_the_published_wind_output_carries_no_internal_label():
    # The as-read output is kept out of the repository; the published form
    # leaves out its status row and says so, with that row's facts.
    text = (REPOSITORY / "docs" / "results" / WIND_OUTPUT).read_text(encoding="utf-8")
    rows = saved_output_rows(WIND_OUTPUT)

    assert text.startswith(BMRS_AND_CARBON_LINE + PUBLISHED_WIND_COMMENTS + "section,")
    assert INTERNAL_LABELS.findall(text) == []
    assert section_rows(rows, "1")[0]["line"] == "2"
    assert len(rows) == 404
    assert INTERNAL_LABELS.findall(
        "HEADLINE; EXPLORATORY, pre-registration, of Q28's reverse cut"
    ) == [
        "HEADLINE",
        "EXPLORATORY",
        "pre-regist",
        "Q2",
        "reverse cut",
    ]


def test_no_chart_shows_how_a_question_was_set_up_or_a_bar_it_had_to_clear(written):
    assert charts_showing_framing_words(written) == {}


def test_the_wind_chart_states_its_half_hours_dates_and_that_it_is_a_pattern(
    written,
):
    # The fixture's population row: 500 half-hours, 1 September 2025 to
    # 31 July 2026. The output's scale row (12.345) is not drawn, keyed or
    # read out.
    spec = written_specs(written)[WIND_CHART]

    assert reading_line(written, WIND_CHART).endswith(
        "500 half-hours, 1 Sep 2025 – 31 Jul 2026. A pattern, not proof of cause."
    )
    assert (
        "The miss is the actual minus the forecast. 500 half-hours, "
        in reading_line(written, WIND_CHART)
    )
    assert "1 GW of gas" not in json.dumps(spec, ensure_ascii=False)
    assert "12.3" not in json.dumps(spec, ensure_ascii=False)
    assert text_fields_drawn(written, WIND_CHART) == [("value_label", "value")]
    assert titles_with_figures(written, [WIND_CHART]) == {}


def test_a_wind_figure_past_the_charts_fixed_scale_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    with_dates_moved(saved / WIND_OUTPUT, {"2.000000,9.000000": "2.000000,17.000000"})

    with pytest.raises(ValueError, match="runs past the scale"):
        write_charts(saved, tmp_path / "out")


def test_a_wind_output_whose_range_reaches_zero_is_refused_under_its_title(
    tmp_path,
):
    # The title says a bigger shortfall went with a dirtier grid; a range
    # that takes in zero does not bear that out.
    saved = saved_outputs_folder(tmp_path / "saved")
    with_dates_moved(saved / WIND_OUTPUT, {"6.250000,2.000000": "6.250000,-0.100000"})

    with pytest.raises(ValueError, match="title would not hold"):
        write_charts(saved, tmp_path / "out")


def test_a_wind_output_without_one_of_its_rows_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    without_lines_containing(
        saved / WIND_OUTPUT, "wind_positive,actual,,500,,,,,,-0.95"
    )

    with pytest.raises(ValueError, match="0 rows for section 5, line 11, actual"):
        write_charts(saved, tmp_path / "out")


def test_a_turn_up_output_without_one_of_its_bands_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    without_lines_containing(saved / Q37_OUTPUT, ",5 2 GW or more,")

    with pytest.raises(ValueError, match="no row for band '5 2 GW or more'"):
        write_charts(saved, tmp_path / "out")


Q38_OUTPUT = "q38_nuclear_output_and_outage_notices.csv"


def test_a_nuclear_output_without_a_middle_month_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    without_lines_containing(saved / Q38_OUTPUT, "1,2025-10-01,")

    with pytest.raises(ValueError, match="no month between 2025-09-01 and 2025-11-01"):
        write_charts(saved, tmp_path / "out")


def test_a_nuclear_month_without_one_of_its_notice_classes_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    without_lines_containing(saved / Q38_OUTPUT, "3,2025-11-01,,,none,")

    with pytest.raises(ValueError, match="no 'none' gap for 2025-11-01"):
        write_charts(saved, tmp_path / "out")


def test_a_nuclear_notice_class_the_chart_has_no_part_for_is_refused(tmp_path):
    # A half-hour under both kinds of notice would fall in no part of a bar.
    saved = saved_outputs_folder(tmp_path / "saved")
    add_lines(
        saved / Q38_OUTPUT,
        [nuclear_gap_fixture_line("2025-11-01", "both", "generating", "1.0")],
    )

    with pytest.raises(ValueError, match="notice class with no part: 'both'"):
        write_charts(saved, tmp_path / "out")


def test_a_nuclear_gap_in_a_month_with_no_totals_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    add_lines(
        saved / Q38_OUTPUT,
        [nuclear_gap_fixture_line("2025-12-01", "none", "generating", "1.0")],
    )

    with pytest.raises(ValueError, match="a month it does not total: 2025-12-01"):
        write_charts(saved, tmp_path / "out")


def test_a_desnz_month_the_nuclear_output_does_not_hold_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    add_lines(saved / "desnz_nuclear_supplied_by_month.csv", ["2025-12,0.9000,yes"])

    with pytest.raises(ValueError, match="no month 2025-12 to compare"):
        write_charts(saved, tmp_path / "out")


def test_the_q28_reverse_chart_reads_its_range_from_its_saved_output(written):
    # The fixture's read runs 1 September 2025 to 31 July 2026.
    assert "fifth of the half-hours, 1 Sep 2025 – 31 Jul 2026, least" in (
        reading_line(written, "q28_reverse_carbon_miss_by_under_delivery_fifth.vl.json")
    )


def test_a_q28_reverse_range_past_the_fixed_scale_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    reverse = saved / "q28_reverse_exploratory.csv"
    text = reverse.read_text(encoding="utf-8")
    reverse.write_text(
        text.replace("2.000000,1.000000,3.000000", "2.000000,1.000000,3.100000"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runs past the scale"):
        write_charts(saved, tmp_path / "out")


def test_q28s_answer_and_second_result_state_their_saved_figures():
    rows = saved_output_rows("q28_reverse_exploratory.csv")
    slope = [row for row in rows if row["section"] == "3" and row["line"] == "2"][0]
    read = [row for row in rows if row["section"] == "1"][0]
    dates = output_date_range(rows)
    fifths = item_rows(rows, "2", Q28_REVERSE_FIFTH_ITEM)
    least = one_row(fifths, "fifth", "1")["value"]
    most = one_row(fifths, "fifth", "5")["value"]
    section_two = [row for row in rows if row["section"] == "2"]
    all_half_hours = one_row(section_two, "line", "2")
    mean_size = all_half_hours["mean_abs_error"]
    q28 = saved_output_rows("q8_q28_history.csv")
    q28_ratio = Decimal(item_row(q28, "3", "ratio, top over bottom")["value"])
    q28_bar = item_row(q28, "3", "verdict (supported at a ratio of 1.25 or more)")
    difference = [row for row in rows if row["section"] == "3" and row["line"] == "1"][
        0
    ]
    dirtier_with_most = one_row(fifths, "fifth", "5")["share_dirtier"]
    dirtier_with_least = one_row(fifths, "fifth", "1")["share_dirtier"]
    page = PAGE.read_text(encoding="utf-8")
    shown = FOLD_OUT.sub("", exploratory_block())
    folded = exploratory_fold_out()

    ratio_low = Decimal(
        item_row(q28, "3", "bootstrap 90% interval for the ratio, low")["value"]
    )
    ratio_high = Decimal(
        item_row(q28, "3", "bootstrap 90% interval for the ratio, high")["value"]
    )
    assert answer_of(page, "q28") == (
        "<strong>Only slightly.</strong> "
        "Under-delivery was <strong>"
        f"{whole((q28_ratio - 1) * 100)}% larger</strong> in the fifth of half-hours "
        "where the carbon forecast missed most than in the fifth where it missed "
        "least (90% range "
        f"{whole((ratio_low - 1) * 100)}% to {whole((ratio_high - 1) * 100)}%). "
        "Sorted by under-delivery instead, the grid came out "
        f"{tenths(-Decimal(least))} g/kWh cleaner than forecast with the least "
        f"and {tenths(most)} g/kWh dirtier with the most."
    )
    assert "wind" not in answer_of(page, "q28")
    assert page.count('<section class="further"') == 1
    assert ratio_low > 1
    assert (
        '<h4 id="q28-reverse-title">More under-delivery, slightly dirtier than '
        "forecast</h4>" in shown
    )
    assert (
        "<p>With the least under-delivery, the grid came out "
        f"{tenths(-Decimal(least))} g/kWh cleaner than forecast on average; with "
        f"the most, {tenths(most)} g/kWh dirtier. Even with the most "
        "under-delivery, the grid came out dirtier than forecast in only about "
        f"half the half-hours ({percent_of(dirtier_with_most, 1, 0)}%, against "
        f"{percent_of(dirtier_with_least, 1, 0)}% with the least).</p>" in shown
    )
    assert (
        f"<figcaption>The same {int(read['half_hours']):,} half-hours. Source:" in shown
    )
    assert (
        "<p><strong>This does not show cause:</strong> wind is linked to both "
        "measures.</p>" in shown
    )
    assert (
        "<dd>Each extra GW of under-delivery went with a carbon miss about "
        f"{tenths(slope['value'])} g/kWh further towards dirtier than forecast "
        f"(90% range {tenths(slope['interval_low'])} to "
        f"{tenths(slope['interval_high'])}). The fifth with the most "
        f"under-delivery came out {tenths(difference['value'])} g/kWh dirtier "
        "against forecast than the fifth with the least (90% range "
        f"{tenths(difference['interval_low'])} to "
        f"{tenths(difference['interval_high'])}); misses had a mean size of "
        f"{tenths(mean_size)} g/kWh in either direction." in folded
    )
    assert f"Q28's {int(read['half_hours']):,} half-hours, {dates}; read once" in folded
    assert (
        "<dd>An association only. Asked after the first comparison had been "
        "read, so not written down before the data was read: a further look at "
        "a window already seen, published whatever it showed." in folded
    )
    assert read["result"] == "matches Q28"
    assert q28_bar["result"] == "not supported"
    assert Decimal(least) < 0 < Decimal(most)
    assert tenths(Decimal(dirtier_with_most) * 2) == "1.0"


def test_q28s_reverse_chart_shows_each_fifths_gw_range_and_miss():
    # The committed chart data against the committed saved output: GW is MWh
    # per half-hour x 2 / 1,000, one decimal; the miss to one decimal.
    saved = saved_output_rows("q28_reverse_exploratory.csv")
    fifths = [row for row in saved if row["item"] == Q28_REVERSE_FIFTH_ITEM]
    chart = saved_output_rows("q28_reverse_carbon_miss_by_under_delivery_fifth.csv")
    gigawatts_per_mwh = Decimal(2) / Decimal(1000)

    assert len(chart) == len(fifths) == 5
    for saved_row, chart_row in zip(fifths, chart, strict=True):
        low = Decimal(saved_row["under_delivery_from_mwh"]) * gigawatts_per_mwh
        high = Decimal(saved_row["under_delivery_to_mwh"]) * gigawatts_per_mwh
        tenth = Decimal("0.1")
        gigawatt_range = (
            f"{low.quantize(tenth, rounding=ROUND_HALF_UP)}–"
            f"{high.quantize(tenth, rounding=ROUND_HALF_UP)} GW"
        )
        assert chart_row["label"].endswith(gigawatt_range)
        assert chart_row["mean_error"] == saved_row["value"]
        expected_label = signed_tenths(saved_row["value"])
        if expected_label == "-0.0":
            expected_label = "0.0"
        assert chart_row["mean_label"].replace("−", "-") == expected_label
        assert chart_row["interval_low"] == saved_row["interval_low"]
        assert chart_row["interval_high"] == saved_row["interval_high"]


STRATA_RESULT = "q28_reverse_strata_exploratory.csv"
# The wording fixed before the strata read for the within-cell slope's 90%
# range: above zero, including zero, below zero.
STRATA_ABOVE_ZERO = "a positive slope remains within the same time of day and month"
STRATA_INCLUDES_ZERO = "no clear slope remains within the same time of day and month"
STRATA_BELOW_ZERO = "the sign reverses within the same time of day and month"
NUMBER_WORDS = {4: "four", 5: "five", 6: "six", 13: "thirteen", 17: "seventeen"}


def strata_clause(low, high):
    """The clause fixed before the read for a 90% range from low to high."""
    if Decimal(low) > 0:
        return STRATA_ABOVE_ZERO
    if Decimal(high) < 0:
        return STRATA_BELOW_ZERO
    return STRATA_INCLUDES_ZERO


def page_signed(value):
    """A value to one decimal with its sign, as the page's tables write it."""
    return signed_tenths(value).replace("-", "−")


def strata_month_label(stratum):
    """Q29's '2025-08 (partial: 22 to 31)' as 'Aug 2025 (22 to 31)'."""
    month, _, part = stratum.partition(" (partial: ")
    year, number = month.split("-")
    label = f"{list(FULL_MONTHS)[int(number) - 1]} {year}"
    if not part:
        return label
    return f"{label} ({part.removesuffix(')')})"


def strata_rows(rows, section):
    found = []
    for row in rows:
        if row["section"] == section:
            found.append(row)
    return found


def strata_row(rows, section, line):
    return one_row(strata_rows(rows, section), "line", line)


def above_zero(rows):
    """The strata whose 90% range lies wholly above zero."""
    found = []
    for row in rows:
        if Decimal(row["interval_low"]) > 0:
            found.append(row["stratum"])
    return found


def at_or_below_zero(rows):
    """The strata whose slope is zero or negative."""
    found = []
    for row in rows:
        if Decimal(row["value"]) <= 0:
            found.append(row["stratum"])
    return found


def strata_read_day(rows):
    """The day the strata read records, from its label line."""
    label = strata_row(rows, "1", "1")["item"]
    return re.search(rf"chosen on ({LONG_DATE})", label).group(1)


def latest_day(*days):
    """The latest of some "6 November 2026" days, in the same form."""
    parsed = [datetime.strptime(day, "%d %B %Y") for day in days]
    return f"{max(parsed).day} {max(parsed):%B %Y}"


def result_block(title_id):
    """One of Q28's further results: the section its heading names."""
    page = PAGE.read_text(encoding="utf-8")
    opening = f'<section class="further" aria-labelledby="{title_id}">'
    return page.split(opening, 1)[1].split("</section>", 1)[0]


def exploratory_block():
    """Q28's second result: the half-hours sorted by under-delivery."""
    return result_block("q28-reverse-title")


def exploratory_fold_out():
    """What that result keeps inside its fold-out."""
    return "\n".join(FOLD_OUT.findall(exploratory_block()))


def test_q28s_strata_cut_states_its_saved_figures_and_its_fixed_clause():
    rows = saved_output_rows(STRATA_RESULT)
    population = strata_row(rows, "1", "2")
    within = strata_row(rows, "4", "1")
    pooled = strata_row(rows, "4", "2")
    difference = strata_row(rows, "4", "4")
    blocks = strata_rows(rows, "2")
    months = strata_rows(rows, "3")
    low_months = at_or_below_zero(months)
    low_names = []
    for month in low_months:
        low_names.append(FULL_MONTHS[strata_month_label(month).split(" ")[0]])
    clause = strata_clause(within["interval_low"], within["interval_high"])
    block = exploratory_fold_out()

    assert population["result"] == "matches Q28"
    assert Decimal(within["interval_low"]) > 0
    assert clause == STRATA_ABOVE_ZERO
    assert above_zero(blocks) == column_strata(blocks)
    assert low_months == ["2025-12", "2026-01", "2026-02"]
    assert (
        "<dt>Within the same time of day and month</dt>\n          <dd>The "
        "chart does not hold time of day or month fixed, and carbon misses "
        f"differ by both{details_link('Q29')}, so it may partly reflect them. "
        "Within the same time of day and month, each extra GW of "
        "under-delivery went with a carbon miss "
        f"{tenths(within['value'])} g/kWh further towards dirtier than forecast "
        f"(90% range {tenths(within['interval_low'])} to "
        f"{tenths(within['interval_high'])}), against {tenths(pooled['value'])} "
        f"g/kWh with neither held fixed: {clause}. Holding them fixed took "
        f"{hundredths(-Decimal(difference['value']))} g/kWh per GW off the slope "
        f"(90% range {hundredths(-Decimal(difference['interval_high']))} to "
        f"{hundredths(-Decimal(difference['interval_low']))}). The 90% range is "
        f"above zero in all {NUMBER_WORDS[len(blocks)]} time-of-day blocks and in "
        f"{len(above_zero(months))} of the {len(months)} months; in the other "
        f"{len(months) - len(above_zero(months))} it includes zero, and the slope "
        f"itself is at or below zero only in {', '.join(low_names[:-1])} and "
        f"{low_names[-1]}. The slope and range of each of the {len(blocks)} blocks "
        f"and {len(months)} months are in the saved output. "
        f"Q28's {int(population['half_hours']):,} half-hours, "
        f"{output_date_range([population])}; read once on {strata_read_day(rows)}. "
        "Month intervals resample whole days from the whole year, so a month's day "
        "count varies between draws. "
        f'<a href="results/{STRATA_RESULT}">Saved output</a></dd>' in block
    )
    assert (
        "a further look at a window already seen, published whatever it showed. "
        f"{NUMBER_WORDS[len(blocks) + len(months)].capitalize()} slopes were read, "
        "none corrected for reading many." in block
    )
    assert (
        "<dd>Wind output is not held fixed, and both measures are associated "
        "with wind level." in block
    )
    for other in sorted({STRATA_INCLUDES_ZERO, STRATA_BELOW_ZERO}):
        assert other not in block


def column_strata(rows):
    found = []
    for row in rows:
        found.append(row["stratum"])
    return found


def test_q28s_strata_slopes_stay_in_the_saved_output_not_on_the_page():
    # The page gives the within-cell slope and the counts of blocks and
    # months; the 17 per-stratum slopes and ranges are the saved output's,
    # with no table of them, so the registered null keeps the space.
    rows = saved_output_rows(STRATA_RESULT)
    blocks = strata_rows(rows, "2")
    months = strata_rows(rows, "3")
    block = exploratory_block()
    per_stratum = []
    for row in blocks + months:
        per_stratum.append(page_signed(row["interval_high"]))

    assert len(blocks) == 4
    assert len(months) == 13
    assert (
        f"The slope and range of each of the {len(blocks)} blocks and "
        f"{len(months)} months are in the saved output. " in block
    )
    assert f'<a href="results/{STRATA_RESULT}">Saved output</a>' in block
    assert "<table" not in block
    for interval_high in per_stratum:
        assert f"to {interval_high}</td>" not in block


def whole(value):
    return str(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def tenths(value):
    return str(Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def percent_of(part, whole_amount, places):
    share = Decimal(100) * Decimal(part) / Decimal(whole_amount)
    if places == 0:
        return whole(share)
    return tenths(share)


def one_row(rows, column, value):
    found = []
    for row in rows:
        if row[column] == value:
            found.append(row)
    assert len(found) == 1
    return found[0]


def nearest_ten(value):
    return str(Decimal(value / 10).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 10)


def item_rows(rows, section, item):
    found = []
    for row in rows:
        if row["section"] == section and row["item"] == item:
            found.append(row)
    return found


def item_row(rows, section, item):
    found = []
    for row in rows:
        if row["section"] == section and row["item"] == item:
            found.append(row)
    assert len(found) == 1
    return found[0]


def wind_planned_range(capacity_rows):
    """The median wind unit's planned output, % of capacity, over Q23's
    whole months, September 2025 to August 2026."""
    values = []
    for row in capacity_rows:
        whole_month = "2025-09-01" <= row["settlement_month"] <= "2026-08-01"
        if row["fuel_group"] == "wind" and whole_month:
            values.append(Decimal(row["median_unit_planned_pct_of_capacity"]))
    assert len(values) == 12
    return whole(min(values)), whole(max(values))


def test_each_scale_on_the_page_comes_from_its_context_count_or_output():
    page = PAGE.read_text(encoding="utf-8")
    plan = saved_output_rows("context_plan_per_unit_half_hour.csv")
    carbon = saved_output_rows("context_carbon_intensity.csv")
    demand = saved_output_rows("context_demand.csv")
    capacity = saved_output_rows("context_plan_pct_capacity.csv")
    q3 = saved_output_rows("q3_metered_vs_notified.csv")
    q29 = saved_output_rows("q29_final_forecast_error_by_month_and_block.csv")
    q8_q28 = saved_output_rows("q8_q28_history.csv")
    q5 = saved_output_rows("q5_shortfall_with_known_outage.csv")
    q24 = saved_output_rows("q24_system_action_share.csv")
    q22 = saved_output_rows("q22_first_against_latest_run.csv")
    q2 = saved_output_rows("q2_settlement_run_changes.csv")
    coverage = saved_output_rows("q3_coverage.csv")
    q23 = saved_output_rows("q23_pn_accuracy_proxy.csv")

    wind_plan = one_row(plan, "fuel_group", "wind")
    wind_median = one_row(q3, "fuel_group", "wind")["median_deviation_mwh"]
    all_carbon = item_row(carbon, "1", "all half-hours")
    q29_size = one_row(q29, "section", "1")["mean_absolute_error"]
    largest_fifth = []
    for row in q8_q28:
        if row["section"] == "4" and row["quintile"] == "5":
            largest_fifth.append(row["value"])
    all_demand = item_row(demand, "1", "all half-hours")
    planned_total = Decimal(0)
    for row in plan:
        planned_total += Decimal(row["q3_abs_pn_mwh"])
    under_delivery = item_row(q5, "1", "total")["positive_shortfall_mwh"]
    wind_flagged = one_row(
        item_rows(q24, "2", "stretches with a volume, all months"), "fuel_group", "wind"
    )
    bioenergy = one_row(q22, "fuel_group", "bioenergy")
    first = Decimal(bioenergy["first_median_exact"])
    latest = Decimal(bioenergy["latest_median_exact"])
    revised = one_row(q2, "run_pair", "SF->R1")["moved_mwh"]
    cohort = item_row(coverage, "2", "fuel type known (the cohort)")
    no_meter = item_row(coverage, "3", "no_metered_value")
    metered = item_row(coverage, "1", "mapped, fuel type known (the cohort)")
    uninstructed = item_row(q23, "3", "cohort unit-periods: determinable, uninstructed")
    unmetered = item_row(
        q23, "3", "cohort unit-periods: no_metered_value, uninstructed"
    )
    q5_unmetered = item_row(q5, "3", "cohort unit-periods: no_metered_value")
    q5_metered = item_row(q5, "3", "cohort unit-periods: determinable")
    low, high = wind_planned_range(capacity)
    gas_units = saved_output_rows("q2_gas_units_revisions.csv")
    three_gas_units_mwh = Decimal(0)
    three_gas_units_own_mwh = Decimal(0)
    for row in gas_units:
        three_gas_units_mwh += Decimal(row["moved_mwh"])
        three_gas_units_own_mwh += Decimal(row["sf_abs_mwh"])
    three_gas_units_gwh = three_gas_units_mwh / Decimal(1000)
    link_plan = one_row(plan, "fuel_group", "interconnector")
    link_zero_plan = int(link_plan["q3_unit_periods"]) - int(
        link_plan["q3_unit_periods_nonzero_plan"]
    )
    link_zero_share = percent_of(link_zero_plan, link_plan["q3_unit_periods"], 0)

    expected = [
        f"wind's median is {tenths(wind_median)} MWh short per half-hour (about "
        "0.6 MW); a wind unit's median plan is "
        f"{tenths(wind_plan['q3_median_plan_mwh'])} MWh a half-hour (mean "
        f"{tenths(wind_plan['q3_mean_plan_mwh'])}).",
        "an allocation against a plan that is zero in "
        f"{link_zero_share}% of half-hours.",
        f"Its misses had a mean size of {tenths(q29_size)} g/kWh, about "
        f"{percent_of(q29_size, all_carbon['mean_actual_g_per_kwh'], 1)}% of the mean "
        f"carbon intensity of {whole(all_carbon['mean_actual_g_per_kwh'])} g/kWh,",
        f"801 MWh in the largest, about "
        f"{percent_of(largest_fifth[0], all_demand['mean_demand_mwh_per_half_hour'], 0)}% "
        "of mean national demand from NESO's initial outturn "
        '(<a href="#national-demand">INDO</a>), '
        f"{int(all_demand['mean_demand_mwh_per_half_hour']):,} MWh a half-hour.",
        f"in every whole month, against a median plan of {low} to {high}% of capacity;",
        f"16.4 TWh of under-delivery (about {percent_of(under_delivery, planned_total, 0)}% "
        "of the energy these units planned over almost the same dates)",
        f"Sep 2026; about "
        f"{percent_of(wind_flagged['acceptance_volume_mwh'], wind_plan['q3_abs_pn_mwh'], 0)}% "
        "of the energy wind farms planned over almost the same dates)",
        f"its median gap moved from {first.quantize(Decimal('0.01'))} to "
        f"{latest.quantize(Decimal('0.01'))} MWh. The test cannot see",
        "revisions, up or down counted alike, came to about "
        f"{nearest_ten(three_gas_units_gwh)} GWh, "
        f"{percent_of(three_gas_units_mwh, three_gas_units_own_mwh, 0)}% of their "
        f"own {whole(three_gas_units_own_mwh / Decimal(1000))} GWh in the "
        f"three-week run, and {percent_of(three_gas_units_mwh, revised, 0)}% of "
        "the energy revised",
        "Among transmission-connected and embedded units, three gas units account "
        f"for {percent_of(three_gas_units_mwh, revised, 0)}% of all the energy "
        "revised",
        f"no meter reading ({percent_of(no_meter['unit_periods'], int(cohort['unit_periods']) + int(no_meter['unit_periods']), 1)}%), "
        f"and 283,242 with a meter reading and no plan "
        f"({percent_of(int(metered['unit_periods']) - int(cohort['unit_periods']), metered['unit_periods'], 1)}%).",
        f"no meter reading ({percent_of(unmetered['half_hours'], int(uninstructed['half_hours']) + int(unmetered['half_hours']), 1)}% "
        "of half-hours with no instruction).",
        f"no meter reading ({percent_of(q5_unmetered['unit_periods'], int(q5_metered['unit_periods']) + int(q5_unmetered['unit_periods']), 1)}%); ",
        f"on the same {int(all_demand['half_hours_with_demand']):,} half-hours, a context "
        "count.",
    ]
    q29_share = percent_of(q29_size, all_carbon["mean_actual_g_per_kwh"], 1)

    assert phrases_missing_from(every_fold_out(page), expected) == []
    wind_gap = one_row(q3, "fuel_group", "wind")
    median_gaps = {}
    for row in q3:
        median_gaps[row["fuel_group"]] = Decimal(row["median_deviation_mwh"])
    afternoon = one_row(q29, "item", "UTC block 12:00-17:59")["mean_error"]
    evening = one_row(q29, "item", "UTC block 18:00-23:59")["mean_error"]
    assert (
        "<summary>The typical half-hour: medians and spreads</summary>\n"
        "      <p><strong>Below plan in the typical half-hour, for most groups."
        "</strong> "
        "At wind, gas, nuclear, bioenergy and other units the median half-hour "
        "fell short of plan; for hydro, pumped storage and interconnectors the "
        "median gap is zero. "
        "Wind's spread is wide: a quarter of its half-hours fell over "
        f"{tenths(wind_gap['q3_deviation_mwh'])} MWh short and a quarter ran over "
        f"{whole(-Decimal(wind_gap['q1_deviation_mwh']))} MWh above.</p>"
        in fold_outs(page, "q3")
    )
    drawn = set(median_gaps) - {"coal"}
    named_short = {"wind", "gas_turbine", "nuclear", "bioenergy", "other"}
    named_level = {"hydro", "pumped_storage", "interconnector"}
    assert named_short | named_level == drawn
    for short_of_plan in named_short:
        assert median_gaps[short_of_plan] > 0
    for level in named_level:
        assert median_gaps[level] == 0
    assert "% of a wind unit's median plan" not in page
    assert answer_of(page, "q29") == (
        f"<strong>Off by {tenths(q29_size)} g/kWh on average</strong>, about "
        f"{q29_share}% of the mean carbon intensity, with almost no lean either "
        f"way overall. Afternoons came out about {whole(afternoon)} g dirtier than "
        f"forecast and evenings about {whole(-Decimal(evening))} g cleaner."
    )
    assert (
        "<figcaption>For scale only: mean national demand on "
        f"the same {int(all_demand['half_hours_with_demand']):,} half-hours, "
        in in_view(page, "q29")
    )
    assert 283_242 == int(metered["unit_periods"]) - int(cohort["unit_periods"])


SHORT_MONTHS = dict(zip(FULL_MONTHS.values(), FULL_MONTHS.keys(), strict=True))
LONG_DATE = r"\d{1,2} [A-Z][a-z]+ \d{4}"


def hundredths(value):
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def next_half_above(value):
    """The first multiple of 0.5 above the value: the "under" limit the page
    states for it."""
    halves = (Decimal(value) * 2).to_integral_value(rounding=ROUND_FLOOR)
    return str((halves + 1) / 2)


def missing_phrases(expected):
    page = PAGE.read_text(encoding="utf-8")
    missing = []
    for phrase in expected:
        if phrase not in page:
            missing.append(phrase)
    return missing


def article_text(page, article_id):
    return page.split(f'<article id="{article_id}">', 1)[1].split("</article>", 1)[0]


FOLD_OUT = re.compile(r'<details class="method">.*?</details>', re.DOTALL)
ARTICLE_IDS = [
    "q23",
    "q3",
    "q12",
    "q5",
    "q6",
    "q38",
    "q24",
    "q37",
    "q25",
    "q29",
    "q28",
    "wind-shortfall",
    "q8",
    "q30",
    "q1",
    "q10",
    "q2",
]
# The plain name the Limitations give each result, before its asterisk.
RESULT_NAMES = {
    "Q1": "the forecast's drift",
    "Q3": "more or less than planned",
    "Q5": "published outages",
    "Q8": "demand or under-delivery",
    "Q10": "accuracy by lead time",
    "Q12": "the instructed share",
    "Q23": "following the plan",
    "Q24": "system flags",
    "Q25": "short instructions",
    "Q28": "carbon misses and under-delivery",
    "Q29": "misses by month and time of day",
    "Q30": "wind and carbon misses",
    "Q37": "what is turned up with wind turned down",
    "Q38": "nuclear output against capacity",
}


def fold_outs(page, article_id):
    """What an article keeps inside its fold-outs."""
    return "\n".join(FOLD_OUT.findall(article_text(page, article_id)))


def in_view(page, article_id):
    """An article as it shows before any fold-out is opened."""
    return FOLD_OUT.sub("", article_text(page, article_id))


def every_fold_out(page):
    return "\n".join(FOLD_OUT.findall(page))


def answer_of(page, article_id):
    """The inside of an article's answer paragraph."""
    answer = article_text(page, article_id).split('<p class="answer">', 1)[1]
    return answer.split("</p>", 1)[0]


def phrases_missing_from(text, expected):
    missing = []
    for phrase in expected:
        if phrase not in text:
            missing.append(phrase)
    return missing


def details_link(question_id):
    """The asterisk that links a claim to its article on the page."""
    return (
        f'<a class="ref" href="#{question_id.lower()}" '
        f'aria-label="Details, {question_id}">*</a>'
    )


def named_result(question_id):
    return RESULT_NAMES[question_id] + details_link(question_id)


def heading_anchor(heading):
    """The anchor GitHub gives a Markdown heading."""
    kept = re.sub(r"[^a-z0-9 \-]", "", heading.lower())
    return kept.replace(" ", "-")


def register_anchors():
    """The anchor of each question's heading in the register, by question."""
    anchors = {}
    questions = QUESTIONS.read_text(encoding="utf-8")
    for heading in re.findall(r"^### (Q[^\n]+)$", questions, re.MULTILINE):
        anchors[heading.split(".", 1)[0]] = heading_anchor(heading)
    return anchors


def short_date(long_date):
    """'22 August 2025' as '22 Aug 2025'."""
    day, month, year = long_date.split(" ")
    return f"{day} {SHORT_MONTHS[month]} {year}"


def long_date(day):
    """'2025-08-22' as '22 August 2025'."""
    year, month, date = day.split("-")
    month_names = list(FULL_MONTHS.values())
    return f"{int(date)} {month_names[int(month) - 1]} {year}"


def long_date_range(rows):
    """A saved output's first and last dates, written out in full."""
    firsts = []
    lasts = []
    for row in rows:
        if row.get("first_date"):
            firsts.append(row["first_date"])
        if row.get("last_date"):
            lasts.append(row["last_date"])
    return long_date(min(firsts)), long_date(max(lasts))


def data_entry_range(page, article_id):
    """The first and last day an article's Data entry gives."""
    found = re.search(
        rf"<dt>Data</dt>\s*<dd>({LONG_DATE}) to ({LONG_DATE})",
        article_text(page, article_id),
    )
    return found.group(1), found.group(2)


def whole_month_medians(q23_rows, fuel_group):
    """A fuel group's median gap in each of Q23's whole months, September
    2025 to August 2026, % of capacity."""
    values = []
    for row in item_rows(q23_rows, "1", "unit-months"):
        whole_month = "2025-09-01" <= row["settlement_month"] <= "2026-08-01"
        if row["fuel_group"] == fuel_group and whole_month:
            values.append(Decimal(row["median_absolute_pct"]))
    assert len(values) == 12
    return values


def highest_other_month(q23_rows):
    """The largest median gap of any fuel group but wind in a whole month."""
    groups = set()
    for row in q23_rows:
        if row["section"] == "1" and row["fuel_group"] != "wind":
            groups.add(row["fuel_group"])
    highest = Decimal(0)
    for fuel_group in groups:
        highest = max(highest, *whole_month_medians(q23_rows, fuel_group))
    return highest


def wind_output_held_day():
    """'24 November 2026': the day the published wind output's shortfall was
    held, from its comment line."""
    text = (REPOSITORY / "docs" / "results" / WIND_OUTPUT).read_text(encoding="utf-8")
    return re.search(rf"shortfall and wind level as held ({LONG_DATE});", text).group(1)


def page_number(value, places):
    """A value at a fixed number of decimals, halves rounded up, with a true
    minus sign and no plus sign, as the page writes a range's ends."""
    step = Decimal(1).scaleb(-places)
    return with_true_minus(str(Decimal(value).quantize(step, rounding=ROUND_HALF_UP)))


def test_the_findings_and_footer_take_their_figures_from_the_saved_outputs():
    q23 = saved_output_rows("q23_pn_accuracy_proxy.csv")
    q8_q28 = saved_output_rows("q8_q28_history.csv")
    q30 = saved_output_rows("q30_wind_vs_carbon_error.csv")
    reverse = saved_output_rows("q28_reverse_exploratory.csv")
    wind = whole_month_medians(q23, "wind")
    other_limit = next_half_above(highest_other_month(q23))
    demand_rho = item_row(
        q8_q28, "2", "rho_demand: abs(error_api_final) with abs(demand_error_final_mw)"
    )["value"]
    wind_rho = item_row(
        q30,
        "2",
        "rho_abs (primary): abs(wind error, outturn with cohort curtailment "
        "add-back) with abs(error_api_final)",
    )["value"]
    shortfall_rho = item_row(
        q8_q28, "2", "rho_shortfall: abs(error_api_final) with positive shortfall"
    )["value"]
    gap = item_row(q8_q28, "2", "gap, rho_demand minus rho_shortfall")["value"]
    gap_low = item_row(q8_q28, "2", "bootstrap 90% interval for the gap, low")["value"]
    gap_high = item_row(q8_q28, "2", "bootstrap 90% interval for the gap, high")[
        "value"
    ]
    wind_rho_low = item_row(q30, "2", "rho_abs (primary): bootstrap 90% interval, low")[
        "value"
    ]
    wind_rho_high = item_row(
        q30, "2", "rho_abs (primary): bootstrap 90% interval, high"
    )["value"]
    delivered_direction = item_row(
        q30,
        "4",
        "rho_signed (sensitivity): wind error, delivered outturn, with error_api_final",
    )["value"]
    added_back_direction = item_row(
        q30,
        "3",
        "rho_signed (secondary): wind error, outturn with cohort curtailment "
        "add-back, with error_api_final; expected direction negative",
    )["value"]
    ratio = Decimal(item_row(q8_q28, "3", "ratio, top over bottom")["value"])
    ratio_low = Decimal(
        item_row(q8_q28, "3", "bootstrap 90% interval for the ratio, low")["value"]
    )
    ratio_high = Decimal(
        item_row(q8_q28, "3", "bootstrap 90% interval for the ratio, high")["value"]
    )
    read = one_row(reverse, "section", "1")["item"]
    reverse_read_day = re.match(rf"read ({LONG_DATE}),", read).group(1)
    read_day = latest_day(
        reverse_read_day,
        strata_read_day(saved_output_rows(STRATA_RESULT)),
        status_read("Q1").partition(",")[0],
        status_read("Q10").partition(",")[0],
        # Every result's read day, so a result added later moves the stamp.
        *READ_DAYS.values(),
        wind_output_held_day(),
    )
    earliest = earliest_stated_read(PAGE.read_text(encoding="utf-8"))

    expected = [
        f"the median wind farm missed its plan by {tenths(min(wind))} to "
        f"{tenths(max(wind))}% of its capacity in every whole month from "
        "September 2025 to August 2026; the median unit of every other type, "
        f"by under {other_limit}%.",
        '<p class="answer"><strong>Wind strays most.</strong> With no NESO '
        "instruction in force, the median wind farm missed its plan by "
        f"<strong>{tenths(min(wind))} to {tenths(max(wind))}%</strong> of its "
        "capacity in every whole month; the median unit of every other type, by "
        f"under {other_limit}%.</p>",
        f"the median unit of every other type, by under {other_limit}%. In "
        "September 2026",
        "Over the same dates, misses in NESO's demand and wind forecasts have "
        f"rank correlations of only {hundredths(demand_rho)}{details_link('Q8')} "
        f"and {hundredths(wind_rho)}{details_link('Q30')} with the size of the "
        "carbon miss.</li>",
        "<strong>No clear difference: both links are very weak.</strong> The "
        f"rank correlations are <strong>{hundredths(demand_rho)}</strong> for "
        f"demand and {hundredths(shortfall_rho)} for under-delivery, and the gap "
        f"between them ({page_number(gap, 2)}, 90% range {page_number(gap_low, 2)} "
        f"to {page_number(gap_high, 2)}) cannot be told apart from zero.</p>",
        "<strong>Barely, in size.</strong> The sizes of the two misses have a "
        f"rank correlation of <strong>{hundredths(wind_rho)}</strong> (90% range "
        f"{page_number(wind_rho_low, 2)} to {page_number(wind_rho_high, 2)}). In "
        "direction the link is weak and its sign depends on the measure: "
        f"{signed_to(delivered_direction, 2)} on delivered wind alone, "
        f"{signed_to(added_back_direction, 2)} with turned-down wind added "
        "back.</p>",
        "In the fifth of half-hours with the biggest carbon misses (22 Aug 2025 – "
        "16 Aug 2026), under-delivery was only "
        f"{whole((ratio - 1) * 100)}% larger than in the fifth with the smallest "
        f"(90% range {whole((ratio_low - 1) * 100)}% to "
        f"{whole((ratio_high - 1) * 100)}%).{details_link('Q28')} Over the same",
        f"rank correlation of {hundredths(wind_rho)}, inside the ±0.2 written down "
        "as expected before the read. The direction,",
        f"Results read between {earliest.day} {earliest:%B} and {read_day}, from "
        "GridSkew",
    ]
    assert missing_phrases(expected) == []
    assert read_day.endswith(f" {earliest.year}")


def earliest_stated_read(page):
    """The earliest read date the page states for any result: the "Read ..."
    lines of the method blocks and the notes' "read on ..." and "read once
    on ..."."""
    stated = re.compile(rf"\b[Rr]ead (?:once )?(?:on )?({LONG_DATE})")
    days = []
    for found in stated.finditer(page):
        days.append(datetime.strptime(found.group(1), "%d %B %Y"))
    return min(days)


Q5_WITH_NOTICE = ("planned", "unplanned", "both")
Q5_CLASSES = (*Q5_WITH_NOTICE, "none")


def with_notice_and_total(rows):
    """Under-delivery with a notice live, and all of it, MWh, over the rows'
    outage classes: the page's 24% is the first over the second for all
    units."""
    with_notice = Decimal(0)
    total = Decimal(0)
    for row in rows:
        if row["item"] not in Q5_CLASSES:
            continue
        total += Decimal(row["positive_shortfall_mwh"])
        if row["item"] in Q5_WITH_NOTICE:
            with_notice += Decimal(row["positive_shortfall_mwh"])
    return with_notice, total


def q5_rows(rows, section, fuel_group):
    found = []
    for row in rows:
        if row["section"] == section and row["fuel_group"] == fuel_group:
            found.append(row)
    return found


FRACTION_WORDS = {3: "third", 4: "quarter", 5: "fifth", 10: "tenth"}


def group_with_most(rows, column):
    """The fuel group with the largest value in a column."""
    largest = rows[0]
    for row in rows:
        if Decimal(row[column]) > Decimal(largest[column]):
            largest = row
    return largest["fuel_group"]


def test_q5s_nuclear_figures_come_from_its_saved_output():
    q5 = saved_output_rows("q5_shortfall_with_known_outage.csv")
    all_with_notice, all_total = with_notice_and_total(q5_rows(q5, "1", ""))
    nuclear_with_notice, nuclear_total = with_notice_and_total(
        q5_rows(q5, "2", "nuclear")
    )
    share_of_notices = percent_of(nuclear_with_notice, all_with_notice, 0)
    share_without = percent_of(
        all_with_notice - nuclear_with_notice, all_total - nuclear_total, 0
    )
    all_share = percent_of(all_with_notice, all_total, 0)
    by_group = saved_output_rows("q5_under_delivery_by_outage_notice.csv")
    wind = one_row(by_group, "fuel_group", "wind")
    wind_one_in = int(whole(100 / Decimal(wind["with_notice_pct"])))

    page = PAGE.read_text(encoding="utf-8")

    assert answer_of(page, "q5") == (
        f"<strong>Mostly not.</strong> About a quarter (<strong>{all_share}%"
        "</strong>) of under-delivery came with a published outage; for wind, "
        f"the largest part, only about a {FRACTION_WORDS[wind_one_in]} did."
    )
    assert group_with_most(by_group, "total_mwh") == "wind"
    assert (
        f"<dd>Nuclear is {share_of_notices}% of the under-delivery that came with "
        f"a notice; without it, the share falls from {all_share}% to "
        f"{share_without}%. Nuclear's 93% reflects standing notices of partial "
        "derating, not outages the plan missed.</dd>" in fold_outs(page, "q5")
    )
    assert all_total == Decimal(item_row(q5, "1", "total")["positive_shortfall_mwh"])


def test_q5s_wind_and_outside_wind_figures_come_from_its_saved_output():
    q5 = saved_output_rows("q5_shortfall_with_known_outage.csv")
    all_with_notice, all_total = with_notice_and_total(q5_rows(q5, "1", ""))
    wind_with_notice, wind_total = with_notice_and_total(q5_rows(q5, "2", "wind"))
    outside_with_notice = all_with_notice - wind_with_notice
    outside_total = all_total - wind_total
    outside_share = percent_of(outside_with_notice, outside_total, 0)
    by_group = saved_output_rows("q5_under_delivery_by_outage_notice.csv")
    wind_row = one_row(by_group, "fuel_group", "wind")

    page = PAGE.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    assert wind_total == Decimal(wind_row["total_mwh"])
    assert wind_total * 2 > all_total
    assert (
        f"Wind is over half of all under-delivery ({twh(wind_total)} of "
        f"{twh(all_total)} TWh), and only about a tenth of wind's came with one; "
        "outside wind, "
        f"{outside_share}% did."
    ) in without_asterisks(page)
    assert (
        f"<dd>Wind is {twh(wind_total)} of the {twh(all_total)} TWh of "
        f"under-delivery, and {twh(wind_with_notice)} TWh of wind's "
        f"({tenths(wind_row['with_notice_pct'])}%) came with a published outage. "
        f"Outside wind, {outside_share}% did: the {twh(all_with_notice)} TWh that "
        f"came with a notice less wind's {twh(wind_with_notice)} TWh, "
        f"{twh(outside_with_notice)} TWh, over the {twh(all_total)} TWh less "
        f"wind's {twh(wind_total)} TWh, {twh(outside_total)} TWh. Coal and "
        "interconnectors count outside wind.</dd>"
    ) in fold_outs(page, "q5")
    assert (
        f"Wind is over half of all under-delivery ({twh(wind_total)} of "
        f"{twh(all_total)} TWh), and only about a tenth of wind's came with one; "
        f"outside wind, {outside_share}% did"
    ) in flattened(readme)


def test_q6s_on_the_hour_share_comes_from_its_context_count():
    alignment = saved_output_rows("context_q6_start_alignment.csv")
    unplanned = one_row(
        alignment, "item", "class (first publication): Unplanned (as read)"
    )
    lag = saved_output_rows("q6_publication_lag.csv")
    lag_unplanned = one_row(item_rows(lag, "1", ""), "outage_class", "Unplanned")
    on_the_hour = int(unplanned["on_half_hour"])
    notices = int(unplanned["notices"])
    page = PAGE.read_text(encoding="utf-8")
    article = fold_outs(page, "q6")

    assert (
        f"{percent_of(on_the_hour, notices, 0)}% ({on_the_hour:,} of {notices:,}) "
        "give a start exactly on the hour or half-hour, so this is not the time "
        "from failure to notice." in article
    )
    assert (
        "<p>The written start is often a round time, so this is not the time "
        "from failure to notice.</p>" in in_view(page, "q6")
    )
    assert Decimal(on_the_hour) / notices > Decimal("0.5")
    assert int(lag_unplanned["notices"]) == notices
    assert '<a href="results/context_q6_start_alignment.csv">context data</a>' in (
        article
    )


Q25_OTHER = "fuel type OTHER"
Q25_OUTSIDE = "outside the cohort"


def notices_median_and_p90_by_class(rows):
    """Each outage class's notices, median and 90th-percentile lag, as
    saved, from section 1."""
    by_class = {}
    for row in rows:
        if row["section"] != "1":
            continue
        by_class[row["outage_class"]] = (
            row["notices"],
            row["median_lag_minutes"],
            row["p90_lag_minutes"],
        )
    return by_class


def test_q6s_lag_quartiles_rebuild_the_saved_medians_and_90th_percentiles():
    quartiles = saved_output_rows("context_q6_lag_quartiles.csv")
    lag = saved_output_rows("q6_publication_lag.csv")

    assert notices_median_and_p90_by_class(quartiles) == {
        "Planned": ("8545", "-1362.9", "-4.9"),
        "Unplanned": ("11811", "0.9", "29.9"),
        "": ("12", "-6090.5", "20.4"),
    }
    assert notices_median_and_p90_by_class(
        quartiles
    ) == notices_median_and_p90_by_class(lag)


def test_q6s_planned_middle_half_comes_from_its_lag_quartiles():
    quartiles = saved_output_rows("context_q6_lag_quartiles.csv")
    planned = one_row(quartiles, "outage_class", "Planned")
    latest_hours = tenths(-Decimal(planned["p75_lag_minutes"]) / 60)
    earliest_hours = whole(-Decimal(planned["p25_lag_minutes"]) / 60)
    article = fold_outs(PAGE.read_text(encoding="utf-8"), "q6")

    assert (
        "For planned notices, the middle half are published "
        f"between about {latest_hours} and {earliest_hours} hours before the "
        "written start.</dd>"
    ) in article
    assert '<a href="results/context_q6_lag_quartiles.csv">' in article


BATTERY_CLASS = "battery by name or ID"
Q25_UNIT_CLASSES = (BATTERY_CLASS, "solar by name", "other or unnamed")


def battery_count_row(rows, section, item, unit_class):
    found = []
    for row in rows:
        same_item = row["item"] == item and row["unit_class"] == unit_class
        if row["section"] == section and same_item:
            found.append(row)
    assert len(found) == 1
    return found[0]


def class_total(rows, item):
    """The acceptances of one membership summed over its unit classes."""
    total = 0
    for unit_class in Q25_UNIT_CLASSES:
        total += int(battery_count_row(rows, "1", item, unit_class)["acceptances"])
    return total


def q25_count(rows, section, fuel_group, measure):
    """The n of one row of Q25's saved output."""
    found = []
    for row in rows:
        same_measure = row["fuel_group"] == fuel_group and row["measure"] == measure
        if row["section"] == section and same_measure:
            found.append(row)
    assert len(found) == 1
    return int(found[0]["n"])


def q25_cohort_acceptances(rows):
    """Acceptances of every cohort fuel group, all months, from Q25's saved
    output."""
    total = 0
    for row in rows:
        acceptances = row["measure"] == "1 dispatch granularity (acceptances)"
        if row["section"] == "2" and acceptances:
            total += int(row["n"])
    return total


def test_q25s_battery_shares_come_from_their_context_count():
    counts = saved_output_rows("context_q25_battery_named_units.csv")
    q25 = saved_output_rows("q25_instruction_fragmentation_corrected.csv")
    other_battery = int(
        battery_count_row(counts, "1", Q25_OTHER, BATTERY_CLASS)["acceptances"]
    )
    other = int(battery_count_row(counts, "2", Q25_OTHER, "all classes")["acceptances"])
    outside_battery = int(
        battery_count_row(counts, "1", Q25_OUTSIDE, BATTERY_CLASS)["acceptances"]
    )
    outside = int(
        battery_count_row(counts, "2", Q25_OUTSIDE, "all classes")["acceptances"]
    )
    other_fuel_types = int(
        battery_count_row(counts, "2", "cohort, other fuel types", "all classes")[
            "acceptances"
        ]
    )
    other_in_q25 = q25_count(q25, "2", "other", "1 dispatch granularity (acceptances)")
    outside_in_q25 = q25_count(q25, "3", "", "acceptances of units outside the cohort")
    page = PAGE.read_text(encoding="utf-8")
    article = article_text(page, "q25")

    assert (
        "Units identified as batteries by name or ID receive "
        f"{percent_of(other_battery, other, 0)}% ({other_battery:,} of {other:,}) "
        "of the other group's instructions."
    ) in article
    assert (
        "of the instructions to them, units identified as batteries by name or ID "
        f"receive {percent_of(outside_battery, outside, 0)}% "
        f"({outside_battery:,} of {outside:,})."
    ) in article
    assert other == other_in_q25 == class_total(counts, Q25_OTHER)
    assert outside == outside_in_q25 == class_total(counts, Q25_OUTSIDE)
    assert other + other_fuel_types == q25_cohort_acceptances(q25)
    assert (
        "units identified as batteries by name or ID carry "
        f"{percent_of(other_battery, other, 0)}% of its instructions."
    ) in page
    assert '<a href="results/context_q25_battery_named_units.csv">' in article


def test_q25s_check_against_elexons_flag_comes_from_its_check_counts():
    checks = saved_output_rows("context_q25_cadl_check.csv")
    shown = one_row(
        checks, "item", "rerun of 25 October 2026: corrected query, the figures shown"
    )
    compared = int(shown["acceptances_compared"])
    first_year, first_month, first_day = shown["first_settlement_date"].split("-")
    last_year, last_month, last_day = shown["last_settlement_date"].split("-")
    article = article_text(PAGE.read_text(encoding="utf-8"), "q25")

    month_name = datetime(int(last_year), int(last_month), 1).strftime("%B")

    assert (first_year, first_month) == (last_year, last_month)
    assert (
        f"which this count matched on all {compared:,} checked ({int(first_day)} to "
        f"{int(last_day)} {month_name} {last_year})."
    ) in article
    assert int(shown["agree"]) == compared
    assert '<a href="results/context_q25_cadl_check.csv">' in article


def context_count_text(name):
    """A committed counts-only context file, without its two comment lines:
    the attribution and the time of the read-only query."""
    text = (REPOSITORY / "docs" / "results" / name).read_text(encoding="utf-8")
    attribution, counted, rows = text.split("\n", 2)
    assert attribution + "\n" == BMRS_LINE
    return counted, rows


def context_count_rows(name):
    counted, rows = context_count_text(name)
    return list(csv.DictReader(io.StringIO(rows)))


def counted_at(name):
    """'7 November 2026, 16:59 UTC': when a context file's counts were taken,
    from its second comment line."""
    counted, rows = context_count_text(name)
    found = re.match(
        rf"# Counts as held at a read-only query on ({LONG_DATE}, \d\d:\d\d UTC)",
        counted,
    )
    return found.group(1)


def days_from_first_to_last(row):
    first = datetime.strptime(row["first_date"], "%Y-%m-%d").date()
    last = datetime.strptime(row["last_date"], "%Y-%m-%d").date()
    days = []
    while first <= last:
        days.append(first)
        first += timedelta(days=1)
    return days


def joined_spans(days):
    """Sorted [first, last] spans of consecutive days."""
    spans = []
    for day in sorted(days):
        if spans and spans[-1][1] + timedelta(days=1) == day:
            spans[-1][1] = day
            continue
        spans.append([day, day])
    return spans


def date_spans_by_run(rows, run_column):
    """Per run, its spans of settlement dates, joining rows whose dates meet
    or overlap."""
    days_by_run = {}
    for row in rows:
        days = days_by_run.get(row[run_column], set())
        days.update(days_from_first_to_last(row))
        days_by_run[row[run_column]] = days
    spans = {}
    for run, days in days_by_run.items():
        spans[run] = joined_spans(days)
    return spans


def day_month_year(day):
    return f"{day.day} {day:%B %Y}"


def day_month(day):
    return f"{day.day} {day:%B}"


def day_after(day):
    return day + timedelta(days=1)


def held_at_the_read(rows):
    """Q23's runs-by-month rows that held a meter figure at the read."""
    held = []
    for row in rows:
        if row["run_at_read"] != "(none at the read)":
            held.append(row)
    return held


def runs_now_where_the_read_held_ii(rows):
    runs = set()
    for row in rows:
        if row["run_at_read"] == "II":
            runs.add(row["run_held"])
    return runs


def test_q23s_settlement_runs_come_from_its_runs_by_month_count():
    name = "context_q23_runs_by_month.csv"
    rows = context_count_rows(name)
    spans = date_spans_by_run(held_at_the_read(rows), "run_at_read")
    [[r3_start, r3_end]] = spans["R3"]
    [[r2_start, r2_end]] = spans["R2"]
    [[r1_start, r1_end], [r1_week_start, r1_week_end]] = spans["R1"]
    [
        [sf_start, sf_end],
        [sf_second_start, sf_second_end],
        [sf_third_start, sf_third_end],
    ] = spans["SF"]
    [[ii_day, ii_day_end], [ii_start, ii_end]] = spans["II"]

    assert sorted(spans) == ["II", "R1", "R2", "R3", "SF"]
    assert r2_start == day_after(r3_end)
    assert r1_start == day_after(r2_end)
    assert ii_day == ii_day_end
    assert runs_now_where_the_read_held_ii(rows) == {"SF"}
    assert (
        "At the read, the meter figures rest on the seven-month run (R3) from "
        f"{day_month_year(r3_start)} to {day_month_year(r3_end)}, the "
        f"four-month run (R2) to {day_month(r2_end)}, the two-month run (R1) "
        f"to {day_month(r1_end)} and for {r1_week_start.day} to "
        f"{day_month(r1_week_end)}, the three-week run (SF) from "
        f"{day_month(sf_start)} to {day_month(sf_end)}, from "
        f"{day_month(sf_second_start)} to {day_month(sf_second_end)} and on "
        f"{sf_third_start.day} and {day_month(sf_third_end)}, and the one-week "
        f"run (II) on {day_month(ii_day)} and from {ii_start.day} to "
        f"{day_month_year(ii_end)}. The three-week run for "
        f"{day_month(ii_day)} and {ii_start.day} to {day_month(ii_end)} "
        "arrived after the read. "
        f'<a href="results/{name}">Runs by month</a>, counted on '
        f"{counted_at(name)}: the unit half-hour counts are as held then, not "
        "at the read.</dd>" in article_text(PAGE.read_text(encoding="utf-8"), "q23")
    )


def read_day(name):
    """'7 November 2026': the day a context file's counts were read, from its
    second comment line."""
    text = (REPOSITORY / "docs" / "results" / name).read_text(encoding="utf-8")
    basis = text.split("\n")[1]
    return re.match(rf"# Read ({LONG_DATE})", basis).group(1)


def summed(rows, column):
    total = Decimal(0)
    for row in rows:
        total += Decimal(row[column])
    return total


def section_rows(rows, section):
    found = []
    for row in rows:
        if row["section"] == section:
            found.append(row)
    return found


def test_q5s_drawing_power_share_comes_from_its_breakdown():
    breakdown = saved_output_rows("context_q5_drawing_power.csv")
    q5 = saved_output_rows("q5_shortfall_with_known_outage.csv")
    total = one_row(breakdown, "section", "2")
    under_delivery = Decimal(total["under_delivery_mwh"])
    drawing_power = Decimal(total["drawing_power_expected_at_or_below_zero_mwh"])
    published = item_row(q5, "1", "total")
    groups = section_rows(breakdown, "1")
    article = fold_outs(PAGE.read_text(encoding="utf-8"), "q5")

    assert (
        f"<dd>About {percent_of(drawing_power, under_delivery, 0)}% of "
        f"this under-delivery ({tenths(drawing_power / 1_000_000)} of "
        f"{tenths(under_delivery / 1_000_000)} TWh) comes from units drawing "
        "power in half-hours when their expected output was zero or below"
    ) in article
    assert under_delivery == Decimal(published["positive_shortfall_mwh"])
    assert total["unit_periods"] == published["unit_periods"]
    assert summed(groups, "under_delivery_mwh") == under_delivery
    assert summed(groups, "drawing_power_expected_at_or_below_zero_mwh") == (
        drawing_power
    )
    assert '<a href="results/context_q5_drawing_power.csv">' in article


def zero_reading_row(rows, section, item, fuel_group):
    found = []
    for row in rows:
        same_item = row["item"] == item and row["fuel_group"] == fuel_group
        if row["section"] == section and same_item:
            found.append(row)
    assert len(found) == 1
    return found[0]


def stored_row_checks(rows):
    """Per span of section 3: the zero readings whose stored one-week row
    holds 0, and the model's one-week values with no stored row."""
    checks = {}
    for row in section_rows(rows, "3"):
        held = checks.get(row["item"], {})
        held[row["detail"]] = int(row["unit_periods"])
        checks[row["item"]] = held
    return checks


def test_q5s_one_week_zeros_come_from_their_count():
    name = "context_ii_zero_readings.csv"
    zeros = saved_output_rows(name)
    q5 = saved_output_rows("q5_shortfall_with_known_outage.csv")
    gas = zero_reading_row(zeros, "2", "Q5", "gas_turbine")
    every_group = zero_reading_row(zeros, "2", "Q5", "")
    on_the_one_week_run = item_row(q5, "3", "population by metered run: II")
    checks = stored_row_checks(zeros)
    from_17_august = checks["2026-08-17 to 2026-09-27"]
    article = fold_outs(PAGE.read_text(encoding="utf-8"), "q5")

    assert (
        f"In those days, {gas['units']} gas units read exactly zero on the "
        f"one-week run in {int(gas['zero_readings']):,} unit half-hours that the "
        "three-week run, which arrived after the read, shows as not zero; "
        "Elexon's one-week data, as stored, hold these zeros "
        f'(<a href="results/{name}">zero readings</a>).</dd>'
    ) in article
    assert every_group["unit_periods"] == on_the_one_week_run["unit_periods"]
    assert (
        int(gas["zero_readings"])
        <= (from_17_august["zero readings whose stored one-week row holds 0"])
    )
    for held in checks.values():
        assert held["model one-week values with no stored one-week row"] == 0


def september_row(rows, section, fuel_group):
    found = []
    for row in rows:
        september = row["settlement_month"] == "2026-09-01"
        if row["section"] == section and row["fuel_group"] == fuel_group and september:
            found.append(row)
    assert len(found) == 1
    return found[0]


def test_q23s_september_rows_on_the_data_held_at_the_read_equal_its_output():
    runs = saved_output_rows("context_q23_one_week_and_later_runs.csv")
    q23 = saved_output_rows("q23_pn_accuracy_proxy.csv")
    checks = section_rows(runs, "2")

    assert len(checks) == 8
    for check in checks:
        saved = september_row(q23, "1", check["fuel_group"])
        assert (
            check["unit_months"],
            check["half_hours"],
            check["median_absolute_pct_at_read"],
            check["p90_absolute_pct_at_read"],
        ) == (
            saved["units"],
            saved["half_hours"],
            saved["median_absolute_pct"],
            saved["p90_absolute_pct"],
        )


def test_q23s_one_week_run_entry_comes_from_its_counts():
    zeros_name = "context_ii_zero_readings.csv"
    runs_name = "context_q23_one_week_and_later_runs.csv"
    zeros = saved_output_rows(zeros_name)
    runs = saved_output_rows(runs_name)
    q23 = saved_output_rows("q23_pn_accuracy_proxy.csv")
    gas = zero_reading_row(zeros, "2", "Q23", "gas_turbine")
    every_group = zero_reading_row(zeros, "2", "Q23", "")
    on_the_one_week_run = item_row(q23, "3", "population by metered run: II")
    wind = september_row(runs, "1", "wind")
    bioenergy = september_row(runs, "1", "bioenergy")
    gas_measure = september_row(runs, "1", "gas_turbine")
    wind_latest = Decimal(wind["median_absolute_pct_latest"])
    bioenergy_latest = Decimal(bioenergy["median_absolute_pct_latest"])
    article = article_text(PAGE.read_text(encoding="utf-8"), "q23")

    assert (
        f"<dd>At the read, {gas['units']} gas units read exactly zero on the "
        f"one-week run in {int(gas['zero_readings']):,} unit half-hours that the "
        f"three-week run shows as not zero ({int(every_group['zero_readings']):,} "
        f"of the {int(every_group['unit_periods']):,} unit half-hours then on the "
        "one-week run, all groups together). On the September unit half-hours "
        "that have both runs, the "
        "three-week run leaves wind's and bioenergy's medians above their "
        f"whole-month ranges ({tenths(wind_latest)}% and "
        f"{tenths(bioenergy_latest)}% of capacity, against "
        f"{tenths(wind['median_absolute_pct_one_week'])}% and "
        f"{tenths(bioenergy['median_absolute_pct_one_week'])}% on the one-week "
        "run) and lowers gas's 90th percentile from "
        f"{tenths(gas_measure['p90_absolute_pct_one_week'])}% to "
        f"{tenths(gas_measure['p90_absolute_pct_latest'])}%. Counted on "
        f"{read_day(runs_name)} on the data held at the read: "
        f'<a href="results/{zeros_name}">zero readings</a>, '
        f'<a href="results/{runs_name}">one-week and later runs</a>.</dd>'
    ) in article
    assert every_group["unit_periods"] == on_the_one_week_run["half_hours"]
    assert read_day(zeros_name) == read_day(runs_name)
    assert wind_latest > max(whole_month_medians(q23, "wind"))
    assert bioenergy_latest > max(whole_month_medians(q23, "bioenergy"))


def q22_first_day():
    first_day, rest = Q2_WEEK.split("–")
    last_day, month, year = rest.split(" ")
    return datetime.strptime(f"{first_day} {month} {year}", "%d %b %Y").date()


def zero_readings_by_week(rows):
    by_week = {}
    for row in section_rows(rows, "1"):
        week = datetime.strptime(row["item"], "%Y-%m-%d").date()
        by_week[week] = int(row["zero_readings"])
    return by_week


def split_at_week(by_week, change_week):
    """The weekly counts before the change week, and from it on."""
    before = []
    after = []
    for week, zero_readings in by_week.items():
        if week < change_week:
            before.append(zero_readings)
            continue
        after.append(zero_readings)
    return before, after


def test_q22s_week_is_the_last_before_the_one_week_runs_change():
    name = "context_ii_zero_readings.csv"
    by_week = zero_readings_by_week(saved_output_rows(name))
    q22_week = q22_first_day()
    change_week = q22_week + timedelta(days=7)
    before, after = split_at_week(by_week, change_week)
    last_day = max(by_week) + timedelta(days=6)
    article = fold_outs(PAGE.read_text(encoding="utf-8"), "q3")

    assert (
        "This answer covers only that week, the last before a change in the "
        "one-week run: gas units read exactly zero on it, and not zero on a later "
        f"run, in {by_week[q22_week]:,} unit half-hours that week and in "
        f"{min(after):,} to {max(after):,} in each week from "
        f"{day_month(change_week)} to {day_month_year(last_day)} "
        f'(<a href="results/{name}">zero readings by week</a>). Recorded'
    ) in article
    assert max(before) < min(after)
    assert last_day.year == change_week.year


def test_q6s_early_and_late_notices_come_from_their_timing_count():
    timing = saved_output_rows("context_q6_publication_timing.csv")
    lag = saved_output_rows("q6_publication_lag.csv")
    unplanned = one_row(timing, "outage_class", "Unplanned")
    notices = int(unplanned["notices"])
    before = int(unplanned["before_start"])
    late = int(unplanned["over_1h_after_start"])
    one_in = int(whole(Decimal(notices) / late))
    article = fold_outs(PAGE.read_text(encoding="utf-8"), "q6")

    assert (
        f"About half of the unplanned notices ({before:,} of {notices:,}) were "
        "published before their written start; about one in "
        f"{NUMBER_WORDS[one_in]} ({late:,}) more than an hour after it. For "
        "planned notices,"
    ) in article
    assert tenths(Decimal(before) / notices) == "0.5"
    assert notices == int(
        one_row(item_rows(lag, "1", ""), "outage_class", "Unplanned")["notices"]
    )
    for row in timing:
        at_or_after = int(row["at_start"]) + int(row["after_start"])
        assert int(row["before_start"]) + at_or_after == int(row["notices"])
    assert '<a href="results/context_q6_publication_timing.csv">' in article


def allocated_share(rows, question):
    """The percentage of a question's half-hours at unit types S and V."""
    allocated = 0
    for row in section_rows(rows, "1"):
        if row["question"] == question and row["unit_type"] in ("S", "V"):
            allocated += int(row["unit_periods"])
    return percent_of(allocated, question_total(rows, question), 0)


def question_total(rows, question):
    found = []
    for row in section_rows(rows, "1"):
        if row["question"] == question and row["unit_type"] == "all":
            found.append(int(row["unit_periods"]))
    assert len(found) == 1
    return found[0]


def q23_population_size(q23_rows):
    total = 0
    for row in section_rows(q23_rows, "3"):
        if row["item"].startswith("population by metered run: "):
            total += int(row["half_hours"])
    return total


def test_the_unit_glossary_states_the_supplier_and_virtual_unit_shares():
    name = "context_cohort_unit_types.csv"
    types = saved_output_rows(name)
    q3 = saved_output_rows("q3_metered_vs_notified.csv")
    q12 = saved_output_rows("q12_instructed_vs_residual.csv")
    q5 = saved_output_rows("q5_shortfall_with_known_outage.csv")
    q23 = saved_output_rows("q23_pn_accuracy_proxy.csv")
    page = PAGE.read_text(encoding="utf-8")

    assert (
        "Some of those are registered by suppliers or virtual lead parties: about "
        f"{allocated_share(types, 'Q3')}% of the half-hours behind "
        f"{named_result('Q3')}, {named_result('Q12')} and {named_result('Q5')}, "
        f"and {allocated_share(types, 'Q23')}% behind {named_result('Q23')}. "
        "Their metered figures are "
        "settlement allocations, not a station's meter "
        f'(<a href="results/{name}">unit types at each read</a>).</dd>'
    ) in page
    assert allocated_share(types, "Q5") == allocated_share(types, "Q3")
    assert question_total(types, "Q3") == summed(q3, "unit_periods")
    assert question_total(types, "Q3") == summed(q12, "unit_periods")
    assert question_total(types, "Q5") == int(
        item_row(q5, "1", "total")["unit_periods"]
    )
    assert question_total(types, "Q23") == q23_population_size(q23)


def gained_fuel_types(rows):
    """'15 other, 8 gas, 1 bioenergy': the fuel groups of the units that
    gained a fuel type, most units first, and how many units in all."""
    gained = []
    for row in section_rows(rows, "1"):
        if row["item"] == "gained a fuel type":
            gained.append((int(row["units"]), row["fuel_group"]))
    gained.sort(reverse=True)
    parts = []
    units = 0
    for count, fuel_group in gained:
        units += count
        parts.append(f"{count} {FUEL_GROUP_DISPLAY_NAMES[fuel_group].lower()}")
    return units, ", ".join(parts)


def test_q23s_fold_out_states_the_cohort_change_from_its_count():
    name = "context_cohort_change.csv"
    change = saved_output_rows(name)
    q23 = saved_output_rows("q23_pn_accuracy_proxy.csv")
    units, gained = gained_fuel_types(change)
    unchanged = one_row(change, "item", "lost or changed its fuel type")
    at_read = one_row(change, "item", "as held at the read")
    own_units = one_row(
        change, "detail", "units in the read's cohort, metered at the read"
    )
    metered_later = one_row(
        change, "detail", "units in the read's cohort, first metered after the read"
    )
    new_units = one_row(change, "detail", "units given a fuel type after the read")
    at_read_size = int(at_read["unit_periods"])
    added = int(new_units["unit_periods"])
    page = PAGE.read_text(encoding="utf-8")

    assert (
        "<dd>Since the read of 15 October 2026, the registry has given "
        f"{units} more units a fuel type ({gained}) and changed none. Counted on "
        f"{read_day(name)}, that read's half-hours would include {added:,} more at "
        f"{new_units['units']} of those units "
        f"({percent_of(added, at_read_size, 1)}%), and "
        f"{int(metered_later['unit_periods']):,} more at the read's own units, "
        "whose first meter figures arrived after the read "
        f'(<a href="results/{name}">cohort change</a>).</dd>'
    ) in fold_outs(page, "q23")
    assert unchanged["units"] == "0"
    assert at_read_size == int(own_units["unit_periods"]) == q23_population_size(q23)
    assert "Read 15 October 2026, 20:05 UTC." in article_text(page, "q23")
    assert "<li>Fuel groups are the registry's at each read.</li>" in page


def with_a_metered_value(rows):
    metered = []
    for row in rows:
        if row["run_held"] != "(no metered value)":
            metered.append(row)
    return metered


def test_q28s_settlement_runs_come_from_its_runs_held_count():
    name = "context_q28_runs_held.csv"
    spans = date_spans_by_run(
        with_a_metered_value(context_count_rows(name)), "run_held"
    )
    [[r3_start, r3_end]] = spans["R3"]
    [[r2_start, r2_end]] = spans["R2"]
    [[r1_start, r1_end], [r1_week_start, r1_week_end]] = spans["R1"]
    [[sf_start, sf_end]] = spans["SF"]

    assert sorted(spans) == ["R1", "R2", "R3", "SF"]
    assert r2_start == day_after(r3_end)
    assert r1_start == day_after(r2_end)
    assert (
        "<dd>The seven-month run (R3) from "
        f"{day_month_year(r3_start)} to {day_month_year(r3_end)}, the "
        f"four-month run (R2) to {day_month(r2_end)}, the two-month run (R1) "
        f"to {day_month(r1_end)} and for {r1_week_start.day} to "
        f"{day_month(r1_week_end)}, and the three-week run (SF) from "
        f"{day_month(sf_start)} to {day_month_year(sf_end)}. No settlement "
        "data for these dates arrived after the read. "
        f'<a href="results/{name}">Runs by month</a>, counted on '
        f"{counted_at(name)}.</dd>"
        in article_text(PAGE.read_text(encoding="utf-8"), "q28")
    )


ON_TIME_BAND = "up to 30 min after the half-hour ends"
OVER_AN_HOUR_BANDS = (
    "1 to 2 h after the half-hour ends",
    "over 2 h after the half-hour ends",
)


def lateness_counts(rows):
    """Q30's first wind outturns: all half-hours, those over 30 minutes late,
    those over an hour late and those that are zero, from the lateness
    bands; and the dates listed with a late or zero first outturn, with the
    half-hours over an hour late on them."""
    counts = {
        "all": 0,
        "late": 0,
        "over_an_hour": 0,
        "zero": 0,
        "on_dates": 0,
        "zero_on_dates": 0,
    }
    dates = []
    for row in rows:
        half_hours = int(row["half_hours"])
        if row["section"] == "2":
            dates.append(datetime.strptime(row["item"], "%Y-%m-%d").date())
            counts["on_dates"] += half_hours
            counts["zero_on_dates"] += int(row["zero_outturn"])
            continue
        counts["all"] += half_hours
        counts["zero"] += int(row["zero_outturn"])
        if row["item"] != ON_TIME_BAND:
            counts["late"] += half_hours
        if row["item"] in OVER_AN_HOUR_BANDS:
            counts["over_an_hour"] += half_hours
    return counts, dates


def days_and_months(days):
    written = []
    for day in days:
        written.append(day_month(day))
    return ", ".join(written)


def test_q30s_late_outturn_counts_come_from_its_lateness_count():
    name = "context_q30_outturn_lateness.csv"
    counts, dates = lateness_counts(context_count_rows(name))
    article = article_text(PAGE.read_text(encoding="utf-8"), "q30")

    assert counts["on_dates"] == counts["over_an_hour"]
    assert counts["zero_on_dates"] == counts["zero"]
    assert f"<dd>{counts['all']:,} half-hours on 360 days," in article
    assert (
        f"<dd>{counts['late']} first wind outturns were published more than 30 "
        f"minutes late, {counts['zero']} of them zero; those over an hour late "
        f"or zero all fall on {NUMBER_WORDS[len(dates)]} dates "
        f"({day_month_year(dates[0])}; {days_and_months(dates[1:-1])} and "
        f"{day_month_year(dates[-1])}); they are included in the correlations. "
        f'<a href="results/{name}">Lateness counts</a>, counted on '
        f"{counted_at(name)}; no wind outturn for these dates arrived after "
        "the read." in article
    )


WIND_MISS_COUNTS = "context_q30_wind_miss_sign.csv"
WIND_LEVEL_COUNTS = "context_q28_wind_level.csv"


def counts_day(name):
    """'7 November 2026': the day a counts file's read-only queries ran, from
    its basis line."""
    text = (REPOSITORY / "docs" / "results" / name).read_text(encoding="utf-8")
    basis = text.split("\n")[1]
    return re.match(
        rf"# Counts as held at (?:a )?read-only quer(?:y|ies) on ({LONG_DATE})", basis
    ).group(1)


def wind_miss_totals(rows):
    """Q30's half-hours summed over the months, by column."""
    totals = {}
    for column in (
        "half_hours",
        "add_back_below",
        "add_back_above",
        "no_wind_instruction",
        "no_instruction_delivered_below",
        "no_instruction_delivered_above",
    ):
        totals[column] = int(summed(rows, column))
    return totals


def monthly_delivered_below_shares(rows):
    shares = []
    for row in rows:
        shares.append(
            Decimal(100) * Decimal(row["delivered_below"]) / Decimal(row["half_hours"])
        )
    return shares


def wind_level_shares(rows, measure):
    """Per wind fifth, 1 (calmest) to 5, the exact share of its half-hours
    in the measure's top two fifths."""
    in_top_two = {}
    in_fifth = {}
    for row in rows:
        if row["measure"] != measure:
            continue
        wind_fifth = int(row["wind_fifth"])
        half_hours = int(row["half_hours"])
        in_fifth[wind_fifth] = in_fifth.get(wind_fifth, 0) + half_hours
        if int(row["measure_fifth"]) >= 4:
            in_top_two[wind_fifth] = in_top_two.get(wind_fifth, 0) + half_hours
    shares = []
    for wind_fifth in sorted(in_fifth):
        shares.append(
            Decimal(100)
            * Decimal(in_top_two[wind_fifth])
            / Decimal(in_fifth[wind_fifth])
        )
    return shares


def top_two_fifths_share(rows, measure):
    """The exact share of all half-hours in the measure's top two fifths."""
    in_top_two = 0
    total = 0
    for row in rows:
        if row["measure"] != measure:
            continue
        total += int(row["half_hours"])
        if int(row["measure_fifth"]) >= 4:
            in_top_two += int(row["half_hours"])
    return Decimal(100) * Decimal(in_top_two) / Decimal(total)


def rises_at_every_step(values):
    for earlier, later in zip(values, values[1:], strict=False):
        if later <= earlier:
            return False
    return True


def test_q30s_wind_note_comes_from_its_wind_miss_counts():
    rows = saved_output_rows(WIND_MISS_COUNTS)
    totals = wind_miss_totals(rows)
    shares = monthly_delivered_below_shares(rows)
    imbalance = abs(totals["add_back_below"] - totals["add_back_above"])

    assert len(rows) == 13
    assert Decimal(imbalance) / Decimal(totals["half_hours"]) < Decimal("0.05")
    assert (
        f"<dd>Counted on {counts_day(WIND_MISS_COUNTS)}, after the read: delivered "
        "wind, with nothing added back, was below the forecast point in "
        f"{whole(min(shares))}% to {whole(max(shares))}% of each month's "
        "half-hours. With the wind NESO turned down added back, as this result "
        "measures, the two are roughly balanced: below in "
        f"{totals['add_back_below']:,} of the {totals['half_hours']:,} half-hours "
        f"and above in {totals['add_back_above']:,}. Of the 5,705 half-hours "
        "with a forecast of 12 GW or more in which at least one of the studied "
        "wind units was instructed, wind with the add-back was above the "
        "forecast in 3,484 (61%), counted on 8 November 2026. In the "
        f"{totals['no_wind_instruction']:,} half-hours in which NESO instructed "
        "none of the studied wind units, delivered wind was still below the "
        f"forecast in {totals['no_instruction_delivered_below']:,} ("
        f"{percent_of(totals['no_instruction_delivered_below'], totals['no_wind_instruction'], 0)}%) "
        f"and above in {totals['no_instruction_delivered_above']:,}. A bias in the "
        "forecast, a difference in the wind farms each side covers (embedded "
        "wind, for example), instructions to wind units outside those studied "
        "and wind farms cutting their own output without an "
        "instruction could each produce this; these counts do not tell them "
        "apart, and neither Elexon nor NESO documents whether the forecast allows "
        f'for output turned down. <a href="results/{WIND_MISS_COUNTS}">Wind miss '
        "counts</a></dd>" in article_text(PAGE.read_text(encoding="utf-8"), "q30")
    )


def test_q28s_reverse_note_states_its_wind_level_counts():
    rows = saved_output_rows(WIND_LEVEL_COUNTS)
    dirtier = wind_level_shares(rows, "signed carbon miss")
    under = wind_level_shares(rows, "under-delivery")

    assert rises_at_every_step(dirtier)
    assert rises_at_every_step(under[:4])
    assert under[4] < under[3]
    dirtiest_share = whole(top_two_fifths_share(rows, "signed carbon miss"))
    most_under_share = whole(top_two_fifths_share(rows, "under-delivery"))
    assert (
        "Wind output is not held fixed, and both measures are associated with "
        "wind level. Sorting the same half-hours into five groups of almost "
        "equal size by metered wind, counted on "
        f"{counts_day(WIND_LEVEL_COUNTS)} with the units studied then: "
        f"{whole(dirtier[0])}% of the calmest group's half-hours are among the "
        f"{dirtiest_share}% of half-hours with the dirtiest misses against "
        "forecast, a share that rises with each group to "
        f"{whole(dirtier[4])}% of the windiest; {whole(under[0])}% of the calmest "
        f"group's half-hours are among the {most_under_share}% with the most "
        f"under-delivery, rising to {whole(under[3])}% of the "
        f"fourth group, then falling to {whole(under[4])}% of the windiest. These "
        "shares describe the counts; they are not a test. "
        f'<a href="results/{WIND_LEVEL_COUNTS}">Wind level counts</a></dd>'
        in exploratory_fold_out()
    )


def test_the_limitations_state_the_zero_actual_once_for_every_read_carbon_result():
    page = PAGE.read_text(encoding="utf-8")
    q8, q28, q29, q30 = [
        named_result(question_id) for question_id in READ_CARBON_RESULTS
    ]
    names = [q8, q28, WIND_RESULT, q29, q30]
    paired = (
        f'<li id="carbon-label-alignment">The carbon API\'s half-hours may not '
        f"line up with Elexon's. Four results ({q8}, {q28}, {WIND_RESULT} and "
        f"{q30}) pair each carbon value with the Elexon half-hour of the same "
        f"label, and {q29} sets the API's actual against its forecast"
    )
    zero_actual = (
        '<li id="impossible-actual">GridSkew\'s data hold one impossible carbon '
        "actual, 0 g/kWh for 6 August 2026, 11:00 UTC. It lies inside the window "
        f"of {NUMBER_WORDS[len(names)]} results ({', '.join(names[:-1])} and "
        f"{names[-1]}), in the August 2026 cell of the misses-by-month result.</li>"
    )

    assert zero_actual in limitations_section(page)
    assert paired in limitations_section(page)
    assert page.count("6 August 2026, 11:00 UTC") == 1


ALIGNMENT_COUNTS = "context_carbon_label_alignment.csv"
WIND_CLOCK = "1"
ACTUAL_WHOLE = "2"
ACTUAL_BY_SEASON = "3"
ACTUAL_BY_MONTH = "4"
SIGN_TEST = "6"
SAME_LABEL = "0"
HALF_HOUR_AFTER = "1"
READ_CARBON_RESULTS = ["Q8", "Q28", "Q29", "Q30"]


def alignment_file():
    """The committed alignment counts: the line giving their basis, and the
    rows below it."""
    text = (REPOSITORY / "docs" / "results" / ALIGNMENT_COUNTS).read_text(
        encoding="utf-8"
    )
    attribution, basis, rows = text.split("\n", 2)
    assert attribution + "\n" == ALIGNMENT_LINE
    assert basis.startswith("# Measured alignment, not a documented convention.")
    return {"basis": basis, "rows": list(csv.DictReader(io.StringIO(rows)))}


def alignment_rows():
    return alignment_file()["rows"]


def alignment_window():
    """The first and last settlement dates the basis line gives."""
    found = re.search(
        rf"settlement dates ({LONG_DATE}) to ({LONG_DATE})", alignment_file()["basis"]
    )
    return found.group(1), found.group(2)


def by_offset(rows, section, group):
    """One comparison's rows, by offset in half-hours."""
    found = {}
    for row in rows:
        if row["section"] == section and row["group"] == group:
            found[row["offset_half_hours"]] = row
    return found


def groups_in(rows, section):
    groups = []
    for row in rows:
        if row["section"] == section and row["group"] not in groups:
            groups.append(row["group"])
    return groups


def strictly_best_offset(values, higher_is_better):
    """The offset whose value beats every other offset's; None on a tie."""
    ranked = sorted(values, key=values.get, reverse=higher_is_better)
    best = ranked[0]
    runner_up = ranked[1]
    if values[best] == values[runner_up]:
        return None
    return best


def column_values(offsets, column):
    values = {}
    for offset, row in offsets.items():
        values[offset] = Decimal(row[column])
    return values


def opposite_shares(offsets):
    """Each offset's share of moved half-hours whose step went the opposite
    way to the wind step, exact."""
    shares = {}
    for offset, row in offsets.items():
        shares[offset] = Decimal(row["opposite_sign"]) / Decimal(row["common_rows"])
    return shares


def offset_with_highest(offsets, column):
    return strictly_best_offset(column_values(offsets, column), True)


def offset_with_lowest(offsets, column):
    return strictly_best_offset(column_values(offsets, column), False)


def groups_not_best_after_the_label(rows, section, highest, lowest):
    """The groups of a section in which a measure does not peak strictly one
    half-hour after the label: the highest for each column in highest, the
    lowest for each column in lowest."""
    not_best = []
    for group in groups_in(rows, section):
        offsets = by_offset(rows, section, group)
        best = set()
        for column in highest:
            best.add(offset_with_highest(offsets, column))
        for column in lowest:
            best.add(offset_with_lowest(offsets, column))
        if best != {HALF_HOUR_AFTER}:
            not_best.append(group)
    return not_best


def share_of_common(row, column):
    return percent_of(row[column], row["common_rows"], 1)


def step_pair(rows, section, group, first_offset, second_offset):
    """A comparison's correlation of half-hour changes at two offsets, as the
    page gives them."""
    offsets = by_offset(rows, section, group)
    return (
        hundredths(offsets[first_offset]["step_correlation"]),
        hundredths(offsets[second_offset]["step_correlation"]),
    )


def wind_clock_row(rows):
    return by_offset(rows, WIND_CLOCK, "all")[SAME_LABEL]


def limitations_section(page):
    return page.split('<section id="limitations"', 1)[1].split("</section>", 1)[0]


def flattened(text):
    return " ".join(text.split())


def sections_without(questions, question_ids, phrase):
    missing = []
    for question_id in question_ids:
        if phrase not in flattened(register_section(questions, question_id)):
            missing.append(question_id)
    return missing


def articles_without(page, article_ids, phrase):
    missing = []
    for article_id in article_ids:
        if phrase not in article_text(page, article_id):
            missing.append(article_id)
    return missing


def test_the_api_actual_agrees_best_one_half_hour_after_its_label():
    rows = alignment_rows()
    whole = by_offset(rows, ACTUAL_WHOLE, "all")

    assert offset_with_highest(whole, "exactly_equal") == HALF_HOUR_AFTER
    assert offset_with_highest(whole, "within_5") == HALF_HOUR_AFTER
    assert offset_with_lowest(whole, "mean_abs_diff") == HALF_HOUR_AFTER
    assert offset_with_highest(whole, "step_correlation") == HALF_HOUR_AFTER
    assert groups_in(rows, ACTUAL_BY_SEASON) == ["BST", "GMT"]
    assert (
        groups_not_best_after_the_label(
            rows, ACTUAL_BY_SEASON, ["within_5", "step_correlation"], []
        )
        == []
    )
    assert (
        groups_not_best_after_the_label(
            rows, ACTUAL_BY_MONTH, ["within_5"], ["mean_abs_diff"]
        )
        == []
    )
    assert (
        offset_with_highest(by_offset(rows, WIND_CLOCK, "all"), "exactly_equal")
        == SAME_LABEL
    )


def test_the_sign_test_peaks_after_the_label_for_the_api_and_at_it_for_nesos_mix():
    rows = alignment_rows()
    api = opposite_shares(by_offset(rows, SIGN_TEST, "API actual"))
    neso = opposite_shares(by_offset(rows, SIGN_TEST, "NESO historic generation mix"))

    assert strictly_best_offset(api, True) == HALF_HOUR_AFTER
    assert strictly_best_offset(neso, True) == SAME_LABEL


def test_the_carbon_alignment_limitation_comes_from_its_alignment_counts():
    rows = alignment_rows()
    wind = wind_clock_row(rows)
    whole = by_offset(rows, ACTUAL_WHOLE, "all")
    after = whole[HALF_HOUR_AFTER]
    same = whole[SAME_LABEL]
    whole_after, whole_same = step_pair(
        rows, ACTUAL_WHOLE, "all", HALF_HOUR_AFTER, SAME_LABEL
    )
    gmt_after, gmt_same = step_pair(
        rows, ACTUAL_BY_SEASON, "GMT", HALF_HOUR_AFTER, SAME_LABEL
    )
    bst_after, bst_same = step_pair(
        rows, ACTUAL_BY_SEASON, "BST", HALF_HOUR_AFTER, SAME_LABEL
    )
    first, last = alignment_window()
    limitations = limitations_section(PAGE.read_text(encoding="utf-8"))

    assert (
        "whose wind figures equal Elexon's at the same half-hour in "
        f"{share_of_common(wind, 'exactly_equal')}% of the "
        f"{int(wind['common_rows']):,} half-hours from {first} to {last}, the "
        "API's actual agrees most closely one half-hour after its own label: "
        f"within 5 g/kWh in {share_of_common(after, 'within_5')}% of half-hours, "
        f"against {share_of_common(same, 'within_5')}% at the same label; "
        f"exactly equal in {share_of_common(after, 'exactly_equal')}%, against "
        f"{share_of_common(same, 'exactly_equal')}%; a mean absolute difference "
        f"of {tenths(after['mean_abs_diff'])} g/kWh, against "
        f"{tenths(same['mean_abs_diff'])}; and a correlation of half-hour "
        f"changes of {whole_after}, against {whole_same} ({gmt_after} against "
        f"{gmt_same} in GMT, {bst_after} against {bst_same} in BST). The share "
        "within 5 g/kWh and the mean absolute difference are best one half-hour "
        f"after the label in all {len(groups_in(rows, ACTUAL_BY_MONTH))} months."
        in limitations
    )
    assert (
        f'<a href="results/{ALIGNMENT_COUNTS}">Alignment counts</a></li>' in limitations
    )


def test_the_page_credits_nesos_historic_generation_mix_under_its_licence():
    page = PAGE.read_text(encoding="utf-8")

    assert (
        '<p>Historic generation mix: <a href="https://www.neso.energy/data-portal/'
        'historic-generation-mix">NESO Data Portal</a>, Supported by National '
        'Energy SO Open Data. <a href="https://www.neso.energy/data-portal/'
        'neso-open-licence">NESO Open Data Licence v1.0</a></p>' in page
    )


def test_each_read_carbon_result_points_to_its_caveats_under_limitations():
    whole = by_offset(alignment_rows(), ACTUAL_WHOLE, "all")
    within_5_after = share_of_common(whole[HALF_HOUR_AFTER], "within_5")
    pointer = (
        "<dt>Caveats</dt>\n        <dd>"
        '<a href="#carbon-label-alignment">The half-hour alignment</a> and '
        '<a href="#impossible-actual">the one impossible actual</a>: '
        "see Limitations.</dd>"
    )
    article_ids = []
    for question_id in READ_CARBON_RESULTS:
        article_ids.append(question_id.lower())
    page = PAGE.read_text(encoding="utf-8")

    assert articles_without(page, article_ids, pointer) == []
    assert '<li id="carbon-label-alignment">' in page
    assert page.count(f"within 5 g/kWh in {within_5_after}%") == 1


def test_the_read_carbon_results_state_the_alignment_limitation_from_its_counts():
    rows = alignment_rows()
    wind = wind_clock_row(rows)
    whole = by_offset(rows, ACTUAL_WHOLE, "all")
    whole_after, whole_same = step_pair(
        rows, ACTUAL_WHOLE, "all", HALF_HOUR_AFTER, SAME_LABEL
    )
    limitation = (
        "Against NESO's historic generation mix, whose wind equals Elexon's at the "
        f"same half-hour in {share_of_common(wind, 'exactly_equal')}% of "
        f"{int(wind['common_rows']):,} half-hours, the API's actual is within "
        f"5 g/kWh in {share_of_common(whole[HALF_HOUR_AFTER], 'within_5')}% of "
        "half-hours one half-hour after its label, against "
        f"{share_of_common(whole[SAME_LABEL], 'within_5')}% at its own label "
        f"(correlation of half-hour changes {whole_after}, against {whole_same})."
    )
    questions = QUESTIONS.read_text(encoding="utf-8")

    assert sections_without(questions, READ_CARBON_RESULTS, limitation) == []


def test_q2s_gas_units_are_lettered_and_their_share_of_the_group_is_derived():
    gas_units = saved_output_rows("q2_gas_units_revisions.csv")
    prefixes = saved_output_rows("q2_changes_by_unit_prefix.csv")
    article = article_text(PAGE.read_text(encoding="utf-8"), "q2")
    details = article.split("<dt>The three gas units</dt>", 1)[1].split("</dd>", 1)[0]
    gas_units_mwh = Decimal(0)
    for row in gas_units:
        gas_units_mwh += Decimal(row["moved_mwh"])
    transmission_connected = one_row(prefixes, "unit_prefix", "T")
    embedded = one_row(prefixes, "unit_prefix", "E")
    grid_connected_mwh = Decimal(transmission_connected["moved_mwh"]) + Decimal(
        embedded["moved_mwh"]
    )
    tenths_of_group = (Decimal(10) * gas_units_mwh / grid_connected_mwh).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )

    assert [row["unit"] for row in gas_units] == [
        "gas unit A",
        "gas unit B",
        "gas unit C",
    ]
    assert details.endswith(
        "Each unit's revised energy, net change and three-week-run energy are in "
        'a <a href="results/q2_gas_units_revisions.csv">saved output</a>; the '
        "units are lettered, not named."
    )
    assert tenths_of_group == 9
    assert "nine-tenths of this group" in article
    suppliers_mwh = Decimal(one_row(prefixes, "unit_prefix", "2")["moved_mwh"])
    revised_mwh = summed(prefixes, "moved_mwh")
    assert VISIBLE_LIMITS["q2"] in in_view(PAGE.read_text(encoding="utf-8"), "q2")
    assert suppliers_mwh / revised_mwh > Decimal("0.75")
    assert gas_units_mwh / (revised_mwh - suppliers_mwh) > Decimal("0.5")
    assert (
        '<a href="results/q2_gas_units_revisions.csv">the three gas units</a>'
        in article
    )
    rose = []
    for row in gas_units:
        rose.append(Decimal(row["net_change_mwh"]) > 0)
    assert rose == [True, False, False]


def test_each_figure_added_to_an_answer_or_note_comes_from_its_saved_output():
    q12 = saved_output_rows("q12_instructed_vs_residual.csv")
    q5 = saved_output_rows("q5_shortfall_with_known_outage.csv")
    q29 = saved_output_rows("q29_final_forecast_error_by_month_and_block.csv")
    q2 = saved_output_rows("q2_settlement_run_changes.csv")
    prefixes = saved_output_rows("q2_changes_by_unit_prefix.csv")

    shares = {}
    for row in q12:
        shares[row["fuel_group"]] = whole(
            Decimal(100) * Decimal(row["instructed_share_of_abs_deviation"])
        )
    earliest_run = item_row(q5, "3", "population by metered run: II")
    all_units = item_row(q5, "1", "total")
    earliest_mean = Decimal(earliest_run["positive_shortfall_mwh"]) / Decimal(
        earliest_run["unit_periods"]
    )
    overall_mean = Decimal(all_units["positive_shortfall_mwh"]) / Decimal(
        all_units["unit_periods"]
    )
    publications = 0
    for row in q5:
        if row["item"].startswith("Active production unavailability publications"):
            publications += int(row["remit_publications"])
    unmapped = item_row(
        q5,
        "3",
        "Active production unavailability publications by mapping rule: unmapped",
    )["remit_publications"]
    not_drawn_mwh = Decimal(0)
    not_drawn_with_notice_mwh = Decimal(0)
    for row in q5:
        if row["section"] != "2" or row["fuel_group"] not in ("coal", "interconnector"):
            continue
        not_drawn_mwh += Decimal(row["positive_shortfall_mwh"])
        if row["item"] != "none":
            not_drawn_with_notice_mwh += Decimal(row["positive_shortfall_mwh"])
    seen = re.search(
        r"mean (?P<mean>\+?[\d.]+), median (?P<median>[\d.]+)",
        one_row(q29, "section", "1")["item"],
    )
    one_week = one_row(q2, "run_pair", "II->SF")
    three_weeks = one_row(q2, "run_pair", "SF->R1")
    grid_changed = 0
    grid_matched = 0
    prefix_moved_mwh = Decimal(0)
    other_prefixes = []
    for row in prefixes:
        prefix_moved_mwh += Decimal(row["moved_mwh"])
        if row["unit_prefix"] in ("T", "E"):
            grid_changed += int(row["changed"])
            grid_matched += int(row["matched"])
        if row["unit_prefix"] not in UNIT_TYPE_BY_PREFIX:
            other_prefixes.append(row["unit_prefix"])
    other_prefixes.sort()

    expected = [
        "counting the instructed and the not-instructed part each by its size, "
        f"and {shares['other']}% for the other group. For wind "
        f"({shares['wind']}%) and bioenergy ({shares['bioenergy']}%), the part "
        "NESO did not instruct is larger.",
        "rest on the earliest settlement run and carry about twice the "
        "mean under-delivery per unit half-hour "
        f"({tenths(earliest_mean)} MWh against {tenths(overall_mean)});",
        f"{unmapped} of {publications:,} outage publications that name no unit "
        "that could be matched.",
        "Coal (closed units) and interconnectors are not drawn; their "
        f"under-delivery, {hundredths(not_drawn_mwh / Decimal(1_000_000))} TWh, "
        "came with no notice",
        "A September 2026 exploration had partly seen the overall mean and "
        f"median ({seen['mean']} and {seen['median']}, measured with the "
        "opposite sign).",
        "as a share of the earlier run's energy, the sizes of its half-hourly "
        "figures added up, generation and demand alike, over the half-hours both "
        "runs hold "
        f"({int(whole(Decimal(one_week['base_abs_mwh']) / 1000)):,} GWh for the "
        "one-week run, "
        f"{int(whole(Decimal(three_weeks['base_abs_mwh']) / 1000)):,} GWh for "
        "the three-week run).",
        "Between the three-week and two-month runs, transmission-connected and "
        "embedded units changed in "
        f"{percent_of(grid_changed, grid_matched, 1)}% of half-hours,",
        "Other unit types are the units whose identifiers start with "
        f"{', '.join(other_prefixes[:-1])} or {other_prefixes[-1]}.",
    ]
    most_instructed = [
        Decimal(shares["gas_turbine"]),
        Decimal(shares["hydro"]),
        Decimal(shares["pumped_storage"]),
    ]
    page = PAGE.read_text(encoding="utf-8")

    assert phrases_missing_from(every_fold_out(page), expected) == []
    assert answer_of(page, "q12") == (
        "<strong>Most of it for gas, hydro and pumped storage</strong>: "
        f"<strong>{min(most_instructed)} to {max(most_instructed)}%</strong>, and "
        f"{shares['other']}% for the other group. For wind ({shares['wind']}%) "
        f"and bioenergy ({shares['bioenergy']}%), the part NESO did not instruct "
        "is larger."
    )
    assert answer_of(page, "q2") == (
        "<strong>By a few per cent of the energy.</strong> In the week checked, "
        f"{share_as_percent(one_week['moved_share'])} of the energy was revised "
        "between the one-week and three-week runs, and "
        f"<strong>{share_as_percent(three_weeks['moved_share'])}</strong> ("
        f"{whole(Decimal(three_weeks['moved_mwh']) / 1000)} GWh of "
        f"{int(whole(Decimal(three_weeks['base_abs_mwh']) / 1000)):,}) between the "
        "three-week and two-month runs."
    )
    assert Decimal(one_week["moved_share"]) < Decimal("0.05")
    assert "A change of 2% or more of the energy" in fold_outs(page, "q2")
    assert whole(earliest_mean / overall_mean) == "2"
    assert not_drawn_with_notice_mwh == 0
    assert prefix_moved_mwh == Decimal(three_weeks["moved_mwh"])
    assert Decimal(three_weeks["moved_share"]) == (
        Decimal(three_weeks["moved_mwh"]) / Decimal(three_weeks["base_abs_mwh"])
    ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def test_each_result_states_beside_it_the_dates_its_saved_output_or_read_covers():
    # The dates stand in the caption under a chart, or in the line under an
    # answer with no chart; the fold-out keeps the sentence that held them.
    page = PAGE.read_text(encoding="utf-8")
    q8_q28 = saved_output_rows("q8_q28_history.csv")
    q30 = saved_output_rows("q30_wind_vs_carbon_error.csv")
    q8_population = one_row(q8_q28, "section", "1")
    q6_population = item_row(
        saved_output_rows("q6_publication_lag.csv"), "3", "5 in the population"
    )
    q25_months = output_month_range(
        saved_output_rows("q25_instruction_fragmentation_corrected.csv")
    )
    q8_first, q8_last = long_date_range(q8_q28)
    q25_first, q25_last = data_entry_range(page, "q25")
    q6 = re.search(
        rf"first published from ({LONG_DATE}) to the read on ({LONG_DATE})",
        article_text(page, "q6"),
    )
    q6_first, q6_read = q6.group(1), q6.group(2)
    questions = QUESTIONS.read_text(encoding="utf-8")

    folded = [
        f"No clear difference ({output_date_range(q8_q28)}): ranking half-hours",
        f"The same 17,278 half-hours as Q28, {q8_first} to {q8_last}.",
        f"Barely: over {output_date_range(q30)}, the sizes of the two misses",
        "The median unplanned notice first published from "
        f"{short_date(q6_first)} to {short_date(q6_read)} appears",
    ]
    assert phrases_missing_from(every_fold_out(page), folded) == []
    assert (
        f'<p class="note">{int(q8_population["half_hours"]):,} half-hours, '
        f"{output_date_range(q8_q28)}. Source: Elexon BMRS and NESO Carbon "
        "Intensity API.</p>" in in_view(page, "q8")
    )
    assert (
        "<figcaption>Instructions to units with a published fuel type, "
        f"{short_date(q25_first)} – {short_date(q25_last)}. Source: Elexon "
        "BMRS.</figcaption>" in in_view(page, "q25")
    )
    assert (
        f'<p class="note">{int(q6_population["notices"]):,} notices first '
        f"published from {short_date(q6_first)} to {short_date(q6_read)}. Source: "
        "Elexon's feed of outage notices.</p>" in in_view(page, "q6")
    )
    assert q25_months == (
        f"{short_date(q25_first).split(' ', 1)[1]} – "
        f"{short_date(q25_last).split(' ', 1)[1]}"
    )
    assert f"stored publication on or after {q6_first} and event start" in " ".join(
        questions.split()
    )


def test_no_instruction_reached_a_nuclear_unit_as_the_fuel_group_limit_says():
    # The fuel-group answer's limit line says instructions make up none of
    # nuclear's gap: the instructed-share output holds no instructed
    # half-hour and no instructed energy for nuclear.
    nuclear = one_row(
        saved_output_rows("q12_instructed_vs_residual.csv"), "fuel_group", "nuclear"
    )

    assert int(nuclear["instructed_periods"]) == 0
    assert Decimal(nuclear["abs_instructed_deviation_mwh"]) == 0
    assert "and none of nuclear's." in VISIBLE_LIMITS["q3"]


def test_the_limitations_name_the_results_that_set_one_measure_against_another():
    # Q5 sets under-delivery against outage notices, Q8 and Q28 against the
    # carbon miss, Q30 the wind miss against it, Q37 the energy turned up
    # against the wind turned down, Q38 the nuclear gap against outage
    # notices; each is an article on the page; Q28's further results stand
    # inside its article.
    page = PAGE.read_text(encoding="utf-8")
    named = ["Q5", "Q8", "Q28", "Q30", "Q37", "Q38"]
    names = [named_result(question_id) for question_id in named]
    names.insert(3, WIND_RESULT)
    limitation = (
        "<li>Results that set one measure against another ("
        f"{', '.join(names[:-1])} and {names[-1]}) are associations, not causes. "
        "Each result "
        "covers the period given beside it and may not hold in another.</li>"
    )

    assert limitation in page
    for question_id in named:
        assert f'<article id="{question_id.lower()}">' in page
    assert '<article id="wind-shortfall">' in page


SET_UP_EXCEPTIONS = [
    "Q24",
    "Q1 and Q10",
    "Q29",
    "Q25",
    "Q28:",
    "Q28's reverse cut",
    "Q37",
    "Q38",
]


def capitalised(text):
    return text[0].upper() + text[1:]


def page_exception_openings():
    """How the page opens each exception: the result's plain name and its
    asterisk, in the register's order."""
    later_look = (
        "The later, unplanned looks at carbon misses and under-delivery"
        '<a class="ref" href="#q28-reverse-title" aria-label="Details, Q28\'s '
        'later look">*</a>'
    )
    return [
        capitalised(named_result("Q24")),
        f"{capitalised(named_result('Q1'))} and {named_result('Q10')}",
        capitalised(named_result("Q29")),
        capitalised(named_result("Q25")),
        capitalised(named_result("Q28")),
        later_look,
        capitalised(named_result("Q37")),
        capitalised(named_result("Q38")),
    ]


def set_up_fold_out(page):
    """The fold-out near the page's end on how the questions were set up."""
    for fold_out in FOLD_OUT.findall(page):
        if '<summary id="set-up">How the questions were set up</summary>' in fold_out:
            return fold_out
    return ""


def exception_lines(text, opening, closing):
    """The exceptions list between two markers, one entry per line."""
    block = text.split(opening, 1)[1].split(closing, 1)[0]
    lines = []
    for line in block.strip().splitlines():
        lines.append(line.strip())
    return lines


def questions_set_up(questions):
    """The questions page's one section on how the questions were set up,
    which is its last."""
    assert questions.count(SET_UP_HEADING) == 1
    set_up = questions.split(SET_UP_HEADING, 1)[1]
    assert "\n## " not in set_up
    return set_up


def questions_outside_set_up(questions):
    return questions.split(SET_UP_HEADING, 1)[0]


def test_both_pages_qualify_the_written_first_claim_with_the_same_exceptions():
    # The blanket claim holds except where listed; the results page and the
    # questions page each list the same exceptions, one line each, in the
    # same order, and the questions page lists them in its set-up section.
    page = PAGE.read_text(encoding="utf-8")
    questions = QUESTIONS.read_text(encoding="utf-8")
    page_lines = []
    for line in exception_lines(page, '<li id="set-up-exceptions">', "</ul>"):
        if line.startswith("<li>"):
            page_lines.append(line)
    register_items = []
    for line in exception_lines(
        questions_set_up(questions),
        "### Exceptions",
        "### When each method was written",
    ):
        if line.startswith("- "):
            register_items.append(line)

    set_up = set_up_fold_out(page)
    assert (
        "<p>Every method was recorded before its result was read, except "
        "where the result says otherwise" in set_up
    )
    assert '<li id="set-up-exceptions">' in set_up
    assert "Every method was recorded" not in FOLD_OUT.sub("", page)
    assert questions_set_up(questions).startswith(
        "\nEach method was written before the query that answers it and before "
        "its\nresult was read, except where listed under [Exceptions](#exceptions); "
        "the\ntable below gives the dates kept. Each result was read once, at a "
        "fixed cutoff\n"
        "(`analysis_read_until`); Q25 was re-run once over the same period."
    )
    assert len(page_lines) == len(register_items) == len(SET_UP_EXCEPTIONS)
    for page_line, opening, register_item, named in zip(
        page_lines,
        page_exception_openings(),
        register_items,
        SET_UP_EXCEPTIONS,
        strict=True,
    ):
        assert page_line.startswith(f"<li>{opening}: ")
        assert register_item.startswith(f"- {named}")
    assert question_ids_in(visible_text("".join(page_lines))) == []
    assert (
        "It was re-run once over the same period, with the two halves of "
        "instructions that cross midnight joined; the method is unchanged.</li>"
        in page_lines[3]
    )
    assert re.findall(r"pre-regist\w*|exploratory", "".join(page_lines)) == []
    assert (
        "The mean change and the correlation beside the drift's verdict were "
        "added after related figures had been seen.</li>" in page_lines[1]
    )
    assert (
        "The mean drift and the correlation beside Q1's answer were added after "
        "related figures had been seen." in flattened(questions_set_up(questions))
    )


def test_the_results_page_links_each_method_by_that_name():
    page = PAGE.read_text(encoding="utf-8")

    assert page.count(">Full method</a>") == 17
    assert page.count(">Every method</a>") == 1
    assert re.findall(r">(?:Full|Every) rule<", page) == []


def test_design_decisions_links_the_exceptions_instead_of_listing_them():
    design = (REPOSITORY / "docs" / "design-decisions.md").read_text(encoding="utf-8")
    answering = design.split("## Answering the questions", 1)[1].split("\n**", 2)[1]

    assert "[exceptions](research-questions.md#exceptions)" in flattened(answering)
    assert question_ids_in(answering) == []


def register_section(questions, question_id):
    """The register's section for one question, heading to next heading."""
    heading = f"\n### {question_id}. "
    assert heading in questions
    return questions.split(heading, 1)[1].split("\n### ", 1)[0]


def question_ids_in(text):
    """The question IDs the text names, each once, in first-named order."""
    return list(dict.fromkeys(re.findall(r"\bQ\d+(?:-R[0-9A-Z]+)?\b", text)))


README = REPOSITORY / "README.md"
Q1_VERDICT = "q1_forecast_drift_verdict.csv"
Q1_SPLITS = "q1_forecast_drift_splits.csv"
Q1_BESIDE = "q1_forecast_drift_beside.csv"
Q1_POPULATION = "context_q1_population.csv"
Q10_OUTPUT = "q10_forecast_accuracy_by_lead_time.csv"
Q10_LEAD_TIMES = ("about 24 hours", "about 4 hours", "about half an hour")
Q10_PERSISTENCE = (
    "reference, persistence: outturn of the latest half-hour ended at or "
    "before the pick's poll"
)
ARCHIVE_WINDOW = "19 August to 12 November 2026"
STATUS_READ_AT = re.compile(
    rf"\*\*Status:\*\* read on ({LONG_DATE}, \d{{2}}:\d{{2}} UTC)"
)
SET_UP_HEADING = "\n## How the questions were set up\n"
TRAJECTORY = REPOSITORY / "docs" / "carbon-forecast-trajectory.md"


def status_read(question_id):
    """The day and time the questions page's status gives for a question's
    read, as '22 November 2026, 08:20 UTC'."""
    questions = QUESTIONS.read_text(encoding="utf-8")
    status = flattened(register_section(questions, question_id))
    return STATUS_READ_AT.search(status).group(1)


def with_true_minus(text):
    return text.replace("-", "−")


def signed_to(value, places):
    """A value with its sign at a fixed number of decimals, halves rounded
    up, as the page writes it."""
    step = Decimal(1).scaleb(-places)
    rounded = Decimal(value).quantize(step, rounding=ROUND_HALF_UP)
    return with_true_minus(f"{rounded:+f}")


def signed_without_trailing_zeros(value):
    return with_true_minus(f"{Decimal(value).normalize():+f}")


def share_as_percent(share):
    return f"{tenths(Decimal(share) * 100)}%"


def half_hour_long_date(half_hour):
    """'2026-08-19 21:00:00+00:00' as '19 August 2026'."""
    return long_date(half_hour.partition(" ")[0])


def window_of(row):
    """A row's first and last half-hour as '19 August to 12 November 2026'."""
    first = half_hour_long_date(row["first_half_hour"])
    last = half_hour_long_date(row["last_half_hour"])
    first_day, first_month, year = first.split(" ")
    assert last.endswith(f" {year}")
    return f"{first_day} {first_month} to {last}"


def q10_row(rows, section, item, lead_time):
    found = []
    for row in rows:
        named = (row["section"], row["item"], row["horizon"])
        if named == (section, item, lead_time):
            found.append(row)
    assert len(found) == 1
    return found[0]


def q10_accuracy(rows, lead_time):
    return q10_row(rows, "2", "accuracy on the common sample", lead_time)


def lead_time_written(seconds):
    """Seconds as '23 h 51 min', to the nearest minute."""
    minutes = int(whole(Decimal(seconds) / 60))
    return f"{minutes // 60} h {minutes % 60} min"


def test_q1_and_q10_state_their_window_as_their_saved_outputs_hold_it():
    verdict = one_row(saved_output_rows(Q1_VERDICT), "population_is_reached", "True")
    q10 = saved_output_rows(Q10_OUTPUT)
    common = q10_row(q10, "1", "common sample: all three horizons", "")

    assert window_of(verdict) == ARCHIVE_WINDOW
    assert window_of(common) == ARCHIVE_WINDOW


def test_q1s_answer_and_checks_come_from_its_verdict_output():
    verdict = one_row(saved_output_rows(Q1_VERDICT), "population_is_reached", "True")
    # Each version of the verdict: the table's name for it, the column that
    # holds its half-hours and the prefix of its other columns.
    versions = [
        (
            "The method: partial and unchanged polls excluded",
            "complete_half_hours",
            "",
        ),
        (
            "Sensitivity check: one poll per half-hour slot, none excluded",
            "as_published_half_hours",
            "as_published_",
        ),
        (
            "Each weekday weighted to its share of the calendar",
            "complete_half_hours",
            "weekday_reweighted_",
        ),
        (
            "The part the exploration did not see, after 10 September 2026, 05:30 UTC",
            "part_not_seen_half_hours",
            "part_not_seen_",
        ),
    ]
    median_drift = signed_without_trailing_zeros(verdict["median_drift"])
    expected = [
        "<p>Up, over the window as a whole: the answer is "
        f'"{verdict["reported_verdict"]}". Over '
        f"{int(verdict['complete_half_hours']):,} complete half-hours from "
        f"{ARCHIVE_WINDOW}, the middle change from first to final forecast was "
        f"{median_drift} g/kWh and {share_as_percent(verdict['positive_share'])} "
        "of non-zero changes were upward; the method needs at least +2 and at "
        "least 55%.</p>",
    ]
    for version, half_hours_column, prefix in versions:
        median = signed_without_trailing_zeros(verdict[f"{prefix}median_drift"])
        share = share_as_percent(verdict[f"{prefix}positive_share"])
        expected.append(
            f"<tr><td>{version}</td><td>{int(verdict[half_hours_column]):,}</td>"
            f"<td>{median}</td><td>{share}</td></tr>"
        )

    page = PAGE.read_text(encoding="utf-8")

    assert phrases_missing_from(fold_outs(page, "q1"), expected) == []
    assert answer_of(page, "q1") == (
        "<strong>Up, over the window as a whole.</strong> The median change "
        f"from first to final forecast was <strong>{median_drift} g/kWh</strong>, "
        "and nearly three in four of the changes that were not zero ("
        f"{share_as_percent(verdict['positive_share'])}) were upward. For scale, "
        "a day ahead the forecast ran "
        f"{whole(q10_accuracy(saved_output_rows(Q10_OUTPUT), 'about 24 hours')['bias'])} "
        f"g/kWh below NESO's outturn estimate.{details_link('Q10')}"
    )
    assert Decimal("0.7") < Decimal(verdict["positive_share"]) < Decimal("0.75")
    assert (
        "Upward drift is supported when the median change is at least +2 g/kWh "
        "and at least 55% of non-zero changes are upward" in fold_outs(page, "q1")
    )
    assert verdict["reported_verdict"] == "supported: upward drift"
    assert verdict["as_published_verdict"] == "supported: upward drift"
    assert verdict["weekday_reweighted_verdict"] == "supported: upward drift"
    assert verdict["part_not_seen_verdict"] == "supported: upward drift"


def test_q1s_figures_beside_its_verdict_come_from_its_beside_output():
    beside = saved_output_rows(Q1_BESIDE)
    mean = one_row(beside, "item", "mean drift, gCO2/kWh")
    correlation = one_row(
        beside, "item", "Pearson's r of consecutive changes within a half-hour"
    )
    dates = one_row(beside, "item", "UTC dates of the population")
    zero_share = one_row(beside, "item", "share of changes equal to 0")

    expected = [
        f"on the same {int(mean['n']):,} half-hours: the mean change was "
        f"{signed_to(mean['value'], 2)} g/kWh (90% range "
        f"{signed_to(mean['interval_low'], 2)} to "
        f"{signed_to(mean['interval_high'], 2)}, resampling {dates['n']} UTC "
        "dates). Successive changes within a half-hour have a correlation of "
        f"{signed_to(correlation['value'], 3)} (90% range "
        f"{signed_to(correlation['interval_low'], 3)} to "
        f"{signed_to(correlation['interval_high'], 3)}, "
        f"{int(correlation['n']):,} pairs of changes). Forecast values are "
        f"whole g/kWh and {share_as_percent(zero_share['value'])} of changes "
        "are zero; rounding on its own makes successive changes negatively "
        "correlated, by an amount not measured here,",
    ]
    assert missing_phrases(expected) == []


def test_q1s_data_entry_comes_from_its_population_counts():
    population = saved_output_rows(Q1_POPULATION)
    complete = one_row(population, "breakdown", "population")
    partial = one_row(population, "item", "partial polls")["half_hours"]
    unchanged = one_row(population, "item", "no-change polls")["half_hours"]
    wednesdays = one_row(population, "item", "3 Wed")["half_hours"]
    saturdays = one_row(population, "item", "6 Sat")["half_hours"]
    dates = one_row(
        saved_output_rows(Q1_BESIDE), "item", "UTC dates of the population"
    )["n"]

    expected = [
        f"<dd>{int(complete['half_hours']):,} complete half-hours on {dates} UTC "
        "dates, from the one starting at 21:00 UTC on 19 August 2026 to the one "
        "starting at 23:30 UTC on 12 November 2026.",
        f"Partial polls removed {partial} half-hours and runs of unchanged polls "
        f"{unchanged}. Wednesdays hold {wednesdays} complete half-hours and "
        f"Saturdays {saturdays},",
    ]
    assert missing_phrases(expected) == []
    assert complete["first_half_hour"] == "2026-08-19 21:00:00+00:00"
    assert complete["last_half_hour"] == "2026-11-12 23:30:00+00:00"


def test_q1s_time_of_day_and_band_entry_comes_from_its_splits_output():
    splits = saved_output_rows(Q1_SPLITS)
    block_medians = []
    block_half_hours = []
    for row in splits:
        if row["split"] == "time of day (UTC start)":
            block_medians.append(Decimal(row["median_drift"]))
            block_half_hours.append(int(row["half_hours"]))
    bands = {}
    empty_bands = []
    for row in splits:
        if row["split"] != "first-forecast band":
            continue
        if row["half_hours"] == "0":
            empty_bands.append(row["cell"])
            continue
        bands[row["cell"]] = (
            signed_without_trailing_zeros(row["median_drift"]),
            f"{int(row['half_hours']):,}",
        )

    expected = [
        "The median change is "
        f"{signed_without_trailing_zeros(min(block_medians))} to "
        f"{signed_without_trailing_zeros(max(block_medians))} g/kWh in each "
        f"six-hour block ({min(block_half_hours)} to {max(block_half_hours)} "
        "half-hours each). By the band of the first forecast it is "
        f"{bands['low'][0]} for low ({bands['low'][1]} half-hours), "
        f"{bands['moderate'][0]} for moderate ({bands['moderate'][1]}), "
        f"{bands['high'][0]} for high ({bands['high'][1]}) and "
        f"{bands['very high'][0]} for very high ({bands['very high'][1]} "
        "half-hours).",
        "No half-hour has a very low first forecast or one with no band, so "
        f"those rows are left out; the very high band holds "
        f"{bands['very high'][1]} half-hours.",
    ]
    assert missing_phrases(expected) == []
    assert sorted(empty_bands) == ["unclassified", "very low"]
    assert sorted(bands) == ["high", "low", "moderate", "very high"]


def test_q10s_answer_and_table_come_from_its_saved_output():
    q10 = saved_output_rows(Q10_OUTPUT)
    day = q10_accuracy(q10, "about 24 hours")
    half_hour = q10_accuracy(q10, "about half an hour")
    four_hours = q10_accuracy(q10, "about 4 hours")
    expected = [
        f"<p>Over {int(day['half_hours']):,} half-hours from {ARCHIVE_WINDOW}, "
        "about a day ahead the forecast missed NESO's outturn estimate by "
        f"{whole(day['mean_absolute_error'])} g/kWh on average "
        f"and ran {whole(day['bias'])} g/kWh below it; about half an hour ahead, "
        f"{whole(half_hour['mean_absolute_error'])} and "
        f"{whole(half_hour['bias'])}.</p>",
    ]
    for lead_time in Q10_LEAD_TIMES:
        row = q10_accuracy(q10, lead_time)
        expected.append(
            f"<tr><td>{lead_time.capitalize()}</td>"
            f"<td>{int(row['half_hours']):,}</td>"
            f"<td>{hundredths(row['mean_absolute_error'])} "
            f"({hundredths(row['mae_interval_low'])} to "
            f"{hundredths(row['mae_interval_high'])})</td>"
            f"<td>{signed_to(row['bias'], 2)} "
            f"({signed_to(row['bias_interval_low'], 2)} to "
            f"{signed_to(row['bias_interval_high'], 2)})</td></tr>"
        )

    page = PAGE.read_text(encoding="utf-8")

    assert phrases_missing_from(fold_outs(page, "q10"), expected) == []
    assert answer_of(page, "q10") == (
        "<strong>Further off the further ahead.</strong> About a day ahead the "
        "forecast missed NESO's outturn estimate by "
        f"<strong>{whole(day['mean_absolute_error'])} g/kWh</strong> on average "
        f"and ran {whole(day['bias'])} g/kWh below it; about half an hour ahead, "
        f"it missed by {whole(half_hour['mean_absolute_error'])} on average and "
        f"ran {whole(half_hour['bias'])} below."
    )
    assert (
        f'<p class="note">{int(day["half_hours"]):,} half-hours, {ARCHIVE_WINDOW}. '
        "Source: NESO Carbon Intensity API.</p>" in in_view(page, "q10")
    )
    assert Decimal(day["bias"]) > 0
    assert Decimal(half_hour["bias"]) > 0
    assert (
        Decimal(day["mean_absolute_error"])
        > Decimal(four_hours["mean_absolute_error"])
        > Decimal(half_hour["mean_absolute_error"])
    )


def earliest_and_later(q10, lead_time):
    """Q10's bias at a lead time on the half-hours before the part not seen
    and on that part, with each count. The earliest half-hours' bias is not
    in the saved output: it is the whole sample's total error less the part
    not seen's, over the half-hours between them."""
    whole_sample = q10_accuracy(q10, lead_time)
    not_seen = q10_row(q10, "6", "the part not seen", lead_time)
    whole_count = int(whole_sample["half_hours"])
    later_count = int(not_seen["half_hours"])
    later_bias = Decimal(not_seen["bias"])
    earliest_count = whole_count - later_count
    earliest_total = Decimal(whole_sample["bias"]) * whole_count - (
        later_bias * later_count
    )
    return {
        "earliest_half_hours": earliest_count,
        "earliest_bias": earliest_total / earliest_count,
        "later_half_hours": later_count,
        "later_bias": later_bias,
    }


def test_the_sign_change_is_q10s_whole_window_less_the_part_not_seen():
    q10 = saved_output_rows(Q10_OUTPUT)
    day = earliest_and_later(q10, "about 24 hours")
    four_hours = earliest_and_later(q10, "about 4 hours")
    half_hour = earliest_and_later(q10, "about half an hour")
    earliest_change = day["earliest_bias"] - half_hour["earliest_bias"]
    later_change = day["later_bias"] - half_hour["later_bias"]
    earliest_half_hours = day["earliest_half_hours"]
    later_half_hours = day["later_half_hours"]
    sign_change = (
        f"worked by subtraction, the {earliest_half_hours} earliest half-hours "
        "(19 August to 4 September 2026) have a bias of "
        f"{signed_to(day['earliest_bias'], 2)}, "
        f"{signed_to(four_hours['earliest_bias'], 2)} and "
        f"{signed_to(half_hour['earliest_bias'], 2)} g/kWh at 24 hours, 4 hours "
        f"and half an hour ahead, against {signed_to(day['later_bias'], 2)}, "
        f"{signed_to(four_hours['later_bias'], 2)} and "
        f"{signed_to(half_hour['later_bias'], 2)} on the {later_half_hours:,} "
        "from 11 September. The forecast's mean change from about a day ahead to "
        "its final version, the 24-hour bias minus the half-hour bias, was about "
        f"{signed_to(earliest_change, 1)} g/kWh on the {earliest_half_hours} and "
        f"about {signed_to(later_change, 1)} on the {later_half_hours:,}."
    )
    page = PAGE.read_text(encoding="utf-8")

    assert sign_change in fold_outs(page, "q1")
    assert sign_change in fold_outs(page, "q10")
    assert (
        f"<p>On the {earliest_half_hours} earliest half-hours (19 August to "
        "4 September 2026) the forecast ran above the outturn estimate, not "
        "below it; the turn also coincides with a change in when the archive "
        "polls.</p>" in in_view(page, "q10")
    )
    assert day["earliest_bias"] < 0
    assert four_hours["earliest_bias"] < 0
    assert half_hour["earliest_bias"] < 0
    assert four_hours["earliest_half_hours"] == earliest_half_hours
    assert half_hour["earliest_half_hours"] == earliest_half_hours
    assert q10_row(q10, "6", "the part not seen", "about 24 hours")[
        "first_half_hour"
    ].startswith("2026-09-11 10:30")


def test_q1s_later_half_hours_come_from_its_verdict_and_its_earliest_have_none():
    verdict = one_row(saved_output_rows(Q1_VERDICT), "population_is_reached", "True")
    later = (
        f"<p>On the {int(verdict['part_not_seen_half_hours']):,} half-hours after "
        "10 September 2026, 05:30 UTC, the middle change was "
        f"{signed_without_trailing_zeros(verdict['part_not_seen_median_drift'])} "
        f"g/kWh and {share_as_percent(verdict['part_not_seen_positive_share'])} "
        "of non-zero changes were upward. Q10's mean change from about a day ahead "
        "to its final version is negative on the earliest half-hours; Q1's "
        "first-to-last measure is not calculated for them."
    )
    page = PAGE.read_text(encoding="utf-8")

    assert later in fold_outs(page, "q1")
    assert (
        "<p>This covers one window. On its earliest half-hours the forecast fell "
        "on average between a day ahead and its final version"
        f"{details_link('Q10')}, across a change in when the archive polls.</p>"
        in in_view(page, "q1")
    )
    assert (
        "the change in the archive's poll schedule on 10 September 2026"
        in fold_outs(page, "q1")
    )
    assert "upward drift belongs to" not in page


def test_q10s_context_note_comes_from_its_reference_rows():
    q10 = saved_output_rows(Q10_OUTPUT)
    forecast = {}
    persistence = {}
    for lead_time in Q10_LEAD_TIMES:
        forecast[lead_time] = q10_accuracy(q10, lead_time)["mean_absolute_error"]
        persistence[lead_time] = q10_row(q10, "4", Q10_PERSISTENCE, lead_time)[
            "mean_absolute_error"
        ]
    api_final = q10_row(
        q10, "5", "consistency check: error_api_final on the common sample", ""
    )
    day, four_hours, half_hour = Q10_LEAD_TIMES

    expected = [
        f"on the same {int(api_final['half_hours']):,} half-hours: at half an "
        "hour ahead, repeating the latest outturn estimate missed by less "
        f"({hundredths(persistence[half_hour])} g/kWh on average) and so did the "
        "API's own final forecast "
        f"({hundredths(api_final['mean_absolute_error'])}), against the "
        f"forecast's {hundredths(forecast[half_hour])}; at 24 and 4 hours ahead "
        "the forecast missed by less than repeating the latest outturn estimate "
        f"({hundredths(forecast[day])} against {hundredths(persistence[day])}; "
        f"{hundredths(forecast[four_hours])} against "
        f"{hundredths(persistence[four_hours])}).",
        f"The API's final forecast has a bias of {signed_to(api_final['bias'], 2)} "
        "here and −0.6 in Q29, which cover different windows.",
    ]
    assert missing_phrases(expected) == []
    assert Decimal(persistence[half_hour]) < Decimal(forecast[half_hour])
    assert Decimal(api_final["mean_absolute_error"]) < Decimal(forecast[half_hour])
    assert Decimal(forecast[day]) < Decimal(persistence[day])
    assert Decimal(forecast[four_hours]) < Decimal(persistence[four_hours])


def test_q10s_sample_and_lead_times_come_from_its_saved_output():
    q10 = saved_output_rows(Q10_OUTPUT)
    day, four_hours, half_hour = Q10_LEAD_TIMES
    dropped = "dropped: no forecast at this horizon"
    fall_back = "fall-back picks: made more than the lead time ahead"
    no_day_forecast = int(q10_row(q10, "1", dropped, day)["half_hours"])
    no_four_hour_forecast = int(q10_row(q10, "1", dropped, four_hours)["half_hours"])
    q1_half_hours = int(
        q10_row(q10, "1", "Q1 one-look half-hours (2880 needed)", "")["half_hours"]
    )
    written = {}
    for lead_time in (day, four_hours):
        row = q10_accuracy(q10, lead_time)
        written[lead_time] = (
            f"{lead_time_written(row['shortest_horizon_seconds'])}, "
            f"{lead_time_written(row['median_horizon_seconds'])} and "
            f"{lead_time_written(row['longest_horizon_seconds'])}"
        )
    final = q10_accuracy(q10, half_hour)

    expected = [
        f"Q1's {q1_half_hours:,} less {no_day_forecast + no_four_hour_forecast} "
        f"dropped ({no_day_forecast} with no 24-hour forecast, "
        f"{no_four_hour_forecast} with no 4-hour forecast).",
        f"Shortest, median and longest: {written[day]} for the 24-hour forecast; "
        f"{written[four_hours]} for the 4-hour; "
        f"{whole(Decimal(final['shortest_horizon_seconds']) / 60)}, "
        f"{whole(Decimal(final['median_horizon_seconds']) / 60)} and "
        f"{whole(Decimal(final['longest_horizon_seconds']) / 60)} minutes for "
        "the half-hour. An earlier poll stood in for "
        f"{q10_row(q10, '1', fall_back, day)['half_hours']} of the 24-hour picks "
        f"and {q10_row(q10, '1', fall_back, four_hours)['half_hours']} of the "
        "4-hour.",
    ]
    assert missing_phrases(expected) == []
    assert q10_row(q10, "1", dropped, half_hour)["half_hours"] == "0"


def test_q10s_weekday_and_weekend_entry_comes_from_its_calendar_split():
    q10 = saved_output_rows(Q10_OUTPUT)
    day = Q10_LEAD_TIMES[0]
    weekday = q10_row(q10, "3", "settlement date: weekday", day)
    weekend = q10_row(q10, "3", "settlement date: weekend", day)
    bank_holiday = q10_row(q10, "3", "settlement date: bank holiday", day)

    expected = [
        f"About a day ahead the bias is {signed_to(weekday['bias'], 2)} (90% "
        f"range {signed_to(weekday['bias_interval_low'], 2)} to "
        f"{signed_to(weekday['bias_interval_high'], 2)}) on weekdays "
        f"({int(weekday['half_hours']):,} half-hours) and "
        f"{signed_to(weekend['bias'], 2)} "
        f"({signed_to(weekend['bias_interval_low'], 2)} to "
        f"{signed_to(weekend['bias_interval_high'], 2)}) at weekends "
        f"({weekend['half_hours']}); the mean size of the miss is "
        f"{hundredths(weekday['mean_absolute_error'])} and "
        f"{hundredths(weekend['mean_absolute_error'])}. The bank-holiday cell is "
        "empty: 31 August 2026 has no complete trajectory.",
    ]
    assert missing_phrases(expected) == []
    assert bank_holiday["half_hours"] == "0"


def test_each_archive_result_states_the_read_its_register_status_records():
    page = PAGE.read_text(encoding="utf-8")

    assert f"Read once on {status_read('Q1')}.</dd>" in article_text(page, "q1")
    assert f"Read once on {status_read('Q10')}, after Q1's look.</dd>" in (
        article_text(page, "q10")
    )


def test_only_q10_says_the_two_archive_results_are_the_same_revisions():
    page = PAGE.read_text(encoding="utf-8")
    link = (
        "<p>Q10's falling bias is Q1's upward revision seen against outturn: the "
        "same revisions, not separate evidence.</p>"
    )

    assert link in fold_outs(page, "q10")
    assert page.count(link) == 1


def articles_in(page, section_id):
    section = page.split(f'<section id="{section_id}"', 1)[1]
    section = section.split("\n</section>", 1)[0]
    return re.findall(r'<article id="([^"]+)">', section)


def test_the_archive_results_sit_apart_from_the_stored_forecasts_four():
    # The carbon section says its four questions use the stored forecast;
    # Q1 and Q10 use the archive, so they have a section of their own.
    page = PAGE.read_text(encoding="utf-8")

    assert "so these five questions use the" in page
    assert articles_in(page, "carbon") == [
        "q29",
        "q28",
        "wind-shortfall",
        "q8",
        "q30",
    ]
    assert articles_in(page, "archive") == ["q1", "q10"]


def test_q10_points_to_the_alignment_limitation_and_it_names_q10():
    page = PAGE.read_text(encoding="utf-8")

    assert (
        '<a href="#carbon-label-alignment">The half-hour alignment</a>: see '
        "Limitations.</dd>" in article_text(page, "q10")
    )
    assert (
        f"{named_result('Q10')} sets it against the archive's forecasts at the "
        "same label." in limitations_section(page)
    )


def headline_list(page):
    """The items of the page's headline list."""
    return GLANCED_PARTS["headline list"].search(page).group(1)


def test_the_archive_headline_reads_the_same_on_the_page_and_in_the_readme():
    verdict = one_row(saved_output_rows(Q1_VERDICT), "population_is_reached", "True")
    q10 = saved_output_rows(Q10_OUTPUT)
    day = q10_accuracy(q10, "about 24 hours")
    lead = "The median carbon forecast revision was upward."
    earliest_day = earliest_and_later(q10, "about 24 hours")
    earliest_final = earliest_and_later(q10, "about half an hour")
    earliest = earliest_day["earliest_half_hours"]
    rest = (
        f"Over {int(verdict['complete_half_hours']):,} half-hours from "
        f"{ARCHIVE_WINDOW} the median change from first to final forecast was "
        f"{signed_without_trailing_zeros(verdict['median_drift'])} g/kWh, and a "
        f"day ahead the forecast ran on average {whole(day['bias'])} g/kWh below "
        f"NESO's outturn estimate ({int(day['half_hours']):,} half-hours). On the "
        f"{earliest} earliest of those half-hours (19 August to 4 September) it "
        "ran above the outturn instead, and on average fell between a day ahead "
        "and its final version"
    )
    readme = flattened(README.read_text(encoding="utf-8"))
    headlines = headline_list(PAGE.read_text(encoding="utf-8"))

    assert earliest_day["earliest_bias"] < 0 < Decimal(day["bias"])
    # Error is outturn minus forecast, so a rise in the error from the day
    # ahead to the final forecast is a fall in the forecast.
    assert earliest_day["earliest_bias"] < earliest_final["earliest_bias"]
    assert f"<li><strong>{lead}</strong> {rest}.</li>" in without_asterisks(headlines)
    assert f"g/kWh,{details_link('Q1')} and a day ahead the forecast ran" in headlines
    assert f"its final version.{details_link('Q10')}</li>" in headlines
    assert f"- **{lead}** {rest} ([Q1](" in readme


def test_the_readme_gives_q10s_bias_and_q1s_verdict_beside_its_predictions():
    verdict = one_row(saved_output_rows(Q1_VERDICT), "population_is_reached", "True")
    q10 = saved_output_rows(Q10_OUTPUT)
    day = q10_accuracy(q10, "about 24 hours")
    half_hour = q10_accuracy(q10, "about half an hour")
    readme = flattened(README.read_text(encoding="utf-8"))

    assert (
        f"over {int(day['half_hours']):,} half-hours from {ARCHIVE_WINDOW}, "
        f"NESO's outturn estimate was on average {whole(day['bias'])} g/kWh above "
        "the forecast made about a day ahead and "
        f"{whole(half_hour['bias'])} g/kWh above the one made about half an hour "
        "ahead" in readme
    )
    assert (
        f"Over {int(verdict['complete_half_hours']):,} half-hours from "
        f"{ARCHIVE_WINDOW} the median change from first to final forecast was "
        f"{signed_without_trailing_zeros(verdict['median_drift'])} g/kWh, and "
        f"{share_as_percent(verdict['positive_share'])} of the changes that were "
        "not zero were upward" in readme
    )
    assert f"were analysed on {status_read('Q1').partition(',')[0]}" in readme


READ_DAYS = {
    "Q1": "22 November 2026",
    "Q2": "11 October 2026",
    "Q3": "11 October 2026",
    "Q5": "12 October 2026",
    "Q6": "12 October 2026",
    "Q8": "26 October 2026",
    "Q10": "22 November 2026",
    "Q12": "11 October 2026",
    "Q22": "11 October 2026",
    "Q23": "15 October 2026",
    "Q24": "15 October 2026",
    "Q25": "15 October 2026",
    "Q28": "26 October 2026",
    "Q29": "27 October 2026",
    "Q30": "27 October 2026",
    "Q37": "27 November 2026",
    "Q38": "28 November 2026",
}
Q25_RERUN = "re-run 25 October 2026 over the same period"
STATUS_ROW = re.compile(
    r"^\| \[(Q[0-9A-Z-]+)\]\(#([^)]+)\) \| [^|]+ \| ([^|]+) \|$", re.MULTILINE
)


def status_table(questions):
    return questions.split("\n## Status\n", 1)[1].split("\n## ", 1)[0]


def status_cells(questions):
    """Each question's status in the Status table, by question, in order."""
    cells = {}
    for question_id, _anchor, status in STATUS_ROW.findall(status_table(questions)):
        cells[question_id] = status
    return cells


def status_row_anchors(questions):
    """The heading each Status row links, by question."""
    anchors = {}
    for question_id, anchor, _status in STATUS_ROW.findall(status_table(questions)):
        anchors[question_id] = anchor
    return anchors


def expected_status_cells():
    cells = {}
    for question_id, day in READ_DAYS.items():
        cells[question_id] = f"read ({day})"
    cells["Q25"] = f"read ({READ_DAYS['Q25']}; {Q25_RERUN})"
    return cells


def expected_status_openings():
    """How each question's own status opens, for its Status table row."""
    openings = {}
    for question_id, day in READ_DAYS.items():
        openings[question_id] = f"**Status:** read on {day}"
    return openings


def questions_whose_section_lacks(questions, phrases):
    """The questions whose own section does not hold the phrase given for it."""
    lacking = []
    for question_id, phrase in phrases.items():
        if phrase not in flattened(register_section(questions, question_id)):
            lacking.append(question_id)
    return lacking


def test_the_status_table_gives_each_question_its_plain_status():
    questions = QUESTIONS.read_text(encoding="utf-8")

    assert status_cells(questions) == expected_status_cells()
    assert list(status_cells(questions)) == list(register_anchors())
    assert status_row_anchors(questions) == register_anchors()
    assert "One row per question, with the date of its read." in status_table(questions)


def test_each_questions_own_status_opens_as_its_status_table_row_says():
    questions = QUESTIONS.read_text(encoding="utf-8")

    assert questions_whose_section_lacks(questions, expected_status_openings()) == []
    assert sorted(expected_status_openings()) == sorted(register_anchors())
    assert (
        "Re-run once on 25 October 2026 over the same period, on the data then "
        "held," in flattened(register_section(questions, "Q25"))
    )
    assert (
        "from a run on 25 October 2026 over the same period, on the data then held;"
        in (PAGE.read_text(encoding="utf-8"))
    )


def heading_anchors(markdown):
    anchors = []
    for heading in re.findall(r"^#{1,6} (.+)$", markdown, re.MULTILINE):
        anchors.append(heading_anchor(heading))
    return anchors


def pages_that_link_the_method_pages():
    pages = [README, REPOSITORY / "dbt" / "README.md", PAGE]
    pages.extend(sorted((REPOSITORY / "docs").rglob("*.md")))
    return pages


def links_to_missing_headings(method_page):
    """Each link to a heading of one method page, from that page or any
    other, that no heading of the page answers."""
    anchors = heading_anchors(method_page.read_text(encoding="utf-8"))
    from_elsewhere = re.compile(rf"{re.escape(method_page.name)}#([A-Za-z0-9_-]+)")
    missing = []
    for page in pages_that_link_the_method_pages():
        text = page.read_text(encoding="utf-8")
        linked = from_elsewhere.findall(text)
        if page == method_page:
            linked.extend(re.findall(r"\]\(#([A-Za-z0-9_-]+)\)", text))
        for anchor in linked:
            if anchor not in anchors:
                missing.append(f"{page.name}: #{anchor}")
    return missing


def test_every_link_to_a_heading_of_the_method_pages_finds_its_heading():
    questions = QUESTIONS.read_text(encoding="utf-8")
    trajectory = TRAJECTORY.read_text(encoding="utf-8")

    assert links_to_missing_headings(QUESTIONS) == []
    assert links_to_missing_headings(TRAJECTORY) == []
    assert "(research-questions.md#how-the-questions-were-set-up)" in trajectory
    assert "(#how-the-questions-were-set-up)" in questions_outside_set_up(questions)
    assert "exceptions" in heading_anchors(questions_set_up(questions))


def test_design_decisions_links_the_dbt_analyses_by_a_heading_that_exists():
    decisions = (REPOSITORY / "docs" / "design-decisions.md").read_text(
        encoding="utf-8"
    )
    dbt_readme = (REPOSITORY / "dbt" / "README.md").read_text(encoding="utf-8")
    linked = re.findall(r"\(\.\./dbt/README\.md#([A-Za-z0-9_-]+)\)", decisions)

    assert "analyses" in linked
    assert set(linked) <= set(heading_anchors(dbt_readme))


def test_the_settlement_run_ages_are_the_measured_runs_in_months():
    # docs/sources/elexon/020_b1610.md measured the day each run goes live;
    # an average month is 30.44 days.
    runs = (REPOSITORY / "docs" / "sources" / "elexon" / "020_b1610.md").read_text(
        encoding="utf-8"
    )
    number_words = {2: "two", 4: "four", 7: "seven"}
    ages = []
    for run in ("R1", "R2", "R3", "RF"):
        live_from = re.search(rf"\| `{run}` \| \**~?(\d+)", runs).group(1)
        months = int(whole(Decimal(live_from) / Decimal("30.44")))
        ages.append(f"{number_words.get(months, months)} months ({run})")
    r2_months = ages[1].split(" ", 1)[0]

    expected = [
        "about one week (II), three weeks (SF), "
        f"{ages[0]}, {ages[1]}, {ages[2]} and {ages[3]} after the day for the "
        "days studied here; for days from 1 October 2026 the final run comes at "
        "about seven months.",
        f"the {r2_months}-month run (R2) to 29 April",
    ]
    assert missing_phrases(expected) == []


REMIT_NOTICES = (
    REPOSITORY / "dbt" / "models" / "intermediate" / "remit" / "int_remit__notices.sql"
)


def test_the_first_publication_tie_is_worded_as_the_notices_model_takes_it():
    model = flattened(REMIT_NOTICES.read_text(encoding="utf-8"))
    questions = flattened(QUESTIONS.read_text(encoding="utf-8"))

    assert (
        "where publish_time = notice_first_published_at and (valid_to is null "
        "or valid_to > publish_time)" in model
    )
    assert (
        "Its first publication is the one in force at its earliest "
        "`publish_time`: when several share that time, the one with the later "
        "`created_time`, then the higher `revision_number`." in questions
    )


def test_the_system_flag_is_worded_as_a_flag_and_no_more():
    page = PAGE.read_text(encoding="utf-8")

    assert (
        "I report the share carrying NESO's system flag, which marks an "
        "instruction as a system action, possibly for network reasons."
        in article_text(page, "q24")
    )
    assert "<dt>Wind instructions flagged as system actions</dt>" in article_text(
        page, "q23"
    )
    assert (
        "asked at least one wind farm to cut output and flagged it as a system "
        "action, and by" in article_text(page, "q23")
    )


def cohort_acceptances(counts):
    """The acceptances of cohort units, added over every fuel group."""
    total = 0
    for row in counts:
        if row["section"] == "2" and row["measure"].startswith("1 dispatch"):
            total += int(row["n"])
    return total


def test_the_cohorts_share_of_instructions_names_the_count_it_comes_from():
    counts = saved_output_rows("q25_instruction_fragmentation_corrected.csv")
    cohort = cohort_acceptances(counts)
    outside = int(
        one_row(counts, "measure", "acceptances of units outside the cohort")["n"]
    )
    share = percent_of(cohort, cohort + outside, 1)

    assert (
        "Units with a published fuel type only: in the count of short "
        f"instructions{details_link('Q25')} they carry {share}% of all "
        f"instructions by number ({cohort:,} of {cohort + outside:,})."
        in article_text(PAGE.read_text(encoding="utf-8"), "q24")
    )


def test_the_short_instruction_window_is_worded_as_the_questions_page_has_it():
    page = PAGE.read_text(encoding="utf-8")
    questions = flattened(QUESTIONS.read_text(encoding="utf-8"))

    assert (
        "issued from the start of the settlement period three before the one in "
        "which it was issued to the end of the period three after" in questions
    )
    assert (
        "issued from the start of the settlement period three before the one in "
        "which it was issued to the end of the period three after."
        in article_text(page, "q25")
    )


def test_a_published_outage_is_worded_as_the_known_outage_model_reads_it():
    page = PAGE.read_text(encoding="utf-8")

    assert (
        'A unit\'s half-hour counts as "outage published" if an outage notice '
        "for that unit, of any size, was live when the half-hour began and its "
        "outage overlapped any part of the half-hour." in article_text(page, "q5")
    )


# Saved outputs that carry a note on how the committed query differs: the
# note, and a column each still reads by.
NOTED_SAVED_OUTPUTS = {
    "q3_metered_vs_notified.csv": (
        "# The query's column names changed after this read (q1_ and q3_ are now "
        "p25_ and p75_); the query as committed at the read also returned "
        "under_delivery_share_excluding_zero, which this output does not hold; "
        "every figure is as read.",
        "q1_deviation_mwh",
    ),
    "q3_coverage.csv": (
        "# Section 4 of the query was added after this read.",
        "breakdown",
    ),
    "q6_publication_lag.csv": (
        "# The committed query also returns first_published_from and "
        "first_published_to, which this output does not hold; every figure is as "
        "read.",
        "median_lag_minutes",
    ),
    "q25_instruction_fragmentation.csv": (
        "# The committed query changed after this read: the related window at a "
        "half-hour boundary (16 October), the capture kept for each ramp point "
        "(25 October), and its coverage rows, where the row for acceptances "
        "captured more than once is labelled acceptances spanning more than one "
        "capture beside a new row for ramp points captured more than once; every "
        "figure is as read.",
        "measure",
    ),
    "q25_instruction_fragmentation_corrected.csv": (
        "# The committed query labels the row for acceptances captured more than "
        "once acceptances spanning more than one capture and adds a row for ramp "
        "points captured more than once; every figure is as read.",
        "measure",
    ),
    "q12_instructed_vs_residual.csv": (
        "# The query gained first_date and last_date after this read; the query "
        "as committed at the read also returned "
        "median_unexplained_shortfall_mwh, which this output does not hold; every "
        "figure is as read.",
        "instructed_share_of_abs_deviation",
    ),
    "q29_final_forecast_error_by_month_and_block.csv": (
        "# The query's label for the overall row changed after this read; every "
        "figure is as read.",
        "mean_absolute_error",
    ),
}


def saved_outputs_not_read_under_their_note():
    """Noted saved outputs whose second line is not their note, or whose
    rows no longer read by their column."""
    unread = []
    for name, (note, column) in NOTED_SAVED_OUTPUTS.items():
        text = (REPOSITORY / "docs" / "results" / name).read_text(encoding="utf-8")
        second_line = text.splitlines()[1]
        rows = saved_output_rows(name)
        if second_line != note or not rows or column not in rows[0]:
            unread.append(name)
    return unread


def test_every_saved_output_with_a_note_still_reads_by_its_columns():
    assert saved_outputs_not_read_under_their_note() == []


def medians_by_group(rows, column):
    medians = {}
    for row in rows:
        medians[row["fuel_group"]] = Decimal(row[column])
    return medians


def groups_with_a_median_of_zero(medians):
    tied = []
    for group, median in sorted(medians.items()):
        if median == 0:
            tied.append(group)
    return tied


def untied_groups_by_median(medians):
    """The groups whose median is not zero, largest median first."""
    untied = []
    for group, median in medians.items():
        if median != 0:
            untied.append((median, group))
    ordered = []
    for _median, group in sorted(untied, reverse=True):
        ordered.append(group)
    return ordered


def test_q22_states_its_first_run_condition_and_its_ties_from_the_saved_output():
    rows = saved_output_rows("q22_first_against_latest_run.csv")
    first = medians_by_group(rows, "first_median_exact")
    latest = medians_by_group(rows, "latest_median_exact")
    q22 = flattened(register_section(QUESTIONS.read_text(encoding="utf-8"), "Q22"))

    assert groups_with_a_median_of_zero(first) == [
        "coal",
        "hydro",
        "interconnector",
        "pumped_storage",
    ]
    assert groups_with_a_median_of_zero(latest) == groups_with_a_median_of_zero(first)
    assert untied_groups_by_median(latest) == untied_groups_by_median(first)
    assert min(first.values()) >= 0 and min(latest.values()) >= 0
    assert "No numeric threshold. Unit-periods need a first-run value. - **Ties" in q22
    assert (
        "- **Ties (added 25 November 2026, after the read).** Coal, hydro, "
        "interconnector and pumped storage have a median of 0 on both runs; tied "
        "medians have no order between them. The other groups keep their order, "
        "and no median changes sign." in q22
    )


def test_q23_counts_idle_units_and_promises_no_further_version():
    questions = QUESTIONS.read_text(encoding="utf-8")
    q23 = flattened(register_section(questions, "Q23"))

    assert "left out and counted. Idle units count; their share is not reported." in q23
    assert q23.endswith("are not applied and no unit is called inaccurate.")
    assert "ensitivity" not in q23


def test_the_stored_forecasts_one_check_is_worded_as_unpublished():
    page = PAGE.read_text(encoding="utf-8")
    questions = flattened(QUESTIONS.read_text(encoding="utf-8"))
    check = (
        "(17 August to 10 September 2026), it matched a late poll of the archive, "
        "in a check whose output is not published"
    )

    assert f"Where GridSkew's archive overlaps it {check}; after" in page
    assert f"where GridSkew's archive could check it {check}." in questions


def test_remit_is_worded_as_law_in_great_britain_with_eu_guidance_as_context():
    page = PAGE.read_text(encoding="utf-8")
    questions = QUESTIONS.read_text(encoding="utf-8")

    assert (
        "In Great Britain REMIT is enforced by Ofgem; the EU energy regulator "
        "ACER's one-hour guidance is EU guidance, given as context, not a test."
        in article_text(page, "q6")
    )
    assert (
        "In Great Britain REMIT is assimilated law derived from Regulation (EU) "
        "1227/2011, enforced by Ofgem. ACER's one-hour guidance is EU guidance; "
        "it and Regulation 543/2013 Article 15 are context, not a compliance test."
        in flattened(register_section(questions, "Q6"))
    )


DAG_COUNT_WORDS = {2: "Two", 13: "Thirteen"}


def dag_files_by_kind():
    """How many DAG files run dbt and how many collect data."""
    counts = {"dbt": 0, "collection": 0}
    for path in sorted((REPOSITORY / "dags").glob("*.py")):
        kind = "dbt" if path.name.startswith("gridskew__dbt_") else "collection"
        counts[kind] += 1
    return counts


def test_the_readme_counts_the_dags_in_the_dags_folder():
    readme = flattened(README.read_text(encoding="utf-8"))
    counts = dag_files_by_kind()

    assert (
        f"{DAG_COUNT_WORDS[counts['collection']]} Airflow DAGs collect carbon intensity "
        "forecasts and outturn" in readme
    )
    assert f"{DAG_COUNT_WORDS[counts['dbt']]} more run dbt: hourly" in readme


def test_the_readme_says_the_carbon_data_were_modified():
    assert (
        "licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); "
        "modified: aggregated by GridSkew."
        in flattened(README.read_text(encoding="utf-8"))
    )


def test_the_unit_glossary_states_each_conversion_the_page_uses():
    # Worked: a half-hour is 0.5 hours; giga is 1,000 mega; tera is a
    # million mega.
    half_hour_hours = Decimal("0.5")
    megawatts_per_gigawatt = 1000

    expected = [
        f"1 MW for a half-hour is {Decimal(1) * half_hour_hours} MWh. "
        f"1 GW is {megawatts_per_gigawatt:,} MW; 1 TWh is a million MWh."
    ]
    assert missing_phrases(expected) == []


def figure_caption(page, chart_id):
    """The caption under the chart with this id."""
    after_chart = page.split(f'<div class="chart" id="{chart_id}">', 1)[1]
    return after_chart.split("<figcaption>", 1)[1].split("</figcaption>", 1)[0]


def test_each_scale_figure_links_the_context_count_it_divides_by():
    # Each link stands in the fold-out entry that states the scale figure.
    page = PAGE.read_text(encoding="utf-8")

    assert (
        "of half-hours. "
        '<a href="results/context_plan_per_unit_half_hour.csv">Context data</a></dd>'
        in fold_outs(page, "q3")
    )
    assert (
        'MWh a half-hour. <a href="results/context_demand.csv">Context data</a></dd>'
        in fold_outs(page, "q28")
    )
    assert (
        '<a href="results/context_carbon_intensity.csv">context data</a></dd>'
        in fold_outs(page, "q28")
    )
    assert (
        'a context count. <a href="results/context_demand.csv">Context data</a></dd>'
        in fold_outs(page, "q29")
    )


WORDING_PATTERNS = REPOSITORY / "tests" / "fixtures" / "public_wording_patterns.txt"


def on_a_register_pattern():
    """The on-a-register wording, as the wording file holds it."""
    for line in WORDING_PATTERNS.read_text(encoding="utf-8").splitlines():
        name, _separator, pattern = line.partition(" = ")
        if name == "on_a_register":
            return pattern
    raise AssertionError("the wording file holds no on_a_register pattern")


def is_a_context_label(label, kind):
    """Whether a label reads 'context only: <kind>, not a ' and then the
    on-a-register wording, and nothing else."""
    shape = f"context only: {kind}, not a (?:{on_a_register_pattern()})"
    return re.fullmatch(shape, label, re.IGNORECASE) is not None


def test_each_context_row_is_labelled_a_denominator_not_a_result():
    assert is_a_context_label(CONTEXT_LABEL, "denominator")
    assert is_a_context_label(DISTRIBUTION_LABEL, "distribution")
    assert is_a_context_label(BREAKDOWN_LABEL, "breakdown")
    assert not is_a_context_label(CONTEXT_LABEL, "breakdown")
    for name in [
        "context_carbon_intensity.csv",
        "context_demand.csv",
        "context_plan_per_unit_half_hour.csv",
        "context_plan_pct_capacity.csv",
        "context_q6_start_alignment.csv",
        "context_q25_battery_named_units.csv",
    ]:
        for row in saved_output_rows(name):
            assert row["label"] == CONTEXT_LABEL


def test_each_lag_quartile_row_is_labelled_distribution_context_not_a_result():
    for name in ["context_q6_lag_quartiles.csv", "context_q6_publication_timing.csv"]:
        for row in saved_output_rows(name):
            assert row["label"] == DISTRIBUTION_LABEL


def test_each_breakdown_row_is_labelled_context_not_a_result():
    for name in [
        "context_q5_drawing_power.csv",
        "context_cohort_unit_types.csv",
        "context_cohort_change.csv",
        "context_ii_zero_readings.csv",
        "context_q23_one_week_and_later_runs.csv",
        "context_q28_wind_level.csv",
        "context_q30_wind_miss_sign.csv",
    ]:
        for row in saved_output_rows(name):
            assert row["label"] == BREAKDOWN_LABEL


def test_chart_reading_lines_give_scale_from_the_context_counts(written):
    # The fixture, unlike the real counts: wind's mean plan 18.25 MWh and
    # median 7.25, gas 12.35 TWh planned, wind's 700 MWh of under-delivery
    # against 5,600 MWh planned (12.5%), 124.5 g/kWh; each half rounds up.
    # The median wind unit plans 20.05% of capacity in September 2025, one
    # point more each month, so 31.05% in August 2026 (and 32.05% in the
    # provisional September, which the range leaves out).
    q23_scale = (
        "For scale, the median wind unit planned 20 to 31% of its capacity, "
        "depending on the month."
    )
    assert (
        "a wind unit's median plan is 7.3 MWh a half-hour (mean 18.3)."
        in reading_line(written, "q3_gap_from_plan_by_fuel_group.vl.json")
    )
    assert q23_scale in reading_line(written, "q23_typical_gap_by_fuel_group.vl.json")
    assert q23_scale in reading_line(written, "q23_monthly_gap_by_fuel_group.vl.json")
    assert "Sep 2025 – Aug 2026" in x_axis_titles(
        written, "q23_typical_gap_by_fuel_group.vl.json"
    )
    assert "the mean carbon intensity was 125 g/kWh." in reading_line(
        written, "q29_error_by_month.vl.json"
    )
    assert "wind planned 0.0 TWh over these dates and gas 12.4 TWh." in (
        reading_line(written, "q12_instructed_share_by_fuel_group.vl.json")
    )
    assert "wind's under-delivery is about 13% of the energy it planned." in (
        reading_line(written, "q5_under_delivery_by_outage_notice.vl.json")
    )
    assert "the mean carbon intensity was 125 g/kWh." in reading_line(
        written, "q29_error_by_time_of_day.vl.json"
    )
    assert "(NESO's initial outturn), for scale only." in reading_line(
        written, "q29_context_demand_by_month.vl.json"
    )


def test_a_q23_month_with_no_planned_output_context_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    without_lines_containing(saved / "context_plan_pct_capacity.csv", "wind,2026-03-01")

    with pytest.raises(ValueError, match="no planned output"):
        write_charts(saved, tmp_path / "out")


Q1_CHART = "q1_median_drift_by_time_and_band.vl.json"


def test_q1s_chart_states_its_half_hours_dates_and_the_rows_left_out(written):
    # The fixture's verdict: 2,000 half-hours from 20 August to 30 September
    # 2026; its very low and unclassified bands hold no half-hours.
    assert reading_line(written, Q1_CHART).endswith(
        "the API gave its first forecast. 2,000 half-hours, 20 Aug 2026 – "
        "30 Sep 2026. Rows with no half-hours are left out: very low band, no "
        "band."
    )


def test_q1s_chart_names_no_row_left_out_when_every_cell_holds_half_hours(
    tmp_path,
):
    saved = saved_outputs_folder(tmp_path / "saved")
    with_dates_moved(
        saved / "q1_forecast_drift_splits.csv",
        {
            "unclassified,0,,": "unclassified,2,4.0,1.000000",
            "very low,0,,": "very low,5,1.0,0.600000",
        },
    )
    write_charts(saved, tmp_path / "out")

    assert reading_line(tmp_path / "out", Q1_CHART).endswith(
        "the API gave its first forecast. 2,000 half-hours, 20 Aug 2026 – 30 Sep 2026."
    )


def test_q1s_chart_prints_each_median_as_its_csv_writes_it(written):
    assert text_fields_drawn(written, Q1_CHART) == [("median_label", "median_drift")]


def test_a_q1_split_without_one_of_its_cells_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    without_lines_containing(saved / "q1_forecast_drift_splits.csv", "very high")

    with pytest.raises(ValueError, match="no row for first-forecast band, 'very high'"):
        write_charts(saved, tmp_path / "out")


def test_the_page_and_rules_speak_as_i_or_neutrally():
    voice = re.compile(r"\b(we|our|us)\b", re.IGNORECASE)
    page = PAGE.read_text(encoding="utf-8")

    assert voice.findall(page) == []
    assert voice.findall(QUESTIONS.read_text(encoding="utf-8")) == []
    assert page.count("<summary>How I measured it</summary>") == 17
    assert '<h2 id="found-title">What I found</h2>' in page


def test_q23s_answer_says_how_far_units_strayed_not_why():
    # With no instruction in force the gap can still hold a forecast miss,
    # energy bought outside the Balancing Mechanism, a declared cut or a
    # settlement estimate, so the page does not call it the unit's own.
    page = PAGE.read_text(encoding="utf-8")
    shown = in_view(page, "q23")

    assert "With no NESO instruction in force, the median" in answer_of(page, "q23")
    assert (
        "<p>The gap shows how far a unit strayed from its plan, not why.</p>" in shown
    )
    assert (
        "<dd>Only half-hours with no NESO instruction count. From September"
        in fold_outs(page, "q23")
    )
    assert "unit's own" not in shown.split("</h3>", 1)[1]


def test_the_page_states_each_energy_figure_beside_its_saved_output_range():
    q3 = output_date_range(saved_output_rows("q3_metered_vs_notified.csv"))
    q5 = output_date_range(saved_output_rows("q5_shortfall_with_known_outage.csv"))
    q24 = output_month_range(saved_output_rows("q24_system_action_share.csv"))
    q28 = output_date_range(saved_output_rows("q8_q28_history.csv"))
    page = PAGE.read_text(encoding="utf-8")
    shown = [
        f"About a quarter (24%, {q5}) came with one",
        f"possibly for network reasons:</strong> 97% (settlement months {q24}).",
        f"the biggest carbon misses ({q28}), under-delivery",
        f"<p><strong>Did later meter data change one week's fuel-group picture? "
        f"No.</strong> For {Q2_WEEK}, no group's median gap changed sign or order, "
        "and the biggest change was 0.03 MWh per unit per half-hour.",
        f"<figcaption>16.4 TWh of under-delivery, {q5}. Source",
        f"<figcaption>Settlement months {q24}, units with a published fuel type.",
        f"<figcaption>17,278 half-hours, {q28}, all units together.",
        f"<figcaption>Every unit, settlement dates {Q2_WEEK}, three-week to "
        "two-month runs.",
    ]
    folded = [
        f"Below plan in the typical half-hour, for most groups, with wide spreads ({q3}).",
        f"0.03 MWh per unit per half-hour (bioenergy, {Q2_WEEK}):",
        f"of under-delivery ({q5}) came with a published outage",
        f"of 13.4 TWh, settlement months {q24};",
        f"Most of it for gas, hydro and pumped storage ({q3}): 83 to 91%",
        "NESO never instructed a nuclear unit in those dates.",
        "Mean under-delivery per half-hour: 762 MWh",
        f"Material by the method's 2% threshold: for settlement dates {Q2_WEEK}, 3.4%",
    ]

    assert phrases_missing_from(FOLD_OUT.sub("", page), shown) == []
    assert phrases_missing_from(every_fold_out(page), folded) == []


def test_q23_titles_state_no_figures(written):
    # The figures are the answer's, stated once; a title states the point.
    names = [
        "q23_typical_gap_by_fuel_group.vl.json",
        "q23_monthly_gap_by_fuel_group.vl.json",
    ]
    assert titles_with_figures(written, names) == {}


def test_q3_chart_draws_only_the_years_spread_and_median(written):
    # No other period's marks over the year's: Q22's week has its own table.
    assert marks_by_x_field(written, "q3_gap_from_plan_by_fuel_group.vl.json") == [
        ("rule", "q1_deviation_mwh"),
        ("point", "median_deviation_mwh"),
    ]


def test_q3_energy_chart_draws_one_bar_a_group_with_its_direction_in_words(
    written,
):
    # The bar's length is the percentage; the words beside it are the CSV's
    # label, so the direction is read, not inferred from a sign.
    assert marks_by_x_field(written, Q3_ENERGY_CHART) == [
        ("bar", "against_plan_pct"),
        ("text", "against_plan_pct"),
    ]
    assert text_fields_drawn(written, Q3_ENERGY_CHART) == [
        ("label", "against_plan_pct")
    ]
    assert "left: below plan · right: above plan" in x_axis_titles(
        written, Q3_ENERGY_CHART
    )
    assert titles_with_figures(written, [Q3_ENERGY_CHART]) == {}


def test_a_one_way_group_with_no_planned_energy_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    with_dates_moved(
        saved / "q3_metered_vs_notified.csv", {"9000.0,9500.0": "9000.0,0.0"}
    )

    with pytest.raises(ValueError, match="no planned energy for wind"):
        write_charts(saved, tmp_path / "out")


def test_metered_energy_equal_to_plan_is_labelled_level_not_above_or_below(
    tmp_path,
):
    saved = saved_outputs_folder(tmp_path / "saved")
    with_dates_moved(
        saved / "q3_metered_vs_notified.csv", {"6906.9,6900.0": "6900.0,6900.0"}
    )
    write_charts(saved, tmp_path / "out")

    assert "gas_turbine,300,6900.0,6900.0,0.0000,level with plan\n" in (
        tmp_path / "out" / Q3_ENERGY_CSV
    ).read_text(encoding="utf-8")


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

    fuel_list = page.split("const fuelGroupColours = [", 1)[1].split("];", 1)[0]

    assert f'vega.scheme("{FUEL_GROUP_SCHEME}", fuelGroupScheme())' in page
    assert FUEL_GROUP_COLOUR_PAIR.findall(fuel_list) == list(
        FUEL_GROUP_COLOURS.values()
    )
    # Each mode its own steps.
    assert "scheme.push(prefersDark ? dark : light);" in page


def test_labels_inside_a_bar_take_the_page_colour_in_either_mode(written):
    # The strong grey flips with the mode, so its label takes the page's
    # own colour, set by the page's chart theme.
    page = PAGE.read_text(encoding="utf-8")

    assert inside_label_styles(written) == {
        "q24_flagged_share_by_fuel_group.vl.json": [(INSIDE_LABEL_STYLE, None)]
    }
    assert f'"{INSIDE_LABEL_STYLE}": {{ color: pageColour("--surface") }}' in page


def test_the_page_registers_the_split_greys_for_each_mode():
    page = PAGE.read_text(encoding="utf-8")
    greys = []
    for step, (light, dark) in SPLIT_GREYS.items():
        greys.append(f'    {step}: ["{light}", "{dark}"],')

    assert "\n".join(greys) in page
    assert (
        f'vega.scheme("{SPLIT_TWO_SCHEME}", splitScheme(["strong", "soft"]));' in page
    )
    assert (
        f'vega.scheme("{SPLIT_THREE_SCHEME}", '
        'splitScheme(["strong", "mid", "soft"]));' in page
    )
    assert page.count("scheme.push(prefersDark ? dark : light);") == 2


def channel_luminance(channel_hex):
    channel = int(channel_hex, 16) / 255
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(colour):
    red = channel_luminance(colour[1:3])
    green = channel_luminance(colour[3:5])
    blue = channel_luminance(colour[5:7])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first, second):
    lighter, darker = sorted(
        [relative_luminance(first), relative_luminance(second)], reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_every_split_grey_stands_out_from_the_page_and_its_neighbour():
    # WCAG's 3:1 for graphics, in both modes, for the soft grey too; each
    # grey apart from the next one down; and no split grey also means
    # interconnectors, a neutral result or a comparison.
    page = PAGE.read_text(encoding="utf-8")
    light_page, dark_page = re.findall(r"--surface: (#[0-9a-f]{6});", page)
    light_text = re.findall(r"--text: (#[0-9a-f]{6});", page)[0]
    steps = ["strong", "mid", "soft"]

    # The light strong grey stays lighter than the page's text, so a bar
    # part does not read as text.
    assert contrast_ratio(SPLIT_GREYS["strong"][0], light_page) < contrast_ratio(
        light_text, light_page
    )

    for mode, surface in enumerate([light_page, dark_page]):
        greys = []
        for step in steps:
            greys.append(SPLIT_GREYS[step][mode])
        for grey in greys:
            assert contrast_ratio(grey, surface) >= 3.0
        for upper, lower in zip(greys[:-1], greys[1:], strict=True):
            assert contrast_ratio(upper, lower) >= 1.8
    other_greys = set(FUEL_GROUP_COLOURS["interconnector"])
    other_greys.update([NEUTRAL_MARK_COLOUR, COMPARISON_COLOUR])
    for light, dark in SPLIT_GREYS.values():
        assert light not in other_greys
        assert dark not in other_greys


def test_every_chart_shows_fuel_groups_by_their_display_names_only(written):
    assert specs_encoding_raw_fuel_groups(written) == []
    assert fuel_group_names_by_spec(written) == {
        "q12_instructed_share_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q23_monthly_gap_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q23_typical_gap_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q24_flagged_share_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q25_short_instructions_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
        "q3_energy_against_plan_by_fuel_group.vl.json": FUEL_GROUP_DISPLAY_NAMES,
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


class ChartBoxes(HTMLParser):
    """The start tags inside each div.chart, by the box's id."""

    def __init__(self):
        super().__init__()
        self.boxes = {}
        self.open_box = None
        self.div_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.open_box is not None:
            self.boxes[self.open_box].append((tag, attributes))
        elif tag == "div" and "chart" in (attributes.get("class") or "").split():
            self.open_box = attributes["id"]
            self.boxes[self.open_box] = []
        else:
            return
        if tag == "div":
            self.div_depth += 1

    def handle_endtag(self, tag):
        if self.open_box is None or tag != "div":
            return
        self.div_depth -= 1
        if self.div_depth == 0:
            self.open_box = None


def chart_boxes():
    parser = ChartBoxes()
    parser.feed(PAGE.read_text(encoding="utf-8"))
    return parser.boxes


def specs_by_chart_box():
    return dict(VEGA_EMBED_TARGET.findall(PAGE.read_text(encoding="utf-8")))


def spec_description(spec_target):
    return json.loads(shipped_path(spec_target).read_text(encoding="utf-8"))[
        "description"
    ]


def chart_image_style():
    page = PAGE.read_text(encoding="utf-8")
    return page.split("  div.chart > img {\n", 1)[1].split("\n  }\n", 1)[0]


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
        "results/q3_energy_against_plan_by_fuel_group.vl.json",
        "results/q3_gap_from_plan_by_fuel_group.vl.json",
        "results/q12_instructed_share_by_fuel_group.vl.json",
        "results/q5_under_delivery_by_outage_notice.vl.json",
        "results/q38_output_and_gap_by_month.vl.json",
        "results/q24_flagged_share_by_fuel_group.vl.json",
        "results/q37_turn_up_by_wind_turn_down_band.vl.json",
        "results/q25_short_instructions_by_fuel_group.vl.json",
        "results/q29_error_by_time_of_day.vl.json",
        "results/q29_error_by_month.vl.json",
        "results/q29_context_demand_by_month.vl.json",
        "results/q28_shortfall_index_by_miss_fifth.vl.json",
        "results/q28_reverse_carbon_miss_by_under_delivery_fifth.vl.json",
        "results/q28_wind_shortfall_per_gw.vl.json",
        "results/q30_rank_correlations.vl.json",
        "results/q1_median_drift_by_time_and_band.vl.json",
        "results/q2_moved_energy_by_unit_type.vl.json",
    ]
    assert not_shipped([*local_targets(page_tags()), *specs]) == []


def test_every_chart_box_holds_a_png_of_its_chart_for_readers_without_scripts():
    # vega-embed empties the box before it draws the chart, so the image
    # shows only where the chart does not. Its alt text is the spec's
    # description, the chart's title.
    boxes = chart_boxes()
    specs = specs_by_chart_box()

    assert sorted(boxes) == sorted(specs)
    for box, spec in specs.items():
        image = spec.removesuffix(".vl.json") + ".png"
        alt = spec_description(spec)
        assert alt.strip()
        assert boxes[box] == [("img", {"src": image, "alt": alt, "loading": "lazy"})]
        assert shipped_path(image).is_file()


def test_the_chart_png_fits_its_box_on_a_white_ground_in_either_colour_mode():
    # The PNGs are drawn for a light page.
    style = chart_image_style()

    assert "    max-width: 100%;" in style
    assert "    height: auto;" in style
    assert "    background: #ffffff;" in style


def test_the_committed_specs_and_chart_csvs_are_what_the_script_writes(tmp_path):
    # The committed folder is the input as it stands, attribution lines and
    # all. The PNGs have their own test below.
    results = REPOSITORY / "docs" / "results"
    written = tmp_path / "written"
    write_charts(results, written)

    stale = []
    for path in sorted(written.iterdir()):
        if path.suffix == ".png":
            continue
        if path.read_bytes() != (results / path.name).read_bytes():
            stale.append(path.name)
    assert stale == []


def png_width_and_height(path):
    """A PNG's size in pixels, from the header chunk that opens the file."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert header[12:16] == b"IHDR"
    return struct.unpack(">II", header[16:24])


def test_the_script_writes_each_committed_chart_png_at_its_size(tmp_path):
    # Text is drawn with the machine's fonts, so pixels can differ between
    # platforms; the set of charts and each one's size cannot.
    results = REPOSITORY / "docs" / "results"
    written = tmp_path / "written"
    write_charts(results, written)

    written_sizes = {}
    for path in sorted(written.glob("*.png")):
        written_sizes[path.name] = png_width_and_height(path)
    committed_sizes = {}
    for path in sorted(results.glob("*.png")):
        committed_sizes[path.name] = png_width_and_height(path)
    assert len(committed_sizes) == 19
    assert written_sizes == committed_sizes


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="the committed PNGs were drawn with Windows fonts",
)
def test_the_committed_chart_pngs_are_what_the_script_writes(tmp_path):
    results = REPOSITORY / "docs" / "results"
    written = tmp_path / "written"
    write_charts(results, written)

    stale = []
    for path in sorted(written.glob("*.png")):
        if path.read_bytes() != (results / path.name).read_bytes():
            stale.append(path.name)
    assert len(sorted(written.glob("*.png"))) == 19
    assert stale == []


ANY_TAG = re.compile(r"<[^>]+>")
ASTERISK_LINK = re.compile(r'<a class="ref" [^>]*>\*</a>')
ASTERISK_LABEL = re.compile(r"Details, (?P<question>Q\d+)(?:'s later look)?")
SENTENCE_END = re.compile(r"[.?!](?=\s|$)")
# Where a reader glances: none of these names a question by its number.
GLANCED_PARTS = {
    "heading": re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.DOTALL),
    "answer": re.compile(r'<p class="answer">(.*?)</p>', re.DOTALL),
    "lead line": re.compile(r'<p class="lead-in">(.*?)</p>', re.DOTALL),
    "caption": re.compile(r"<figcaption>(.*?)</figcaption>", re.DOTALL),
    "contents list": re.compile(r'<ol id="contents-list">(.*?)</ol>', re.DOTALL),
    "headline list": re.compile(r'<ul class="findings">(.*?)</ul>', re.DOTALL),
    "footer": re.compile(r"<footer>(.*?)</footer>", re.DOTALL),
}
# Asterisks whose target is not their own question's article: the later
# look inside Q28's article, and the limitation on how the carbon API
# labels its half-hours.
ASTERISK_TARGETS_ELSEWHERE = {
    "Details, Q28's later look": "#q28-reverse-title",
    "Details, half-hour alignment": "#carbon-label-alignment",
    "Details, wind's shortfall": "#wind-shortfall",
    "Details, stored forecast": "#stored-forecast",
}
WIND_LINK = (
    '<a class="ref" href="#wind-shortfall" aria-label="Details, wind\'s shortfall">'
    "*</a>"
)
WIND_RESULT = f"wind's shortfall{WIND_LINK}"
# The one line each article shows on what its answer cannot say.
VISIBLE_LIMITS = {
    "q23": "<p>The gap shows how far a unit strayed from its plan, not why.</p>",
    "q3": (
        "<p>These totals include half-hours when NESO told units to change "
        "output, such as asking wind farms to cut back; instructions make up "
        "most of gas's and hydro's gaps"
        '<a class="ref" href="#q12" aria-label="Details, Q12">*</a> and none of '
        "nuclear's. So each percentage mixes NESO's instructions with units "
        "straying from their own plans.</p>"
    ),
    "q12": (
        "<p>The part not instructed is not all the unit's doing: it holds "
        "services NESO buys outside the balancing mechanism, and can hold wind "
        "forecast error and estimated meter readings.</p>"
    ),
    "q5": '<p>"No notice" is not "no outage": small units rarely publish.</p>',
    "q6": (
        "<p>The written start is often a round time, so this is not the time "
        "from failure to notice.</p>"
    ),
    "q24": (
        "<p>The flag is NESO's label in real time, not a measured volume of "
        "network constraint, and later corrections to it are not read.</p>"
    ),
    "q37": (
        "<p>This shows what NESO did in the same half-hours, not that one "
        "instruction replaced another; no location and no price is read.</p>"
    ),
    "q38": (
        "<p>In these months nine of the ten units' highest half-hours are below "
        "their registered capacity, and a notice and a gap going together in "
        "time is not a cause.</p>"
    ),
    "q25": (
        "<p>This covers only units with a published fuel type and measures "
        "each instruction by the run of instructions that touch or overlap it; "
        "the other group is mostly, not wholly, batteries.</p>"
    ),
    "q29": (
        "<p>The times rest on the API's half-hour labels, which may not match "
        'the half-hours they describe<a class="ref" '
        'href="#carbon-label-alignment" aria-label="Details, half-hour '
        'alignment">*</a>; the "actual" is NESO\'s own estimate.</p>'
    ),
    "q28": (
        "<p>Sorting by the size of the miss cannot say whether the grid came "
        "out cleaner or dirtier than forecast; the chart below looks at "
        f"direction. Neither shows cause.{WIND_LINK}</p>"
    ),
    "wind-shortfall": (
        "<p><strong>A pattern, not proof of cause, and not the same everywhere:"
        "</strong> where the least wind was planned, more shortfall went with a "
        "cleaner grid (−30.4). With nothing held fixed the figure is −23.7: "
        "windy half-hours are both clean and prone to large shortfalls.</p>"
    ),
    "q8": (
        "<p>This shows what goes together in one year of the stored forecast, "
        "not what causes a miss.</p>"
    ),
    "q30": (
        "<p>Only metered wind counts, with none from local networks; this shows "
        "what goes together, not why.</p>"
    ),
    "q1": (
        "<p>This covers one window. On its earliest half-hours the forecast fell "
        "on average between a day ahead and its final version"
        '<a class="ref" href="#q10" aria-label="Details, Q10">*</a>, across a '
        "change in when the archive polls.</p>"
    ),
    "q10": (
        "<p>On the 365 earliest half-hours (19 August to 4 September 2026) the "
        "forecast ran above the outturn estimate, not below it; the turn also "
        "coincides with a change in when the archive polls.</p>"
    ),
    "q2": (
        "<p>One week only. Over three-quarters of the later revision is in "
        "suppliers' customer accounts, which the other answers mostly leave out; "
        "three gas units carry most of the rest.</p>"
    ),
}


def visible_text(markup):
    """Markup as a reader sees it: the text between its tags."""
    return ANY_TAG.sub("", markup)


def without_asterisks(markup):
    return ASTERISK_LINK.sub("", markup)


def sentence_count(markup):
    return len(SENTENCE_END.findall(visible_text(markup)))


def glanced_parts(page):
    """Every heading, answer, lead line and caption, the contents and
    headline lists and the footer, each with its kind."""
    parts = []
    for kind, pattern in GLANCED_PARTS.items():
        for part in pattern.findall(page):
            parts.append((kind, part))
    return parts


def parts_naming_a_question(parts):
    naming = []
    for kind, part in parts:
        if question_ids_in(visible_text(part)):
            naming.append((kind, part))
    return naming


def kinds_of(parts):
    kinds = set()
    for kind, _part in parts:
        kinds.add(kind)
    return kinds


def elements_with_class(tags, class_name):
    found = []
    for tag, attributes in tags:
        if class_name in (attributes.get("class") or "").split():
            found.append(tag)
    return found


def test_no_question_number_shows_in_a_heading_answer_caption_or_list():
    # A reader meets each question in plain words; its number is carried by
    # an asterisk's label, not shown. No element wears the tag class, so the
    # headline and contents lists hold none either.
    page = PAGE.read_text(encoding="utf-8")
    parts = glanced_parts(page)

    assert kinds_of(parts) == set(GLANCED_PARTS)
    assert parts_naming_a_question(parts) == []
    assert elements_with_class(page_tags(), "tag") == []


class AsteriskLinks(HTMLParser):
    """Each a.ref on the page: its attributes and the text it shows."""

    def __init__(self):
        super().__init__()
        self.links = []
        self.open_link = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and "ref" in (attributes.get("class") or "").split():
            self.open_link = {"attributes": attributes, "text": ""}

    def handle_data(self, data):
        if self.open_link is not None:
            self.open_link["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.open_link is not None:
            self.links.append(self.open_link)
            self.open_link = None


def asterisk_links():
    parser = AsteriskLinks()
    parser.feed(PAGE.read_text(encoding="utf-8"))
    return parser.links


def asterisk_target(label):
    """Where an asterisk with this label must lead: its question's article,
    or the place named for it."""
    if label in ASTERISK_TARGETS_ELSEWHERE:
        return ASTERISK_TARGETS_ELSEWHERE[label]
    named = ASTERISK_LABEL.fullmatch(label)
    if named is None:
        return None
    return f"#{named['question'].lower()}"


def asterisks_off_their_target(links):
    """Asterisks that do not show one asterisk, carry no label naming their
    question, or lead somewhere other than that question."""
    wrong = []
    for link in links:
        attributes = link["attributes"]
        target = asterisk_target(attributes.get("aria-label") or "")
        if link["text"] != "*" or attributes.get("href") != target:
            wrong.append(attributes)
    return wrong


def asterisks_leading_nowhere(links, page_ids):
    """Asterisks whose link is not an id on the page."""
    lost = []
    for link in links:
        target = link["attributes"].get("href") or ""
        on_page = target.startswith("#") and target[1:] in page_ids
        if not on_page:
            lost.append(target)
    return lost


def test_every_asterisk_is_labelled_with_its_question_and_leads_to_it():
    page = PAGE.read_text(encoding="utf-8")
    links = asterisk_links()
    page_ids = set(re.findall(r'\bid="([^"]+)"', page))

    assert len(links) == page.count('class="ref"') > 0
    assert asterisks_off_their_target(links) == []
    assert asterisks_leading_nowhere(links, page_ids) == []
    assert heading_anchor("Q5. Is the grid's price (PN) higher, when?") == (
        "q5-is-the-grids-price-pn-higher-when"
    )


def articles_off_the_pattern(page):
    """Articles without exactly one answer that leads in bold and runs to at
    most three sentences, or without a fold-out."""
    off = []
    for article_id in ARTICLE_IDS:
        article = article_text(page, article_id)
        if article.count('<p class="answer">') != 1:
            off.append(article_id)
            continue
        answer = answer_of(page, article_id)
        short = sentence_count(answer) <= 3
        leads_in_bold = answer.startswith("<strong>")
        folds_the_rest = FOLD_OUT.search(article) is not None
        if not (short and leads_in_bold and folds_the_rest):
            off.append(article_id)
    return off


def test_every_article_gives_one_short_answer_and_folds_the_rest_away():
    page = PAGE.read_text(encoding="utf-8")

    assert re.findall(r'<article id="([^"]+)">', page) == ARTICLE_IDS
    assert articles_off_the_pattern(page) == []
    assert sentence_count("One 3.5 g/kWh. <strong>Two?</strong> Three; four!") == 3


def limits_out_of_view(page):
    out_of_view = []
    for article_id, limit in VISIBLE_LIMITS.items():
        if limit not in in_view(page, article_id):
            out_of_view.append(article_id)
    return out_of_view


def test_every_article_states_its_main_limit_in_view():
    # One plain line on what the answer cannot say, shown without opening a
    # fold-out.
    page = PAGE.read_text(encoding="utf-8")

    assert sorted(VISIBLE_LIMITS) == sorted(ARTICLE_IDS)
    assert limits_out_of_view(page) == []


def q23_chart_months(with_the_provisional_month):
    """The first and last month Q23's monthly chart draws for wind, with or
    without the provisional last month."""
    months = []
    for row in saved_output_rows("q23_monthly_gap_by_fuel_group.csv"):
        if row["fuel_group"] != "wind":
            continue
        if row["provisional"] == "yes" and not with_the_provisional_month:
            continue
        months.append(row["month_name"])
    return f"{months[0]} – {months[-1]}"


def caption_windows(page):
    """Per chart box, the dates its caption gives: its saved output's where
    the output holds dates, else its read's or its rule's."""
    q3 = output_date_range(saved_output_rows("q3_metered_vs_notified.csv"))
    q5 = output_date_range(saved_output_rows("q5_shortfall_with_known_outage.csv"))
    q24 = output_month_range(saved_output_rows("q24_system_action_share.csv"))
    q37 = output_month_range(saved_output_rows(Q37_OUTPUT))
    q38 = output_month_range(saved_output_rows(Q38_OUTPUT))
    stored_year = output_date_range(saved_output_rows("q8_q28_history.csv"))
    q30 = output_date_range(saved_output_rows("q30_wind_vs_carbon_error.csv"))
    q25_first, q25_last = data_entry_range(page, "q25")
    return {
        "q23-typical-chart": f"whole months {q23_chart_months(False)}",
        "q23-monthly-chart": (
            f"{q23_chart_months(True)}; September 2026 is on early meter data"
        ),
        "q3-energy-chart": q3,
        "q3-chart": q3,
        "q12-chart": q3,
        "q5-chart": q5,
        "q24-chart": f"Settlement months {q24}",
        "q37-chart": f"Settlement months {q37}",
        "q38-chart": f"Settlement months {q38}",
        "q25-chart": f"{short_date(q25_first)} – {short_date(q25_last)}",
        "q29-time-chart": stored_year,
        "q29-month-chart": stored_year,
        "q29-demand-chart": stored_year,
        "q28-chart": stored_year,
        "wind-shortfall-chart": output_date_range(
            [wind_population(saved_output_rows(WIND_OUTPUT))]
        ),
        "q30-chart": q30,
        "q1-chart": ARCHIVE_WINDOW,
        "q2-chart": f"settlement dates {Q2_WEEK}",
    }


def caption_counts():
    """The count each caption opens with, from its saved output."""
    q3 = saved_output_rows("q3_metered_vs_notified.csv")
    q5 = saved_output_rows("q5_shortfall_with_known_outage.csv")
    q29 = saved_output_rows("q29_final_forecast_error_by_month_and_block.csv")
    q8_q28 = saved_output_rows("q8_q28_history.csv")
    q30 = saved_output_rows("q30_wind_vs_carbon_error.csv")
    verdict = one_row(saved_output_rows(Q1_VERDICT), "population_is_reached", "True")
    under_delivery_mwh = Decimal(item_row(q5, "1", "total")["positive_shortfall_mwh"])
    wind_rho = item_row(
        q30,
        "2",
        "rho_abs (primary): abs(wind error, outturn with cohort curtailment "
        "add-back) with abs(error_api_final)",
    )
    one_way = saved_output_rows(Q3_ENERGY_CSV)
    return {
        "one-way unit half-hours": f"{int(summed(one_way, 'unit_periods')):,}",
        "one-way groups": NUMBER_WORDS[len(one_way)],
        "unit half-hours": f"{int(summed(q3, 'unit_periods')):,}",
        "under-delivery, TWh": tenths(under_delivery_mwh / 1_000_000),
        "Q29 half-hours": f"{int(one_row(q29, 'section', '1')['half_hours']):,}",
        "Q28 half-hours": f"{int(one_row(q8_q28, 'section', '1')['half_hours']):,}",
        "wind half-hours": (
            f"{int(wind_population(saved_output_rows(WIND_OUTPUT))['half_hours']):,}"
        ),
        "Q30 half-hours": f"{int(wind_rho['half_hours']):,}",
        "Q1 half-hours": f"{int(verdict['complete_half_hours']):,}",
    }


def expected_captions(windows, counts):
    """Each chart's whole caption: count or scope, dates, source."""
    elexon = "Source: Elexon BMRS."
    carbon = "Source: NESO Carbon Intensity API."
    both = "Source: Elexon BMRS and NESO Carbon Intensity API."
    no_instruction = "Half-hours with no instruction in force"
    unit_half_hours = f"{counts['unit half-hours']} half-hours of individual units"
    q29_half_hours = f"{counts['Q29 half-hours']} half-hours"
    return {
        "q23-typical-chart": (
            f"{no_instruction}, {windows['q23-typical-chart']}. {elexon}"
        ),
        "q23-monthly-chart": (
            f"{no_instruction}, {windows['q23-monthly-chart']}. {elexon}"
        ),
        "q3-energy-chart": (
            f"{counts['one-way unit half-hours']} half-hours of individual units "
            f"in {counts['one-way groups']} fuel groups, "
            f"{windows['q3-energy-chart']}; gas's and nuclear's figures are "
            f"approximate. {elexon}"
        ),
        "q3-chart": f"{unit_half_hours}, {windows['q3-chart']}. {elexon}",
        "q12-chart": f"{unit_half_hours}, {windows['q12-chart']}. {elexon}",
        "q5-chart": (
            f"{counts['under-delivery, TWh']} TWh of under-delivery, "
            f"{windows['q5-chart']}. Source: Elexon BMRS and REMIT."
        ),
        "q24-chart": (
            f"{windows['q24-chart']}, units with a published fuel type. {elexon}"
        ),
        "q37-chart": f"{windows['q37-chart']}, every unit NESO instructed. {elexon}",
        "q38-chart": (
            f"{windows['q38-chart']}, the ten nuclear units. Source: Elexon BMRS "
            "and REMIT."
        ),
        "q25-chart": (
            "Instructions to units with a published fuel type, "
            f"{windows['q25-chart']}. {elexon}"
        ),
        "q29-time-chart": (
            f"{q29_half_hours}, {windows['q29-time-chart']}; times are UTC. {carbon}"
        ),
        "q29-month-chart": (
            f"The same {q29_half_hours}, {windows['q29-month-chart']}. {carbon}"
        ),
        "q29-demand-chart": (
            "For scale only: mean national demand on the same "
            f"{q29_half_hours}, {windows['q29-demand-chart']}. {elexon}"
        ),
        "q28-chart": (
            f"{counts['Q28 half-hours']} half-hours, {windows['q28-chart']}, "
            f"all units together. {both}"
        ),
        "wind-shortfall-chart": (
            f"{counts['wind half-hours']} half-hours, "
            f"{windows['wind-shortfall-chart']}, "
            f"wind units. {both}"
        ),
        "q30-chart": (
            f"{counts['Q30 half-hours']} half-hours, {windows['q30-chart']}. {both}"
        ),
        "q1-chart": (
            f"{counts['Q1 half-hours']} complete half-hours, "
            f"{windows['q1-chart']}. {carbon}"
        ),
        "q2-chart": (
            f"Every unit, {windows['q2-chart']}, three-week to two-month runs. {elexon}"
        ),
    }


def captions_not_as_expected(page, captions):
    """Per chart box whose caption differs, the caption the page gives."""
    different = {}
    for chart_id, caption in captions.items():
        on_page = figure_caption(page, chart_id)
        if on_page != caption:
            different[chart_id] = on_page
    return different


def captions_without_their_window(page, windows):
    without = []
    for chart_id, window in windows.items():
        if window not in figure_caption(page, chart_id):
            without.append(chart_id)
    return without


def test_every_chart_caption_gives_its_scope_its_dates_and_its_source():
    # The later look's chart shares the article and the half-hours of the
    # chart above it, whose caption gives the dates; every other caption
    # gives its own.
    page = PAGE.read_text(encoding="utf-8")
    windows = caption_windows(page)
    counts = caption_counts()
    later_look = (
        f"The same {counts['Q28 half-hours']} half-hours. Source: Elexon BMRS "
        "and NESO Carbon Intensity API."
    )

    assert sorted([*windows, "q28-reverse-chart"]) == sorted(chart_boxes())
    assert page.count("<figcaption>") == len(windows) + 1
    assert captions_without_their_window(page, windows) == []
    assert captions_not_as_expected(page, expected_captions(windows, counts)) == {}
    assert figure_caption(page, "q28-reverse-chart") == later_look
    assert article_text(page, "q28").index('id="q28-chart"') < (
        article_text(page, "q28").index('id="q28-reverse-chart"')
    )


def contents_links(page):
    """Each link in the contents list: its target and its words."""
    contents = page.split('<ol id="contents-list">', 1)[1].split("</ol>", 1)[0]
    return re.findall(r'<a href="#([^"]+)">([^<]+)</a>', contents)


def headings_in_page_order(page):
    """Each section's id and heading, then each of its articles', as they
    come in the page's main part."""
    main = page.split("<main>", 1)[1].split("</main>", 1)[0]
    return re.findall(
        r'<(?:section|article) id="([^"]+)"[^>]*>\s*<h[23][^>]*>([^<]+)</h[23]>',
        main,
    )


def test_the_contents_list_each_section_and_question_in_its_headings_words():
    page = PAGE.read_text(encoding="utf-8")
    listed = contents_links(page)

    assert listed == headings_in_page_order(page)
    assert len(listed) == len(ARTICLE_IDS) + 7


# The groups whose units send power one way: the only ones a metered total
# against a planned total is stated for.
ONE_WAY_FUEL_GROUPS = ["wind", "gas_turbine", "nuclear", "bioenergy", "hydro"]
GROUPS_WITHOUT_A_PERCENTAGE = ["pumped_storage", "other", "interconnector", "coal"]
DRAW_ON_OPENING = (
    'document.getElementById("{box}").closest("details")'
    '.addEventListener("toggle", function () {{\n'
    "    if (this.open) {{\n"
    '      vegaEmbed("#{box}", "{spec}", chartOptions);\n'
    "    }}\n"
    "  }});"
)


def against_plan_percentages(q3_rows):
    """Per fuel group with planned energy, its metered total as an exact
    percentage above (positive) or below its planned total."""
    percentages = {}
    for row in q3_rows:
        planned = Decimal(row["abs_pn_mwh"])
        if planned == 0:
            continue
        metered = Decimal(row["abs_metered_mwh"])
        percentages[row["fuel_group"]] = Decimal(100) * (metered - planned) / planned
    return percentages


def whole_percent(percentage):
    """The size of a percentage to the nearest whole percent, halves away
    from zero; its direction is said in words beside it."""
    return whole(abs(percentage))


def direction_in_words(percentage):
    if percentage > 0:
        return "above"
    return "below"


def twh(mwh):
    return tenths(Decimal(mwh) / 1_000_000)


def chart_boxes_inside_fold_outs(page):
    return re.findall(r'<div class="chart" id="([^"]+)">', every_fold_out(page))


def folded_charts_not_drawn_on_opening(page):
    """Chart boxes inside a fold-out that the page does not draw each time
    the fold-out opens, or draws at load as well."""
    specs = specs_by_chart_box()
    wrong = []
    for box in chart_boxes_inside_fold_outs(page):
        handler = DRAW_ON_OPENING.format(box=box, spec=specs[box])
        drawn_once_in_the_handler = page.count(f'vegaEmbed("#{box}"') == 1
        if handler not in page or not drawn_once_in_the_handler:
            wrong.append(box)
    return wrong


def percentages_with_idle_draw_taken_off(q3_rows, drawing_power_rows):
    """Per one-way group, its metered total against plan with the power its
    units drew when nothing was expected of them taken off, not added: the
    saved total adds the draw's size, so it comes off twice."""
    percentages = {}
    for group in ONE_WAY_FUEL_GROUPS:
        row = one_row(q3_rows, "fuel_group", group)
        drawn = Decimal(
            one_row(section_rows(drawing_power_rows, "1"), "fuel_group", group)[
                "drawing_power_expected_at_or_below_zero_mwh"
            ]
        )
        planned = Decimal(row["abs_pn_mwh"])
        sent_out_net = Decimal(row["abs_metered_mwh"]) - 2 * drawn
        percentages[group] = Decimal(100) * (sent_out_net - planned) / planned
    return percentages


def groups_whose_whole_percent_moves(as_saved, with_draw_taken_off):
    moved = []
    for group in ONE_WAY_FUEL_GROUPS:
        if whole_percent(as_saved[group]) != whole_percent(with_draw_taken_off[group]):
            moved.append(group)
    return moved


def instructed_share_bounds(q3_rows, q12_rows, fuel_group):
    """The least and most of a group's gap between its totals that NESO's
    instructions can account for. The gap is the instructed part plus the
    rest; the rest is no larger than its summed size, and the instructed
    part no larger than its own."""
    totals = one_row(q3_rows, "fuel_group", fuel_group)
    parts = one_row(q12_rows, "fuel_group", fuel_group)
    gap = abs(Decimal(totals["abs_metered_mwh"]) - Decimal(totals["abs_pn_mwh"]))
    rest = Decimal(parts["abs_unexplained_shortfall_mwh"])
    instructed = Decimal(parts["abs_instructed_deviation_mwh"])
    return {
        "at least": (gap - rest) / gap,
        "at most": min(instructed, gap + rest) / gap,
    }


def test_q3s_answer_gives_the_direction_then_each_groups_energy_against_plan():
    # The result is the direction of the typical half-hour; the
    # percentages are scale from the same saved output. Each is a whole
    # percent, halves away from zero, with "less" or "more" for its sign; a
    # percentage that rounded to nothing would hide its sign, so none may.
    # Gas leads the totals because its typical half-hour leans the other way.
    q3 = saved_output_rows("q3_metered_vs_notified.csv")
    against_plan = against_plan_percentages(q3)
    wind = against_plan["wind"]
    hydro = against_plan["hydro"]
    gas = against_plan["gas_turbine"]
    nuclear = against_plan["nuclear"]
    bioenergy = against_plan["bioenergy"]

    assert answer_of(PAGE.read_text(encoding="utf-8"), "q3") == (
        "<strong>Below plan in the typical half-hour, for most groups.</strong> "
        "Added up over the whole period, the totals can lean the other way: "
        "the energy metered at gas units came to about "
        f"{whole_percent(gas)}% more than planned, while wind units came to "
        f"{whole_percent(wind)}% less and hydro units {whole_percent(hydro)}% "
        "less. Nuclear and bioenergy units each came to about "
        f"{whole_percent(nuclear)}% less."
    )
    assert max(wind, hydro, nuclear, bioenergy) < 0 < gas
    assert Decimal(one_row(q3, "fuel_group", "gas_turbine")["median_deviation_mwh"]) > 0
    assert whole_percent(bioenergy) == whole_percent(nuclear)
    for group in ONE_WAY_FUEL_GROUPS:
        assert whole_percent(against_plan[group]) != "0"


def test_q3s_energy_chart_holds_the_one_way_groups_from_the_saved_output():
    # The committed chart data against the committed saved output. Coal has
    # no plan; the other three take power in as well as send it out, and
    # the saved totals add sizes whatever their direction.
    q3 = saved_output_rows("q3_metered_vs_notified.csv")
    against_plan = against_plan_percentages(q3)
    chart = saved_output_rows(Q3_ENERGY_CSV)
    drawn = []
    for row in chart:
        drawn.append(row["fuel_group"])

    assert drawn == ONE_WAY_FUEL_GROUPS
    assert sorted([*drawn, *GROUPS_WITHOUT_A_PERCENTAGE]) == sorted(
        column_strata_of(q3, "fuel_group")
    )
    for row in chart:
        saved = one_row(q3, "fuel_group", row["fuel_group"])
        percentage = against_plan[row["fuel_group"]]
        assert row["metered_mwh"] == saved["abs_metered_mwh"]
        assert row["planned_mwh"] == saved["abs_pn_mwh"]
        assert row["unit_periods"] == saved["unit_periods"]
        assert Decimal(row["against_plan_pct"]) == percentage.quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        assert row["label"] == (
            f"{whole_percent(percentage)}% {direction_in_words(percentage)}"
        )


def test_q3_shows_the_energy_chart_and_folds_the_medians_away():
    page = PAGE.read_text(encoding="utf-8")
    shown = in_view(page, "q3")
    folded = fold_outs(page, "q3")
    median_limit = (
        "<p>The median shows which way the typical half-hour leans, not how "
        "much energy a group gained or lost, and a 2 MW unit counts the same as "
        "a 1 GW unit.</p>"
    )

    assert '<div class="chart" id="q3-energy-chart">' in shown
    assert shown.index('<p class="answer">') < shown.index('id="q3-energy-chart"')
    assert 'id="q3-chart"' not in shown
    assert median_limit not in shown
    registered = folded.split(
        "<summary>The typical half-hour: medians and spreads</summary>", 1
    )[1].split("</details>", 1)[0]
    assert '<div class="chart" id="q3-chart">' in registered
    assert "<figcaption>" in registered
    assert median_limit in registered
    assert chart_boxes_inside_fold_outs(page) == ["q3-chart"]
    assert folded_charts_not_drawn_on_opening(page) == []


def test_q3s_fold_out_says_what_the_percentages_count_and_which_groups_have_none():
    q3 = saved_output_rows("q3_metered_vs_notified.csv")
    q12 = saved_output_rows("q12_instructed_vs_residual.csv")
    drawing_power = saved_output_rows("context_q5_drawing_power.csv")
    totals = []
    for group in ONE_WAY_FUEL_GROUPS:
        row = one_row(q3, "fuel_group", group)
        totals.append(
            f"{FUEL_GROUP_DISPLAY_NAMES[group].lower()} "
            f"{twh(row['abs_metered_mwh'])} against {twh(row['abs_pn_mwh'])}"
        )
    totals[0] += " TWh"
    gas_drawn = one_row(section_rows(drawing_power, "1"), "fuel_group", "gas_turbine")[
        "drawing_power_expected_at_or_below_zero_mwh"
    ]
    with_draw_taken_off = percentages_with_idle_draw_taken_off(q3, drawing_power)
    as_saved = against_plan_percentages(q3)
    gas_bounds = instructed_share_bounds(q3, q12, "gas_turbine")
    hydro_bounds = instructed_share_bounds(q3, q12, "hydro")
    wind_bounds = instructed_share_bounds(q3, q12, "wind")
    page = PAGE.read_text(encoding="utf-8")

    assert (
        "<dt>Energy against plan</dt>\n        <dd>Context from the same saved "
        "output, not the measure set in advance. For each group I add up the size of "
        "every unit's metered energy, half-hour by half-hour, and the size of its "
        "planned energy, over the same half-hours as the medians, and give the "
        "first as a percentage above or below the second, to the nearest whole "
        f"percent: {', '.join(totals)}. Sizes are added whatever their direction, "
        "so power a unit draws while idle counts as energy produced. The outage "
        f"answer{details_link('Q5')}, whose half-hours run one day longer, counts "
        f"{twh(gas_drawn)} TWh drawn by gas units when nothing was expected of "
        "them; taken off rather than added, gas comes to about "
        f"{whole_percent(with_draw_taken_off['gas_turbine'])}% above plan, not "
        f"{whole_percent(as_saved['gas_turbine'])}%, and nuclear about "
        f"{whole_percent(with_draw_taken_off['nuclear'])}% below, not "
        f"{whole_percent(as_saved['nuclear'])}%. A group's gap is its instructed "
        "part plus the rest, and the rest can be no larger than its summed size "
        f"in the instructed-share answer{details_link('Q12')}: so instructions "
        "account for more than half of gas's excess and at least three-quarters "
        "of hydro's shortfall, and for wind the same sums leave it open, from "
        "none of the shortfall to nine-tenths of it. "
        "Pumped storage, the other group (mostly batteries) and "
        "interconnectors take power in as well as send it out, so for them a "
        "ratio of sizes does not show more or less produced; they are left out, "
        "as is coal, which has no plan.</dd>" in fold_outs(page, "q3")
    )
    assert with_draw_taken_off["gas_turbine"] > 0 > with_draw_taken_off["nuclear"]
    assert groups_whose_whole_percent_moves(as_saved, with_draw_taken_off) == [
        "gas_turbine",
        "nuclear",
    ]
    assert gas_bounds["at least"] > Decimal("0.5")
    assert hydro_bounds["at least"] >= Decimal("0.75")
    assert wind_bounds["at least"] <= 0
    assert tenths(wind_bounds["at most"]) == "0.9"
    assert VISIBLE_LIMITS["q3"] in in_view(page, "q3")
    assert "on days when NESO asked at least one wind farm to cut output" in fold_outs(
        page, "q23"
    )
    assert Decimal(one_row(q3, "fuel_group", "coal")["abs_pn_mwh"]) == 0
    assert (
        '<a href="results/q3_energy_against_plan_by_fuel_group.csv">energy '
        "against plan</a></dd>" in fold_outs(page, "q3")
    )


def column_strata_of(rows, column):
    values = []
    for row in rows:
        values.append(row[column])
    return values


# Words for how a question was set up or judged against a bar. None shows
# before a fold-out is opened; each fold-out keeps the facts in its own
# entry.
FRAMING_WORDS = re.compile(
    r"pre-regist\w*|\bregistered\b|set in advance|set beforehand|"
    r"before the read|before its result was read|\bunplanned\b|one look|"
    r"read once|exploratory|the test needed|the rule needs|not tested|is tested",
    re.IGNORECASE,
)
# The same words used of the data itself, not of a question's set-up: an
# outage notice's class, who registered a unit, and the capacity a unit is
# registered at.
DATA_USES_OF_FRAMING_WORDS = [
    "unplanned notices",
    "Unplanned (",
    "registered by suppliers",
    "43.4% unplanned",
    "registered nuclear capacity",
    "registered capacity",
    "Registered capacity",
]
# Bars a result once had to clear; each stays in its fold-out's measure.
THRESHOLDS_OUT_OF_VIEW = [
    "25%",
    "at least +2",
    "55%",
    "lead by 0.1",
    "±0.2",
    "2% or more",
]
FOLD_OUT_BODY = re.compile(
    r'(<details class="method">\s*<summary[^>]*>.*?</summary>).*?</details>',
    re.DOTALL,
)


# How a bar a result had to clear is worded, wherever a reader glances:
# answers, lead lines, limit lines, captions and the headline list. A use
# that is not a bar is listed here with its reason.
THRESHOLD_WORDING = re.compile(
    r"\bneeded\b|\bat least\b|or more counts|to count as|\bthe bar\b|"
    r"\bthreshold",
    re.IGNORECASE,
)
PLAIN_USES_OF_THRESHOLD_WORDING = []


def glanced_text(page):
    """The text of every answer, lead line, caption and limit line, and of
    the headline list."""
    parts = []
    for kind in ("answer", "lead line", "caption", "headline list"):
        parts.extend(GLANCED_PARTS[kind].findall(page))
    parts.extend(VISIBLE_LIMITS.values())
    return visible_text(" ".join(parts))


def threshold_wording_in(text):
    for plain_use in PLAIN_USES_OF_THRESHOLD_WORDING:
        text = text.replace(plain_use, "")
    return THRESHOLD_WORDING.findall(text)


def test_no_answer_caption_limit_line_or_headline_words_a_bar_to_clear():
    page = PAGE.read_text(encoding="utf-8")

    assert threshold_wording_in(glanced_text(page)) == []
    assert limits_out_of_view(page) == []
    assert threshold_wording_in("it needed 25%, at least +2, to count as yes") == [
        "needed",
        "at least",
        "to count as",
    ]


def text_in_view(page):
    """Everything a reader meets before opening a fold-out: the page's text
    with each fold-out cut down to its summary, and the words carried by
    image descriptions, link labels and the page's description."""
    body = page.split("<body>", 1)[1].split("<script", 1)[0]
    shown = [visible_text(FOLD_OUT_BODY.sub(r"\1", body))]
    for _tag, attributes in page_tags():
        for name in ("alt", "aria-label", "content"):
            shown.append(attributes.get(name) or "")
    return " ".join(" ".join(shown).split())


def framing_words_in(text):
    for data_use in DATA_USES_OF_FRAMING_WORDS:
        text = text.replace(data_use, "")
    return FRAMING_WORDS.findall(text)


def thresholds_in(text):
    found = []
    for threshold in THRESHOLDS_OUT_OF_VIEW:
        if threshold in text:
            found.append(threshold)
    return found


def test_no_word_about_how_a_question_was_set_up_shows_before_a_fold_out_opens():
    page = PAGE.read_text(encoding="utf-8")
    in_view_text = text_in_view(page)

    assert framing_words_in(in_view_text) == []
    assert thresholds_in(in_view_text) == []
    assert framing_words_in("an unplanned look, registered but never read") == [
        "unplanned",
        "registered",
    ]
    assert "How I measured it" in in_view_text
    assert "Recorded 25 October 2026" not in in_view_text


# Further words the two method pages once used for a question's set-up.
# Outside the questions page's set-up section, neither page keeps any.
METHOD_PAGE_FRAMING_WORDS = re.compile(
    r"reading rule|rule comes first|already seen|recorded before|"
    r"\bamended\b|expected before|\bverdicts?\b|predict\w*",
    re.IGNORECASE,
)
# The same words used of the data itself: an outage notice's class, a
# unit's place in the Balancing Mechanism, the name of the saved output's
# columns, which Q1 gives once on each page, the capacity a unit is
# registered at, and a claim about nuclear output that Q38 says it does not
# test.
METHOD_PAGE_DATA_USES = [
    "planned and unplanned",
    "`unplanned`",
    "registered in the Balancing Mechanism",
    "`no directional drift`, in columns named verdict.",
    "registered-nuclear-capacity",
    "registered nuclear capacity",
    "registered capacity",
    "the registered figure",
    "lowest for decades is not tested here",
]


def method_page_framing_words_in(text):
    text = flattened(text)
    for data_use in METHOD_PAGE_DATA_USES:
        text = text.replace(data_use, "")
    return FRAMING_WORDS.findall(text) + METHOD_PAGE_FRAMING_WORDS.findall(text)


def test_no_word_about_how_a_question_was_set_up_shows_outside_the_set_up_section():
    questions = QUESTIONS.read_text(encoding="utf-8")
    trajectory = TRAJECTORY.read_text(encoding="utf-8")

    assert method_page_framing_words_in(questions_outside_set_up(questions)) == []
    assert method_page_framing_words_in(trajectory) == []
    assert method_page_framing_words_in(
        "**Already seen.** Registered 5 October, amended before the\nread; one\nlook."
    ) == ["Registered", "before the read", "one look", "Already seen", "amended"]
    assert method_page_framing_words_in(
        "**Predicted.** No verdict; the two verdicts, in columns named verdict."
    ) == ["Predicted", "verdict", "verdicts", "verdict"]
    assert questions.count("in columns named verdict") == 1
    assert trajectory.count("in columns named verdict") == 1
    assert "Each result was read once" in questions_set_up(questions)


EARLIER_LOOKS = {
    "Q1": (
        "**Earlier look.** On 11 September 2026 an exploration compared, for 851 "
        "half-hours from 19 August to 10 September 2026,"
    ),
    "Q10": (
        "**Earlier look.** On 11 September 2026 an exploration compared every "
        "stored forecast with outturn"
    ),
    "Q24": (
        "**Earlier look.** An exploration read level-energy shares on 11 October "
        "2026. The measure was changed to acceptance volume on 12 October 2026, "
        "after that first look at the data."
    ),
    "Q25": (
        "**Earlier look.** An exploration read measure (1) on 11 October 2026. "
        "Measure (2), the result, was added on 12 October 2026 and set to count "
        "acceptances, as the BSC does, on 15 October 2026, both before measure "
        "(2) was first read."
    ),
    "Q30": (
        "- **Earlier look.** One day of wind values (22 August 2025), during "
        "endpoint checks."
    ),
    "Q37": (
        "**Earlier look.** Q24 read wind's flagged share and its acceptance "
        "volume, both directions together, on 15 October 2026. No figure by "
        "direction, by half-hour or by what else was instructed in the same "
        "half-hour was read before this method was written."
    ),
    "Q38": (
        "**Earlier look.** Three related figures had been read before this "
        "method was written: the nuclear units' planned output as a share of "
        "registry capacity, by month (a context count, 1 November 2026); Q5's "
        "split of nuclear under-delivery against plan by outage notice "
        "(12 October 2026); and Q3's nuclear metered total (11 October 2026). "
        "Metered output as a share of capacity, and the gap below capacity "
        "split by notice, had not been read."
    ),
}


def questions_with_an_earlier_look(questions):
    with_one = []
    for question_id in register_anchors():
        if "**Earlier look.**" in register_section(questions, question_id):
            with_one.append(question_id)
    return with_one


def questions_whose_earlier_look_opens_otherwise(questions):
    otherwise = []
    for question_id, opening in EARLIER_LOOKS.items():
        if opening not in flattened(register_section(questions, question_id)):
            otherwise.append(question_id)
    return otherwise


def test_each_question_whose_data_was_seen_earlier_says_so_in_its_own_section():
    questions = QUESTIONS.read_text(encoding="utf-8")

    assert questions_whose_earlier_look_opens_otherwise(questions) == []
    assert questions_with_an_earlier_look(questions) == list(EARLIER_LOOKS)


WRITTEN_DATES_ROWS = [
    "| Q1 | 5 October | 6, 12, 13, 25 and 31 October | no drift value read |",
    "| Q2 | 25 September | | no metered quantity read |",
    "| Q3 | before its read, date not kept | | |",
    "| Q5 | before its read; population and definitions 12 October | | no Q5 "
    "value read |",
    "| Q6 | before its read; population 11 October | | no lag read |",
    "| Q8 | before its read; completed 25 October | | no demand value held; a "
    "counts-only check of the demand data's coverage on 26 October |",
    "| Q10 | before 12 October | 12, 25 and 31 October | not read |",
    "| Q12 | restated 11 October | | no Q12 read |",
    "| Q22 | 9 October | | no Q3 or Q22 query run |",
    "| Q23 | 12 October | 12 October | not read |",
    "| Q24 | 12 October | 12 October | level-energy shares read on 11 October |",
    "| Q25 | 12 October | 12 and 15 October | measure (1) read on 11 October; "
    "measure (2) not read |",
    "| Q28 | 25 October | 26 October | no figure of it read |",
    "| Q29 | 27 October | | no figure of it read |",
    "| Q30 | 27 October | 27 October | no wind data held when written; not read "
    "when changed |",
    "| Q37 | 27 November | 27 November | Q24's acceptance volume for wind "
    "read on 15 October; no figure by direction or by half-hour read |",
    "| Q38 | 28 November | 28 November | planned share of capacity by month read "
    "on 1 November, Q5's nuclear rows on 12 October and Q3's nuclear total on "
    "11 October; no metered share of capacity or gap split read |",
]


def written_dates_rows(questions):
    table = questions_set_up(questions).split(
        "\n### When each method was written\n", 1
    )[1]
    rows = []
    for line in table.splitlines():
        if line.startswith("| Q"):
            rows.append(line)
    return rows


def test_the_set_up_section_dates_each_questions_method():
    questions = QUESTIONS.read_text(encoding="utf-8")
    rows = written_dates_rows(questions)

    assert rows == WRITTEN_DATES_ROWS
    assert sorted(question_ids_in(" ".join(rows))) == sorted(register_anchors())
    assert flattened(questions_set_up(questions)).endswith(
        flattened(WRITTEN_DATES_ROWS[-1])
    )


Q1_VERDICT_QUERY = REPOSITORY / "dbt" / "analyses" / "q1_forecast_drift_verdict.sql"
QUINTILE_TIE_ORDER = REPOSITORY / "dbt" / "macros" / "quintile_tie_order.sql"
Q30_QUERY = REPOSITORY / "dbt" / "analyses" / "q30_wind_vs_carbon_error.sql"
Q8_Q28_QUERY = REPOSITORY / "dbt" / "analyses" / "q8_q28_history.sql"
QUINTILE_RATIO_IN_SQL = (
    ">= 1.25 * observed.bottom_sum * observed.top_half_hours then 'supported' "
    "else 'not supported'"
)


def test_q1s_answer_thresholds_on_both_method_pages_are_its_querys():
    query = flattened(Q1_VERDICT_QUERY.read_text(encoding="utf-8"))
    verdict = one_row(saved_output_rows(Q1_VERDICT), "population_is_reached", "True")
    questions = QUESTIONS.read_text(encoding="utf-8")
    q1 = flattened(register_section(questions, "Q1"))
    trajectory = flattened(TRAJECTORY.read_text(encoding="utf-8"))

    assert (
        "when median_drift >= 2 and positive_weight >= 0.55 * "
        "nullif(non_zero_weight, 0) then 'supported: upward drift' when "
        "median_drift <= -2 and positive_weight <= 0.45 * "
        "nullif(non_zero_weight, 0) then 'contradicted: downward drift' else "
        "'no directional drift' end as verdict" in query
    )
    assert verdict["half_hours_needed"] == "2880"
    assert (
        "- **Answer.** Drift is called upward when the median drift is at least +2 "
        "and the share of positive drifts, zeros excluded, is at least 55%; "
        "downward when the median is at most −2 and the positive share at most "
        "45%; otherwise there is no directional drift. The saved output writes "
        "these as `supported: upward drift`, `contradicted: downward drift` and "
        "`no directional drift`, in columns named verdict." in q1
    )
    assert "a complete trajectory; at least 2,880 of them." in q1
    assert "on the first day the population reaches 2,880, under the" in q1
    assert (
        "- **Answer:** drift is called upward when the median drift is at least +2 "
        "and at least 55% of non-zero drifts are positive; downward when the "
        "median is at most −2 and at most 45% are positive; otherwise there is no "
        "directional drift. The saved output writes these as "
        "`supported: upward drift`, `contradicted: downward drift` and "
        "`no directional drift`, in columns named verdict." in trajectory
    )
    assert "complete trajectory; at least 2,880 of them (60 days' worth)." in (
        trajectory
    )
    assert "gives a result when the population reaches 2,880 and" in trajectory


def test_each_other_method_keeps_the_thresholds_its_answer_is_called_by():
    history_query = flattened(Q8_Q28_QUERY.read_text(encoding="utf-8"))
    questions = QUESTIONS.read_text(encoding="utf-8")
    answers = {
        "Q2": (
            "- **Labels.** Moved share: small below 0.5%, material at 2% or more. "
            "Changed share: small below 5%, material at 20% or more."
        ),
        "Q8": (
            "- **Answer.** Demand is called stronger at a gap of 0.1 or more, "
            "shortfall at −0.1 or less; otherwise there is no clear difference."
        ),
        "Q28": (
            'The answer is called "yes" (`supported` in the saved output) when the '
            "top quintile's mean is at least 25% above the bottom's, otherwise "
            '"no" (`not supported`).'
        ),
    }

    assert QUINTILE_RATIO_IN_SQL in history_query
    assert (
        "when observed.gap >= 0.1 then 'demand stronger' when observed.gap <= -0.1 "
        "then 'shortfall stronger' else 'no clear difference'" in history_query
    )
    assert questions_whose_section_lacks(questions, answers) == []


def test_q28_states_its_measure_and_tie_order_as_its_query_computes_them():
    tie_order = flattened(QUINTILE_TIE_ORDER.read_text(encoding="utf-8"))
    history_query = flattened(Q8_Q28_QUERY.read_text(encoding="utf-8"))
    questions = QUESTIONS.read_text(encoding="utf-8")
    q28 = flattened(register_section(questions, "Q28"))

    assert (
        "md5('{{ seed }}:' || {{ draw }} || ':' || {{ pick }} || ':' || "
        "extract(epoch from {{ period_start }})::bigint)" in tie_order
    )
    assert (
        "order by abs_carbon_error, "
        "{{ quintile_tie_order(bootstrap_seed, 'draw', 'pick', 'period_start') }}, "
        "period_start, pick" in history_query
    )
    assert (
        "sum(greatest(shortfall.unexplained_shortfall_mwh, 0)) as "
        "positive_shortfall_mwh" in history_query
    )
    assert "where shortfall.determinability = 'determinable'" in history_query
    assert (
        "- **Measure.** Per half-hour, the cohort sum of the positive part of "
        "`unexplained_shortfall_mwh` (under-delivery, as Q5) over determinable "
        "unit-periods." in q28
    )
    assert (
        "- **Answer.** Quintiles of `abs(error_api_final)`. Ties are ordered by a "
        "fixed pseudo-random number (the first eight hexadecimal digits of the "
        "md5 of the seed, the bootstrap draw and pick, and the half-hour's epoch "
        "seconds), then by period start; the "
        "half-hours sharing the bottom quintile's largest error are reported." in q28
    )
    assert "substr(md5(" in tie_order and ", 1, 8))::bit(32)::bigint" in tie_order
    assert (
        "Resampling whole days keeps the correlation within a day; correlation "
        "between days is not modelled." in q28
    )
    assert (
        "Resampling whole days keeps the correlation within a day; correlation "
        "between days is not modelled." in history_query.replace("-- ", "")
    )
    q30_query = flattened(Q30_QUERY.read_text(encoding="utf-8"))
    assert "{% set bootstrap_seed = 20261025 %}" in history_query
    assert "{% set bootstrap_seed = var('q30_bootstrap_seed') %}" in q30_query
    assert (
        "{{ quintile_tie_order(bootstrap_seed, '0', '0', 'period_start') }}"
        in q30_query
    )
    assert (
        "quintiles of the absolute wind error, with Q28's tie order on Q30's own "
        "seed." in flattened(register_section(questions, "Q30"))
    )


def test_the_set_up_fold_out_keeps_the_claim_its_context_counts_and_the_ranges_role():
    page = PAGE.read_text(encoding="utf-8")
    set_up = set_up_fold_out(page)

    assert page.index('<section id="limitations"') < page.index('<summary id="set-up">')
    assert (
        "Each result was read once (the count of short instructions"
        f"{details_link('Q25')} was re-run once over the same period," in set_up
    )
    assert (
        "A figure given for scale sets a result against a context count read on "
        "1 November 2026, a denominator, not a result: " in set_up
    )
    for context_count in (
        "context_plan_per_unit_half_hour.csv",
        "context_plan_pct_capacity.csv",
        "context_demand.csv",
        "context_carbon_intensity.csv",
    ):
        assert f'<a href="results/{context_count}">' in set_up
    assert (
        "<p>The 90% ranges are a guide to uncertainty, not part of any method's "
        "answer.</p>" in set_up
    )
    assert (
        '<p>"How I measured it", under each answer, gives the measure, the '
        "numbers and the dates.</p>" in FOLD_OUT.sub("", page)
    )


def wind_population(rows):
    return one_row(section_rows(rows, "1"), "line", "2")


def per_gw(rows, section, line, outcome, measure="wind_positive"):
    """One figure of the wind output: an outcome's change per extra GW of a
    measure, at a section and line."""
    found = []
    for row in rows:
        place = (row["section"], row["line"], row["measure"], row["outcome"])
        if place == (section, line, measure, outcome):
            found.append(row)
    assert len(found) == 1
    return found[0]


def figure_and_range(row, places):
    """'7.9 (4.9 to 11.2)' without the bracket: a figure's value, low and
    high as the page writes them."""
    return (
        page_number(row["value"], places),
        page_number(row["interval_low"], places),
        page_number(row["interval_high"], places),
    )


def table_cell(row):
    """'+7.93 (4.93 to 11.19)': a figure with its sign and its 90% range."""
    return (
        f"{signed_to(row['value'], 2)} ({page_number(row['interval_low'], 2)} to "
        f"{page_number(row['interval_high'], 2)})"
    )


def gigawatts_of(mwh_per_half_hour):
    return tenths(Decimal(mwh_per_half_hour) * 2 / 1000)


NUMBER_WORDS_SMALL = {1: "one", 2: "two", 3: "three", 4: "four"}
WAYS_OF_ALLOWING_FOR_THE_PLAN = [
    (
        "Same month and time of day, planned output as a second variable (the "
        "answer's largest figure)",
        "4",
        "4",
    ),
    ("Twenty bands of planned output", "5", "13"),
    (
        "Planned output as a second variable, month and time of day not held",
        "3",
        "4",
    ),
    ("Five bands of planned output, each band's own means removed", "5", "12"),
    ("Five bands of planned output", "5", "11"),
]
NOTHING_HELD_FIXED = (
    "Nothing held fixed (the misleading plain comparison)",
    "3",
    "2",
)


def wind_article_fold_out(summary):
    """One of the wind article's fold-outs, by its summary."""
    page = PAGE.read_text(encoding="utf-8")
    for fold_out in FOLD_OUT.findall(article_text(page, "wind-shortfall")):
        if f"<summary>{summary}</summary>" in fold_out:
            return fold_out
    return ""


FIVE_WAYS = "The five ways of allowing for the plan"


def test_the_wind_articles_answer_states_its_figures_from_the_published_output():
    # Each figure to one decimal, halves up, straight from the saved value
    # (0.649 is 0.6, not 0.65 rounded again). Every way allows for planned
    # wind output; only the first also holds month and time of day fixed.
    # A way cannot be told apart from zero when its 90% range takes zero in.
    wind = saved_output_rows(WIND_OUTPUT)
    actual, actual_low, actual_high = figure_and_range(
        per_gw(wind, "4", "4", "actual"), 1
    )
    every_way = []
    ranges_above_zero = 0
    for _name, section, line in WAYS_OF_ALLOWING_FOR_THE_PLAN:
        row = per_gw(wind, section, line, "actual")
        every_way.append(Decimal(row["value"]))
        if Decimal(row["interval_low"]) > 0:
            ranges_above_zero += 1
    page = PAGE.read_text(encoding="utf-8")

    assert answer_of(page, "wind-shortfall") == (
        "<strong>It tended to be, but the size depends on the method.</strong> "
        f"Across {NUMBER_WORDS[len(every_way)]} ways of allowing for planned wind, "
        "the estimate ranges from "
        f"{page_number(min(every_way), 1)} to {signed_to(max(every_way), 1)} "
        "g/kWh per extra GW of wind shortfall; "
        f"{NUMBER_WORDS_SMALL[ranges_above_zero]} have 90% ranges wholly above "
        f"zero. The largest, {actual} ({actual_low} to {actual_high}), is the "
        "only one that also holds month and time of day fixed."
    )
    assert max(every_way) == every_way[0]
    assert (
        "<h3>When wind farms fall short of their plans, is the grid dirtier?</h3>"
        in in_view(page, "wind-shortfall")
    )
    assert answer_of(page, "wind-shortfall").count("<strong>") == 1
    assert Decimal(per_gw(wind, "4", "4", "actual")["interval_low"]) > 0
    assert "roughly 5 to 8" not in page


def test_the_wind_article_says_what_the_forecast_did_and_that_it_is_a_pattern():
    wind = saved_output_rows(WIND_OUTPUT)
    forecast, forecast_low, forecast_high = figure_and_range(
        per_gw(wind, "4", "4", "forecast"), 1
    )
    miss, miss_low, miss_high = figure_and_range(
        per_gw(wind, "4", "4", "signed_miss"), 1
    )
    plain = page_number(per_gw(wind, "3", "2", "actual")["value"], 1)
    least_planned = planned_fifths(wind)[0]
    least = page_number(least_planned["value"], 1)
    yardstick = tenths(one_row(section_rows(wind, "6"), "line", "2")["value"])
    page = PAGE.read_text(encoding="utf-8")
    shown = in_view(page, "wind-shortfall")
    stored_forecast = (
        '<a class="ref" href="#stored-forecast" aria-label="Details, stored '
        'forecast">*</a>'
    )

    assert (
        f"<p>The stored forecast moved almost as much ({forecast}; {forecast_low} "
        f"to {forecast_high}), so its miss moved by only about {miss} ({miss_low} "
        f"to {miss_high}). That does not show the forecast saw the shortfall "
        "coming: NESO does not say when the stored forecast is made."
        f"{stored_forecast}</p>" in shown
    )
    assert (
        '<dt id="stored-forecast">Stored forecast</dt>' in page
        and "NESO does not document when it is made."
        in page.split('<dt id="stored-forecast">', 1)[1].split("</dd>", 1)[0]
    )
    assert "where checked" not in shown
    assert (
        "<p><strong>A pattern, not proof of cause, and not the same everywhere:"
        "</strong> where the least wind was planned, more shortfall went with a "
        f"cleaner grid ({least}). With nothing held fixed the figure is {plain}: "
        "windy half-hours are both clean and prone to large shortfalls.</p>"
    ) == VISIBLE_LIMITS["wind-shortfall"]
    assert VISIBLE_LIMITS["wind-shortfall"] in shown
    assert Decimal(least_planned["interval_high"]) < 0
    assert Decimal(per_gw(wind, "3", "2", "actual")["interval_high"]) < 0
    assert '<div class="chart" id="wind-shortfall-chart">' in shown
    assert f"{yardstick} g/kWh" not in visible_text(shown)
    assert f"<dd>{yardstick} g/kWh is what replacing 1 GW" in fold_outs(
        page, "wind-shortfall"
    )
    assert (
        "26.1 GW. It is a size for comparison, not something these figures "
        "estimate.</dd>" in fold_outs(page, "wind-shortfall")
    )


def test_the_wind_fold_out_gives_every_way_of_allowing_for_the_plan():
    wind = saved_output_rows(WIND_OUTPUT)
    folded = wind_article_fold_out(FIVE_WAYS)
    actuals = []
    misses = []
    for name, section, line in [*WAYS_OF_ALLOWING_FOR_THE_PLAN, NOTHING_HELD_FIXED]:
        cells = []
        for outcome in ("actual", "forecast", "signed_miss"):
            cells.append(f"<td>{table_cell(per_gw(wind, section, line, outcome))}</td>")
        assert f"<tr><td>{name}</td>{''.join(cells)}</tr>" in folded
        misses.append(Decimal(per_gw(wind, section, line, "signed_miss")["value"]))
        assert Decimal(per_gw(wind, section, line, "signed_miss")["interval_low"]) > 0
    wholly_above_zero = []
    for name, section, line in WAYS_OF_ALLOWING_FOR_THE_PLAN:
        row = per_gw(wind, section, line, "actual")
        actuals.append(page_number(row["value"], 1))
        if Decimal(row["interval_low"]) > 0:
            wholly_above_zero.append(name)
    banded_forecast = per_gw(wind, "5", "13", "forecast")

    assert (
        "<dd>Across the five ways the actual's figure runs from "
        f"{actuals[-1]} to {actuals[0]} ({', '.join(actuals)}). Only the first "
        "two have ranges wholly above zero, and the first is the largest." in folded
    )
    assert (
        "Only the first holds month and time of day fixed, and the size is less "
        "settled than that figure alone suggests." in folded
    )
    assert wholly_above_zero == [
        WAYS_OF_ALLOWING_FOR_THE_PLAN[0][0],
        WAYS_OF_ALLOWING_FOR_THE_PLAN[1][0],
    ]
    assert (
        "With twenty bands the forecast's figure cannot be told apart from zero "
        f"({page_number(banded_forecast['interval_low'], 1)} to "
        f"{page_number(banded_forecast['interval_high'], 1)}). On every row of "
        "the table the miss's range lies above zero and its figure lies between "
        f"{hundredths(min(misses))} and {hundredths(max(misses))}.</dd>" in folded
    )


def planned_fifths(rows):
    """The actual's figure inside each fifth of planned wind output, least
    planned first."""
    fifths = []
    for row in section_rows(rows, "5"):
        place = (row["line"], row["measure"], row["outcome"])
        if place == ("15", "wind_positive", "actual"):
            fifths.append(row)
    assert [row["fifth"] for row in fifths] == ["1", "2", "3", "4", "5"]
    return fifths


def test_the_wind_fold_out_says_where_the_sign_turns_and_what_cannot_be_said():
    # Inside the fifth of half-hours with the least wind planned the figure
    # is below zero with its whole range; in the rest it is above zero, with
    # a range that takes zero in for all but one.
    wind = saved_output_rows(WIND_OUTPUT)
    least_planned, *rest = planned_fifths(wind)
    least, least_low, least_high = figure_and_range(least_planned, 1)
    others = []
    ranges_taking_in_zero = 0
    for row in rest:
        others.append(Decimal(row["value"]))
        if Decimal(row["interval_low"]) <= 0:
            ranges_taking_in_zero += 1
    folded = wind_article_fold_out(FIVE_WAYS)
    page = PAGE.read_text(encoding="utf-8")

    assert (
        "<dt>Inside one band of planned output</dt>\n        <dd>Taking only the "
        "fifth of half-hours with the least wind planned, the figure for the "
        f"actual is {least} ({least_low} to {least_high}): there, more shortfall "
        "went with a cleaner grid. In the other four fifths it is "
        f"{signed_to(min(others), 1)} to {signed_to(max(others), 1)}, and "
        f"{NUMBER_WORDS_SMALL[ranges_taking_in_zero]} of those four ranges include "
        "zero.</dd>" in folded
    )
    assert Decimal(least_planned["interval_high"]) < 0 < min(others)
    assert (
        "<dt>What cannot be said</dt>\n        <dd>That under-delivery causes the "
        "higher actual. The comparison is of half-hours, not of an intervention; "
        "weather, demand and imports move with a wind shortfall too.</dd>"
        in wind_article_fold_out("How I measured it")
    )
    assert "What cannot be said" not in in_view(page, "wind-shortfall")


def test_the_wind_fold_outs_give_the_measure_the_contrast_and_the_data_as_held():
    wind = saved_output_rows(WIND_OUTPUT)
    folded = fold_outs(PAGE.read_text(encoding="utf-8"), "wind-shortfall")
    population = wind_population(wind)
    every_half_hour = one_row(
        section_rows(wind, "2"), "item", wind_item(wind, "2", "2", "actual")
    )
    fifths = {}
    for row in section_rows(wind, "2"):
        if row["line"] == "1" and row["measure"] == "wind_positive":
            fifths[(row["outcome"], row["fifth"])] = row
    middle = fifths[("actual", "3")]
    plain, plain_low, plain_high = figure_and_range(per_gw(wind, "3", "2", "actual"), 1)
    gas_actual = per_gw(wind, "4", "4", "actual", "gas_positive")
    gas_miss = per_gw(wind, "4", "4", "signed_miss", "gas_positive")
    units = {}
    for row in section_rows(wind, "1"):
        if row["line"] in ("3", "4"):
            units[row["measure"]] = row["value"]
    recomputed = one_row(
        [row for row in section_rows(wind, "1") if row["line"] == "6"],
        "measure",
        "all fuels",
    )
    demand = one_row(section_rows(wind, "6"), "line", "1")["value"]
    figures_read = one_row(section_rows(wind, "7"), "line", "2")["value"]
    q28_half_hours = int(
        one_row(saved_output_rows("q8_q28_history.csv"), "section", "1")["half_hours"]
    )

    assert (
        f"It ran from {whole(every_half_hour['range_from'])} to "
        f"{int(whole(every_half_hour['range_to'])):,} MWh in a half-hour ("
        f"{gigawatts_of(every_half_hour['range_from'])} to "
        f"{gigawatts_of(every_half_hour['range_to'])} GW); the middle fifth of "
        f"half-hours is {whole(middle['range_from'])} to "
        f"{whole(middle['range_to'])} MWh ({gigawatts_of(middle['range_from'])} "
        f"to {gigawatts_of(middle['range_to'])} GW)." in folded
    )
    assert (
        f"<dd>With nothing held fixed the actual's figure is {plain} ({plain_low} "
        f"to {plain_high}). By fifth of wind under-delivery the mean actual falls "
        f"from {tenths(fifths[('actual', '1')]['value'])} to "
        f"{tenths(fifths[('actual', '5')]['value'])} g/kWh and the mean forecast "
        f"from {tenths(fifths[('forecast', '1')]['value'])} to "
        f"{tenths(fifths[('forecast', '5')]['value'])}: half-hours with much wind "
        "under-delivery are windy, and windy half-hours are clean. That is not "
        "an impact.</dd>" in folded
    )
    assert (
        "with gas at 394 g/kWh would add at the mean national demand of "
        f"{tenths(demand)} GW." in folded
    )
    assert (
        "gas under-delivery's figure for the actual cannot be told apart from "
        f"zero ({table_cell(gas_actual).replace(' (', '; ')}; its miss is "
        f"{table_cell(gas_miss)}.</dd>" in folded
    )
    assert (
        Decimal(gas_actual["interval_low"]) < 0 < Decimal(gas_actual["interval_high"])
    )
    assert (
        f"<dd>{int(population['half_hours']):,} half-hours on "
        f"{population['days']} days, 22 August 2025 to 16 August 2026: one more "
        f"than the {q28_half_hours:,} behind carbon misses and under-delivery"
        f"{details_link('Q28')}, because by this read the final settlement run had "
        f"reached {recomputed['half_hours']} of the half-hours." in folded
    )
    assert int(population["half_hours"]) == q28_half_hours + 1
    assert (
        f"{units['wind']} wind units and {units['gas']} gas units of "
        f"{units['all fuels']}." in folded
    )
    assert (
        f"{figures_read} figures were read, none corrected for reading many; only "
        "the rows above are reported on their own. "
        f'<a href="results/{WIND_OUTPUT}">Saved output</a> · '
        '<a href="results/q28_wind_shortfall_per_gw.csv">chart data</a></dd>' in folded
    )
    assert long_date_range([population]) == ("22 August 2025", "16 August 2026")


def wind_item(rows, section, line, outcome):
    """The item text of the wind output's one unmeasured row at a section
    and line, for an outcome."""
    found = []
    for row in section_rows(rows, section):
        if row["line"] == line and row["outcome"] == outcome and not row["measure"]:
            found.append(row["item"])
    assert len(found) == 1
    return found[0]


def test_q24s_answer_gives_each_groups_flagged_share_from_its_chart_data():
    flagged = {}
    for row in saved_output_rows("q24_flagged_share_by_fuel_group.csv"):
        flagged[row["fuel_group"]] = whole(row["flagged_pct"])

    assert answer_of(PAGE.read_text(encoding="utf-8"), "q24") == (
        f"<strong>Almost all of it for wind: {flagged['wind']}%.</strong> The "
        "flag marks an instruction as possibly for network reasons, such as "
        "relieving a bottleneck, rather than balancing supply and demand. "
        f"Hydro {flagged['hydro']}%, pumped storage {flagged['pumped_storage']}%, "
        f"gas {flagged['gas_turbine']}%, the other group (mostly batteries) "
        f"{flagged['other']}%, bioenergy {flagged['bioenergy']}%."
    )


Q37_OUTPUT = "q37_wind_turn_down_and_turn_up.csv"
NESO_CARBON_METHOD = (
    "https://www.neso.energy/data-portal/national-carbon-intensity-forecast/"
    "national_carbon_intensity_forecast_methodology"
)
Q37_CHART_DATA = "q37_turn_up_by_wind_turn_down_band.csv"
Q37_ALL_MONTHS = "wind turned down, all months"
Q37_NO_WIND_TURNED_DOWN = "0 none"
Q37_WIND_NOT_MEASURED = "6 wind turn-down not measured"
Q37_LARGEST_BAND = "5 2 GW or more"


def turn_up_with_and_without_wind_turn_down(rows):
    """Q37's energy turned up in the half-hours with wind turned down as a
    system action and in those with none: every group's, gas's and gas's
    flagged part, in MWh, beside the gas energy turned down and each
    side's half-hours and fuel groups."""
    sides = {}
    for side_name in ["with", "without"]:
        sides[side_name] = {
            "half_hours": 0,
            "all_mwh": Decimal(0),
            "gas_mwh": Decimal(0),
            "flagged_gas_mwh": Decimal(0),
            "gas_turned_down_mwh": Decimal(0),
            "fuel_groups": set(),
        }
    bands_counted = set()
    for row in section_rows(rows, "3"):
        band = row["wind_turn_down_band"]
        if band == Q37_WIND_NOT_MEASURED:
            # On neither side of the comparison.
            continue
        side = sides["with"]
        if band == Q37_NO_WIND_TURNED_DOWN:
            side = sides["without"]
        if band not in bands_counted:
            side["half_hours"] += int(row["half_hours"])
            bands_counted.add(band)
        side["all_mwh"] += Decimal(row["turned_up_mwh"])
        side["fuel_groups"].add(row["fuel_group"])
        if row["fuel_group"] == "gas_turbine":
            side["gas_mwh"] += Decimal(row["turned_up_mwh"])
            side["flagged_gas_mwh"] += Decimal(row["flagged_turned_up_mwh"])
            side["gas_turned_down_mwh"] += Decimal(row["turned_down_mwh"])
    return sides


def average_gas_gw(side):
    """A side's gas energy turned up as average power: a half-hour's MWh
    times two is its MW."""
    return tenths(2 * side["gas_mwh"] / side["half_hours"] / 1000)


def gas_share(side):
    return percent_of(side["gas_mwh"], side["all_mwh"], 0)


def net_gas_mwh(side):
    """A side's gas energy turned up less its gas energy turned down."""
    return side["gas_mwh"] - side["gas_turned_down_mwh"]


NESO_COMBINED_CYCLE_GRAMS_PER_KWH = 394
NESO_OPEN_CYCLE_GRAMS_PER_KWH = 651


def million_tonnes_of_co2(gas_mwh):
    """Gas energy at NESO's combined-cycle rate, as an exact number of
    million tonnes of CO2: grams per kWh are kilograms per MWh."""
    kilograms = Decimal(gas_mwh) * NESO_COMBINED_CYCLE_GRAMS_PER_KWH
    return kilograms / 1000 / 1_000_000


def month_in_full(settlement_month):
    """'2026-08-01' as 'August 2026'."""
    return long_date(settlement_month).split(" ", 1)[1]


def flagged_share_of_planned(row):
    """The wind energy turned down as a system action, as an exact
    percentage of the energy the wind farms planned."""
    return (
        Decimal(100)
        * Decimal(row["flagged_turned_down_mwh"])
        / Decimal(row["planned_mwh"])
    )


def lowest_and_highest_month(rows):
    """Q37's months with the smallest and the largest share of planned wind
    energy turned down as a system action."""
    lowest = None
    highest = None
    for row in section_rows(rows, "1"):
        share = flagged_share_of_planned(row)
        if lowest is None or share < flagged_share_of_planned(lowest):
            lowest = row
        if highest is None or share > flagged_share_of_planned(highest):
            highest = row
    return lowest, highest


def test_q37s_answer_takes_its_shares_and_gas_power_from_its_saved_output():
    rows = saved_output_rows(Q37_OUTPUT)
    all_months = item_row(rows, "2", Q37_ALL_MONTHS)
    half_hours = all_months["half_hours"]
    any_turn_down = all_months["half_hours_with_flagged_wind_turn_down"]
    sides = turn_up_with_and_without_wind_turn_down(rows)

    assert answer_of(PAGE.read_text(encoding="utf-8"), "q37") == (
        "<strong>Mostly gas, and on average more gas the more wind is turned "
        "down in the same half-hour.</strong> NESO turned wind down as a "
        "system action in "
        f"{percent_of(any_turn_down, half_hours, 0)}% of half-hours, and by 1 GW "
        "or more in "
        f"{percent_of(all_months['half_hours_at_1_gw_or_more'], half_hours, 0)}%. "
        "Where wind is turned down as a system action, gas is "
        f"{gas_share(sides['with'])}% of the energy turned up, "
        f"{average_gas_gw(sides['with'])} GW on average; elsewhere, gas is still "
        f"{gas_share(sides['without'])}% of it, but "
        f"{average_gas_gw(sides['without'])} GW."
    )


def test_q37s_headline_reads_the_same_on_the_page_and_in_the_readme():
    rows = saved_output_rows(Q37_OUTPUT)
    all_months = item_row(rows, "2", Q37_ALL_MONTHS)
    any_turn_down = all_months["half_hours_with_flagged_wind_turn_down"]
    sides = turn_up_with_and_without_wind_turn_down(rows)
    lead = "In half-hours when NESO turns wind down, gas is most of what it turns up."
    rest = (
        f"In the {percent_of(any_turn_down, all_months['half_hours'], 0)}% of "
        "half-hours with wind turned down as a system action (Aug 2025 – Oct "
        f"2026), gas was {gas_share(sides['with'])}% of the energy turned up, "
        f"{average_gas_gw(sides['with'])} GW on average; in the others it was "
        f"{gas_share(sides['without'])}%, at {average_gas_gw(sides['without'])} GW"
    )
    headlines = headline_list(PAGE.read_text(encoding="utf-8"))
    readme = flattened(README.read_text(encoding="utf-8"))

    assert f"<li><strong>{lead}</strong> {rest}.</li>" in without_asterisks(headlines)
    assert f"{rest}.{details_link('Q37')}</li>" in headlines
    assert f"- **{lead}** {rest} ([Q37](" in readme


def test_q37_gives_its_carbon_estimate_in_view_beside_what_it_is_not():
    with_wind = turn_up_with_and_without_wind_turn_down(saved_output_rows(Q37_OUTPUT))[
        "with"
    ]
    net_tonnes = million_tonnes_of_co2(net_gas_mwh(with_wind))

    assert (
        "<p>Where wind is turned down as a system action, NESO turned up "
        f"{twh(net_gas_mwh(with_wind))} TWh more gas than it turned down: about "
        f"{whole(net_tonnes)} million tonnes of CO2 at the "
        f"{NESO_COMBINED_CYCLE_GRAMS_PER_KWH} g per kWh NESO uses for "
        "combined-cycle gas. That is not what would have been saved had the "
        "wind run.</p>" in in_view(PAGE.read_text(encoding="utf-8"), "q37")
    )


def test_q37s_carbon_estimate_is_its_net_gas_at_nesos_combined_cycle_rate():
    sides = turn_up_with_and_without_wind_turn_down(saved_output_rows(Q37_OUTPUT))
    with_wind = sides["with"]
    net_tonnes = million_tonnes_of_co2(net_gas_mwh(with_wind))

    assert (
        twh(with_wind["gas_mwh"]),
        twh(with_wind["flagged_gas_mwh"]),
        twh(with_wind["gas_turned_down_mwh"]),
        twh(net_gas_mwh(with_wind)),
        tenths(net_tonnes),
        tenths(million_tonnes_of_co2(with_wind["gas_mwh"])),
        whole(net_tonnes),
        twh(net_gas_mwh(sides["without"])),
    ) == ("16.4", "4.6", "1.9", "14.5", "5.7", "6.5", "6", "1.2")


def test_q37s_carbon_entry_shows_its_working_from_its_saved_output():
    rows = saved_output_rows(Q37_OUTPUT)
    all_months = item_row(rows, "2", Q37_ALL_MONTHS)
    sides = turn_up_with_and_without_wind_turn_down(rows)
    with_wind = sides["with"]
    without = sides["without"]
    net_tonnes = million_tonnes_of_co2(net_gas_mwh(with_wind))
    gross_tonnes = million_tonnes_of_co2(with_wind["gas_mwh"])

    assert (
        "<dt>Carbon</dt>\n        <dd>In the "
        f"{with_wind['half_hours']:,} half-hours with wind turned down as a "
        "system action, NESO turned gas units up by "
        f"{twh(with_wind['gas_mwh'])} TWh and down by "
        f"{twh(with_wind['gas_turned_down_mwh'])} TWh: "
        f"{twh(net_gas_mwh(with_wind))} TWh more gas, beside the "
        f"{twh(all_months['flagged_turned_down_mwh'])} TWh of wind turned "
        "down. The two figures being close does not show one replacing the "
        "other: no instruction here is linked to another, and the "
        f"{twh(with_wind['gas_mwh'])} TWh is all the gas turned up in those "
        "half-hours, for any reason. At "
        f"{NESO_COMBINED_CYCLE_GRAMS_PER_KWH} g of CO2 per kWh, the "
        f'rate <a href="{NESO_CARBON_METHOD}">NESO\'s carbon intensity '
        "method</a> uses for combined-cycle gas, that is "
        f"{tenths(net_tonnes)} million tonnes of CO2, or "
        f"{tenths(gross_tonnes)} million without taking off the gas turned "
        "down. The rate counts CO2 from generation only and is an average "
        "over Great Britain's plants that rests on 2017 fuel emission "
        "factors. The gas group includes open-cycle units, whose "
        f"rate is {NESO_OPEN_CYCLE_GRAMS_PER_KWH} g, and the output does not "
        "split them; a unit held at part load also emits more per kWh. So "
        "the rate is more likely low than high for this gas. This is the CO2 "
        "of the gas NESO turned up in the same half-hours, not what would "
        "have been saved had the wind run: in the "
        f"{without['half_hours']:,} half-hours with no wind turned down as a "
        "system action, NESO still turned up "
        f"{twh(net_gas_mwh(without))} TWh more gas than it turned down. Gas a "
        "unit had already planned to burn is not counted. Added 27 November "
        "2026, after the read, from the saved output; no new data was read.</dd>"
        in PAGE.read_text(encoding="utf-8")
    )


def test_q37s_method_gives_the_carbon_rates_the_page_uses():
    method = QUESTIONS.read_text(encoding="utf-8")

    assert (
        "- **CO2 estimate (added 27 November 2026, after the read).** Worked\n"
        "  from the saved output; no new data is read. The gas energy turned\n"
        "  up less the gas energy turned down, in half-hours with any flagged\n"
        f"  wind turn-down, times {NESO_COMBINED_CYCLE_GRAMS_PER_KWH} gCO2/kWh, "
        "NESO's carbon intensity\n"
        f"  factor for combined-cycle gas ([methodology]({NESO_CARBON_METHOD})).\n"
    ) in method
    assert f"open-cycle units ({NESO_OPEN_CYCLE_GRAMS_PER_KWH} gCO2/kWh)" in method


def test_q37s_result_gives_the_wind_turned_down_from_its_saved_output():
    rows = saved_output_rows(Q37_OUTPUT)
    all_months = item_row(rows, "2", Q37_ALL_MONTHS)
    half_hours = int(all_months["half_hours"])
    any_turn_down = int(all_months["half_hours_with_flagged_wind_turn_down"])
    at_100_mw = int(all_months["half_hours_at_100_mw_or_more"])
    at_1_gw = int(all_months["half_hours_at_1_gw_or_more"])
    lowest, highest = lowest_and_highest_month(rows)

    assert (half_hours, any_turn_down, at_100_mw, at_1_gw) == (
        20_928,
        13_873,
        12_580,
        9_322,
    )
    assert (
        f"<dd>NESO turned down {twh(all_months['flagged_turned_down_mwh'])} TWh at "
        "wind farms as system actions (settlement months "
        f"{output_month_range(rows)}), "
        f"{whole(flagged_share_of_planned(all_months))}% of the energy the wind "
        f"farms planned; by month from {whole(flagged_share_of_planned(lowest))}% "
        f"({month_in_full(lowest['settlement_month'])}) to "
        f"{whole(flagged_share_of_planned(highest))}% "
        f"({month_in_full(highest['settlement_month'])}). Of {half_hours:,} "
        f"half-hours, {any_turn_down:,} have such a turn-down "
        f"({percent_of(any_turn_down, half_hours, 0)}%), {at_100_mw:,} one of 100 "
        f"MW or more ({percent_of(at_100_mw, half_hours, 0)}%) and {at_1_gw:,} one "
        f"of 1 GW or more ({percent_of(at_1_gw, half_hours, 0)}%). In the "
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q37")
    )


def test_q37s_result_gives_the_energy_turned_up_on_each_side_from_its_bands():
    # The half-hours on each side are the bands' own, so they must add up
    # to the counts the months give.
    rows = saved_output_rows(Q37_OUTPUT)
    all_months = item_row(rows, "2", Q37_ALL_MONTHS)
    sides = turn_up_with_and_without_wind_turn_down(rows)
    with_wind = sides["with"]
    without = sides["without"]

    assert with_wind["half_hours"] == int(
        all_months["half_hours_with_flagged_wind_turn_down"]
    )
    assert with_wind["half_hours"] + without["half_hours"] == int(
        all_months["half_hours"]
    )
    assert without["half_hours"] == 7_055
    assert (
        f"In the {with_wind['half_hours']:,}, NESO turned up "
        f"{twh(with_wind['all_mwh'])} TWh, {twh(with_wind['gas_mwh'])} TWh of it at "
        f"gas units ({gas_share(with_wind)}%). In the {without['half_hours']:,} "
        f"with none, it turned up {twh(without['all_mwh'])} TWh, "
        f"{twh(without['gas_mwh'])} TWh of it at gas units "
        f"({gas_share(without)}%). By band, gas averages "
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q37")
    )


def test_q37s_measure_gives_the_flagged_share_of_gas_turned_up_from_its_bands():
    with_wind = turn_up_with_and_without_wind_turn_down(saved_output_rows(Q37_OUTPUT))[
        "with"
    ]
    flagged = percent_of(with_wind["flagged_gas_mwh"], with_wind["gas_mwh"], 0)

    assert (
        "energy turned up to replace energy turned down behind a bottleneck need "
        f"not carry the flag ({flagged}% of the gas energy turned up in half-hours "
        "with wind turned down as a system action does).</dd>"
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q37")
    )


def test_q37s_days_are_its_saved_outputs_first_and_last_half_hours():
    # The output's months open with a part month; the day it starts and the
    # last half-hour read are the all-months row's.
    rows = saved_output_rows(Q37_OUTPUT)
    all_months = item_row(rows, "2", Q37_ALL_MONTHS)
    first_day = half_hour_long_date(all_months["first_half_hour"])
    last_day = half_hour_long_date(all_months["last_half_hour"])
    last_time = all_months["last_half_hour"].partition(" ")[2][:5]
    folded = fold_outs(PAGE.read_text(encoding="utf-8"), "q37")

    assert (
        f"<dd>Settlement months {output_month_range(rows)}; the first is a part "
        f"month, from {first_day}. Each half-hour is placed by" in folded
    )
    assert (
        f"<dd>{first_day} to {last_day}, with data to {last_day} {last_time} UTC. "
        f"Read {status_read('Q37')}.</dd>" in folded
    )


def test_q37s_chart_entry_names_every_group_its_second_part_holds():
    sides = turn_up_with_and_without_wind_turn_down(saved_output_rows(Q37_OUTPUT))

    assert sorted(sides["with"]["fuel_groups"] | sides["without"]["fuel_groups"]) == [
        "bioenergy",
        "gas_turbine",
        "hydro",
        "no published fuel type",
        "other",
        "pumped_storage",
        "wind",
    ]
    assert (
        "Everything else is bioenergy, hydro, pumped storage, wind, the other "
        "group and units with no published fuel type.</dd>"
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q37")
    )


def test_q37s_title_holds_band_by_band_in_its_chart_data():
    # "The more wind NESO turns down in a half-hour, the more gas": the gas
    # bar grows at every step from no wind turned down to 2 GW or more.
    bands = []
    gas_mw = []
    for row in saved_output_rows(Q37_CHART_DATA):
        bands.append(row["wind_turn_down_band"])
        gas_mw.append(Decimal(row["gas_mw"]))

    assert bands == sorted(bands)
    assert len(bands) == 6
    assert gas_mw == sorted(set(gas_mw))


def turn_up_by_band(rows):
    """Q37's energy turned up in each band of wind turned down as a system
    action: every group's and gas's, in MWh, beside the band's half-hours."""
    bands = {}
    for row in section_rows(rows, "3"):
        band = bands.setdefault(
            row["wind_turn_down_band"],
            {
                "half_hours": int(row["half_hours"]),
                "all_mwh": Decimal(0),
                "gas_mwh": Decimal(0),
            },
        )
        band["all_mwh"] += Decimal(row["turned_up_mwh"])
        if row["fuel_group"] == "gas_turbine":
            band["gas_mwh"] += Decimal(row["turned_up_mwh"])
    return bands


def test_q37s_result_gives_gas_at_both_ends_of_its_bands_from_its_saved_output():
    # "Falls from ... to ...": gas's share drops at every step up the bands.
    bands = turn_up_by_band(saved_output_rows(Q37_OUTPUT))
    none = bands[Q37_NO_WIND_TURNED_DOWN]
    largest = bands[Q37_LARGEST_BAND]
    exact_shares = []
    for name in sorted(bands):
        exact_shares.append(bands[name]["gas_mwh"] / bands[name]["all_mwh"])

    assert exact_shares == sorted(set(exact_shares), reverse=True)
    assert (
        f"By band, gas averages {average_gas_gw(none)} GW with no wind turned "
        f"down as a system action and {average_gas_gw(largest)} GW at 2 GW or "
        "more, while its share of the energy turned up falls from "
        f"{gas_share(none)}% to {gas_share(largest)}%.</dd>"
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q37")
    )


def test_q37s_limits_say_gas_is_most_of_the_turn_up_in_every_band():
    bands = turn_up_by_band(saved_output_rows(Q37_OUTPUT))

    assert len(bands) == 6
    for band in bands.values():
        assert 2 * band["gas_mwh"] > band["all_mwh"]
    assert (
        "Gas is most of the energy NESO turns up in every band, including "
        "half-hours with no wind turned down as a system action."
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q37")
    )


def test_q37_leaves_nothing_out_in_its_saved_output():
    # Section 4 holds what is left out and what fails a check; a half-hour
    # with a wind stretch left out is counted in its month.
    rows = saved_output_rows(Q37_OUTPUT)
    not_measured = set()
    for row in section_rows(rows, "1") + section_rows(rows, "2"):
        not_measured.add(row["half_hours_wind_not_measured"])
    bands = []
    for row in section_rows(rows, "3"):
        bands.append(row["wind_turn_down_band"])

    assert section_rows(rows, "4") == []
    assert not_measured == {"0"}
    assert Q37_WIND_NOT_MEASURED not in bands
    assert (
        "<dd>Nothing: every instruction in the period had a plan to compare "
        "with, a single ramp to read its level from and a flag value.</dd>"
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q37")
    )


def test_q37s_band_names_are_short_enough_to_show_in_full():
    # The interactive chart cuts an axis label wider than 180 pixels; the
    # longest name that shows in full has 23 characters.
    names = []
    for row in saved_output_rows(Q37_CHART_DATA):
        names.append(row["label"].partition("|")[0])

    assert names[0] == "None as a system action"
    assert len(names) == 6
    for name in names:
        assert len(name) <= 23


Q38_ALL_MONTHS = "output against registered capacity"
Q38_CHART_DATA = "q38_output_and_gap_by_month.csv"
Q38_COMPARISON = "context_q38_desnz_monthly_nuclear.csv"
DESNZ_INPUT = "desnz_nuclear_supplied_by_month.csv"
Q38_CLASSES = ("planned", "unplanned", "none")
Q38_UNITS_OUTSIDE = ["DNGB21", "DNGB22", "HINB-7", "HINB-8", "HUNB-7", "HUNB-8"]
# The settlement runs as the page names them, earliest first.
Q38_RUN_NAMES = {
    "II": "one-week",
    "SF": "three-week",
    "R1": "two-month",
    "R2": "four-month",
    "R3": "seven-month",
    "RF": "14-month",
}


def nuclear_share(row):
    """A row's metered output as an exact percentage of its capacity."""
    return Decimal(100) * Decimal(row["output_mwh"]) / Decimal(row["capacity_mwh"])


def lowest_and_highest_nuclear_month(rows):
    months = section_rows(rows, "1")
    lowest = min(months, key=nuclear_share)
    highest = max(months, key=nuclear_share)
    return lowest, highest


def nuclear_gap_by(rows, column, key):
    """Q38's gap over all months (section 4), in MWh, summed by the column
    named by key: the notice class, the generating state or both."""
    totals = {}
    for row in section_rows(rows, "4"):
        if key == "both":
            group = (row["notice_class"], row["output_state"])
        else:
            group = row[key]
        totals[group] = totals.get(group, Decimal(0)) + Decimal(row[column])
    return totals


def gap_shares(rows, column, places):
    """Each notice class's share of the gap in the column, at whole numbers
    or one decimal, and the whole-number share with any notice, beside the
    gap's total. At one decimal the three classes add to 100.0, as the page
    shows them."""
    by_class = nuclear_gap_by(rows, column, "notice_class")
    total = summed(section_rows(rows, "4"), column)
    shares = {}
    for notice_class in Q38_CLASSES:
        shares[notice_class] = percent_of(by_class[notice_class], total, places)
    shares["any notice"] = percent_of(
        by_class["planned"] + by_class["unplanned"], total, 0
    )
    if places == 1:
        added = Decimal(0)
        for notice_class in Q38_CLASSES:
            added += Decimal(shares[notice_class])
        assert added == 100
    return shares, total


def test_q38s_gap_split_adds_up_to_its_months_and_holds_no_other_class():
    # Section 4 splits the gap over all months by notice class and
    # generating state; it must add up to section 2's gap and to section
    # 3's months, and hold only the three classes the page names.
    rows = saved_output_rows(Q38_OUTPUT)
    all_months = item_row(rows, "2", Q38_ALL_MONTHS)
    classes = set()
    for row in section_rows(rows, "3") + section_rows(rows, "4"):
        classes.add(row["notice_class"])

    assert classes == set(Q38_CLASSES)
    for column in ("gap_mwh", "gap_below_highest_mwh"):
        assert summed(section_rows(rows, "4"), column) == Decimal(all_months[column])
        assert abs(
            summed(section_rows(rows, "3"), column) - Decimal(all_months[column])
        ) <= Decimal("0.5")


def test_q38s_answer_takes_its_shares_from_its_saved_output():
    rows = saved_output_rows(Q38_OUTPUT)
    all_months = item_row(rows, "2", Q38_ALL_MONTHS)
    lowest, highest = lowest_and_highest_nuclear_month(rows)
    months = section_rows(rows, "1")
    shares, _total = gap_shares(rows, "gap_mwh", 1)

    assert all_months["units"] == "10"
    assert (
        answer_of(PAGE.read_text(encoding="utf-8"), "q38") == "<strong>About "
        f"{whole(nuclear_share(all_months))}% of it; nearly all of the gap came "
        "with a published outage notice.</strong> From "
        f"{month_in_full(months[0]['settlement_month'])} to "
        f"{month_in_full(months[-1]['settlement_month'])} the ten nuclear units' "
        f"metered output was {whole(nuclear_share(all_months))}% of their "
        f"registered capacity, between {whole(nuclear_share(lowest))}% "
        f"({month_in_full(lowest['settlement_month'])}) and "
        f"{whole(nuclear_share(highest))}% "
        f"({month_in_full(highest['settlement_month'])}) by month. Of the gap "
        f"below capacity, {shares['any notice']}% fell in half-hours when a "
        f"notice was in force for the unit ({shares['planned']}% planned, "
        f"{shares['unplanned']}% unplanned)."
    )


def test_q38s_result_gives_the_energy_and_the_gap_by_notice_from_its_saved_output():
    rows = saved_output_rows(Q38_OUTPUT)
    all_months = item_row(rows, "2", Q38_ALL_MONTHS)
    lowest, highest = lowest_and_highest_nuclear_month(rows)
    shares, total = gap_shares(rows, "gap_mwh", 1)

    assert (
        f"<dd>The ten units metered {twh(all_months['output_mwh'])} TWh; their "
        "registered capacity, held for every half-hour, comes to "
        f"{twh(all_months['capacity_mwh'])} TWh "
        f"(settlement months {output_month_range(rows)}), so output is "
        f"{whole(nuclear_share(all_months))}% of it; by month from "
        f"{whole(nuclear_share(lowest))}% "
        f"({month_in_full(lowest['settlement_month'])}) to "
        f"{whole(nuclear_share(highest))}% "
        f"({month_in_full(highest['settlement_month'])}). The gap below "
        f"capacity is {twh(total)} TWh: {shares['planned']}% of it in "
        "half-hours with a planned notice in force for the unit, "
        f"{shares['unplanned']}% with an unplanned notice and {shares['none']}% "
        "with none. No half-hour had both kinds of notice.</dd>"
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )


def test_q38s_gap_while_not_generating_always_had_a_notice():
    rows = saved_output_rows(Q38_OUTPUT)
    by_state = nuclear_gap_by(rows, "gap_mwh", "output_state")
    by_both = nuclear_gap_by(rows, "gap_mwh", "both")
    shares, total = gap_shares(rows, "gap_mwh", 1)

    assert ("none", "not generating") not in by_both
    assert (
        by_both[("none", "generating")]
        == nuclear_gap_by(rows, "gap_mwh", "notice_class")["none"]
    )
    assert (
        f"<dd>{percent_of(by_state['not generating'], total, 0)}% of the gap "
        f"({twh(by_state['not generating'])} TWh) fell in half-hours when the "
        "unit was not generating, every one of them with a notice in force. The "
        f"{shares['none']}% with no notice was all in half-hours when the unit "
        "was generating.</dd>" in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )


def test_q38s_second_gap_and_the_capacities_beside_it_come_from_their_sources():
    rows = saved_output_rows(Q38_OUTPUT)
    units = section_rows(rows, "5")
    registered_mw = summed(units, "registry_capacity_mw")
    reaching = []
    for unit in units:
        if Decimal(unit["highest_half_hour_mw"]) >= Decimal(
            unit["registry_capacity_mw"]
        ):
            reaching.append(unit["national_grid_bm_unit"])
    shares, total = gap_shares(rows, "gap_below_highest_mwh", 1)
    method = flattened(register_section(QUESTIONS.read_text(encoding="utf-8"), "Q38"))

    assert reaching == ["HEYM28"]
    assert len(units) - len(reaching) == 9
    assert (
        "nine of the ten units' highest half-hours are below their registered"
        in (VISIBLE_LIMITS["q38"])
    )
    assert "EDF gives the five stations' supply to the grid as 5,958 MW in all" in (
        method
    )
    assert (
        f"<dd>The ten units are registered at {int(whole(registered_mw)):,} MW; EDF "
        "gives the five stations' supply to the grid as 5,958 MW, and only one "
        "unit's highest half-hour reaches its registered figure. Part of the gap "
        "below registered capacity can therefore have no notice, so a second gap "
        "is measured below each unit's highest metered half-hour, or below its "
        f"registered capacity where that is lower: {twh(total)} TWh, {shares['planned']}% of it with a planned notice, "
        f"{shares['unplanned']}% with an unplanned notice and {shares['none']}% "
        "with none.</dd>" in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )


def test_q38s_units_are_listed_by_station_with_their_output_shares():
    rows = saved_output_rows(Q38_OUTPUT)
    by_station = {}
    for unit in section_rows(rows, "5"):
        by_station.setdefault(unit["station"], []).append(
            f"{unit['national_grid_bm_unit']} {whole(nuclear_share(unit))}%"
        )
    stations = []
    for station in sorted(by_station):
        units = sorted(by_station[station])
        assert len(units) == 2
        stations.append(f"{station} {units[0]} and {units[1]}")

    assert len(stations) == 5
    assert (
        "<dd>Output as a share of registered capacity, by station: "
        f"{'; '.join(stations)}. Sizewell B's two units are the two "
        "turbine-generators of its one reactor.</dd>"
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )


def test_q38_gives_published_outages_nuclear_share_with_a_notice():
    q5 = saved_output_rows("q5_shortfall_with_known_outage.csv")
    nuclear_with_notice, nuclear_total = with_notice_and_total(
        q5_rows(q5, "2", "nuclear")
    )

    first, last = long_date_range(q5)

    assert (
        f"measures under-delivery against each unit's plan; for nuclear, "
        f"{percent_of(nuclear_with_notice, nuclear_total, 0)}% of it came with a "
        f"notice ({first} to {last}). This measures the gap below registered "
        "capacity instead, so an outage during which a unit's plan is zero, which "
        "that measure cannot see, is counted here. The two shares measure "
        "different things over different dates; their closeness is not a check "
        "of one on the other.</dd>"
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )


def test_the_desnz_comparison_is_the_nuclear_output_beside_desnzs_figures():
    # Recomputed from its two inputs: the saved output's months and DESNZ's
    # thirteen figures as copied from Energy Trends table 5.4.
    rows = saved_output_rows(Q38_OUTPUT)
    output_by_month = {}
    for row in section_rows(rows, "1"):
        output_by_month[row["settlement_month"][:7]] = Decimal(row["output_mwh"])
    desnz = saved_output_rows(DESNZ_INPUT)
    comparison = saved_output_rows(Q38_COMPARISON)
    output_total = Decimal(0)
    desnz_total = Decimal(0)
    differences = []

    assert len(desnz) == len(section_rows(comparison, "1")) == 13
    for figure, compared in zip(desnz, section_rows(comparison, "1"), strict=True):
        output_twh = output_by_month[figure["month"]] / Decimal(1_000_000)
        desnz_twh = Decimal(figure["nuclear_twh"])
        above = Decimal(100) * (output_twh - desnz_twh) / desnz_twh
        differences.append(above)
        output_total += output_twh
        desnz_total += desnz_twh
        assert compared["month"] == figure["month"]
        assert Decimal(compared["output_twh"]) == output_twh
        assert compared["desnz_twh"] == figure["nuclear_twh"]
        assert compared["output_above_desnz_pct"] == hundredths(above)
        assert compared["desnz_provisional"] == figure["provisional"]
        assert abs(above) < 5
    total = one_row(comparison, "section", "2")
    above_total = Decimal(100) * (output_total - desnz_total) / desnz_total
    assert Decimal(total["output_twh"]) == output_total
    assert Decimal(total["desnz_twh"]) == desnz_total
    assert total["output_above_desnz_pct"] == hundredths(above_total)
    for row in comparison:
        assert row["label"] == COMPARISON_LABEL
    assert is_a_context_label(COMPARISON_LABEL, "comparison")
    assert [row["month"] for row in desnz if row["provisional"] == "yes"] == ["2026-09"]
    assert (
        f"<dd>The output is between {tenths(-min(differences))}% below and "
        f"{tenths(max(differences))}% above DESNZ's figure in each of the thirteen "
        f"months from {month_in_full(desnz[0]['month'] + '-01')} to "
        f"{month_in_full(desnz[-1]['month'] + '-01')}, and {tenths(above_total)}% "
        f"above it over them ({tenths(output_total)} against "
        f"{tenths(desnz_total)} TWh)."
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )
    assert "Energy Trends table 5.4</a>, September 2026 provisional);" in (
        fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )


def test_desnzs_figures_carry_their_source_and_licence_on_every_page():
    text = (REPOSITORY / "docs" / "results" / DESNZ_INPUT).read_text(encoding="utf-8")
    source = (
        "https://www.gov.uk/government/statistics/electricity-section-5-energy-trends"
    )
    licence = "Contains public sector information licensed under the"
    page = PAGE.read_text(encoding="utf-8")
    footer = page.split("<footer>", 1)[1].split("</footer>", 1)[0]
    readme = README.read_text(encoding="utf-8")
    sources = readme.split("## Data sources and licences", 1)[1].split("\n## ", 1)[0]

    assert text.startswith(OGL_LINE + "# Department for Energy Security and Net Zero")
    assert "file ET_5.4_NOV_26.xlsx, published 26 November 2026" in text
    assert source in text
    assert source in footer and licence in footer
    assert source in sources and licence in flattened(sources)
    # Four providers are credited, so no wording for two.
    assert "None of the providers endorses this project." in footer
    assert "None of the providers endorses this project." in sources
    assert "Neither provider" not in page + readme
    assert '<a href="results/context_q38_desnz_monthly_nuclear.csv">' in fold_outs(
        page, "q38"
    )


def test_q38s_measure_gives_the_energy_drawn_from_its_saved_output():
    all_months = item_row(saved_output_rows(Q38_OUTPUT), "2", Q38_ALL_MONTHS)

    assert Decimal(all_months["above_capacity_mwh"]) == 0
    assert (
        "counted as zero where it is negative "
        f"({hundredths(Decimal(all_months['drawn_mwh']) / 1_000_000)} TWh drawn in "
        "all); the gap is capacity minus output, counted as zero where output is "
        "above capacity." in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )


def test_q38_leaves_nothing_out_and_names_its_settlement_runs():
    rows = saved_output_rows(Q38_OUTPUT)
    checks = section_rows(rows, "6")
    half_hours = item_row(
        rows, "6", "half-hours from the first settlement date to the cutoff"
    )["unit_half_hours"]
    by_run = {}
    outside = []
    metered_rows = {}
    for row in checks:
        item = row["item"]
        if item.startswith("left out") or item.startswith("listed units left out"):
            assert row["units"] == "0"
        if item.startswith("unit half-hours by metered run: "):
            by_run[item.rpartition(" ")[2]] = int(row["unit_half_hours"])
        if item == "registry NUCLEAR unit outside the listed units":
            outside.append(row["national_grid_bm_unit"])
            metered_rows[row["national_grid_bm_unit"]] = row["unit_half_hours"]
            assert Decimal(row["output_mwh"]) == 0
            assert Decimal(row["drawn_mwh"]) == 0
    runs = []
    for run, name in Q38_RUN_NAMES.items():
        runs.append(f"{by_run[run]:,} from the {name} run")
    runs[0] = runs[0].replace(" from ", " come from ", 1)

    assert item_row(rows, "6", "half-hours without all 10 listed units measured")[
        "unit_half_hours"
    ] == ("0")
    for unit in section_rows(rows, "5"):
        assert unit["unit_half_hours"] == half_hours
    assert outside == Q38_UNITS_OUTSIDE
    # Dungeness B and Hinkley Point B: a metered row for every half-hour,
    # none with output; Hunterston B: no metered row.
    assert metered_rows == {
        "DNGB21": half_hours,
        "DNGB22": half_hours,
        "HINB-7": half_hours,
        "HINB-8": half_hours,
        "HUNB-7": "0",
        "HUNB-8": "0",
    }
    assert sorted(by_run) == sorted(Q38_RUN_NAMES)
    assert sum(by_run.values()) == 10 * int(half_hours)
    assert (
        f"<dd>Nothing: each of the ten units has all {int(half_hours):,} "
        "half-hours from 1 September 2025 to 31 October 2026, and no output was "
        "above registered capacity. Of the six other nuclear units in the "
        "registry, the four at Dungeness B and Hinkley Point B have metered rows "
        "with no output, and the two at Hunterston B have no metered rows in "
        "these months. Of the "
        f"{sum(by_run.values()):,} unit half-hours, {', '.join(runs[:-1])} and "
        f"{runs[-1]}.</dd>" in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )


def test_q38s_days_are_its_saved_outputs_first_month_and_last_half_hour():
    rows = saved_output_rows(Q38_OUTPUT)
    all_months = item_row(rows, "2", Q38_ALL_MONTHS)
    first_day = long_date(section_rows(rows, "1")[0]["settlement_month"])
    last_day = half_hour_long_date(all_months["last_half_hour"])
    last_time = all_months["last_half_hour"].partition(" ")[2][:5]

    assert first_day == "1 September 2025"
    assert (
        f"<dd>{first_day} to {last_day}, with data to {last_day} {last_time} UTC. "
        f"Read {status_read('Q38')}.</dd>"
        in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )


def test_q38s_chart_data_fills_each_months_capacity_from_its_saved_output():
    rows = saved_output_rows(Q38_OUTPUT)
    saved_months = section_rows(rows, "1")
    chart_rows = saved_output_rows(Q38_CHART_DATA)
    parts = ["output_pct", "planned_gap_pct", "unplanned_gap_pct", "no_notice_gap_pct"]

    assert len(chart_rows) == 14
    for saved, drawn in zip(saved_months, chart_rows, strict=True):
        assert drawn["settlement_month"] == saved["settlement_month"]
        assert drawn["output_pct"] == saved["output_pct_of_capacity"]
        filled = summed([drawn], parts[0])
        for part in parts[1:]:
            filled += Decimal(drawn[part])
        assert abs(filled - 100) <= Decimal("0.02")


def test_q38s_method_entry_states_what_had_been_read_and_links_its_files():
    assert (
        "<dd>Recorded 28 November 2026, before any metered share of capacity or "
        "split of the gap was read; the nuclear units' planned share of capacity "
        "by month (a context count, 1 November 2026), their rows in published "
        f"outages{details_link('Q5')} (12 October 2026) and their metered total "
        f"in more or less than planned{details_link('Q3')} (11 October 2026) had "
        "been read. " in fold_outs(PAGE.read_text(encoding="utf-8"), "q38")
    )
    for file_name in (Q38_OUTPUT, Q38_CHART_DATA):
        assert f'<a href="results/{file_name}">' in fold_outs(
            PAGE.read_text(encoding="utf-8"), "q38"
        )

    short = {}
    for row in saved_output_rows("q25_short_instructions_by_fuel_group.csv"):
        short[row["fuel_group"]] = Decimal(row["short_joined_pct"])
    other_one_in = NUMBER_WORDS[int(whole(100 / short["other"]))]
    pumped_one_in = NUMBER_WORDS[int(whole(100 / short["pumped_storage"]))]

    assert answer_of(PAGE.read_text(encoding="utf-8"), "q25") == (
        f"<strong>Yes, far more often.</strong> About one in {other_one_in} "
        "instructions to the other group, mostly batteries, lasts under 10 "
        f"minutes (<strong>{whole(short['other'])}%</strong>), and one in "
        f"{pumped_one_in} to pumped storage. For gas it is about 1 in "
        f"{nearest_ten(100 / short['gas_turbine'])}, and less for hydro, "
        "bioenergy and wind."
    )
    assert (
        max(short["hydro"], short["bioenergy"], short["wind"]) < (short["gas_turbine"])
    )


def test_q6s_answer_and_tiles_give_the_saved_medians_in_plain_words():
    # Of the unplanned notices about half are out before their written
    # start, and 9 in 10 by 30 minutes after it; the tile's median is 0.9
    # minutes after. Planned notices' median is in hours before the start.
    by_class = notices_median_and_p90_by_class(
        saved_output_rows("q6_publication_lag.csv")
    )
    unplanned_notices, unplanned_median, unplanned_p90 = by_class["Unplanned"]
    planned_notices, planned_median, _planned_p90 = by_class["Planned"]
    hours_before = whole(-Decimal(planned_median) / 60)
    timing = one_row(
        saved_output_rows("context_q6_publication_timing.csv"),
        "outage_class",
        "Unplanned",
    )
    before = int(timing["before_start"])
    by_30_minutes_after = (
        before + int(timing["at_start"]) + int(timing["up_to_30min_after_start"])
    )
    notices = int(timing["notices"])
    page = PAGE.read_text(encoding="utf-8")
    shown = in_view(page, "q6")

    assert whole(unplanned_median) == "1"
    assert 0 < Decimal(unplanned_p90) < 30
    assert (before, by_30_minutes_after, notices) == (5_583, 10_652, 11_811)
    assert notices == int(unplanned_notices)
    assert tenths(Decimal(before) / notices) == "0.5"
    assert whole(Decimal(10 * by_30_minutes_after) / notices) == "9"
    assert answer_of(page, "q6") == (
        "<strong>Around the written start, for unplanned notices.</strong> About "
        "half appear before it and half after; <strong>9 in 10</strong> are out "
        "by 30 minutes after it. The median planned notice appears about "
        f"{hours_before} hours before its written start."
    )
    assert '<p class="value">about 1 min</p>' in shown
    assert f'<p class="label">Unplanned ({int(unplanned_notices):,} notices)</p>' in (
        shown
    )
    assert f'<p class="label">Planned ({int(planned_notices):,} notices)</p>' in shown
    assert f'<p class="value">about {hours_before} h</p>' in shown


def test_every_in_page_link_has_a_target():
    page = PAGE.read_text(encoding="utf-8")
    targets = set(re.findall(r'\bid="([^"]+)"', page))
    links = set(re.findall(r'href="#([^"]+)"', page))

    assert "national-demand" in links
    assert sorted(links - targets) == []


def test_the_page_shows_no_figure_with_four_or_more_decimals():
    assert FOUR_OR_MORE_DECIMALS.findall(page_text()) == []


def test_the_pages_workflow_runs_on_pushes_to_main_for_the_site_and_results():
    # BaseLoader keeps the key "on" a string; YAML 1.1 would read it as True.
    workflow = yaml.load(PAGES_WORKFLOW.read_text(encoding="utf-8"), yaml.BaseLoader)

    assert workflow["on"] == {
        "push": {"branches": ["main"], "paths": ["site/**", "docs/results/**"]},
        "workflow_dispatch": "",
    }


def actions_used(job):
    """The actions a workflow job's steps use, without their versions."""
    used = []
    for step in job["steps"]:
        if "uses" in step:
            used.append(step["uses"].partition("@")[0])
    return used


def test_the_pages_workflow_deploys_in_a_job_after_the_upload():
    # In one job the deploy step looked the upload up before GitHub listed
    # it, and the run failed.
    workflow = yaml.load(PAGES_WORKFLOW.read_text(encoding="utf-8"), yaml.BaseLoader)
    build = workflow["jobs"]["build"]
    deploy = workflow["jobs"]["deploy"]

    assert sorted(workflow["jobs"]) == ["build", "deploy"]
    assert actions_used(build)[-1] == "actions/upload-pages-artifact"
    assert actions_used(deploy) == ["actions/deploy-pages"]
    assert deploy["needs"] == "build"
    assert deploy["environment"] == {
        "name": "github-pages",
        "url": "${{ steps.deployment.outputs.page_url }}",
    }
    assert deploy["steps"][0]["id"] == "deployment"


def test_the_pages_workflow_asks_for_exactly_its_three_permissions():
    workflow = yaml.load(PAGES_WORKFLOW.read_text(encoding="utf-8"), yaml.BaseLoader)

    assert workflow["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert jobs_with_their_own_permissions(workflow) == []
