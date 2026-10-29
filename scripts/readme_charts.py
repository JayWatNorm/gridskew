"""Draw the results charts from the saved one-look outputs.

Reads each read's saved output from a folder, under the file names below,
and writes into the output folder:

- each saved output as CSV, its attribution as the first line and its rows
  unchanged (LF line endings);
- for each chart, a PNG and a Vega-Lite spec that reads its CSV by URL
  (``results/<name>.csv``), never inlined.

Q3 and Q12 come from the delivery result, whose chart CSVs hold the values
each chart shows. Nothing is queried and nothing is recomputed. A rerun on
the same input writes identical files.

    python scripts/readme_charts.py <folder of saved outputs> <output folder>
"""

import csv
import io
import json
import math
import sys
import textwrap
from pathlib import Path

import altair
import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

BMRS_ATTRIBUTION = (
    "Contains BMRS data © Elexon Limited copyright and database right 2026."
)
CARBON_ATTRIBUTION = (
    "Carbon intensity data: NESO Carbon Intensity API, CC BY 4.0, "
    "modified: aggregated by GridSkew."
)
BMRS_AND_CARBON_ATTRIBUTION = f"{BMRS_ATTRIBUTION} {CARBON_ATTRIBUTION}"

DELIVERY_RESULT = "delivery_by_fuel_group.csv"
Q2_RESULT = "q2_settlement_run_changes.csv"
Q22_RESULT = "q22_first_against_latest_run.csv"
Q5_RESULT = "q5_shortfall_with_known_outage.csv"
Q6_RESULT = "q6_publication_lag.csv"
Q23_RESULT = "q23_pn_accuracy_proxy.csv"
Q24_RESULT = "q24_system_action_share.csv"
Q25_RESULT = "q25_instruction_fragmentation.csv"
Q8_Q28_RESULT = "q8_q28_history.csv"
Q29_RESULT = "q29_final_forecast_error_by_month_and_block.csv"
Q30_RESULT = "q30_wind_vs_carbon_error.csv"
SPEC_DATA_FOLDER = "results"

# What each read's saved output does not hold, as the public research
# questions record it.
Q5_CUTOFF = "cutoff 2026-09-27 22:30 UTC"
Q6_POPULATION = "first publications from 2024-11-01 to the read on 2026-10-12"
Q23_TO_Q25_CUTOFF = "cutoff 2026-09-30 22:30 UTC"
Q8_Q28_READ = "read 2026-10-26 11:21 UTC"
Q29_WINDOW = "settlement dates 2025-08-22 to 2026-08-16"
Q29_READ = "read 2026-10-27 06:15 UTC"
Q30_READ = "read 2026-10-27, database cutoff 14:03 UTC"

Q3_COLUMNS = [
    "fuel_group",
    "unit_periods",
    "first_date",
    "last_date",
    "p25_deviation_mwh",
    "median_deviation_mwh",
    "p75_deviation_mwh",
    "analysis_read_until",
]
Q12_COLUMNS = [
    "fuel_group",
    "unit_periods",
    "first_date",
    "last_date",
    "abs_instructed_deviation_mwh",
    "abs_unexplained_shortfall_mwh",
    "instructed_share_of_abs_deviation",
    "analysis_read_until",
]
OUTAGE_CLASSES = ["planned", "unplanned", "both", "none"]
ALL_FUEL_GROUPS = "all fuel groups"
OUTAGE_CLASSES_DRAWN = ["Planned", "Unplanned"]
Q25_CHAIN_MEASURE = "2 continuous acceptance duration, BSC (acceptances)"
Q25_SPAN_MEASURE = "1 dispatch granularity (acceptances)"
Q28_POPULATION_ITEM = (
    "population: matched days (all three values in at least 46 half-hours), "
    "every half-hour on them with all three"
)
Q28_QUINTILES = {
    "item": "quintile of abs(error_api_final): mean positive shortfall, MWh",
    "range_prefix": "abs(error_api_final) ",
    "range_title": "Quintile of the final forecast's absolute error, gCO2/kWh",
    "value_title": "Mean positive unexplained shortfall per half-hour, MWh",
}
Q30_POPULATION_ITEM = (
    "population: window half-hours with error_api_final, a forecast, "
    "a first outturn and a known add-back"
)
Q30_QUINTILES = {
    "item": (
        "quintile of abs(wind error, outturn with cohort curtailment add-back): "
        "mean abs(error_api_final)"
    ),
    "range_prefix": "abs(wind error) ",
    "range_title": "Quintile of the absolute wind forecast miss",
    "value_title": "Mean absolute error of the final carbon forecast, gCO2/kWh",
}
Q30_RHO_ITEM = (
    "rho_abs (primary): abs(wind error, outturn with cohort curtailment add-back) "
    "with abs(error_api_final)"
)

