"""Draw the results charts from the saved one-look outputs.

Reads each read's saved output from a folder, under the file names below,
and writes into the output folder:

- each saved output as CSV, its attribution as the first line and its rows
  unchanged (LF line endings);
- for each chart, a PNG and a Vega-Lite spec that reads its CSV by URL
  (``results/<name>.csv``), never inlined.

Q3 and Q12 come from the delivery result, whose chart CSVs hold the values
each chart shows; they are written only when the folder holds
``delivery_by_fuel_group.csv``, and every other file is written without it.
A saved output that lacks a row a chart reads is refused. Nothing is
queried and nothing is recomputed: fuel groups, outage classes and measures
are shown under readable names, and the CSVs keep the saved codes. A rerun
on the same input writes identical files.

The page that shows the specs sets their font and the colours of text,
axes and gridlines; each spec sets its data colours.

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
Q25_CORRECTED_RESULT = "q25_instruction_fragmentation_corrected.csv"
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
# The fuel groups of the registry seed (dbt/seeds), as the charts show them;
# an empty fuel group is a row for all of them.
FUEL_GROUP_NAMES = {
    "": "All fuel groups",
    "bioenergy": "Bioenergy",
    "coal": "Coal",
    "gas_turbine": "Gas turbine",
    "hydro": "Hydro",
    "interconnector": "Interconnector",
    "nuclear": "Nuclear",
    "oil": "Oil",
    "other": "Other",
    "pumped_storage": "Pumped storage",
    "unclassified": "Unclassified",
    "wind": "Wind",
}
OUTAGE_CLASS_NAMES = {
    "planned": "Planned",
    "unplanned": "Unplanned",
    "both": "Both",
    "none": "None",
}
OUTAGE_CLASSES = list(OUTAGE_CLASS_NAMES)
OUTAGE_CLASSES_DRAWN = ["Planned", "Unplanned"]
Q6_MEASURES = {"median_lag_minutes": "Median", "p90_lag_minutes": "90th percentile"}
Q23_MEASURES = {"median_absolute_pct": "Median", "p90_absolute_pct": "90th percentile"}
Q24_MEASURES = {
    "so_flagged_volume_pct": "Acceptance volume",
    "so_flagged_level_energy_pct": "Level energy",
}
Q25_CHAIN_MEASURE = "2 continuous acceptance duration, BSC (acceptances)"
Q25_SPAN_MEASURE = "1 dispatch granularity (acceptances)"
Q25_MEASURES = {
    Q25_CHAIN_MEASURE: "Continuous acceptance duration (BSC)",
    Q25_SPAN_MEASURE: "Own span of each acceptance",
}
Q29_MEASURES = {
    "mean_error": "Mean error",
    "mean_absolute_error": "Mean absolute error",
}
Q28_POPULATION_ITEM = (
    "population: matched days (all three values in at least 46 half-hours), "
    "every half-hour on them with all three"
)
Q28_QUINTILES = {
    "chart": "q28_shortfall_by_error_quintile",
    "item": "quintile of abs(error_api_final): mean positive shortfall, MWh",
    "range_prefix": "abs(error_api_final) ",
    "range_title": "Quintile of the final forecast's absolute error, gCO2/kWh",
    "value_title": "Mean positive unexplained shortfall per half-hour, MWh",
    "value_format": ".6f",
    "range_on_two_lines": False,
}
Q30_POPULATION_ITEM = (
    "population: window half-hours with error_api_final, a forecast, "
    "a first outturn and a known add-back"
)
Q30_QUINTILES = {
    "chart": "q30_carbon_error_by_wind_miss_quintile",
    "item": (
        "quintile of abs(wind error, outturn with cohort curtailment add-back): "
        "mean abs(error_api_final)"
    ),
    "range_prefix": "abs(wind error) ",
    "range_title": "Quintile of the absolute wind forecast miss",
    "value_title": "Mean absolute error of the final carbon forecast, gCO2/kWh",
    "value_format": ".6f",
    "range_on_two_lines": True,
}
# A quintile's range on two lines, broken after "to", where the ranges are
# too long for a phone.
RANGE_ON_TWO_LINES = (
    "[slice(datum.label, 0, indexof(datum.label, ' to ') + 3), "
    "slice(datum.label, indexof(datum.label, ' to ') + 4)]"
)
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
MINIMUM_FIGURE_HEIGHT_INCHES = 3.5
INCHES_PER_BAR = 0.25
LEGEND_ROW_INCHES = 0.22

# The page wraps each chart's caption for a phone, and more widely in a
# chart box at least this wide; the wide lines fit the narrowest such box.
NARROW_CAPTION_CHARACTERS = 46
WIDE_CAPTION_CHARACTERS = 100
WIDE_BOX_PIXELS = 700
AXIS_TITLE_CHARACTERS = 32
BAR_STEP_PIXELS = 34
GROUPED_BAR_STEP_PIXELS = 42
STACKED_BAR_STEP_PIXELS = 28
LINE_CHART_HEIGHT_PIXELS = 320
BAR_CORNER_RADIUS = 3
LABEL_GAP_PIXELS = 4
# Room beside a negative bar for its value label, on charts whose values
# fall either side of zero.
SIGNED_SCALE_PADDING_PIXELS = 56
SHARE_TICKS = [0, 25, 50, 75, 100]
FRACTION_TICKS = [0, 0.25, 0.5, 0.75, 1]

# The categorical palette in its fixed order, checked for colour-vision
# deficiency adjacent pair by adjacent pair; series take it in order. Grey
# is for "none" and "other".
CATEGORY_COLOURS = [
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
]
NEUTRAL_COLOUR = "#a3a19a"
BAR_COLOUR = CATEGORY_COLOURS[0]
MARKER_COLOUR = "#1b2a4a"
ZERO_LINE_COLOUR = "#8c959f"
GRID_COLOUR = "#e6e8eb"
SERIES_COLOURS = CATEGORY_COLOURS[:2]
LINE_STYLES = ["-", "--"]
OUTAGE_CLASS_COLOURS = [*CATEGORY_COLOURS[:3], NEUTRAL_COLOUR]


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


def matching_rows(rows, column, value):
    """The rows whose column holds the value. None is an error, so a renamed
    label in a saved output cannot draw an empty chart."""
    selected = []
    for row in rows:
        if row[column] == value:
            selected.append(row)
    if not selected:
        raise ValueError(f"The saved output has no row with {column} {value!r}")
    return selected


def row_for(rows, section, item):
    for row in matching_rows(rows, "section", section):
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


def fuel_group_name(fuel_group):
    return FUEL_GROUP_NAMES[fuel_group]


def group_labels(rows):
    labels = []
    for row in rows:
        labels.append(f"{fuel_group_name(row['fuel_group'])} (n={row['unit_periods']})")
    return labels


def new_figure(row_count, inches_per_row=INCHES_PER_GROUP):
    height = max(
        MINIMUM_FIGURE_HEIGHT_INCHES, FIGURE_MARGIN_INCHES + inches_per_row * row_count
    )
    figure, axes = pyplot.subplots(figsize=(FIGURE_WIDTH_INCHES, height))
    quiet_frame(axes)
    return figure, axes


def quiet_frame(axes):
    """Only the axes that carry values, over a light grid behind the marks."""
    axes.spines["top"].set_visible(False)
    axes.spines["right"].set_visible(False)
    axes.set_axisbelow(True)
    axes.grid(axis="x", color=GRID_COLOUR, linewidth=0.8)


def share_axis(axes):
    axes.set_xlim(0, 100)
    axes.set_xticks(SHARE_TICKS)


def png_title(axes, title, subtitle_lines):
    lines = []
    for line in [title, *subtitle_lines]:
        lines.extend(textwrap.wrap(line, TITLE_LINE_CHARACTERS, break_on_hyphens=False))
    axes.set_title("\n".join(lines), fontsize=10, loc="left")


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


def draw_grouped_bars(
    path, categories, series, x_label, title, subtitle_lines, shares=False
):
    """Horizontal bars, one group per category, one bar per series; each
    series is a label and its values as text, in category order. Shares run
    from 0 to 100."""
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
            height=bar_height * 0.9,
            color=SERIES_COLOURS[series_index],
            label=label,
        )
        axes.bar_label(bars, labels=values, padding=3, fontsize=8)
    axes.axvline(0, color=ZERO_LINE_COLOUR, linewidth=0.8)
    axes.set_yticks(range(len(categories)), categories)
    axes.invert_yaxis()
    if shares:
        share_axis(axes)
    else:
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
    bars = axes.barh(positions, widths, height=0.6, color=BAR_COLOUR)
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


def evenly_wrapped(line, width):
    """The line in as few lines of at most width characters as it needs,
    of about equal length, so no line is left with a word or two."""
    fewest = textwrap.wrap(line, width, break_on_hyphens=False)
    for even_width in range(math.ceil(len(line) / len(fewest)), width):
        lines = textwrap.wrap(line, even_width, break_on_hyphens=False)
        if len(lines) == len(fewest):
            return lines
    return fewest


def wrapped(lines, width):
    wrapped_lines = []
    for line in lines:
        wrapped_lines.extend(evenly_wrapped(line, width))
    return wrapped_lines


def caption(lines):
    """The chart's caption above the plot, with its lines wrapped for a
    phone and, in a wider chart box, for a wider screen. The box is measured
    again whenever the plot width changes, that is whenever the page is
    resized."""
    narrow = json.dumps(wrapped(lines, NARROW_CAPTION_CHARACTERS), ensure_ascii=False)
    wide = json.dumps(wrapped(lines, WIDE_CAPTION_CHARACTERS), ensure_ascii=False)
    box_width = "containerSize()[0] + 0 * width"
    return altair.TitleParams(
        text=altair.ExprRef(
            expr=f"{box_width} < {WIDE_BOX_PIXELS} ? {narrow} : {wide}"
        ),
        anchor="start",
    )


def page_layout(caption_lines, height):
    """As wide as the page allows, at a height in pixels."""
    return {
        "width": "container",
        "height": height,
        "autosize": altair.AutoSizeParams(type="fit-x", contains="padding"),
        "title": caption(caption_lines),
    }


def display_name(field, names):
    """A Vega expression that shows each value of the field under its
    name; a value without one is shown as it is."""
    expression = f"datum.{field}"
    for value, name in reversed(list(names.items())):
        expression = (
            f"datum.{field} == {json.dumps(value)} ? {json.dumps(name)} : {expression}"
        )
    return expression


def prefix_removed(field, prefix):
    return f"replace(datum.{field}, {json.dumps(prefix)}, '')"


def legend(columns):
    return altair.Legend(title=None, orient="bottom", columns=columns)


def line_legend(columns):
    """A legend whose line symbols are thick enough to match by colour."""
    return altair.Legend(
        title=None, orient="bottom", columns=columns, symbolStrokeWidth=3
    )


def series_colour(name_field, names, colours, columns):
    return altair.Color(
        f"{name_field}:N",
        sort=list(names),
        scale=altair.Scale(domain=list(names), range=colours),
        legend=legend(columns),
    )


def share_scale():
    return altair.Scale(domain=[0, 100])


def share_ticks():
    return altair.Axis(values=SHARE_TICKS)


def value_labels(position, value_field, value_format):
    """Each bar's value beside its end: right of a positive bar, left of a
    negative one."""
    negative = f"datum.{value_field} < 0"
    return (
        altair.Chart()
        .mark_text(
            align=altair.ExprRef(expr=f"{negative} ? 'right' : 'left'"),
            dx=altair.ExprRef(
                expr=f"{negative} ? -{LABEL_GAP_PIXELS} : {LABEL_GAP_PIXELS}"
            ),
        )
        .encode(text=altair.Text(f"{value_field}:Q", format=value_format), **position)
    )


def labelled_bars(position, colour, tooltip, value_field, value_format):
    """Bars at the position channels, each with its value at its end."""
    bars = (
        altair.Chart()
        .mark_bar(cornerRadiusEnd=BAR_CORNER_RADIUS)
        .encode(color=colour, tooltip=tooltip, **position)
    )
    return [bars, value_labels(position, value_field, value_format)]


def grouped_position(category, x, series_field, names):
    """One row per category, one bar per series beside the next, in the
    order of the series names."""
    return {
        "x": x,
        "y": category,
        "yOffset": altair.YOffset(
            f"{series_field}:N",
            sort=list(names.values()),
            scale=altair.Scale(paddingInner=0.15),
        ),
    }


def category_axis(field, title=None, axis=altair.Undefined):
    return altair.Y(
        f"{field}:N",
        sort=None,
        title=title,
        axis=axis,
        scale=altair.Scale(paddingInner=0.3),
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
        loc="left",
    )
    save_figure(figure, path)


def deviation_spec(rows, csv_name):
    data = csv_data(
        csv_name, ["p25_deviation_mwh", "median_deviation_mwh", "p75_deviation_mwh"]
    )
    fuel_group = category_axis("fuel_group_name")
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("unit_periods:N", title="Unit-periods"),
        altair.Tooltip("p25_deviation_mwh:Q", title="Lower quartile, MWh"),
        altair.Tooltip("median_deviation_mwh:Q", title="Median, MWh"),
        altair.Tooltip("p75_deviation_mwh:Q", title="Upper quartile, MWh"),
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
        .mark_point(filled=True, color=MARKER_COLOUR, size=60, opacity=1)
        .encode(x="median_deviation_mwh:Q", y=fuel_group, tooltip=tooltip)
    )
    return (
        altair.layer(interquartile_range, median, data=data)
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES)
        )
        .properties(
            **page_layout(
                ["Median and interquartile range by fuel group", describe_read(rows)],
                len(rows) * BAR_STEP_PIXELS,
            ),
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
        axes.barh(position, 100 * float(share), height=0.6, color=BAR_COLOUR)
    axes.set_yticks(positions, group_labels(rows))
    # Top to bottom, with room for a group that has no bar.
    axes.set_ylim(len(rows) - 0.5, -0.5)
    share_axis(axes)
    axes.set_xlabel("Share of the deviation from PN that was instructed, %")
    axes.set_title(
        "Instructed share of the deviation by fuel group\n" + describe_read(rows),
        fontsize=10,
        loc="left",
    )
    save_figure(figure, path)


def instructed_share_spec(rows, csv_name):
    data = csv_data(csv_name, ["instructed_share_of_abs_deviation"])
    return (
        altair.Chart(data)
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES)
        )
        .mark_bar(color=BAR_COLOUR, cornerRadiusEnd=BAR_CORNER_RADIUS)
        .encode(
            x=altair.X(
                "instructed_share_of_abs_deviation:Q",
                title="Share of the deviation from PN that was instructed",
                axis=altair.Axis(format="%", values=FRACTION_TICKS),
                scale=altair.Scale(domain=[0, 1]),
            ),
            y=category_axis("fuel_group_name"),
            tooltip=[
                altair.Tooltip("fuel_group_name:N", title="Fuel group"),
                altair.Tooltip("unit_periods:N", title="Unit-periods"),
                altair.Tooltip(
                    "abs_instructed_deviation_mwh:N", title="Instructed deviation, MWh"
                ),
                altair.Tooltip(
                    "abs_unexplained_shortfall_mwh:N",
                    title="Unexplained shortfall, MWh",
                ),
                altair.Tooltip(
                    "instructed_share_of_abs_deviation:Q", title="Instructed share"
                ),
            ],
        )
        .properties(
            **page_layout(
                [
                    "Instructed share of the deviation by fuel group",
                    describe_read(rows),
                ],
                len(rows) * BAR_STEP_PIXELS,
            ),
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
        group = fuel_group_name(row["fuel_group"])
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
        axes.barh(
            positions,
            widths,
            left=lefts,
            height=0.6,
            color=colour,
            edgecolor="white",
            linewidth=1,
            label=OUTAGE_CLASS_NAMES[outage_class],
        )
        next_lefts = []
        for left, width in zip(lefts, widths, strict=True):
            next_lefts.append(left + width)
        lefts = next_lefts
    axes.set_yticks(positions, names)
    axes.invert_yaxis()
    share_axis(axes)
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
    groups = shares_by_group(rows)
    draw_stacked_shares(output / f"{name}.png", groups, title, subtitle)

    data = csv_data(Q5_RESULT, ["share_of_positive_shortfall_pct"])
    chart = (
        altair.Chart(data)
        .transform_filter(
            altair.FieldOneOfPredicate(field="item", oneOf=OUTAGE_CLASSES)
        )
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES),
            outage_class_name=display_name("item", OUTAGE_CLASS_NAMES),
            stack_order=f"indexof({json.dumps(OUTAGE_CLASSES)}, datum.item)",
        )
        .mark_bar()
        .encode(
            x=altair.X(
                "share_of_positive_shortfall_pct:Q",
                title="Share of positive unexplained shortfall, %",
                scale=share_scale(),
                axis=share_ticks(),
            ),
            y=category_axis("fuel_group_name"),
            color=series_colour(
                "outage_class_name",
                OUTAGE_CLASS_NAMES.values(),
                OUTAGE_CLASS_COLOURS,
                2,
            ),
            order=altair.Order("stack_order:Q"),
            tooltip=[
                altair.Tooltip("fuel_group_name:N", title="Fuel group"),
                altair.Tooltip("outage_class_name:N", title="Outage class"),
                altair.Tooltip("unit_periods:N", title="Unit-periods"),
                altair.Tooltip(
                    "positive_shortfall_mwh:N", title="Positive shortfall, MWh"
                ),
                altair.Tooltip(
                    "share_of_positive_shortfall_pct:Q",
                    title="Share of positive shortfall, %",
                    format=".1f",
                ),
            ],
        )
        .properties(**page_layout(subtitle, len(groups) * STACKED_BAR_STEP_PIXELS))
    )
    write_spec(chart, output / f"{name}.vl.json")


def write_q6_chart(rows, output):
    drawn = []
    unclassified = None
    for row in matching_rows(rows, "section", "1"):
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
    series = []
    for row in drawn:
        categories.append(f"{row['outage_class']} (n={row['notices']})")
    for measure, label in Q6_MEASURES.items():
        values = []
        for row in drawn:
            values.append(row[measure])
        series.append((label, values))
    name = "q6_publication_lag_by_class"
    x_title = "Lag from event start to first publication, minutes"
    draw_grouped_bars(
        output / f"{name}.png", categories, series, x_title, title, subtitle
    )

    data = csv_data(Q6_RESULT, ["section", *Q6_MEASURES])
    position = grouped_position(
        category_axis("outage_class"),
        altair.X(
            "value:Q",
            title=x_title,
            scale=altair.Scale(padding=SIGNED_SCALE_PADDING_PIXELS),
        ),
        "measure_name",
        Q6_MEASURES,
    )
    tooltip = [
        altair.Tooltip("outage_class:N", title="Outage class"),
        altair.Tooltip("notices:N", title="Notices"),
        altair.Tooltip("measure_name:N", title="Measure"),
        altair.Tooltip("value:Q", title="Minutes", format=",.1f"),
    ]
    colour = series_colour(
        "measure_name", Q6_MEASURES.values(), SERIES_COLOURS, len(Q6_MEASURES)
    )
    chart = (
        altair.layer(
            *labelled_bars(position, colour, tooltip, "value", ",.1f"), data=data
        )
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=1))
        .transform_filter(
            altair.FieldOneOfPredicate(field="outage_class", oneOf=OUTAGE_CLASSES_DRAWN)
        )
        .transform_fold(list(Q6_MEASURES), as_=["measure", "value"])
        .transform_calculate(measure_name=display_name("measure", Q6_MEASURES))
        .properties(**page_layout(subtitle, len(drawn) * GROUPED_BAR_STEP_PIXELS))
    )
    write_spec(chart, output / f"{name}.vl.json")


def fuel_group_colours(groups):
    """Each fuel group's line colour: the palette in order, grey for other."""
    colours = {}
    coloured = []
    for group in groups:
        if group == "other":
            colours[group] = NEUTRAL_COLOUR
            continue
        coloured.append(group)
    for group, colour in zip(coloured, CATEGORY_COLOURS[: len(coloured)], strict=True):
        colours[group] = colour
    return colours


