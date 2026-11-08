import csv
import io
import json
import re
import statistics
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from scripts.readme_charts import (
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

# Hand-made saved outputs for the reads the fixture folder does not hold.
# Q3's groups come in an order the chart keeps; coal is a closed unit the
# chart leaves out. Q12's order differs from the
# chart's (largest gap first); interconnectors are left out. Q2's unit types
# sum to round figures, with E beside T and C beside V.
CONTEXT_LABEL = "context only: denominator, not a registered result"
DISTRIBUTION_LABEL = "context only: distribution, not a registered result"
BREAKDOWN_LABEL = "context only: breakdown, not a registered result"
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


NEW_SAVED_OUTPUTS = {
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
        f"1,all half-hours,1920,1920,23064,11532,22.15,{CONTEXT_LABEL}",
        "2,settlement month 2025-08 (partial: 22 to 31),480,480,21350,10675,5.12,"
        f"{CONTEXT_LABEL}",
        f"2,settlement month 2025-09,1440,1440,23635,11817,17.02,{CONTEXT_LABEL}",
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
    "context_q23_one_week_and_later_runs.csv",
    "context_q23_runs_by_month.csv",
    "context_q25_battery_named_units.csv",
    "context_q25_cadl_check.csv",
    "context_q28_runs_held.csv",
    "context_q28_wind_level.csv",
    "context_q30_outturn_lateness.csv",
    "context_q30_wind_miss_sign.csv",
    "context_q5_drawing_power.csv",
    "context_q6_lag_quartiles.csv",
    "context_q6_publication_timing.csv",
    "context_q6_start_alignment.csv",
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
    "q28_reverse_carbon_miss_by_under_delivery_fifth.csv",
    "q28_reverse_carbon_miss_by_under_delivery_fifth.png",
    "q28_reverse_carbon_miss_by_under_delivery_fifth.vl.json",
    "q28_reverse_exploratory.csv",
    "q28_reverse_strata_exploratory.csv",
    "q28_shortfall_index_by_miss_fifth.csv",
    "q28_shortfall_index_by_miss_fifth.png",
    "q28_shortfall_index_by_miss_fifth.vl.json",
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
        "context_q23_one_week_and_later_runs.csv": BMRS_LINE,
        "context_q23_runs_by_month.csv": BMRS_LINE,
        "context_q25_battery_named_units.csv": BMRS_LINE,
        "context_q25_cadl_check.csv": BMRS_LINE,
        "context_q28_runs_held.csv": BMRS_LINE,
        "context_q28_wind_level.csv": BMRS_AND_CARBON_LINE,
        "context_q30_outturn_lateness.csv": BMRS_LINE,
        "context_q30_wind_miss_sign.csv": BMRS_LINE,
        "context_q5_drawing_power.csv": BMRS_LINE,
        "context_q6_lag_quartiles.csv": BMRS_LINE,
        "context_q6_publication_timing.csv": BMRS_LINE,
        "context_q6_start_alignment.csv": BMRS_LINE,
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
        "q28_reverse_carbon_miss_by_under_delivery_fifth.csv": BMRS_AND_CARBON_LINE,
        "q28_reverse_exploratory.csv": BMRS_AND_CARBON_LINE,
        "q28_reverse_strata_exploratory.csv": BMRS_AND_CARBON_LINE,
        "q28_shortfall_index_by_miss_fifth.csv": BMRS_AND_CARBON_LINE,
        "q29_context_demand_by_month.csv": BMRS_LINE,
        "q29_final_forecast_error_by_month_and_block.csv": CARBON_LINE,
        "q2_changes_by_unit_prefix.csv": BMRS_LINE,
        "q2_gas_units_revisions.csv": BMRS_LINE,
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
        "q3_gap_from_plan_by_fuel_group.vl.json": [("rule", group), ("point", group)],
        "q5_under_delivery_by_outage_notice.vl.json": [("bar", part), ("point", group)],
    }
    assert fuel_colours_not_from_the_scheme(written) == []


def test_bars_split_into_parts_take_the_page_split_greys(written):
    # Colour means fuel group; greys mean the split inside a bar.
    assert split_schemes(written) == {
        "q12_instructed_share_by_fuel_group.vl.json": SPLIT_TWO_SCHEME,
        "q24_flagged_share_by_fuel_group.vl.json": SPLIT_TWO_SCHEME,
        "q5_under_delivery_by_outage_notice.vl.json": SPLIT_THREE_SCHEME,
    }


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
    # months; Q2's week is its rule's.
    q3 = "22 Aug 2025 – 26 Sep 2026"
    assert q3 in x_axis_titles(written, "q3_gap_from_plan_by_fuel_group.vl.json")
    assert q3 in x_axis_titles(written, "q12_instructed_share_by_fuel_group.vl.json")
    assert "22 Aug 2025 – 27 Sep 2026" in x_axis_titles(
        written, "q5_under_delivery_by_outage_notice.vl.json"
    )
    assert "energy in TWh, settlement months Aug 2025 – Sep 2026." in reading_line(
        written, "q24_flagged_share_by_fuel_group.vl.json"
    )
    assert (
        f"for {Q2_WEEK} came from"
        in (
            written_specs(written)["q2_moved_energy_by_unit_type.vl.json"][
                "description"
            ]
        )
    )


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
    assert f"the first is a part month, from {start}. Units with" in page


def test_q28_says_which_way_it_compares_and_points_to_the_reverse():
    # Q28 sorts half-hours by the size of the carbon miss, not its sign,
    # then compares under-delivery; the reverse is Q31's and Q32's.
    page = PAGE.read_text(encoding="utf-8")
    article = page.split('<article id="q28">', 1)[1].split("</article>", 1)[0]
    rules = (
        "https://github.com/JayWatNorm/gridskew/blob/main/docs/research-questions.md"
    )
    reverse = (
        '<p class="note">The reverse question, how far under-delivery goes with the '
        "carbon forecast's miss, and in which direction, is registered as "
        f'<a href="{rules}#q31-when-units-unexplained-deviations-add-carbon-is-the-'
        'last-carbon-forecast-too-clean">Q31</a> and '
        f'<a href="{rules}#q32-when-wind-farms-fall-short-of-their-plans-is-the-'
        'last-carbon-forecast-too-clean-and-is-there-no-such-link-for-gas">Q32</a>, '
        "read on new data in 2027.</p>"
    )

    assert (
        "<h3>In the half-hours when NESO's stored carbon forecast missed most, "
        "did units fall further short of their plans?" in article
    )
    assert (
        "Half-hours are sorted by how far the forecast missed, not which way, so "
        "this cannot say whether under-delivery makes the forecast too clean or "
        "too dirty, or by how much.</p>" in article
    )
    assert reverse in article
    assert "<!-- Q28-REVERSE-EXPLORATORY -->" in article


def signed_tenths(value):
    """A saved value to one decimal, halves up, with its sign, as the page
    states it."""
    rounded = Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if rounded > 0:
        return f"+{rounded}"
    return str(rounded)


def test_the_q28_reverse_chart_reads_its_range_from_its_saved_output(written):
    # The fixture's read runs 1 September 2025 to 31 July 2026.
    assert "fifth of Q28's half-hours, 1 Sep 2025 – 31 Jul 2026, least" in (
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


def test_q28s_exploratory_reverse_block_states_its_saved_figures_and_label():
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
    page = PAGE.read_text(encoding="utf-8")
    block = page.split('<section class="exploratory"', 1)[1].split("</section>", 1)[0]

    assert '<h4 id="q28-reverse-title">Exploratory: the reverse direction</h4>' in block
    assert (
        "<p>Q28 sorted by the size of the carbon miss and found a small rise in "
        f"under-delivery ({whole((q28_ratio - 1) * 100)}%; its test needed 25%). "
        "This sorts the same half-hours by how much units under-delivered, and "
        "looks at the direction of the miss. Each extra GW of under-delivery went "
        f"with a carbon miss about {tenths(slope['value'])} g/kWh further towards "
        f"dirtier than forecast (90% range {tenths(slope['interval_low'])} to "
        f"{tenths(slope['interval_high'])}). On average, the fifth with the least "
        f"under-delivery came out {tenths(-Decimal(least))} g/kWh cleaner than "
        f"forecast and the fifth with the most {tenths(most)} g/kWh dirtier; misses "
        f"had a mean size of {tenths(mean_size)} g/kWh in either direction. Time of "
        "day and month are not held fixed, and carbon misses differ by both (Q29), "
        "so this may partly reflect them.</p>" in block
    )
    assert (
        f"<figcaption>Q28's {int(read['half_hours']):,} half-hours, {dates};" in block
    )
    assert (
        '<p class="note">Exploratory: asked after Q28 was read, so not '
        "pre-registered. It shows association, not cause; Q31 and Q32 test this "
        "direction on new data.</p>" in block
    )
    assert read["result"] == "matches Q28"
    assert q28_bar["result"] == "not supported"
    assert Decimal(least) < 0 < Decimal(most)


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
NUMBER_WORDS = {4: "four", 6: "six", 13: "thirteen", 17: "seventeen"}


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


def strata_table_row(label, row):
    return (
        f"<tr><td>{label}</td><td>{int(row['half_hours']):,}</td>"
        f"<td>{page_signed(row['value'])}</td>"
        f"<td>{page_signed(row['interval_low'])} to "
        f"{page_signed(row['interval_high'])}</td></tr>"
    )


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


def exploratory_block():
    page = PAGE.read_text(encoding="utf-8")
    return page.split('<section class="exploratory"', 1)[1].split("</section>", 1)[0]


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
    block = exploratory_block()

    assert population["result"] == "matches Q28"
    assert Decimal(within["interval_low"]) > 0
    assert clause == STRATA_ABOVE_ZERO
    assert above_zero(blocks) == column_strata(blocks)
    assert low_months == ["2025-12", "2026-01", "2026-02"]
    assert "<h5>Within the same time of day and month</h5>" in block
    assert (
        "<p>Within the same time of day and month, each extra GW of "
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
        f"{low_names[-1]}.</p>" in block
    )
    assert (
        f'<p class="note">Q28\'s {int(population["half_hours"]):,} half-hours, '
        f"{output_date_range([population])}; read once on {strata_read_day(rows)}. "
        "Month intervals resample whole days from the whole year, so a month's day "
        "count varies between draws. "
        f'<a href="results/{STRATA_RESULT}">Saved output</a></p>' in block
    )
    assert (
        '<p class="note">Exploratory and association only: a further look at a '
        "window already seen, published whatever it showed. "
        f"{NUMBER_WORDS[len(blocks) + len(months)].capitalize()} slopes were read, "
        "none corrected for reading many. Wind output is not held fixed, and both "
        "measures are associated with wind level." in block
    )
    for other in sorted({STRATA_INCLUDES_ZERO, STRATA_BELOW_ZERO}):
        assert other not in block


def column_strata(rows):
    found = []
    for row in rows:
        found.append(row["stratum"])
    return found


def test_q28s_strata_tables_show_each_slope_and_range_from_the_saved_output():
    rows = saved_output_rows(STRATA_RESULT)
    by_block = []
    for row in strata_rows(rows, "2"):
        by_block.append(strata_table_row(row["stratum"].replace("-", "–"), row))
    by_block.append(
        strata_table_row("All, within block and month", strata_row(rows, "4", "1"))
    )
    by_block.append(
        strata_table_row("All, neither held fixed", strata_row(rows, "4", "2"))
    )
    by_month = []
    for row in strata_rows(rows, "3"):
        by_month.append(strata_table_row(strata_month_label(row["stratum"]), row))
    block = exploratory_block()

    assert len(by_block) == 6
    assert len(by_month) == 13
    block_rows = "\n            ".join(by_block)
    month_rows = "\n              ".join(by_month)
    assert '<caption class="note">By time of day (UTC)</caption>' in block
    assert f"<tbody>\n            {block_rows}\n          </tbody>" in block
    assert "<summary>By month</summary>" in block
    assert f"<tbody>\n              {month_rows}\n            </tbody>" in block


def test_q31_and_q32_note_the_strata_cut_from_its_saved_output():
    rows = saved_output_rows(STRATA_RESULT)
    within = strata_row(rows, "4", "1")
    questions = QUESTIONS.read_text(encoding="utf-8")
    sentence = (
        f"A second exploratory cut, read on {strata_read_day(rows)}, held time of "
        f"day and month fixed: a slope of {signed_tenths(within['value'])} g/kWh per "
        f"GW (90% range {signed_tenths(within['interval_low'])} to "
        f"{signed_tenths(within['interval_high'])}) remained. It uses no data in "
        "this question's window"
    )

    for question_id in ("Q31", "Q32"):
        assert sentence in " ".join(register_section(questions, question_id).split())


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
        f"(about 0.6 MW), about "
        f"{percent_of(wind_median, wind_plan['q3_median_plan_mwh'], 1)}% of a wind "
        f"unit's median plan of {tenths(wind_plan['q3_median_plan_mwh'])} MWh a "
        f"half-hour (mean {tenths(wind_plan['q3_mean_plan_mwh'])}).",
        "an allocation against a plan that is zero in "
        f"{link_zero_share}% of half-hours.",
        f"<strong>9.3 g/kWh</strong>, about "
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
        f"Waiting for 2,880 complete half-hours ({2880 // 48} days' worth).",
        "<figcaption>For scale only; no link is tested. Mean national demand",
        f"on the same {int(all_demand['half_hours_with_demand']):,} half-hours, a context "
        "count.",
    ]
    missing = []
    for phrase in expected:
        if phrase not in page:
            missing.append(phrase)

    assert missing == []
    assert 283_242 == int(metered["unit_periods"]) - int(cohort["unit_periods"])


RULES_URL = (
    "https://github.com/JayWatNorm/gridskew/blob/main/docs/research-questions.md"
)
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
    read = one_row(reverse, "section", "1")["item"]
    reverse_read_day = re.match(rf"read ({LONG_DATE}),", read).group(1)
    read_day = latest_day(
        reverse_read_day, strata_read_day(saved_output_rows(STRATA_RESULT))
    )
    earliest = earliest_stated_read(PAGE.read_text(encoding="utf-8"))

    expected = [
        f"the median wind farm missed its plan by {tenths(min(wind))} to "
        f"{tenths(max(wind))}% of its capacity in every whole month from "
        "September 2025 to August 2026; the median unit of every other type, "
        f"by under {other_limit}%.",
        f"<strong>{tenths(min(wind))} to {tenths(max(wind))}%</strong> of its "
        "capacity in every whole month, against a median plan of",
        f"the median unit of every other type, by under {other_limit}%. In "
        "September 2026",
        "Over the same dates, misses in NESO's demand and wind forecasts have "
        f"rank correlations of only {hundredths(demand_rho)} and "
        f"{hundredths(wind_rho)} with the size of the carbon miss.",
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

    expected = [
        f"About a quarter (<strong>{all_share}%</strong>) of under-delivery",
        f"Nuclear is {share_of_notices}% of the under-delivery that came with a "
        f"notice; without it, the share falls from {all_share}% to {share_without}%. "
        "Nuclear's 93% reflects "
        "standing notices of partial derating, not outages the plan missed.</p>",
    ]
    assert missing_phrases(expected) == []
    assert all_total == Decimal(item_row(q5, "1", "total")["positive_shortfall_mwh"])


def test_q6s_on_the_hour_share_comes_from_its_context_count():
    alignment = saved_output_rows("context_q6_start_alignment.csv")
    unplanned = one_row(
        alignment, "item", "class (first publication): Unplanned (as read)"
    )
    lag = saved_output_rows("q6_publication_lag.csv")
    lag_unplanned = one_row(item_rows(lag, "1", ""), "outage_class", "Unplanned")
    on_the_hour = int(unplanned["on_half_hour"])
    notices = int(unplanned["notices"])
    article = article_text(PAGE.read_text(encoding="utf-8"), "q6")

    assert (
        f"{percent_of(on_the_hour, notices, 0)}% ({on_the_hour:,} of {notices:,}) "
        "give a start exactly on the hour or half-hour, so this is not the time "
        "from failure to notice." in article
    )
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
    article = article_text(PAGE.read_text(encoding="utf-8"), "q6")

    assert (
        '<p class="note">For planned notices, the middle half are published '
        f"between about {latest_hours} and {earliest_hours} hours before the "
        "written start.</p>"
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
    article = article_text(PAGE.read_text(encoding="utf-8"), "q5")

    assert (
        f'<p class="note">About {percent_of(drawing_power, under_delivery, 0)}% of '
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
    article = article_text(PAGE.read_text(encoding="utf-8"), "q5")

    assert (
        f"In those days, {gas['units']} gas units read exactly zero on the "
        f"one-week run in {int(gas['zero_readings']):,} unit half-hours that the "
        "three-week run, which arrived after the read, shows as not zero; "
        "Elexon's one-week data, as stored, hold these zeros "
        f'(<a href="results/{name}">zero readings</a>).</p>'
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
    article = article_text(PAGE.read_text(encoding="utf-8"), "q3")

    assert (
        "This answer covers only that week, the last before a change in the "
        "one-week run: gas units read exactly zero on it, and not zero on a later "
        f"run, in {by_week[q22_week]:,} unit half-hours that week and in "
        f"{min(after):,} to {max(after):,} in each week from "
        f"{day_month(change_week)} to {day_month_year(last_day)} "
        f'(<a href="results/{name}">zero readings by week</a>).</p>'
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
    article = article_text(PAGE.read_text(encoding="utf-8"), "q6")

    assert (
        f"About half of the unplanned notices ({before:,} of {notices:,}) were "
        "published before their written start; about one in "
        f"{NUMBER_WORDS[one_in]} ({late:,}) more than an hour after it.</p>"
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
        f"{allocated_share(types, 'Q3')}% of the half-hours in Q3, Q5 and Q12, and "
        f"{allocated_share(types, 'Q23')}% in Q23. Their metered figures are "
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


def test_the_fuel_group_glossary_states_the_cohort_change_from_its_count():
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
        f"Since Q23's read on 15 October 2026, the registry has given {units} more "
        f"units a fuel type ({gained}) and changed none. Counted on "
        f"{read_day(name)}, Q23's half-hours would include {added:,} more at "
        f"{new_units['units']} of those units "
        f"({percent_of(added, at_read_size, 1)}%), and "
        f"{int(metered_later['unit_periods']):,} more at the read's own units, "
        "whose first meter figures arrived after the read "
        f'(<a href="results/{name}">cohort change</a>).</dd>'
    ) in page
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
UNREAD_RULES_WITH_A_CARBON_ACTUAL = ["Q31", "Q32", "Q33", "Q35", "Q36"]


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
        f"and above in {totals['add_back_above']:,}. In the "
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


def test_q36s_split_reason_comes_from_the_wind_miss_counts():
    totals = wind_miss_totals(saved_output_rows(WIND_MISS_COUNTS))
    share = percent_of(
        totals["no_instruction_delivered_below"], totals["no_wind_instruction"], 0
    )
    q36 = flattened(register_section(QUESTIONS.read_text(encoding="utf-8"), "Q36"))

    assert (
        "The reason for the splits: in Q30's year, delivered wind was below the "
        f"wind forecast in {share}% of the {totals['no_wind_instruction']:,} "
        "half-hours with no wind instruction," in q36
    )
    assert (
        "Price split: half-hours whose system price for their settlement period "
        "is below zero, and the rest. Export-limit split: half-hours with an "
        "active cohort wind unit whose MEL level in force at the half-hour's "
        "start, from the latest MEL published before then, was below its final "
        "PN level at the half-hour's start, and the rest. A half-hour with no "
        "system price is unknown for the price split; a half-hour in which any "
        "active cohort wind unit has no MEL in force at its start, or no final "
        "PN level, is unknown for the export-limit split. Unknown half-hours are "
        "counted on their own and are in neither side. A split is reported only "
        "if no half-hour of the primary's population is unknown for it: for the "
        "price split, every half-hour has a price; for the export-limit split, "
        "every active cohort wind unit has an MEL in force and a final PN level "
        "in every half-hour. Otherwise it is reported as not available, with the "
        "count of unknown half-hours and, for the export-limit split, the count "
        "of units with a half-hour lacking an MEL or a PN. GridSkew does not yet "
        "capture system prices or export limits (MEL), so neither split can be "
        "made until they are captured." in q36
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
        f'<a href="results/{WIND_LEVEL_COUNTS}">Wind level counts</a></p>'
        in exploratory_block()
    )


def test_each_read_carbon_result_sharing_q28s_half_hours_notes_the_zero_actual():
    coverage = (
        "<dt>Coverage</dt>\n        <dd>GridSkew's data hold an impossible "
        "0 g/kWh actual for 6 August 2026, 11:00 UTC, which also falls in Q29's "
        "August 2026 cell.</dd>"
    )

    assert (
        articles_without(PAGE.read_text(encoding="utf-8"), ["q8", "q28"], coverage)
        == []
    )


def test_the_unread_carbon_rules_register_an_invalid_value_rule_first():
    questions = QUESTIONS.read_text(encoding="utf-8")
    bullet = (
        "No rule for carbon actuals that cannot be right is chosen yet; "
        "GridSkew's data already hold one, 0 g/kWh for 6 August 2026, 11:00 UTC. "
        "Before any carbon actual in the window is looked at, an exact rule is "
        "registered here, with a reason that "
        "does not depend on this question's results. The half-hours it leaves "
        "out are counted and reported, and the result with none left out is "
        "reported beside the primary."
    )

    assert sections_without(questions, UNREAD_RULES_WITH_A_CARBON_ACTUAL, bullet) == []
    assert (
        "the rule for invalid carbon values added to Q31 to Q36 does not apply to it."
        in flattened(register_section(questions, "Q34"))
    )


def test_q32_registers_a_split_by_wind_level_beside_its_primary():
    q32 = flattened(register_section(QUESTIONS.read_text(encoding="utf-8"), "Q32"))

    assert (
        "Beside the primary, never changing the registered primary verdict, each "
        "group's correlation is repeated within each fifth of metered wind" in q32
    )
    assert "a split by wind level is added beside the primary" in q32


def test_q28_registers_a_seven_day_block_sensitivity_not_yet_run():
    q28 = flattened(register_section(QUESTIONS.read_text(encoding="utf-8"), "Q28"))

    assert (
        "recomputed by resampling blocks of seven consecutive days in place of "
        "single days (1,000 draws). The window is cut into blocks of seven "
        "consecutive days from its first day, without overlap; the window's 360 "
        "days are not a multiple of seven, so the last block holds the remaining "
        "3 days. Each draw takes blocks, each equally likely, with replacement "
        "until 360 days are filled; the last block drawn keeps only its first "
        "days needed to reach 360. "
        "It is reported beside the registered results, which stand whatever it "
        "shows," in q28
    )
    assert "Not yet run:" in q28


ALIGNMENT_COUNTS = "context_carbon_label_alignment.csv"
WIND_CLOCK = "1"
ACTUAL_WHOLE = "2"
ACTUAL_BY_SEASON = "3"
ACTUAL_BY_MONTH = "4"
FORECAST_STEPS = "5"
SIGN_TEST = "6"
FORECAST_AGAINST_MIX = "7"
SAME_LABEL = "0"
HALF_HOUR_AFTER = "1"
HALF_HOUR_BEFORE = "-1"
AN_HOUR_BEFORE = "-2"
UNREAD_CARBON_RULES = ["Q4", "Q31", "Q32", "Q33", "Q35", "Q36"]
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
        'ngeso-open-licence">NESO Open Data Licence v1.0</a></p>' in page
    )


def test_each_read_carbon_result_points_to_the_alignment_limitation():
    whole = by_offset(alignment_rows(), ACTUAL_WHOLE, "all")
    pointer = (
        "<dd>Added 7 November 2026: the carbon API's actual agrees most closely "
        "with NESO's Elexon-aligned generation mix one half-hour after its label "
        f"(within 5 g/kWh in {share_of_common(whole[HALF_HOUR_AFTER], 'within_5')}% "
        "of half-hours, against "
        f"{share_of_common(whole[SAME_LABEL], 'within_5')}% at the label); this "
        "result pairs values by label, as published. "
        '<a href="#carbon-label-alignment">Limitations</a></dd>'
    )
    article_ids = []
    for question_id in READ_CARBON_RESULTS:
        article_ids.append(question_id.lower())
    page = PAGE.read_text(encoding="utf-8")

    assert articles_without(page, article_ids, pointer) == []
    assert '<li id="carbon-label-alignment">' in page


def test_the_unread_carbon_rules_state_the_alignment_basis_from_its_counts():
    rows = alignment_rows()
    wind_share = share_of_common(wind_clock_row(rows), "exactly_equal")
    first, last = alignment_window()
    whole_after, whole_same = step_pair(
        rows, ACTUAL_WHOLE, "all", HALF_HOUR_AFTER, SAME_LABEL
    )
    months = len(groups_in(rows, ACTUAL_BY_MONTH))
    missingness = (
        "the half-hours the primary has and a sensitivity lacks are counted, at "
        "the window's edges and within it."
    )
    basis = (
        "The basis is a measured alignment, "
        "not a documented convention: against NESO's historic generation mix, "
        f"whose wind equals Elexon's at the same half-hour in {wind_share}% of "
        "half-hours, the API's actual agrees best 30 minutes after its label in "
        f"each of the {months} months from {first} to {last} (share within "
        "5 g/kWh and mean absolute difference), and the correlation of half-hour "
        f"changes is {whole_after} there, against {whole_same} at its own label; "
        "a test of whether intensity moves against large changes in Elexon's "
        "wind peaks one half-hour after the label for the API's actual and at "
        "the label for NESO's series. The API's documentation gives each value's "
        "start and end in UTC but does not say what interval its actual is "
        "measured over."
    )
    questions = QUESTIONS.read_text(encoding="utf-8")

    assert sections_without(questions, UNREAD_CARBON_RULES, missingness) == []
    assert sections_without(
        questions,
        UNREAD_CARBON_RULES,
        "registered primary verdict; a sensitivity whose conclusion differs "
        "is reported as such",
    ) == ["Q33"]
    assert (
        sections_without(
            questions,
            ["Q33"],
            "never changing the registered primary verdict (a sensitivity whose "
            "conclusion differs is reported as such)",
        )
        == []
    )
    assert sections_without(questions, UNREAD_CARBON_RULES, basis) == []


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


def test_q33s_disclosure_of_the_forecasts_timing_comes_from_the_alignment_counts():
    rows = alignment_rows()
    actual_before, actual_same = step_pair(
        rows, FORECAST_STEPS, "all", AN_HOUR_BEFORE, SAME_LABEL
    )
    mix_before, mix_same = step_pair(
        rows, FORECAST_AGAINST_MIX, "all", HALF_HOUR_BEFORE, SAME_LABEL
    )
    disclosure = (
        "over that year the stored forecast's half-hour changes follow the "
        "actual's changes of an hour earlier most closely (correlation "
        f"{actual_before}, against {actual_same} at the same label), and against "
        f"NESO's generation mix, with the half-hour before ({mix_before}, against "
        f"{mix_same})."
    )

    assert (
        offset_with_highest(by_offset(rows, FORECAST_STEPS, "all"), "step_correlation")
        == AN_HOUR_BEFORE
    )
    assert (
        offset_with_highest(
            by_offset(rows, FORECAST_AGAINST_MIX, "all"), "step_correlation"
        )
        == HALF_HOUR_BEFORE
    )
    assert (
        sections_without(QUESTIONS.read_text(encoding="utf-8"), ["Q33"], disclosure)
        == []
    )


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
        "as a share of the earlier run's total "
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
    assert missing_phrases(expected) == []
    assert whole(earliest_mean / overall_mean) == "2"
    assert not_drawn_with_notice_mwh == 0
    assert prefix_moved_mwh == Decimal(three_weeks["moved_mwh"])
    assert Decimal(three_weeks["moved_share"]) == (
        Decimal(three_weeks["moved_mwh"]) / Decimal(three_weeks["base_abs_mwh"])
    ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def test_each_answer_states_the_dates_its_saved_output_or_read_covers():
    page = PAGE.read_text(encoding="utf-8")
    q8_q28 = saved_output_rows("q8_q28_history.csv")
    q30 = saved_output_rows("q30_wind_vs_carbon_error.csv")
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

    expected = [
        f"No clear difference ({output_date_range(q8_q28)}): ranking half-hours",
        f"The same 17,278 half-hours as Q28, {q8_first} to {q8_last}.",
        f"Barely: over {output_date_range(q30)}, the sizes of the two misses",
        f"Far more often ({short_date(q25_first)} – {short_date(q25_last)}).",
        "The median unplanned notice first published from "
        f"{short_date(q6_first)} to {short_date(q6_read)} appears",
    ]
    assert missing_phrases(expected) == []
    assert q25_months == (
        f"{short_date(q25_first).split(' ', 1)[1]} – "
        f"{short_date(q25_last).split(' ', 1)[1]}"
    )
    assert f"stored publication on or after {q6_first} and event start" in " ".join(
        questions.split()
    )


def test_the_limitations_name_the_results_that_set_one_measure_against_another():
    # Q5 sets under-delivery against outage notices, Q8 and Q28 against the
    # carbon miss, Q30 the wind miss against it; each is an article on the
    # page, beside the exploratory block.
    page = PAGE.read_text(encoding="utf-8")
    named = ["Q5", "Q8", "Q28", "Q30"]
    limitation = (
        f"<li>Results that set one measure against another ({', '.join(named)} "
        "and the exploratory cuts) are associations, not causes. Each result "
        "covers the period given beside it and may not hold in another.</li>"
    )

    assert limitation in page
    for question_id in named:
        assert f'<article id="{question_id.lower()}">' in page
    assert '<section class="exploratory"' in page


def register_section(questions, question_id):
    """The register's section for one question, heading to next heading."""
    heading = f"\n### {question_id}. "
    assert heading in questions
    return questions.split(heading, 1)[1].split("\n### ", 1)[0]


def test_still_to_come_lists_the_registered_questions_with_the_registers_dates():
    page = PAGE.read_text(encoding="utf-8")
    questions = QUESTIONS.read_text(encoding="utf-8")
    item = page.split("<li><strong>Also registered, not yet read:</strong>", 1)[1]
    item = item.split("</li>", 1)[0]
    dated = re.findall(r"(Q[0-9A-Z-]+) \(about ([A-Z][a-z]+ \d{4})\)", item)
    first, last = re.search(r"and Q(\d+) to Q(\d+)\.", item).groups()

    assert dated == [
        ("Q22-RF", "November 2027"),
        ("Q2-R2RF", "May 2027"),
        ("Q2-R1RF", "August 2027"),
    ]
    for question_id, month in dated:
        assert f"(about {month})" in " ".join(
            register_section(questions, question_id).split()
        )
    for number in range(int(first), int(last) + 1):
        register_section(questions, f"Q{number}")
    assert f'are in <a href="{RULES_URL}">every rule</a>.' in item


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
        f"{ages[0]}, {ages[1]}, {ages[2]} and {ages[3]} after the day.",
        f"the {r2_months}-month run (R2) to 29 April",
    ]
    assert missing_phrases(expected) == []


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


def caption_of_chart(page, chart_id):
    """The figcaption under the chart with this id."""
    after_chart = page.split(f'<div class="chart" id="{chart_id}"></div>', 1)[1]
    return after_chart.split("</figcaption>", 1)[0]


def test_each_scale_figure_links_the_context_count_it_divides_by():
    page = PAGE.read_text(encoding="utf-8")

    assert '<a href="results/context_plan_per_unit_half_hour.csv">' in (
        caption_of_chart(page, "q3-chart")
    )
    assert '<a href="results/context_demand.csv">' in caption_of_chart(
        page, "q28-chart"
    )
    assert '<a href="results/context_carbon_intensity.csv">' in caption_of_chart(
        page, "q28-reverse-chart"
    )


def test_each_context_row_is_labelled_a_denominator_not_a_result():
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
    assert "for scale only; no link is tested." in reading_line(
        written, "q29_context_demand_by_month.vl.json"
    )


def test_a_q23_month_with_no_planned_output_context_is_refused(tmp_path):
    saved = saved_outputs_folder(tmp_path / "saved")
    without_lines_containing(saved / "context_plan_pct_capacity.csv", "wind,2026-03-01")

    with pytest.raises(ValueError, match="no planned output"):
        write_charts(saved, tmp_path / "out")


def test_the_page_and_rules_speak_as_i_or_neutrally():
    voice = re.compile(r"\b(we|our|us)\b", re.IGNORECASE)
    page = PAGE.read_text(encoding="utf-8")

    assert voice.findall(page) == []
    assert voice.findall(QUESTIONS.read_text(encoding="utf-8")) == []
    assert page.count("<summary>How I measured it</summary>") == 12
    assert '<h2 id="found-title">What I found</h2>' in page


def test_q23s_answer_says_how_far_units_strayed_not_why():
    # With no instruction in force the gap can still hold a forecast miss,
    # energy bought outside the Balancing Mechanism, a declared cut or a
    # settlement estimate, so the page does not call it the unit's own.
    page = PAGE.read_text(encoding="utf-8")
    answer = page.split('<article id="q23">', 1)[1].split('<p class="answer">', 1)[1]
    answer = answer.split("</p>", 1)[0]

    assert (
        "Only half-hours with no NESO instruction count. The gap shows how far "
        "a unit strayed from its plan, not why." in answer
    )
    assert "unit's own" not in answer


def test_the_page_states_each_energy_figure_beside_its_saved_output_range():
    q3 = output_date_range(saved_output_rows("q3_metered_vs_notified.csv"))
    q5 = output_date_range(saved_output_rows("q5_shortfall_with_known_outage.csv"))
    q24 = output_month_range(saved_output_rows("q24_system_action_share.csv"))
    q28 = output_date_range(saved_output_rows("q8_q28_history.csv"))
    page = PAGE.read_text(encoding="utf-8")
    expected = [
        f"About a quarter (24%, {q5}) came with one",
        f"possibly for network reasons:</strong> 97% (settlement months {q24}).",
        f"the biggest carbon misses ({q28}), under-delivery",
        f"Slightly less, with wide spreads ({q3}).",
        f"0.03 MWh per unit per half-hour (bioenergy, {Q2_WEEK}):",
        f"of under-delivery ({q5}) came with a published outage",
        f"of 13.4 TWh, settlement months {q24};",
        f"<figcaption>Settlement months {q24}; the first is a part month",
        f"Most of it for gas, hydro and pumped storage ({q3}): <strong>83 to 91%",
        "NESO never instructed a nuclear unit in those dates.",
        f"No. Over {q28}, the fifth of half-hours with the biggest misses",
        f"Mean under-delivery per half-hour, {q28}: 762 MWh",
        f"Materially: for settlement dates {Q2_WEEK}, 3.4%",
    ]
    missing = []
    for phrase in expected:
        if phrase not in page:
            missing.append(phrase)

    assert missing == []


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
        "results/q29_context_demand_by_month.vl.json",
        "results/q28_shortfall_index_by_miss_fifth.vl.json",
        "results/q28_reverse_carbon_miss_by_under_delivery_fifth.vl.json",
        "results/q30_rank_correlations.vl.json",
        "results/q2_moved_energy_by_unit_type.vl.json",
    ]
    assert not_shipped([*local_targets(page_tags()), *specs]) == []


def test_the_committed_specs_and_chart_csvs_are_what_the_script_writes(tmp_path):
    # The committed folder is the input as it stands, attribution lines and
    # all. PNGs are left out: their pixels depend on the platform's fonts.
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


def test_the_pages_workflow_asks_for_exactly_its_three_permissions():
    workflow = yaml.load(PAGES_WORKFLOW.read_text(encoding="utf-8"), yaml.BaseLoader)

    assert workflow["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert jobs_with_their_own_permissions(workflow) == []