FIGURE_WIDTH_INCHES = 8
INCHES_PER_GROUP = 0.45
FIGURE_MARGIN_INCHES = 1.6
DPI = 100
PAD_INCHES = 0.2
TITLE_LINE_CHARACTERS = 70
SPEC_TITLE_CHARACTERS = 45
SPEC_SUBTITLE_CHARACTERS = 55
MINIMUM_FIGURE_HEIGHT_INCHES = 3.5
INCHES_PER_BAR = 0.25
LEGEND_ROW_INCHES = 0.22
BAR_COLOUR = "#4c72b0"
MARKER_COLOUR = "#1b2a4a"
ZERO_LINE_COLOUR = "#999999"
SERIES_COLOURS = ["#4c72b0", "#dd8452"]
LINE_STYLES = ["-", "--"]
OUTAGE_CLASS_COLOURS = ["#4c72b0", "#c44e52", "#8172b3", "#c8c8c8"]
# Vega's default category colours, so the PNG and the page agree.
FUEL_GROUP_COLOURS = [
    "#4c78a8",
    "#f58518",
    "#e45756",
    "#72b7b2",
    "#54a24b",
    "#eeca3b",
    "#b279a2",
    "#ff9da6",
    "#9d755d",
    "#bab0ac",
]


def read_delivery(path):
    with Path(path).open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    cutoffs = set()
    for row in rows:
        cutoffs.add(row["analysis_read_until"])
    if len(cutoffs) != 1:
        raise ValueError(f"The result must come from one cutoff, not {sorted(cutoffs)}")
    return rows


def copy_saved_output(results, output, name, attribution):
    """Write the saved output with its attribution as the first line; return
    its rows. Text mode reads Windows line endings as LF."""
    with (results / name).open(encoding="utf-8") as saved:
        rows_text = saved.read()
    (output / name).write_bytes(f"# {attribution}\n{rows_text}".encode("utf-8"))
    return list(csv.DictReader(io.StringIO(rows_text)))


def descending_by(column):
    def sort_key(row):
        return (-float(row[column]), row["fuel_group"])

    return sort_key


def write_values(path, rows, columns):
    with path.open("w", newline="", encoding="utf-8") as target:
        target.write(f"# {BMRS_ATTRIBUTION}\n")
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            values = []
            for column in columns:
                values.append(row[column])
            writer.writerow(values)


def rows_in_section(rows, section):
    selected = []
    for row in rows:
        if row["section"] == section:
            selected.append(row)
    return selected


def row_for(rows, section, item):
    for row in rows_in_section(rows, section):
        if row["item"] == item:
            return row
    raise ValueError(f"The saved output has no row for section {section}, {item!r}")


def first_and_last(rows, column):
    values = []
    for row in rows:
        values.append(row[column])
    return min(values), max(values)


def describe_read(rows):
    first_dates = []
    last_dates = []
    for row in rows:
        first_dates.append(row["first_date"])
        last_dates.append(row["last_date"])
    cutoff = rows[0]["analysis_read_until"] or "all stored history"
    return f"{min(first_dates)} to {max(last_dates)}; cutoff {cutoff}"


def group_labels(rows):
    labels = []
    for row in rows:
        labels.append(f"{row['fuel_group']} (n={row['unit_periods']})")
    return labels


def new_figure(row_count, inches_per_row=INCHES_PER_GROUP):
    height = max(
        MINIMUM_FIGURE_HEIGHT_INCHES, FIGURE_MARGIN_INCHES + inches_per_row * row_count
    )
    return pyplot.subplots(figsize=(FIGURE_WIDTH_INCHES, height))


def png_title(axes, title, subtitle_lines):
    lines = []
    for line in [title, *subtitle_lines]:
        lines.extend(textwrap.wrap(line, TITLE_LINE_CHARACTERS))
    axes.set_title("\n".join(lines), fontsize=10)


def save_figure(figure, path):
    figure.tight_layout()
    figure.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=PAD_INCHES)
    pyplot.close(figure)


def save_figure_with_legend(figure, handles, columns, path):
    """The legend goes below the plot, in space kept free for it; the image
    is cropped to what is drawn, so a long title is never cut."""
    figure.legend(
        handles=handles, loc="lower center", ncol=columns, fontsize=8, frameon=False
    )
    legend_rows = math.ceil(len(handles) / columns)
    legend_inches = LEGEND_ROW_INCHES * (legend_rows + 1)
    figure.tight_layout(rect=(0, legend_inches / figure.get_figheight(), 1, 1))
    figure.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=PAD_INCHES)
    pyplot.close(figure)


def legend_handles(axes):
    handles, _labels = axes.get_legend_handles_labels()
    return handles