def draw_lines_by_group(path, rows, measures, y_label, title, subtitle_lines):
    """One line per fuel group and measure: solid for the first measure,
    dashed for the second."""
    months = sorted(monthly(rows))
    groups = sorted(fuel_groups(rows))
    colours = fuel_group_colours(groups)
    figure, axes = pyplot.subplots(figsize=(FIGURE_WIDTH_INCHES, 7))
    quiet_frame(axes)
    axes.grid(axis="y", color=GRID_COLOUR, linewidth=0.8)
    handles = []
    for group in groups:
        colour = colours[group]
        handles.append(Line2D([], [], color=colour, label=fuel_group_name(group)))
        for measure, line_style in zip(measures, LINE_STYLES, strict=True):
            positions = []
            values = []
            for row in rows:
                if row["fuel_group"] != group:
                    continue
                positions.append(months.index(row["settlement_month"]))
                values.append(float(row[measure]))
            axes.plot(positions, values, line_style, color=colour, linewidth=1.6)
    for label, line_style in zip(measures.values(), LINE_STYLES, strict=True):
        handles.append(
            Line2D([], [], color=MARKER_COLOUR, linestyle=line_style, label=label)
        )
    month_labels = []
    for month in months:
        month_labels.append(month[:7])
    axes.set_xticks(range(len(months)), month_labels, rotation=90)
    axes.set_ylim(bottom=0)
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
    unit_months = matching_rows(rows, "section", "1")
    first_month, last_month = first_and_last(unit_months, "settlement_month")
    title = "Q23. Uninstructed deviation from PN as % of capacity, by fuel group"
    subtitle = [
        "Median (solid) and 90th percentile (dashed) of the absolute % across "
        "unit-months; a proxy, not NESO's measure",
        f"Months {first_month[:7]} to {last_month[:7]}; {Q23_TO_Q25_CUTOFF}; "
        "units and half-hours per point in the CSV",
    ]
    name = "q23_absolute_deviation_by_month"
    y_title = "Absolute deviation from PN, % of capacity"
    draw_lines_by_group(
        output / f"{name}.png", unit_months, Q23_MEASURES, y_title, title, subtitle
    )

    groups = sorted(fuel_groups(unit_months))
    colours = fuel_group_colours(groups)
    group_names = []
    group_colours = []
    for group in groups:
        group_names.append(fuel_group_name(group))
        group_colours.append(colours[group])
    picked = altair.selection_point(
        name="picked_fuel_group", fields=["fuel_group_name"], bind="legend"
    )
    data = csv_data(Q23_RESULT, ["section", *Q23_MEASURES])
    chart = (
        altair.Chart(data)
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=1))
        .transform_fold(list(Q23_MEASURES), as_=["measure", "value"])
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES),
            measure_name=display_name("measure", Q23_MEASURES),
        )
        .mark_line(strokeWidth=2, strokeCap="round", strokeJoin="round")
        .encode(
            x=altair.X(
                "settlement_month:O",
                title=None,
                axis=altair.Axis(
                    labelExpr="slice(datum.label, 0, 7)",
                    labelAngle=0,
                    labelOverlap="parity",
                ),
            ),
            y=altair.Y("value:Q", title=y_title),
            color=altair.Color(
                "fuel_group_name:N",
                sort=group_names,
                scale=altair.Scale(domain=group_names, range=group_colours),
                legend=line_legend(3),
            ),
            detail="measure_name:N",
            strokeDash=altair.StrokeDash(
                "measure_name:N",
                sort=list(Q23_MEASURES.values()),
                legend=None,
            ),
            opacity=altair.when(picked)
            .then(altair.value(1))
            .otherwise(altair.value(0.15)),
            tooltip=[
                altair.Tooltip("fuel_group_name:N", title="Fuel group"),
                altair.Tooltip("settlement_month:N", title="Month"),
                altair.Tooltip("units:N", title="Units"),
                altair.Tooltip("half_hours:N", title="Half-hours"),
                altair.Tooltip("measure_name:N", title="Measure"),
                altair.Tooltip("value:Q", title="Absolute % of capacity", format=".3f"),
            ],
        )
        .add_params(picked)
        .properties(**page_layout(subtitle, LINE_CHART_HEIGHT_PIXELS))
    )
    write_spec(chart, output / f"{name}.vl.json")