def draw_grouped_bars(path, categories, series, x_label, title, subtitle_lines):
    """Horizontal bars, one group per category, one bar per series; each
    series is a label and its values as text, in category order."""
    figure, axes = new_figure(len(categories) * len(series), INCHES_PER_BAR)
    bar_height = 0.8 / len(series)
    for series_index, (label, values) in enumerate(series):
        positions = []
        widths = []
        for category_index, value in enumerate(values):
            offset = (series_index + 0.5) * bar_height - 0.4
            positions.append(category_index + offset)
            widths.append(float(value))
        bars = axes.barh(
            positions,
            widths,
            height=bar_height,
            color=SERIES_COLOURS[series_index],
            label=label,
        )
        axes.bar_label(bars, labels=values, padding=3, fontsize=8)
    axes.axvline(0, color=ZERO_LINE_COLOUR, linewidth=0.8)
    axes.set_yticks(range(len(categories)), categories)
    axes.invert_yaxis()
    axes.margins(x=0.15)
    axes.set_xlabel(x_label)
    png_title(axes, title, subtitle_lines)
    save_figure_with_legend(figure, legend_handles(axes), len(series), path)


def draw_bars(path, categories, values, x_label, y_label, title, subtitle_lines):
    figure, axes = new_figure(len(categories))
    positions = list(range(len(categories)))
    widths = []
    for value in values:
        widths.append(float(value))
    bars = axes.barh(positions, widths, color=BAR_COLOUR)
    axes.bar_label(bars, labels=values, padding=3, fontsize=8)
    axes.set_yticks(positions, categories)
    axes.invert_yaxis()
    axes.margins(x=0.15)
    axes.set_xlabel(x_label)
    axes.set_ylabel(y_label)
    png_title(axes, title, subtitle_lines)
    save_figure(figure, path)


def csv_data(csv_name, number_fields):
    """The chart's CSV by URL, with the fields it reads as numbers parsed."""
    parse = {}
    for field in number_fields:
        parse[field] = "number"
    return altair.UrlData(
        url=f"{SPEC_DATA_FOLDER}/{csv_name}",
        format=altair.CsvDataFormat(type="csv", parse=parse),
    )


def wrapped(lines, width):
    wrapped_lines = []
    for line in lines:
        wrapped_lines.extend(textwrap.wrap(line, width))
    return wrapped_lines


def page_layout(title, subtitle_lines):
    """As wide as the page allows, with the title wrapped to fit a phone."""
    return {
        "width": "container",
        "title": altair.TitleParams(
            text=wrapped([title], SPEC_TITLE_CHARACTERS),
            subtitle=wrapped(subtitle_lines, SPEC_SUBTITLE_CHARACTERS),
            anchor="start",
        ),
    }


def label_expression(labels):
    """A Vega expression that shows each value under its label."""
    expression = "datum.label"
    for value, label in reversed(list(labels.items())):
        expression = (
            f"datum.label == {json.dumps(value)} ? {json.dumps(label)} : {expression}"
        )
    return expression


def prefix_removed(prefix):
    return f"replace(datum.label, {json.dumps(prefix)}, '')"


def series_colour(series_field, series_labels, colours):
    return altair.Color(
        f"{series_field}:N",
        sort=list(series_labels),
        scale=altair.Scale(domain=list(series_labels), range=colours),
        legend=altair.Legend(
            title=None, orient="bottom", labelExpr=label_expression(series_labels)
        ),
    )


def grouped_bars(
    chart, category, series_field, series_labels, value_field, value_title
):
    """Horizontal bars per category, one bar per series value beside the
    next, in the order of series_labels."""
    return chart.mark_bar().encode(
        x=altair.X(f"{value_field}:Q", title=value_title),
        y=category,
        yOffset=altair.YOffset(f"{series_field}:N", sort=list(series_labels)),
        color=series_colour(series_field, series_labels, SERIES_COLOURS),
    )


def write_spec(chart, path):
    text = json.dumps(chart.to_dict(), sort_keys=True, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


def draw_deviation(rows, path):
    figure, axes = new_figure(len(rows))
    positions = list(range(len(rows)))
    for position, row in zip(positions, rows, strict=True):
        lower = float(row["p25_deviation_mwh"])
        upper = float(row["p75_deviation_mwh"])
        axes.plot([lower, upper], [position, position], color=BAR_COLOUR, linewidth=4)
        axes.plot(
            float(row["median_deviation_mwh"]), position, "o", color=MARKER_COLOUR
        )
    axes.axvline(0, color=ZERO_LINE_COLOUR, linewidth=0.8)
    axes.set_yticks(positions, group_labels(rows))
    axes.invert_yaxis()
    axes.set_xlabel("Metered minus notified (PN) energy per unit-period, MWh")
    axes.set_title(
        "Median and interquartile range by fuel group\n" + describe_read(rows),
        fontsize=10,
    )
    save_figure(figure, path)


def deviation_spec(rows, csv_name):
    data = csv_data(
        csv_name, ["p25_deviation_mwh", "median_deviation_mwh", "p75_deviation_mwh"]
    )
    fuel_group = altair.Y("fuel_group:N", sort=None, title=None)
    tooltip = [
        "fuel_group:N",
        "unit_periods:N",
        "p25_deviation_mwh:Q",
        "median_deviation_mwh:Q",
        "p75_deviation_mwh:Q",
    ]
    interquartile_range = (
        altair.Chart()
        .mark_rule(color=BAR_COLOUR, strokeWidth=4)
        .encode(
            x=altair.X(
                "p25_deviation_mwh:Q",
                title="Metered minus notified (PN) energy per unit-period, MWh",
            ),
            x2="p75_deviation_mwh:Q",
            y=fuel_group,
            tooltip=tooltip,
        )
    )
    median = (
        altair.Chart()
        .mark_point(filled=True, color=MARKER_COLOUR)
        .encode(x="median_deviation_mwh:Q", y=fuel_group, tooltip=tooltip)
    )
    return altair.layer(interquartile_range, median, data=data).properties(
        **page_layout(
            "Median and interquartile range by fuel group", [describe_read(rows)]
        )
    )


def draw_instructed_share(rows, path):
    figure, axes = new_figure(len(rows))
    positions = list(range(len(rows)))
    for position, row in zip(positions, rows, strict=True):
        share = row["instructed_share_of_abs_deviation"]
        # The mart leaves the share empty when the group has no deviation.
        if not share:
            axes.text(
                1, position, "undefined: no deviation", va="center", color="#555555"
            )
            continue
        axes.barh(position, 100 * float(share), color=BAR_COLOUR)
    axes.set_yticks(positions, group_labels(rows))
    # Top to bottom, with room for a group that has no bar.
    axes.set_ylim(len(rows) - 0.5, -0.5)
    axes.set_xlim(0, 100)
    axes.set_xlabel("Share of the deviation from PN that was instructed, %")
    axes.set_title(
        "Instructed share of the deviation by fuel group\n" + describe_read(rows),
        fontsize=10,
    )
    save_figure(figure, path)


def instructed_share_spec(rows, csv_name):
    data = csv_data(csv_name, ["instructed_share_of_abs_deviation"])
    return (
        altair.Chart(data)
        .mark_bar(color=BAR_COLOUR)
        .encode(
            x=altair.X(
                "instructed_share_of_abs_deviation:Q",
                title="Share of the deviation from PN that was instructed",
                axis=altair.Axis(format="%"),
                scale=altair.Scale(domain=[0, 1]),
            ),
            y=altair.Y("fuel_group:N", sort=None, title=None),
            tooltip=[
                "fuel_group:N",
                "unit_periods:N",
                "abs_instructed_deviation_mwh:N",
                "abs_unexplained_shortfall_mwh:N",
                "instructed_share_of_abs_deviation:Q",
            ],
        )
        .properties(
            **page_layout(
                "Instructed share of the deviation by fuel group", [describe_read(rows)]
            )
        )
    )


def write_chart_values(rows, columns, output, name):
    csv_name = f"{name}.csv"
    write_values(output / csv_name, rows, columns)
    return csv_name


def write_delivery_charts(rows, output):
    by_metered_energy = sorted(rows, key=descending_by("abs_metered_mwh"))
    name = "q3_deviation_by_fuel_group"
    csv_name = write_chart_values(by_metered_energy, Q3_COLUMNS, output, name)
    draw_deviation(by_metered_energy, output / f"{name}.png")
    write_spec(deviation_spec(by_metered_energy, csv_name), output / f"{name}.vl.json")

    by_deviation = sorted(rows, key=descending_by("abs_deviation_mwh"))
    name = "q12_instructed_share_by_fuel_group"
    csv_name = write_chart_values(by_deviation, Q12_COLUMNS, output, name)
    draw_instructed_share(by_deviation, output / f"{name}.png")
    write_spec(
        instructed_share_spec(by_deviation, csv_name), output / f"{name}.vl.json"
    )


def shares_by_group(rows):
    """Per group (all fuel groups first), the share of each outage class."""
    groups = {}
    for row in rows:
        if row["item"] not in OUTAGE_CLASSES:
            continue
        group = row["fuel_group"] or ALL_FUEL_GROUPS
        if group not in groups:
            groups[group] = {}
        groups[group][row["item"]] = row["share_of_positive_shortfall_pct"]
    return groups


def draw_stacked_shares(path, groups, title, subtitle_lines):
    names = list(groups)
    figure, axes = new_figure(len(names))
    positions = list(range(len(names)))
    lefts = [0.0] * len(names)
    for outage_class, colour in zip(OUTAGE_CLASSES, OUTAGE_CLASS_COLOURS, strict=True):
        widths = []
        for name in names:
            widths.append(float(groups[name].get(outage_class, "0")))
        axes.barh(positions, widths, left=lefts, color=colour, label=outage_class)
        next_lefts = []
        for left, width in zip(lefts, widths, strict=True):
            next_lefts.append(left + width)
        lefts = next_lefts
    axes.set_yticks(positions, names)
    axes.invert_yaxis()
    axes.set_xlabel("Share of positive unexplained shortfall, %")
    png_title(axes, title, subtitle_lines)
    save_figure_with_legend(figure, legend_handles(axes), len(OUTAGE_CLASSES), path)


def write_q5_chart(rows, output):
    total = row_for(rows, "1", "total")
    title = "Q5. Shortfall in half-hours with an outage already published in REMIT"
    subtitle = [
        "Share of positive unexplained shortfall by outage class known at the "
        "half-hour's start",
        f"n={total['unit_periods']} determinable cohort unit-periods, "
        f"{total['first_date']} to {total['last_date']}; {Q5_CUTOFF}",
    ]
    name = "q5_shortfall_by_outage_class"
    draw_stacked_shares(output / f"{name}.png", shares_by_group(rows), title, subtitle)

    data = csv_data(Q5_RESULT, ["share_of_positive_shortfall_pct"])
    class_labels = {}
    for outage_class in OUTAGE_CLASSES:
        class_labels[outage_class] = outage_class
    chart = (
        altair.Chart(data)
        .transform_filter(
            altair.FieldOneOfPredicate(field="item", oneOf=OUTAGE_CLASSES)
        )
        .mark_bar()
        .encode(
            x=altair.X(
                "share_of_positive_shortfall_pct:Q",
                title="Share of positive unexplained shortfall, %",
            ),
            y=altair.Y(
                "fuel_group:N",
                sort=None,
                title=None,
                axis=altair.Axis(labelExpr=label_expression({"": ALL_FUEL_GROUPS})),
            ),
            color=series_colour("item", class_labels, OUTAGE_CLASS_COLOURS),
            tooltip=[
                "fuel_group:N",
                "item:N",
                "unit_periods:N",
                "positive_shortfall_mwh:N",
                "share_of_positive_shortfall_pct:Q",
            ],
        )
        .properties(**page_layout(title, subtitle))
    )
    write_spec(chart, output / f"{name}.vl.json")


def write_q6_chart(rows, output):
    drawn = []
    unclassified = None
    for row in rows_in_section(rows, "1"):
        if row["outage_class"] in OUTAGE_CLASSES_DRAWN:
            drawn.append(row)
        if not row["outage_class"]:
            unclassified = row
    population = row_for(rows, "3", "5 in the population")
    title = "Q6. Lag from event start to first REMIT publication, by outage class"
    subtitle = [
        "Minutes; negative means published before the event starts",
        f"n={population['notices']} notices, {Q6_POPULATION}; "
        f"{unclassified['notices']} notices with no class are not drawn",
    ]
    categories = []
    medians = []
    ninetieths = []
    for row in drawn:
        categories.append(f"{row['outage_class']} (n={row['notices']})")
        medians.append(row["median_lag_minutes"])
        ninetieths.append(row["p90_lag_minutes"])
    name = "q6_publication_lag_by_class"
    draw_grouped_bars(
        output / f"{name}.png",
        categories,
        [("median", medians), ("90th percentile", ninetieths)],
        "Lag from event start to first publication, minutes",
        title,
        subtitle,
    )

    measures = {"median_lag_minutes": "median", "p90_lag_minutes": "90th percentile"}
    data = csv_data(Q6_RESULT, ["section", *measures])
    chart = (
        altair.Chart(data)
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=1))
        .transform_filter(
            altair.FieldOneOfPredicate(field="outage_class", oneOf=OUTAGE_CLASSES_DRAWN)
        )
        .transform_fold(list(measures), as_=["measure", "value"])
    )
    chart = (
        grouped_bars(
            chart,
            altair.Y("outage_class:N", sort=None, title=None),
            "measure",
            measures,
            "value",
            "Lag from event start to first publication, minutes",
        )
        .encode(tooltip=["outage_class:N", "notices:N", "measure:N", "value:Q"])
        .properties(**page_layout(title, subtitle))
    )
    write_spec(chart, output / f"{name}.vl.json")