def write_q24_chart(rows, output):
    first_month, last_month = first_and_last(
        matching_rows(rows, "section", "1"), "settlement_month"
    )
    by_fuel_group = matching_rows(rows, "section", "2")
    title = "Q24. Share flagged as a system action, by fuel group"
    subtitle = [
        "Share of acceptance volume, with the level-energy share beside it; all months",
        f"Months {first_month[:7]} to {last_month[:7]}; {Q23_TO_Q25_CUTOFF}; "
        "acceptances and stretches per group in the CSV",
    ]
    categories = []
    for row in by_fuel_group:
        categories.append(
            f"{fuel_group_name(row['fuel_group'])} (n={row['acceptances']})"
        )
    series = []
    for measure, label in Q24_MEASURES.items():
        values = []
        for row in by_fuel_group:
            values.append(row[measure])
        series.append((label, values))
    name = "q24_system_action_share_by_fuel_group"
    x_title = "Flagged as a system action, %"
    draw_grouped_bars(
        output / f"{name}.png",
        categories,
        series,
        x_title,
        title,
        subtitle,
        shares=True,
    )

    data = csv_data(Q24_RESULT, ["section", *Q24_MEASURES])
    position = grouped_position(
        category_axis("fuel_group_name"),
        altair.X("value:Q", title=x_title, scale=share_scale(), axis=share_ticks()),
        "measure_name",
        Q24_MEASURES,
    )
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("acceptances:N", title="Acceptances"),
        altair.Tooltip("stretches:N", title="Stretches"),
        altair.Tooltip("acceptance_volume_mwh:N", title="Acceptance volume, MWh"),
        altair.Tooltip("measure_name:N", title="Measure"),
        altair.Tooltip("value:Q", title=x_title, format=".1f"),
    ]
    colour = series_colour(
        "measure_name", Q24_MEASURES.values(), SERIES_COLOURS, len(Q24_MEASURES)
    )
    chart = (
        altair.layer(
            *labelled_bars(position, colour, tooltip, "value", ".1f"), data=data
        )
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=2))
        .transform_fold(list(Q24_MEASURES), as_=["measure", "value"])
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES),
            measure_name=display_name("measure", Q24_MEASURES),
        )
        .properties(
            **page_layout(subtitle, len(by_fuel_group) * GROUPED_BAR_STEP_PIXELS)
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


def write_q25_chart(rows, output):
    first_month, last_month = first_and_last(
        matching_rows(rows, "section", "1"), "settlement_month"
    )
    title = "Q25. Share of acceptances under 10 minutes, by fuel group"
    subtitle = [
        "Continuous acceptance duration as the BSC chains acceptances, with "
        "each acceptance's own span beside it",
        f"Months {first_month[:7]} to {last_month[:7]}; {Q23_TO_Q25_CUTOFF}; "
        "acceptances per group in the CSV",
    ]
    shares = {}
    for row in matching_rows(rows, "section", "2"):
        if row["measure"] not in Q25_MEASURES:
            continue
        if row["fuel_group"] not in shares:
            shares[row["fuel_group"]] = {}
        shares[row["fuel_group"]][row["measure"]] = row
    categories = []
    for fuel_group, by_measure in shares.items():
        categories.append(
            f"{fuel_group_name(fuel_group)} (n={by_measure[Q25_CHAIN_MEASURE]['n']})"
        )
    series = []
    for measure, label in Q25_MEASURES.items():
        values = []
        for by_measure in shares.values():
            values.append(by_measure[measure]["under_10_minutes_pct"])
        series.append((label, values))
    name = "q25_short_acceptances_by_fuel_group"
    x_title = "Under 10 minutes, % of acceptances"
    draw_grouped_bars(
        output / f"{name}.png",
        categories,
        series,
        x_title,
        title,
        subtitle,
        shares=True,
    )

    data = csv_data(Q25_RESULT, ["section", "under_10_minutes_pct"])
    position = grouped_position(
        category_axis("fuel_group_name"),
        altair.X(
            "under_10_minutes_pct:Q",
            title=x_title,
            scale=share_scale(),
            axis=share_ticks(),
        ),
        "measure_name",
        Q25_MEASURES,
    )
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("measure_name:N", title="Measure"),
        altair.Tooltip("n:N", title="Acceptances"),
        altair.Tooltip("under_10_minutes:N", title="Under 10 minutes"),
        altair.Tooltip("under_10_minutes_pct:Q", title=x_title, format=".1f"),
        altair.Tooltip("median_minutes:N", title="Median minutes"),
    ]
    colour = series_colour("measure_name", Q25_MEASURES.values(), SERIES_COLOURS, 1)
    chart = (
        altair.layer(
            *labelled_bars(position, colour, tooltip, "under_10_minutes_pct", ".1f"),
            data=data,
        )
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=2))
        .transform_filter(
            altair.FieldOneOfPredicate(field="measure", oneOf=list(Q25_MEASURES))
        )
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES),
            measure_name=display_name("measure", Q25_MEASURES),
        )
        .properties(**page_layout(subtitle, len(shares) * GROUPED_BAR_STEP_PIXELS))
    )
    write_spec(chart, output / f"{name}.vl.json")