def draw_lines_by_group(path, rows, measures, y_label, title, subtitle_lines):
    """One line per fuel group and measure: solid for the first measure,
    dashed for the second."""
    months = sorted(monthly(rows))
    groups = sorted(fuel_groups(rows))
    figure, axes = pyplot.subplots(figsize=(FIGURE_WIDTH_INCHES, 7))
    handles = []
    for group, colour in zip(groups, FUEL_GROUP_COLOURS, strict=False):
        handles.append(Line2D([], [], color=colour, label=group))
        for measure, line_style in zip(measures, LINE_STYLES, strict=True):
            positions = []
            values = []
            for row in rows:
                if row["fuel_group"] != group:
                    continue
                positions.append(months.index(row["settlement_month"]))
                values.append(float(row[measure]))
            axes.plot(positions, values, line_style, color=colour)
    for label, line_style in zip(measures.values(), LINE_STYLES, strict=True):
        handles.append(
            Line2D([], [], color=MARKER_COLOUR, linestyle=line_style, label=label)
        )
    month_labels = []
    for month in months:
        month_labels.append(month[:7])
    axes.set_xticks(range(len(months)), month_labels, rotation=90)
    axes.set_ylabel(y_label)
    png_title(axes, title, subtitle_lines)
    save_figure_with_legend(figure, handles, 5, path)


def monthly(rows):
    months = set()
    for row in rows:
        months.add(row["settlement_month"])
    return months


def fuel_groups(rows):
    groups = set()
    for row in rows:
        groups.add(row["fuel_group"])
    return groups


def write_q23_chart(rows, output):
    unit_months = rows_in_section(rows, "1")
    first_month, last_month = first_and_last(unit_months, "settlement_month")
    title = "Q23. Uninstructed deviation from PN as % of capacity, by fuel group"
    subtitle = [
        "Median (solid) and 90th percentile (dashed) of the absolute % across "
        "unit-months; a proxy, not NESO's measure",
        f"Months {first_month[:7]} to {last_month[:7]}; {Q23_TO_Q25_CUTOFF}; "
        "units and half-hours per point in the CSV",
    ]
    measures = {
        "median_absolute_pct": "median",
        "p90_absolute_pct": "90th percentile",
    }
    name = "q23_absolute_deviation_by_month"
    draw_lines_by_group(
        output / f"{name}.png",
        unit_months,
        measures,
        "Absolute deviation from PN, % of capacity",
        title,
        subtitle,
    )

    data = csv_data(Q23_RESULT, ["section", *measures])
    chart = (
        altair.Chart(data)
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=1))
        .transform_fold(list(measures), as_=["measure", "value"])
        .mark_line()
        .encode(
            x=altair.X(
                "settlement_month:O",
                title=None,
                axis=altair.Axis(labelExpr="slice(datum.label, 0, 7)"),
            ),
            y=altair.Y("value:Q", title="Absolute deviation from PN, % of capacity"),
            color=altair.Color(
                "fuel_group:N",
                scale=altair.Scale(range=FUEL_GROUP_COLOURS),
                legend=altair.Legend(title=None, orient="bottom", columns=4),
            ),
            strokeDash=altair.StrokeDash(
                "measure:N",
                sort=list(measures),
                legend=altair.Legend(
                    title=None, orient="bottom", labelExpr=label_expression(measures)
                ),
            ),
            tooltip=[
                "fuel_group:N",
                "settlement_month:N",
                "units:N",
                "half_hours:N",
                "measure:N",
                "value:Q",
            ],
        )
        .properties(**page_layout(title, subtitle))
    )
    write_spec(chart, output / f"{name}.vl.json")


def write_q24_chart(rows, output):
    first_month, last_month = first_and_last(
        rows_in_section(rows, "1"), "settlement_month"
    )
    by_fuel_group = rows_in_section(rows, "2")
    title = "Q24. Share flagged as a system action, by fuel group"
    subtitle = [
        "Share of acceptance volume, with the level-energy share beside it; all months",
        f"Months {first_month[:7]} to {last_month[:7]}; {Q23_TO_Q25_CUTOFF}; "
        "acceptances and stretches per group in the CSV",
    ]
    measures = {
        "so_flagged_volume_pct": "acceptance volume",
        "so_flagged_level_energy_pct": "level energy",
    }
    categories = []
    for row in by_fuel_group:
        categories.append(f"{row['fuel_group']} (n={row['acceptances']})")
    series = []
    for measure, label in measures.items():
        values = []
        for row in by_fuel_group:
            values.append(row[measure])
        series.append((label, values))
    name = "q24_system_action_share_by_fuel_group"
    draw_grouped_bars(
        output / f"{name}.png",
        categories,
        series,
        "Flagged as a system action, %",
        title,
        subtitle,
    )

    data = csv_data(Q24_RESULT, ["section", *measures])
    chart = (
        altair.Chart(data)
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=2))
        .transform_fold(list(measures), as_=["measure", "value"])
    )
    chart = (
        grouped_bars(
            chart,
            altair.Y("fuel_group:N", sort=None, title=None),
            "measure",
            measures,
            "value",
            "Flagged as a system action, %",
        )
        .encode(
            tooltip=[
                "fuel_group:N",
                "acceptances:N",
                "stretches:N",
                "acceptance_volume_mwh:N",
                "measure:N",
                "value:Q",
            ]
        )
        .properties(**page_layout(title, subtitle))
    )
    write_spec(chart, output / f"{name}.vl.json")