def quintile_chart(csv_name, quintiles, subtitle, quintile_count):
    data = csv_data(csv_name, ["value"])
    range_labels = altair.Undefined
    if quintiles["range_on_two_lines"]:
        range_labels = altair.Axis(labelExpr=RANGE_ON_TWO_LINES)
    position = {
        "x": altair.X(
            "value:Q",
            title=wrapped([quintiles["value_title"]], AXIS_TITLE_CHARACTERS),
        ),
        "y": category_axis(
            "range",
            wrapped([quintiles["range_title"]], AXIS_TITLE_CHARACTERS),
            range_labels,
        ),
    }
    tooltip = [
        altair.Tooltip("quintile:N", title="Quintile"),
        altair.Tooltip("range:N", title=quintiles["range_title"]),
        altair.Tooltip("half_hours:N", title="Half-hours"),
        altair.Tooltip(
            "value:Q", title=quintiles["value_title"], format=quintiles["value_format"]
        ),
    ]
    return (
        altair.layer(
            *labelled_bars(
                position,
                altair.value(BAR_COLOUR),
                tooltip,
                "value",
                quintiles["value_format"],
            ),
            data=data,
        )
        .transform_filter(
            altair.FieldEqualPredicate(field="item", equal=quintiles["item"])
        )
        .transform_calculate(range=prefix_removed("result", quintiles["range_prefix"]))
        .properties(**page_layout(subtitle, quintile_count * BAR_STEP_PIXELS))
    )