def write_q25_chart(rows, output):
    first_month, last_month = first_and_last(
        rows_in_section(rows, "1"), "settlement_month"
    )
    measures = {
        Q25_CHAIN_MEASURE: "continuous acceptance duration (BSC)",
        Q25_SPAN_MEASURE: "own span of each acceptance",
    }
    title = "Q25. Share of acceptances under 10 minutes, by fuel group"
    subtitle = [
        "Continuous acceptance duration as the BSC chains acceptances, with "
        "each acceptance's own span beside it",
        f"Months {first_month[:7]} to {last_month[:7]}; {Q23_TO_Q25_CUTOFF}; "
        "acceptances per group in the CSV",
    ]
    shares = {}
    for row in rows_in_section(rows, "2"):
        if row["measure"] not in measures:
            continue
        if row["fuel_group"] not in shares:
            shares[row["fuel_group"]] = {}
        shares[row["fuel_group"]][row["measure"]] = row
    categories = []
    for fuel_group, by_measure in shares.items():
        categories.append(f"{fuel_group} (n={by_measure[Q25_CHAIN_MEASURE]['n']})")
    series = []
    for measure, label in measures.items():
        values = []
        for by_measure in shares.values():
            values.append(by_measure[measure]["under_10_minutes_pct"])
        series.append((label, values))
    name = "q25_short_acceptances_by_fuel_group"
    draw_grouped_bars(
        output / f"{name}.png",
        categories,
        series,
        "Under 10 minutes, % of acceptances",
        title,
        subtitle,
    )

    data = csv_data(Q25_RESULT, ["section", "under_10_minutes_pct"])
    chart = (
        altair.Chart(data)
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=2))
        .transform_filter(
            altair.FieldOneOfPredicate(field="measure", oneOf=list(measures))
        )
    )
    chart = (
        grouped_bars(
            chart,
            altair.Y("fuel_group:N", sort=None, title=None),
            "measure",
            measures,
            "under_10_minutes_pct",
            "Under 10 minutes, % of acceptances",
        )
        .encode(
            tooltip=[
                "fuel_group:N",
                "measure:N",
                "n:N",
                "under_10_minutes:N",
                "under_10_minutes_pct:Q",
                "median_minutes:N",
            ]
        )
        .properties(**page_layout(title, subtitle))
    )
    write_spec(chart, output / f"{name}.vl.json")


def quintile_rows(rows, item):
    selected = []
    for row in rows:
        if row["item"] == item:
            selected.append(row)
    return selected


def quintile_chart(csv_name, quintiles, title, subtitle):
    data = csv_data(csv_name, ["value"])
    return (
        altair.Chart(data)
        .transform_filter(
            altair.FieldEqualPredicate(field="item", equal=quintiles["item"])
        )
        .mark_bar(color=BAR_COLOUR)
        .encode(
            x=altair.X("value:Q", title=quintiles["value_title"]),
            y=altair.Y(
                "result:N",
                sort=None,
                title=quintiles["range_title"],
                axis=altair.Axis(labelExpr=prefix_removed(quintiles["range_prefix"])),
            ),
            tooltip=["quintile:N", "result:N", "half_hours:N", "value:Q"],
        )
        .properties(**page_layout(title, subtitle))
    )


def write_quintile_chart(rows, output, name, quintiles, title, subtitle):
    range_prefix = quintiles["range_prefix"]
    quintile_values = quintile_rows(rows, quintiles["item"])
    categories = []
    values = []
    for row in quintile_values:
        categories.append(
            f"{row['quintile']}: {row['result'].removeprefix(range_prefix)}"
        )
        values.append(row["value"])
    draw_bars(
        output / f"{name}.png",
        categories,
        values,
        quintiles["value_title"],
        quintiles["range_title"],
        title,
        subtitle,
    )


def write_q28_chart(rows, output):
    population = row_for(rows, "1", Q28_POPULATION_ITEM)
    ratio = row_for(rows, "3", "ratio, top over bottom")["value"]
    low = row_for(rows, "3", "bootstrap 90% interval for the ratio, low")["value"]
    high = row_for(rows, "3", "bootstrap 90% interval for the ratio, high")["value"]
    verdict = row_for(rows, "3", "verdict (supported at a ratio of 1.25 or more)")
    title = (
        "Q28. Mean positive unexplained shortfall by quintile of the final "
        "carbon forecast's absolute error"
    )
    subtitle = [
        f"Ratio top over bottom {ratio} (90% interval {low} to {high}): "
        f"{verdict['result']}",
        f"n={population['half_hours']} half-hours on {population['days']} days, "
        f"{population['first_date']} to {population['last_date']}; {Q8_Q28_READ}",
    ]
    name = "q28_shortfall_by_error_quintile"
    write_quintile_chart(rows, output, name, Q28_QUINTILES, title, subtitle)
    chart = quintile_chart(Q8_Q28_RESULT, Q28_QUINTILES, title, subtitle)
    write_spec(chart, output / f"{name}.vl.json")


def write_q30_chart(rows, output):
    population = row_for(rows, "1", Q30_POPULATION_ITEM)
    rho = row_for(rows, "2", Q30_RHO_ITEM)["value"]
    low = row_for(rows, "2", "rho_abs (primary): bootstrap 90% interval, low")
    high = row_for(rows, "2", "rho_abs (primary): bootstrap 90% interval, high")
    title = (
        "Q30. Mean absolute carbon forecast error by quintile of the wind forecast miss"
    )
    subtitle = [
        f"Rank correlation of the two misses {rho} (90% interval {low['value']} "
        f"to {high['value']})",
        f"n={population['half_hours']} half-hours on {population['days']} days, "
        f"{population['first_date']} to {population['last_date']}; {Q30_READ}",
    ]
    name = "q30_carbon_error_by_wind_miss_quintile"
    write_quintile_chart(rows, output, name, Q30_QUINTILES, title, subtitle)
    chart = quintile_chart(Q30_RESULT, Q30_QUINTILES, title, subtitle)
    write_spec(chart, output / f"{name}.vl.json")


def write_q29_split_chart(rows, output, section, prefix, name, title):
    (overall,) = rows_in_section(rows, "1")
    split = rows_in_section(rows, section)
    subtitle = [
        "gCO2/kWh, actual minus forecast: positive means dirtier than forecast",
        f"n={overall['half_hours']} half-hours, {Q29_WINDOW}; {Q29_READ}",
    ]
    measures = {
        "mean_error": "mean error",
        "mean_absolute_error": "mean absolute error",
    }
    categories = []
    for row in split:
        categories.append(f"{row['item'].removeprefix(prefix)} (n={row['half_hours']})")
    series = []
    for measure, label in measures.items():
        values = []
        for row in split:
            values.append(row[measure])
        series.append((label, values))
    draw_grouped_bars(
        output / f"{name}.png", categories, series, "gCO2/kWh", title, subtitle
    )

    data = csv_data(Q29_RESULT, ["section", *measures])
    chart = (
        altair.Chart(data)
        .transform_filter(
            altair.FieldEqualPredicate(field="section", equal=int(section))
        )
        .transform_fold(list(measures), as_=["measure", "value"])
    )
    chart = (
        grouped_bars(
            chart,
            altair.Y(
                "item:N",
                sort=None,
                title=None,
                axis=altair.Axis(labelExpr=prefix_removed(prefix)),
            ),
            "measure",
            measures,
            "value",
            "gCO2/kWh",
        )
        .encode(
            tooltip=[
                "item:N",
                "half_hours:N",
                "measure:N",
                "value:Q",
                "median_error:N",
                "zero_error_share:N",
            ]
        )
        .properties(**page_layout(title, subtitle))
    )
    write_spec(chart, output / f"{name}.vl.json")


def write_q29_charts(rows, output):
    write_q29_split_chart(
        rows,
        output,
        "2",
        "settlement month ",
        "q29_error_by_month",
        "Q29. Error of the API's final carbon forecast by settlement month",
    )
    write_q29_split_chart(
        rows,
        output,
        "3",
        "UTC block ",
        "q29_error_by_utc_block",
        "Q29. Error of the API's final carbon forecast by UTC block of the day",
    )


def write_charts(result_folder, output_folder):
    results = Path(result_folder)
    output = Path(output_folder)
    output.mkdir(parents=True, exist_ok=True)

    write_delivery_charts(read_delivery(results / DELIVERY_RESULT), output)
    copy_saved_output(results, output, Q2_RESULT, BMRS_ATTRIBUTION)
    copy_saved_output(results, output, Q22_RESULT, BMRS_ATTRIBUTION)
    q5 = copy_saved_output(results, output, Q5_RESULT, BMRS_ATTRIBUTION)
    write_q5_chart(q5, output)
    q6 = copy_saved_output(results, output, Q6_RESULT, BMRS_ATTRIBUTION)
    write_q6_chart(q6, output)
    q23 = copy_saved_output(results, output, Q23_RESULT, BMRS_ATTRIBUTION)
    write_q23_chart(q23, output)
    q24 = copy_saved_output(results, output, Q24_RESULT, BMRS_ATTRIBUTION)
    write_q24_chart(q24, output)
    q25 = copy_saved_output(results, output, Q25_RESULT, BMRS_ATTRIBUTION)
    write_q25_chart(q25, output)
    q8_q28 = copy_saved_output(
        results, output, Q8_Q28_RESULT, BMRS_AND_CARBON_ATTRIBUTION
    )
    write_q28_chart(q8_q28, output)
    q29 = copy_saved_output(results, output, Q29_RESULT, CARBON_ATTRIBUTION)
    write_q29_charts(q29, output)
    q30 = copy_saved_output(results, output, Q30_RESULT, BMRS_AND_CARBON_ATTRIBUTION)
    write_q30_chart(q30, output)


if __name__ == "__main__":
    write_charts(sys.argv[1], sys.argv[2])