def write_quintile_chart(rows, output, csv_name, quintiles, title, subtitle):
    """The PNG and the spec of one chart of quintiles, named by the output."""
    name = quintiles["chart"]
    range_prefix = quintiles["range_prefix"]
    quintile_values = matching_rows(rows, "item", quintiles["item"])
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
    chart = quintile_chart(csv_name, quintiles, subtitle, len(quintile_values))
    write_spec(chart, output / f"{name}.vl.json")


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
    write_quintile_chart(rows, output, Q8_Q28_RESULT, Q28_QUINTILES, title, subtitle)


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
    write_quintile_chart(rows, output, Q30_RESULT, Q30_QUINTILES, title, subtitle)


def write_q29_split_chart(rows, output, split, title):
    (overall,) = matching_rows(rows, "section", "1")
    split_rows = matching_rows(rows, "section", split["section"])
    subtitle = [
        "gCO2/kWh, actual minus forecast: positive means dirtier than forecast",
        f"n={overall['half_hours']} half-hours, {Q29_WINDOW}; {Q29_READ}",
    ]
    categories = []
    for row in split_rows:
        categories.append(
            f"{row['item'].removeprefix(split['prefix'])} (n={row['half_hours']})"
        )
    series = []
    for measure, label in Q29_MEASURES.items():
        values = []
        for row in split_rows:
            values.append(row[measure])
        series.append((label, values))
    draw_grouped_bars(
        output / f"{split['name']}.png", categories, series, "gCO2/kWh", title, subtitle
    )

    data = csv_data(Q29_RESULT, ["section", *Q29_MEASURES])
    position = grouped_position(
        category_axis("period"),
        altair.X(
            "value:Q",
            title="gCO2/kWh",
            scale=altair.Scale(padding=SIGNED_SCALE_PADDING_PIXELS),
        ),
        "measure_name",
        Q29_MEASURES,
    )
    tooltip = [
        altair.Tooltip("period:N", title=split["period_title"]),
        altair.Tooltip("half_hours:N", title="Half-hours"),
        altair.Tooltip("measure_name:N", title="Measure"),
        altair.Tooltip("value:Q", title="gCO2/kWh", format=".3f"),
        altair.Tooltip("median_error:N", title="Median error"),
        altair.Tooltip("zero_error_share:N", title="Share with no error"),
    ]
    colour = series_colour("measure_name", Q29_MEASURES.values(), SERIES_COLOURS, 1)
    chart = (
        altair.layer(
            *labelled_bars(position, colour, tooltip, "value", ".3f"), data=data
        )
        .transform_filter(
            altair.FieldEqualPredicate(field="section", equal=int(split["section"]))
        )
        .transform_fold(list(Q29_MEASURES), as_=["measure", "value"])
        .transform_calculate(
            period=prefix_removed("item", split["prefix"]),
            measure_name=display_name("measure", Q29_MEASURES),
        )
        .properties(**page_layout(subtitle, len(split_rows) * GROUPED_BAR_STEP_PIXELS))
    )
    write_spec(chart, output / f"{split['name']}.vl.json")


def write_q29_charts(rows, output):
    write_q29_split_chart(
        rows,
        output,
        {
            "section": "2",
            "prefix": "settlement month ",
            "name": "q29_error_by_month",
            "period_title": "Settlement month",
        },
        "Q29. Error of the API's final carbon forecast by settlement month",
    )
    write_q29_split_chart(
        rows,
        output,
        {
            "section": "3",
            "prefix": "UTC block ",
            "name": "q29_error_by_utc_block",
            "period_title": "UTC block",
        },
        "Q29. Error of the API's final carbon forecast by UTC block of the day",
    )


def write_charts(result_folder, output_folder):
    results = Path(result_folder)
    output = Path(output_folder)
    output.mkdir(parents=True, exist_ok=True)

    delivery = results / DELIVERY_RESULT
    if delivery.exists():
        write_delivery_charts(read_delivery(delivery), output)
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
    copy_saved_output(results, output, Q25_CORRECTED_RESULT, BMRS_ATTRIBUTION)
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
