"""Draw the results charts from the saved one-look outputs.

Reads each read's saved output from a folder, under the file names below,
and writes into the output folder:

- each saved output as CSV, its attribution as the first line and its rows
  unchanged (LF line endings);
- for each chart, a chart CSV of exactly the values it draws, picked,
  joined or summed from the saved outputs, with the same attribution line
  (Q29 reads its saved output directly);
- for each chart, a PNG and a Vega-Lite spec that reads its CSV by URL
  (``results/<name>.csv``), never inlined.

A saved output that lacks a row a chart reads is refused. Nothing is
queried. Fuel groups and measures are shown under readable names; the CSVs
keep the saved codes. A rerun on the same input writes identical files.

The page that shows the specs sets their font and the colours of text,
axes and gridlines. Each spec sets its data colours, and each colour has
one meaning on every chart, but for the Q23 charts' fuel-group colours: the
page registers them for its colour mode as a scheme the specs name.

    python scripts/readme_charts.py <folder of saved outputs> <output folder>
"""

import csv
import io
import json
import statistics
import sys
import textwrap
from decimal import ROUND_HALF_UP, Decimal
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

Q2_RESULT = "q2_settlement_run_changes.csv"
Q2_BY_PREFIX_RESULT = "q2_changes_by_unit_prefix.csv"
Q3_RESULT = "q3_metered_vs_notified.csv"
Q3_COVERAGE_RESULT = "q3_coverage.csv"
Q12_RESULT = "q12_instructed_vs_residual.csv"
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

# The fuel groups of the registry seed (dbt/seeds), as the charts show them;
# an empty fuel group is a row for all of them.
FUEL_GROUP_NAMES = {
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
# Coal units had closed; interconnectors are links whose metered figure is
# an allocation, never instructed. The page says which chart leaves out
# which.
CLOSED_UNITS = {"coal"}
CLOSED_UNITS_AND_LINKS = {"coal", "interconnector"}

# Q2: Elexon's BM unit ID prefixes, as unit types.
SUPPLIER_UNITS = "Suppliers' customer accounts"
TRANSMISSION_AND_EMBEDDED_UNITS = "Transmission-connected and embedded units"
OTHER_UNIT_TYPES = "Other unit types"
UNIT_TYPE_BY_PREFIX = {
    "2": SUPPLIER_UNITS,
    "T": TRANSMISSION_AND_EMBEDDED_UNITS,
    "E": TRANSMISSION_AND_EMBEDDED_UNITS,
}
UNIT_TYPE_LABELS = {
    SUPPLIER_UNITS: "Suppliers' customer accounts",
    TRANSMISSION_AND_EMBEDDED_UNITS: "Transmission-connected|and embedded units",
    OTHER_UNIT_TYPES: "Other unit types",
}

# Q5: a half-hour under both kinds of notice counts as unplanned.
Q5_PLANNED_CLASSES = ["planned"]
Q5_UNPLANNED_CLASSES = ["unplanned", "both"]
Q5_NO_NOTICE_CLASSES = ["none"]
Q5_SEGMENTS = {
    "planned_mwh": "Planned outage",
    "unplanned_mwh": "Unplanned outage",
    "no_notice_mwh": "No notice",
}

# Q23: the whole months of the window; August 2025 starts on the 22nd and
# September 2026 is on early meter data.
Q23_WHOLE_MONTHS = [
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
]
Q23_PROVISIONAL_MONTH = "2026-09-01"
Q23_CHART_MONTHS = [*Q23_WHOLE_MONTHS, Q23_PROVISIONAL_MONTH]
# The month-by-month line runs dashed from the last whole month into the
# provisional one.
Q23_DASHED_FROM_MONTH = Q23_WHOLE_MONTHS[-1]
# A 90th percentile across fewer units than this is too thin to draw.
Q23_UNITS_FOR_A_PERCENTILE = 20
Q23_EMPHASISED_GROUP = "wind"
# Fixed scales, % of capacity; a value past one is refused, not clipped.
Q23_TYPICAL_AXIS_TOP = 12
Q23_MONTHLY_AXIS_TOP = 10
Q23_MONTHLY_HEIGHT_PIXELS = 300
# Line-end names keep this far apart, in % of capacity (15 px on the 300 px
# plot), and this far above zero.
Q23_END_NAME_GAP = Decimal("0.5")
Q23_END_NAME_FLOOR = Decimal("0.2")
Q23_KEY_TYPICAL = "Median of 12 monthly medians"
Q23_KEY_ONE_IN_TEN = "90th percentile"
# The band marks the provisional month on the month-by-month chart, and
# ends the time axis just past its last month; the axis starts at the first
# month, padded in pixels.
Q23_BAND_FROM = "2026-08-17"
Q23_BAND_TO = "2026-09-12"
Q23_TIME_PADDING_PIXELS = 14
MONTH_NAMES = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

# One colour per fuel group, the same on both Q23 charts, as light and dark
# steps of the validated categorical palette. The page registers the steps
# for its colour mode as this scheme; the PNGs use the light ones.
FUEL_GROUP_SCHEME = "gridskew-fuel-groups"
FUEL_GROUP_COLOURS = {
    "wind": ("#eb6834", "#d95926"),
    "other": ("#1baf7a", "#199e70"),
    "bioenergy": ("#6250d6", "#9085e9"),
    "nuclear": ("#eda100", "#c98500"),
    "hydro": ("#2a78d6", "#3987e5"),
    "gas_turbine": ("#e87ba4", "#d55181"),
    "pumped_storage": ("#008300", "#008300"),
}

# Q3's key.
Q3_KEY_SPREAD = "Interquartile range (middle 50%)"
Q3_KEY_MEDIAN = "Median"

Q12_PARTS = {"instructed_mwh": "Instructed by NESO", "rest_mwh": "Not instructed"}
Q24_PARTS = {"flagged_pct": "Flagged", "not_flagged_pct": "Not flagged"}
Q24_AXIS_TITLE = "Share of the energy instructions moved, %"

Q25_JOINED_MEASURE = "2 continuous acceptance duration, BSC (acceptances)"
Q25_OWN_SPAN_MEASURE = "1 dispatch granularity (acceptances)"

Q28_QUINTILE_ITEM = "quintile of abs(error_api_final): mean positive shortfall, MWh"
Q28_RANGE_PREFIX = "abs(error_api_final) "
# The rule: supported at a ratio of 1.25 or more, so 125 on the index.
Q28_NEEDED_INDEX = 125
# Only the largest fifth's index has an interval.
Q28_RANGE_KEY = "90% range (largest fifth)"

Q29_BLOCKS = {
    "UTC block 00:00-05:59": "Night|00:00–05:59",
    "UTC block 06:00-11:59": "Morning|06:00–11:59",
    "UTC block 12:00-17:59": "Afternoon|12:00–17:59",
    "UTC block 18:00-23:59": "Evening|18:00–23:59",
}
DIRTIER = "Dirtier than forecast"
CLEANER = "Cleaner than forecast"
PART_MONTH = "Part month (fewer days)"

# Q30: each rank correlation with the rows that hold it and its interval.
Q30_CORRELATIONS = [
    {
        "label": "Size of misses|turned-down wind added back",
        "measure": "Size of the two misses, with turned-down wind added back",
        "section": "2",
        "value": (
            "rho_abs (primary): abs(wind error, outturn with cohort curtailment "
            "add-back) with abs(error_api_final)"
        ),
        "low": "rho_abs (primary): bootstrap 90% interval, low",
        "high": "rho_abs (primary): bootstrap 90% interval, high",
    },
    {
        "label": "Direction|turned-down wind added back",
        "measure": ("Direction of the two misses, with turned-down wind added back"),
        "section": "3",
        "value": (
            "rho_signed (secondary): wind error, outturn with cohort curtailment "
            "add-back, with error_api_final; expected direction negative"
        ),
        "low": "rho_signed (secondary): bootstrap 90% interval, low",
        "high": "rho_signed (secondary): bootstrap 90% interval, high",
    },
    {
        "label": "Size of misses|delivered wind",
        "measure": "Size of the two misses, delivered wind only",
        "section": "4",
        "value": (
            "rho_abs (sensitivity): abs(wind error, delivered outturn) "
            "with abs(error_api_final)"
        ),
        "low": "rho_abs (sensitivity): bootstrap 90% interval, low",
        "high": "rho_abs (sensitivity): bootstrap 90% interval, high",
    },
    {
        "label": "Direction|delivered wind",
        "measure": "Direction of the two misses, delivered wind only",
        "section": "4",
        "value": (
            "rho_signed (sensitivity): wind error, delivered outturn, "
            "with error_api_final"
        ),
        "low": "rho_signed (sensitivity): bootstrap 90% interval, low",
        "high": "rho_signed (sensitivity): bootstrap 90% interval, high",
    },
]
# The size stated before the read, not a test; it applies to the rows that
# compare the size of the two misses, not their direction.
Q30_EXPECTED_SIZE = 0.2
Q30_SIZE_LABEL_START = "Size of misses"

Q2_TITLE = "Where the {gigawatt_hours} GWh of revisions came from"
Q2_AXIS_TITLE = "Share of the energy revised, %"
Q3_TITLE = "Most fuel types produce slightly less than planned, with wide spreads"
Q12_TITLE = (
    "Instructions are most of the gap for gas, hydro and pumped storage, "
    "under half for wind"
)
Q5_TITLE = "All units: {share}% of under-delivery came with a published outage"
Q23_TYPICAL_TITLE = (
    "With no NESO instruction, wind farms stray furthest from their plans"
)
Q23_MONTHLY_TITLE = "Wind stayed furthest from plan in every whole month"
Q24_TITLE = (
    "NESO flags almost all the energy its instructions move at wind farms as "
    "managing the network"
)
Q25_TITLE = (
    "Batteries (the other group) and pumped storage get far more short "
    "instructions than power stations"
)
Q28_TITLE = "Bigger carbon misses came with slightly more under-delivery, not 25% more"
Q29_TIME_TITLE = "Afternoons came out dirtier than forecast, evenings cleaner"
Q29_MONTH_TITLE = (
    "Winter months came out cleaner than forecast, April to July slightly dirtier"
)
Q30_TITLE = "Wind and carbon forecast misses barely move together"

# "How to read this", under each chart's title.
Q2_READING = (
    "Each bar is one type of unit's share of the energy revised between the "
    "three-week and two-month runs."
)
Q3_READING = (
    "Each row is a fuel group: the dot is its median gap between plan and "
    "meter, the bar its interquartile range (the middle 50% of half-hours)."
)
Q12_READING = (
    "Each bar is a fuel group's whole gap between plan and meter, split into "
    "the part NESO instructed and the rest; the figure is the instructed "
    "share."
)
Q5_READING = (
    "Each bar is a fuel group's under-delivery, split by whether an outage "
    "notice was live when the half-hour began; the figure is the share with "
    "a notice."
)
Q23_TYPICAL_READING = (
    "Each bar is the median of a fuel group's 12 monthly medians; the pale "
    "line, the median of its 12 monthly 90th percentiles (1 unit in 10 misses "
    "by more)."
)
Q23_MONTHLY_READING = (
    "Each line is a fuel group's median gap from plan, month by month, in "
    "half-hours with no instruction; the dashed end is September 2026, on "
    "early meter data."
)
Q24_READING = (
    "Each bar is one fuel group's instructed energy as 100%: blue is the "
    "share NESO flagged as managing the network. Under each name is that "
    "energy in TWh."
)
Q25_READING = (
    "Each row is a fuel group. The dot is the share of its instructions "
    "under 10 minutes once follow-on instructions are joined, Elexon's test; "
    "the ring counts each on its own. Each step on the scale is ten times "
    "the last."
)
Q28_READING = (
    "Each row is a fifth of the half-hours, smallest carbon misses first; the "
    "dot is their mean under-delivery, with the smallest fifth set to 100. A "
    "dot at 125 or beyond would mean yes."
)
Q29_TIME_READING = "Each bar is the mean miss in a six-hour block of the day."
Q29_MONTH_READING = "Each point is one month's mean miss."
Q30_READING = (
    "Each row compares the two misses one way: the dot is the rank "
    "correlation, the line its 90% range. In the size rows, a dot in the "
    "shaded band is as small as expected; the direction rows were expected "
    "to be negative."
)

# Each chart's key: every mark it draws, as the key draws it.
KEY_FIELD = "key"
# Layers drawn without a key entry: the axes' titles, or a word drawn on
# the band, explain them.
UNKEYED_LAYERS = {"zero line", "connector", "provisional band"}
BAR_SYMBOL = "M-1,-0.45H1V0.45H-1Z"
TICK_SYMBOL = "M-0.18,-1H0.18V1H-0.18Z"
LINE_SYMBOL = "M-1,0H1"
DASH_SYMBOL = "M-1,0H-0.55M-0.2,0H0.2M0.55,0H1"
BAND_FILL = "rgba(140, 149, 159, 0.2)"
NO_FILL = "transparent"
# A pale line's entry in a key: thinner and lighter than a bar's.
THIN_BAR_SYMBOL = "M-1,-0.2H1V0.2H-1Z"
PALE_KEY_COLOUR = "#c9ced4"
FIGURE_WIDTH_INCHES = 8
INCHES_PER_ROW = 0.5
FIGURE_MARGIN_INCHES = 1.6
DPI = 100
PAD_INCHES = 0.2
TITLE_LINE_CHARACTERS = 70
MINIMUM_FIGURE_HEIGHT_INCHES = 3.2
LEGEND_ROW_INCHES = 0.22

# The page wraps each chart's title, and how to read it, to fill the chart
# box at its width: each line as full as it goes at the widest of these box
# widths the box reaches. A box is 341 px inside its border on a 375 px
# phone and at most 734 px; its text runs inside 28 px of padding. Average
# character widths, measured 5.9 px for a 13.5 px semibold title and 5.5 px
# for a 12.5 px reading line, with a margin.
BOX_WIDTHS_PIXELS = [340, 400, 480, 560, 640, 720]
BOX_PADDING_PIXELS = 28
TITLE_CHARACTER_PIXELS = 6.5
READING_CHARACTER_PIXELS = 5.7
BOX_WIDTH = "containerSize()[0] + 0 * width"
ROW_STEP_PIXELS = 34
TWO_LINE_ROW_STEP_PIXELS = 44
LINE_CHART_HEIGHT_PIXELS = 220
BAR_CORNER_RADIUS = 3
LABEL_GAP_PIXELS = 4
# Room beside a negative bar for its value label, on charts whose values
# fall either side of zero.
SIGNED_SCALE_PADDING_PIXELS = 56
SHARE_TICKS = [0, 25, 50, 75, 100]
LOG_SHARE_TICKS = [0.01, 0.1, 1, 10, 100]

# One meaning per colour, on every chart: blue is the measured result, its
# light tint the result's spread, grey the rest, mid grey a comparison mark
# or a line a verdict needed. Orange and aqua are dirtier and cleaner than
# forecast. From the validated categorical palette and its blue ramp.
RESULT_COLOUR = "#2a78d6"
PLANNED_COLOUR = "#184f95"
UNPLANNED_COLOUR = "#3987e5"
SPREAD_COLOUR = "#9ec5f4"
REST_COLOUR = "#a3a19a"
COMPARISON_COLOUR = "#8c959f"
DIRTIER_COLOUR = "#eb6834"
CLEANER_COLOUR = "#1baf7a"
INSIDE_LABEL_COLOUR = "#ffffff"
GRID_COLOUR = "#e6e8eb"


def copy_saved_output(results, output, name, attribution):
    """Write the saved output with its attribution as the first line; return
    its rows. Text mode reads Windows line endings as LF."""
    with (results / name).open(encoding="utf-8") as saved:
        rows_text = saved.read()
    (output / name).write_bytes(f"# {attribution}\n{rows_text}".encode("utf-8"))
    return list(csv.DictReader(io.StringIO(rows_text)))


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


def fixed(value, places):
    """The value at a fixed number of decimal places, halves rounded up."""
    step = Decimal(1).scaleb(-places)
    return str(value.quantize(step, rounding=ROUND_HALF_UP))


def percentage(part, whole):
    return Decimal(100) * part / whole


def total_of(values):
    total = Decimal(0)
    for value in values:
        total += value
    return total


def fuel_group_name(fuel_group):
    return FUEL_GROUP_NAMES[fuel_group]


def write_chart_csv(output, name, attribution, columns, rows):
    """The values one chart draws, with the attribution as the first line;
    returns the file name."""
    csv_name = f"{name}.csv"
    with (output / csv_name).open("w", newline="", encoding="utf-8") as target:
        target.write(f"# {attribution}\n")
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            values = []
            for column in columns:
                values.append(row[column])
            writer.writerow(values)
    return csv_name


# PNG drawing


def new_figure(row_count):
    height = max(
        MINIMUM_FIGURE_HEIGHT_INCHES, FIGURE_MARGIN_INCHES + INCHES_PER_ROW * row_count
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


def png_title(axes, title, subtitle_lines):
    lines = []
    for line in [title, *subtitle_lines]:
        lines.extend(textwrap.wrap(line, TITLE_LINE_CHARACTERS, break_on_hyphens=False))
    axes.set_title("\n".join(lines), fontsize=10, loc="left")


def png_label(label):
    return label.replace("|", "\n")


def row_labels(axes, labels):
    """One row per label, the first at the top."""
    lines = []
    for label in labels:
        lines.append(png_label(label))
    axes.set_yticks(range(len(labels)), lines)
    axes.set_ylim(len(labels) - 0.5, -0.5)


def save_figure(figure, path, handles=None):
    """Any legend goes below the plot, in space kept free for it; the image
    is cropped to what is drawn, so a long title is never cut."""
    if not handles:
        figure.tight_layout()
    else:
        figure.legend(
            handles=handles,
            loc="lower center",
            ncol=len(handles),
            fontsize=8,
            frameon=False,
        )
        legend_inches = LEGEND_ROW_INCHES * 2
        figure.tight_layout(rect=(0, legend_inches / figure.get_figheight(), 1, 1))
    figure.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=PAD_INCHES)
    pyplot.close(figure)


def bar_label(axes, position, value, text, colour="#3d444d"):
    axes.annotate(
        text,
        (value, position),
        xytext=(LABEL_GAP_PIXELS, 0),
        textcoords="offset points",
        va="center",
        fontsize=8,
        color=colour,
    )


def stacked_bars(axes, rows, parts, colours, scale):
    """Horizontal bars, one per row, with each part stacked in order."""
    lefts = [0.0] * len(rows)
    for (field, name), colour in zip(parts.items(), colours, strict=True):
        widths = []
        for row in rows:
            widths.append(float(row[field]) / scale)
        axes.barh(
            range(len(rows)),
            widths,
            left=lefts,
            height=0.6,
            color=colour,
            edgecolor="white",
            linewidth=1,
            label=name,
        )
        next_lefts = []
        for left, width in zip(lefts, widths, strict=True):
            next_lefts.append(left + width)
        lefts = next_lefts
    return lefts


def legend_handles(axes):
    handles, _labels = axes.get_legend_handles_labels()
    return handles


# Vega-Lite specs


def csv_data(csv_name, number_fields):
    """The chart's CSV by URL, with the fields it reads as numbers parsed."""
    parse = {}
    for field in number_fields:
        parse[field] = "number"
    return altair.UrlData(
        url=f"{SPEC_DATA_FOLDER}/{csv_name}",
        format=altair.CsvDataFormat(type="csv", parse=parse),
    )


def lines_for_box(text, character_pixels, box_pixels):
    """The text in lines as full as a chart box this wide holds."""
    characters = int((box_pixels - BOX_PADDING_PIXELS) / character_pixels)
    return textwrap.wrap(text, characters, break_on_hyphens=False)


def wrapped_to_box(text, character_pixels):
    """A Vega expression: the text wrapped to fill the chart box at its
    width. The box is measured again whenever the plot width changes, that
    is whenever the page is resized."""
    choices = []
    for box_pixels in BOX_WIDTHS_PIXELS:
        lines = lines_for_box(text, character_pixels, box_pixels)
        choices.append(json.dumps(lines, ensure_ascii=False))
    expression = choices[-1]
    narrower = list(zip(BOX_WIDTHS_PIXELS[1:], choices[:-1], strict=True))
    for box_pixels, lines in reversed(narrower):
        expression = f"{BOX_WIDTH} < {box_pixels} ? {lines} : ({expression})"
    return expression


def caption(title, reading):
    """The chart's title above the plot, with how to read the chart under
    it, as one line that write_spec wraps to the box."""
    return altair.TitleParams(
        text=altair.ExprRef(expr=wrapped_to_box(title, TITLE_CHARACTER_PIXELS)),
        subtitle=[reading],
        anchor="start",
    )


def reading_wrapped_to_box(spec):
    """The spec with how to read the chart wrapped to the box like the
    title. Vega-Lite's schema takes only fixed text for a subtitle, so
    Altair cannot write this; Vega-Lite passes the signal through to Vega."""
    title = spec["title"]
    reading = " ".join(title["subtitle"])
    title["subtitle"] = {"signal": wrapped_to_box(reading, READING_CHARACTER_PIXELS)}
    return spec


def page_layout(title, reading, height):
    """As wide as the page allows, at a height in pixels."""
    return {
        "width": "container",
        "height": height,
        "autosize": altair.AutoSizeParams(type="fit-x", contains="padding"),
        "title": caption(title, reading),
        "description": title,
    }


def key_mark(stroke, fill, shape):
    """How one mark looks in a chart's key: its line colour, its fill and
    its symbol (a shape name or an SVG path)."""
    return {"stroke": stroke, "fill": fill, "shape": shape}


def key_legend():
    return altair.Legend(
        title=None,
        orient="bottom",
        direction="vertical",
        symbolSize=110,
        symbolStrokeWidth=2,
        symbolOpacity=1,
        labelLimit=0,
    )


def key_scale(keys, look):
    looks = []
    for mark in keys.values():
        looks.append(mark[look])
    return altair.Scale(domain=list(keys), range=looks)


def key_stroke(keys):
    """A line's colour from the chart's key."""
    return altair.Stroke(
        f"{KEY_FIELD}:N", scale=key_scale(keys, "stroke"), legend=key_legend()
    )


def key_fill(keys):
    """A filled mark's colour (a bar, tick or band) from the chart's key."""
    return altair.Fill(
        f"{KEY_FIELD}:N", scale=key_scale(keys, "fill"), legend=key_legend()
    )


def key_shape(keys):
    return altair.Shape(
        f"{KEY_FIELD}:N", scale=key_scale(keys, "shape"), legend=key_legend()
    )


def keyed(chart, label):
    """The layer's rows named by their entry in the chart's key."""
    return chart.transform_calculate(**{KEY_FIELD: json.dumps(label)})


def keyed_points(keys):
    """A point layer's outline, fill and symbol, all from the chart's key."""
    return {
        "stroke": key_stroke(keys),
        "fill": key_fill(keys),
        "shape": key_shape(keys),
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


def row_axis(field, order=None):
    """Rows in the given order, else the CSV's; a label holding '|' is shown
    on two lines."""
    return altair.Y(
        f"{field}:N",
        sort=order,
        title=None,
        axis=altair.Axis(labelExpr="split(datum.label, '|')"),
        scale=altair.Scale(paddingInner=0.3),
    )


def column_of(rows, column):
    values = []
    for row in rows:
        values.append(row[column])
    return values


def axis_title(lines):
    return altair.Axis(title=lines)


def drawn_once():
    """A layer for a reference mark: the rows counted down to one, so a line,
    band or note is drawn once, not once per row."""
    return altair.Chart().transform_aggregate(rows="count()")


def zero_line():
    return (
        drawn_once()
        .mark_rule(color=COMPARISON_COLOUR)
        .encode(x=altair.datum(0))
        .properties(name="zero line")
    )


def fill_by_name(name_field, names, colours, columns):
    return altair.Color(
        f"{name_field}:N",
        sort=list(names),
        scale=altair.Scale(domain=list(names), range=colours),
        legend=altair.Legend(title=None, orient="bottom", columns=columns),
    )


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


def label_above(x, y, text_field):
    return (
        altair.Chart()
        .mark_text(baseline="bottom", dy=-7)
        .encode(x=x, y=y, text=f"{text_field}:N")
    )


def stacked_parts(row_field, parts, colours, x_title, tooltip, scale):
    """Bars stacked from the part columns, each part at its value divided by
    the scale."""
    stack_order = f"indexof({json.dumps(list(parts))}, datum.part)"
    return (
        altair.Chart()
        .transform_fold(list(parts), as_=["part", "amount"])
        .transform_calculate(
            part_name=display_name("part", parts),
            stack_order=stack_order,
            scaled=f"datum.amount / {scale}",
        )
        .mark_bar(stroke=None)
        .encode(
            x=altair.X("scaled:Q", stack="zero", axis=axis_title(x_title)),
            y=row_axis(row_field),
            color=fill_by_name("part_name", parts.values(), colours, 2),
            order=altair.Order("stack_order:Q"),
            tooltip=tooltip,
        )
    )


def write_spec(chart, path):
    spec = reading_wrapped_to_box(chart.to_dict())
    text = json.dumps(spec, sort_keys=True, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


# Q2: where the revised energy came from


def moved_energy_by_unit_type(rows):
    matching_rows(rows, "unit_prefix", "2")
    moved = {SUPPLIER_UNITS: Decimal(0)}
    moved[TRANSMISSION_AND_EMBEDDED_UNITS] = Decimal(0)
    moved[OTHER_UNIT_TYPES] = Decimal(0)
    for row in rows:
        unit_type = UNIT_TYPE_BY_PREFIX.get(row["unit_prefix"], OTHER_UNIT_TYPES)
        moved[unit_type] += Decimal(row["moved_mwh"])
    total = total_of(moved.values())
    chart_rows = []
    for unit_type, moved_mwh in moved.items():
        chart_rows.append(
            {
                "unit_type": unit_type,
                "label": UNIT_TYPE_LABELS[unit_type],
                "moved_mwh": fixed(moved_mwh, 3),
                "share_of_moved_pct": fixed(percentage(moved_mwh, total), 6),
            }
        )
    return chart_rows, total


def write_q2_chart(rows, output):
    name = "q2_moved_energy_by_unit_type"
    chart_rows, total = moved_energy_by_unit_type(rows)
    title = Q2_TITLE.format(gigawatt_hours=fixed(total / 1000, 0))
    columns = ["unit_type", "label", "moved_mwh", "share_of_moved_pct"]
    csv_name = write_chart_csv(output, name, BMRS_ATTRIBUTION, columns, chart_rows)

    figure, axes = new_figure(len(chart_rows))
    labels = []
    for position, row in enumerate(chart_rows):
        share = float(row["share_of_moved_pct"])
        axes.barh(position, share, height=0.6, color=RESULT_COLOUR)
        bar_label(axes, position, share, f"{share:.0f}%")
        labels.append(row["label"])
    row_labels(axes, labels)
    axes.set_xlim(0, 100)
    axes.set_xticks(SHARE_TICKS)
    axes.set_xlabel(Q2_AXIS_TITLE)
    png_title(
        axes,
        title,
        [
            "Between the three-week and two-month settlement runs, settlement "
            "dates 10 to 16 August 2026"
        ],
    )
    save_figure(figure, output / f"{name}.png")

    data = csv_data(csv_name, ["moved_mwh", "share_of_moved_pct"])
    position = {
        "x": altair.X(
            "share_of_moved_pct:Q",
            scale=altair.Scale(domain=[0, 100]),
            axis=altair.Axis(values=SHARE_TICKS, title=[Q2_AXIS_TITLE]),
        ),
        "y": row_axis("label"),
    }
    tooltip = [
        altair.Tooltip("unit_type:N", title="Unit type"),
        altair.Tooltip("moved_mwh:Q", title="Revised, MWh", format=",.0f"),
        altair.Tooltip("share_of_moved_pct:Q", title="Share, %", format=".1f"),
    ]
    # One series: the title and axis name it, so it needs no key.
    bars = (
        altair.Chart()
        .mark_bar(cornerRadiusEnd=BAR_CORNER_RADIUS, fill=RESULT_COLOUR)
        .encode(tooltip=tooltip, **position)
    )
    shares = (
        altair.Chart()
        .mark_text(align="left", dx=LABEL_GAP_PIXELS)
        .encode(text="share_label:N", **position)
    )
    chart = (
        altair.layer(bars, shares, data=data)
        .transform_calculate(
            share_label="format(datum.share_of_moved_pct, '.0f') + '%'"
        )
        .properties(
            **page_layout(title, Q2_READING, len(chart_rows) * TWO_LINE_ROW_STEP_PIXELS)
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


# Q3: which way each fuel group misses its plan


def gap_from_plan_rows(q3_rows):
    """Q3's quartiles per fuel group, in the saved order; closed units are
    left out."""
    chart_rows = []
    for row in q3_rows:
        fuel_group = row["fuel_group"]
        if fuel_group in CLOSED_UNITS:
            continue
        chart_rows.append(
            {
                "fuel_group": fuel_group,
                "unit_periods": row["unit_periods"],
                "q1_deviation_mwh": row["q1_deviation_mwh"],
                "median_deviation_mwh": row["median_deviation_mwh"],
                "q3_deviation_mwh": row["q3_deviation_mwh"],
            }
        )
    return chart_rows


def draw_gap_from_plan(rows, path):
    figure, axes = new_figure(len(rows))
    for position, row in enumerate(rows):
        lower = float(row["q1_deviation_mwh"])
        upper = float(row["q3_deviation_mwh"])
        axes.plot(
            [lower, upper], [position, position], color=SPREAD_COLOUR, linewidth=6
        )
        axes.plot(
            float(row["median_deviation_mwh"]), position, "o", color=RESULT_COLOUR
        )
    axes.axvline(0, color=COMPARISON_COLOUR, linewidth=0.8)
    labels = []
    for row in rows:
        labels.append(fuel_group_name(row["fuel_group"]))
    row_labels(axes, labels)
    axes.set_xlabel(
        "Plan minus meter, MWh per unit per half-hour "
        "(left: produced more than planned; right: produced less)"
    )
    png_title(
        axes,
        Q3_TITLE,
        [
            "Bar: interquartile range of all units' half-hours; dot: median, "
            "22 August 2025 to 26 September 2026."
        ],
    )
    handles = [
        Line2D([], [], color=SPREAD_COLOUR, linewidth=6, label=Q3_KEY_SPREAD),
        Line2D(
            [], [], color=RESULT_COLOUR, marker="o", linestyle="", label=Q3_KEY_MEDIAN
        ),
    ]
    save_figure(figure, path, handles)


def write_q3_chart(q3_rows, output):
    name = "q3_gap_from_plan_by_fuel_group"
    chart_rows = gap_from_plan_rows(q3_rows)
    columns = list(chart_rows[0])
    csv_name = write_chart_csv(output, name, BMRS_ATTRIBUTION, columns, chart_rows)
    draw_gap_from_plan(chart_rows, output / f"{name}.png")

    measures = ["q1_deviation_mwh", "median_deviation_mwh", "q3_deviation_mwh"]
    data = csv_data(csv_name, measures)
    fuel_group = row_axis("fuel_group_name")
    x_axis = axis_title(
        [
            "Plan minus meter, megawatt-hours (MWh)",
            "per unit per half-hour",
            "left: produced more than planned · right: less",
        ]
    )
    keys = {
        Q3_KEY_SPREAD: key_mark(SPREAD_COLOUR, SPREAD_COLOUR, BAR_SYMBOL),
        Q3_KEY_MEDIAN: key_mark(RESULT_COLOUR, RESULT_COLOUR, "circle"),
    }
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("unit_periods:Q", title="Half-hours, all units", format=","),
        altair.Tooltip(
            "q1_deviation_mwh:Q", title="25th percentile, MWh", format=".1f"
        ),
        altair.Tooltip("median_deviation_mwh:Q", title="Median, MWh", format=".1f"),
        altair.Tooltip(
            "q3_deviation_mwh:Q", title="75th percentile, MWh", format=".1f"
        ),
    ]
    spread = keyed(
        altair.Chart().mark_rule(strokeWidth=8, strokeCap="round"), Q3_KEY_SPREAD
    ).encode(
        x=altair.X("q1_deviation_mwh:Q", axis=x_axis),
        x2="q3_deviation_mwh:Q",
        y=fuel_group,
        stroke=key_stroke(keys),
        tooltip=tooltip,
    )
    middle = keyed(
        altair.Chart().mark_point(filled=False, size=80, strokeWidth=2, opacity=1),
        Q3_KEY_MEDIAN,
    ).encode(
        x="median_deviation_mwh:Q", y=fuel_group, tooltip=tooltip, **keyed_points(keys)
    )
    chart = (
        altair.layer(zero_line(), spread, middle, data=data)
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES)
        )
        .properties(
            **page_layout(Q3_TITLE, Q3_READING, len(chart_rows) * ROW_STEP_PIXELS)
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


# Q12: how much of the gap NESO instructed


def instructed_share_rows(rows):
    """Per fuel group, largest gap first, the two parts and the instructed
    share; closed units and links are left out."""
    chart_rows = []
    for row in rows:
        if row["fuel_group"] in CLOSED_UNITS_AND_LINKS:
            continue
        chart_rows.append(
            {
                "fuel_group": row["fuel_group"],
                "unit_periods": row["unit_periods"],
                "instructed_mwh": row["abs_instructed_deviation_mwh"],
                "rest_mwh": row["abs_unexplained_shortfall_mwh"],
                "parts_mwh": str(
                    Decimal(row["abs_instructed_deviation_mwh"])
                    + Decimal(row["abs_unexplained_shortfall_mwh"])
                ),
                "instructed_share_pct": fixed(
                    Decimal(100) * Decimal(row["instructed_share_of_abs_deviation"]), 4
                ),
            }
        )
    return sorted(chart_rows, key=largest_parts_first)


def largest_parts_first(row):
    return (-Decimal(row["parts_mwh"]), row["fuel_group"])


def write_q12_chart(rows, output):
    name = "q12_instructed_share_by_fuel_group"
    chart_rows = instructed_share_rows(rows)
    columns = list(chart_rows[0])
    csv_name = write_chart_csv(output, name, BMRS_ATTRIBUTION, columns, chart_rows)

    figure, axes = new_figure(len(chart_rows))
    ends = stacked_bars(
        axes, chart_rows, Q12_PARTS, [RESULT_COLOUR, REST_COLOUR], 1_000_000
    )
    for position, row in enumerate(chart_rows):
        share = float(row["instructed_share_pct"])
        bar_label(axes, position, ends[position], f"{share:.0f}% instructed")
    labels = []
    for row in chart_rows:
        labels.append(fuel_group_name(row["fuel_group"]))
    row_labels(axes, labels)
    axes.margins(x=0.1)
    axes.set_xlabel("Size of each part of the gap between plan and meter, TWh")
    png_title(
        axes,
        Q12_TITLE,
        ["Instructed share at each bar's end; 22 August 2025 to 26 September 2026"],
    )
    save_figure(figure, output / f"{name}.png", legend_handles(axes))

    data = csv_data(
        csv_name, ["instructed_mwh", "rest_mwh", "parts_mwh", "instructed_share_pct"]
    )
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("part_name:N", title="Part"),
        altair.Tooltip("scaled:Q", title="TWh", format=".2f"),
        altair.Tooltip(
            "instructed_share_pct:Q", title="Instructed share, %", format=".0f"
        ),
    ]
    bars = stacked_parts(
        "fuel_group_name",
        Q12_PARTS,
        [RESULT_COLOUR, REST_COLOUR],
        ["Size of each part of the gap,", "terawatt-hours (TWh)"],
        tooltip,
        1_000_000,
    )
    shares = (
        altair.Chart()
        .transform_calculate(
            parts_twh="datum.parts_mwh / 1000000",
            share_label=("format(datum.instructed_share_pct, '.0f') + '% instructed'"),
        )
        .mark_text(align="left", dx=LABEL_GAP_PIXELS)
        .encode(
            x="parts_twh:Q",
            y=row_axis("fuel_group_name"),
            text="share_label:N",
        )
    )
    chart = (
        altair.layer(bars, shares, data=data)
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES)
        )
        .properties(
            **page_layout(Q12_TITLE, Q12_READING, len(chart_rows) * ROW_STEP_PIXELS)
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


# Q5: under-delivery by whether an outage was published


def summed_mwh(by_class, outage_classes):
    total = Decimal(0)
    for outage_class in outage_classes:
        row = by_class.get(outage_class)
        if row is not None:
            total += Decimal(row["positive_shortfall_mwh"])
    return total


def notice_rows(rows):
    """Per fuel group, largest first, under-delivery by notice and the share
    with one; closed units and links are left out."""
    by_group = {}
    for row in matching_rows(rows, "section", "2"):
        fuel_group = row["fuel_group"]
        if fuel_group in CLOSED_UNITS_AND_LINKS:
            continue
        if fuel_group not in by_group:
            by_group[fuel_group] = {}
        by_group[fuel_group][row["item"]] = row
    chart_rows = []
    for fuel_group, by_class in by_group.items():
        planned = summed_mwh(by_class, Q5_PLANNED_CLASSES)
        unplanned = summed_mwh(by_class, Q5_UNPLANNED_CLASSES)
        no_notice = summed_mwh(by_class, Q5_NO_NOTICE_CLASSES)
        total = planned + unplanned + no_notice
        chart_rows.append(
            {
                "fuel_group": fuel_group,
                "planned_mwh": str(planned),
                "unplanned_mwh": str(unplanned),
                "no_notice_mwh": str(no_notice),
                "total_mwh": str(total),
                "with_notice_pct": fixed(percentage(planned + unplanned, total), 4),
            }
        )
    return sorted(chart_rows, key=largest_total_first)


def largest_total_first(row):
    return (-Decimal(row["total_mwh"]), row["fuel_group"])


def share_with_a_notice(rows):
    """For all units together, the share of under-delivery with a notice."""
    overall = {}
    for row in matching_rows(rows, "section", "1"):
        overall[row["item"]] = row
    with_notice = summed_mwh(overall, Q5_PLANNED_CLASSES + Q5_UNPLANNED_CLASSES)
    total = Decimal(row_for(rows, "1", "total")["positive_shortfall_mwh"])
    return percentage(with_notice, total)


def write_q5_chart(rows, output):
    name = "q5_under_delivery_by_outage_notice"
    chart_rows = notice_rows(rows)
    title = Q5_TITLE.format(share=fixed(share_with_a_notice(rows), 0))
    columns = list(chart_rows[0])
    csv_name = write_chart_csv(output, name, BMRS_ATTRIBUTION, columns, chart_rows)
    colours = [PLANNED_COLOUR, UNPLANNED_COLOUR, REST_COLOUR]

    figure, axes = new_figure(len(chart_rows))
    ends = stacked_bars(axes, chart_rows, Q5_SEGMENTS, colours, 1_000_000)
    for position, row in enumerate(chart_rows):
        share = float(row["with_notice_pct"])
        bar_label(axes, position, ends[position], f"{share:.0f}% with notice")
    labels = []
    for row in chart_rows:
        labels.append(fuel_group_name(row["fuel_group"]))
    row_labels(axes, labels)
    axes.margins(x=0.1)
    axes.set_xlabel("Under-delivery, TWh")
    png_title(
        axes,
        title,
        [
            "Share with a published outage at each bar's end; 22 August 2025 to "
            "27 September 2026"
        ],
    )
    save_figure(figure, output / f"{name}.png", legend_handles(axes))

    data = csv_data(csv_name, [*Q5_SEGMENTS, "total_mwh", "with_notice_pct"])
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("part_name:N", title="Outage notice"),
        altair.Tooltip("scaled:Q", title="TWh", format=".2f"),
        altair.Tooltip("with_notice_pct:Q", title="With a notice, %", format=".0f"),
    ]
    bars = stacked_parts(
        "fuel_group_name",
        Q5_SEGMENTS,
        colours,
        ["Under-delivery, terawatt-hours (TWh)"],
        tooltip,
        1_000_000,
    )
    shares = (
        altair.Chart()
        .transform_calculate(
            total_twh="datum.total_mwh / 1000000",
            share_label=("format(datum.with_notice_pct, '.0f') + '% with notice'"),
        )
        .mark_text(align="left", dx=LABEL_GAP_PIXELS)
        .encode(x="total_twh:Q", y=row_axis("fuel_group_name"), text="share_label:N")
    )
    chart = (
        altair.layer(bars, shares, data=data)
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES)
        )
        .properties(**page_layout(title, Q5_READING, len(chart_rows) * ROW_STEP_PIXELS))
    )
    write_spec(chart, output / f"{name}.vl.json")


# Q23: how far units miss their plans when not instructed


def monthly_gaps(rows):
    """Per fuel group, its rows by month; closed units are left out, and a
    group without a colour is refused."""
    by_group = {}
    for row in matching_rows(rows, "section", "1"):
        fuel_group = row["fuel_group"]
        if fuel_group in CLOSED_UNITS:
            continue
        if fuel_group not in FUEL_GROUP_COLOURS:
            raise ValueError(f"Fuel group {fuel_group!r} has no colour")
        if fuel_group not in by_group:
            by_group[fuel_group] = {}
        by_group[fuel_group][row["settlement_month"]] = row
    return by_group


def chart_month_rows(by_month, months):
    """The group's rows for the months, in order; a missing month is refused,
    so no line is drawn across a gap."""
    rows = []
    for month in months:
        if month not in by_month:
            raise ValueError(f"The saved output has no row for month {month}")
        rows.append(by_month[month])
    return rows


def column_values(rows, column):
    values = []
    for row in rows:
        values.append(Decimal(row[column]))
    return values


def fewest_units(rows):
    return min(column_values(rows, "units"))


def typical_percentile(rows):
    """The median of the monthly 90th percentiles, for groups thick enough for
    one."""
    if fewest_units(rows) < Q23_UNITS_FOR_A_PERCENTILE:
        return ""
    return fixed(statistics.median(column_values(rows, "p90_absolute_pct")), 4)


def one_decimal_label(value, suffix=""):
    if value == "":
        return ""
    return fixed(Decimal(value), 1) + suffix


def groups_in_colour_order(by_group):
    groups = []
    for fuel_group in FUEL_GROUP_COLOURS:
        if fuel_group in by_group:
            groups.append(fuel_group)
    return groups


def refused_past(value, top):
    if Decimal(value) > top:
        raise ValueError(f"{value} lies past the chart's scale, which ends at {top}")


def typical_gap_rows(rows):
    """Per fuel group, largest first: the median of its 12 whole months'
    median gaps and of their 90th percentiles, with their labels."""
    chart_rows = []
    for fuel_group, by_month in monthly_gaps(rows).items():
        whole = chart_month_rows(by_month, Q23_WHOLE_MONTHS)
        typical = fixed(
            statistics.median(column_values(whole, "median_absolute_pct")), 4
        )
        one_in_ten = typical_percentile(whole)
        for value in (typical, one_in_ten):
            if value:
                refused_past(value, Q23_TYPICAL_AXIS_TOP)
        chart_rows.append(
            {
                "fuel_group": fuel_group,
                "fewest_units": str(fewest_units(whole)),
                "typical_pct": typical,
                "typical_label": one_decimal_label(typical, "%"),
                "one_in_ten_pct": one_in_ten,
                "one_in_ten_label": one_decimal_label(one_in_ten, "%"),
            }
        )
    return sorted(chart_rows, key=largest_typical_gap_first)


def largest_typical_gap_first(row):
    return (-Decimal(row["typical_pct"]), row["fuel_group"])


def end_name_heights(end_values):
    """Where each line's name sits at its end, top first: at the line's end,
    moved only as far as keeps END_NAME_GAP between names and every name
    above the floor."""
    ordered = sorted(end_values.items(), key=largest_end_first)
    heights = []
    for position, (_fuel_group, value) in enumerate(ordered):
        height = value
        if position > 0:
            height = min(value, heights[-1] - Q23_END_NAME_GAP)
        heights.append(height)
    for position in range(len(heights) - 1, -1, -1):
        lowest = Q23_END_NAME_FLOOR
        if position < len(heights) - 1:
            lowest = heights[position + 1] + Q23_END_NAME_GAP
        heights[position] = max(heights[position], lowest)
    placed = {}
    for (fuel_group, _value), height in zip(ordered, heights, strict=True):
        placed[fuel_group] = fixed(height, 3)
    return placed


def largest_end_first(item):
    fuel_group, value = item
    return (-value, fuel_group)


def month_name(month):
    year, number, _day = month.split("-")
    return f"{MONTH_NAMES[int(number) - 1]} {year}"


def monthly_gap_rows(rows):
    """Per fuel group, in colour order, its median gap in each chart month,
    the provisional month marked, and where its line's name sits."""
    by_group = monthly_gaps(rows)
    groups = groups_in_colour_order(by_group)
    end_values = {}
    for fuel_group in groups:
        end_row = chart_month_rows(by_group[fuel_group], [Q23_PROVISIONAL_MONTH])[0]
        end_values[fuel_group] = Decimal(end_row["median_absolute_pct"])
    end_heights = end_name_heights(end_values)
    chart_rows = []
    for fuel_group in groups:
        month_rows = chart_month_rows(by_group[fuel_group], Q23_CHART_MONTHS)
        for month, row in zip(Q23_CHART_MONTHS, month_rows, strict=True):
            refused_past(row["median_absolute_pct"], Q23_MONTHLY_AXIS_TOP)
            provisional = month == Q23_PROVISIONAL_MONTH
            end_name = ""
            if provisional:
                end_name = end_heights[fuel_group]
            chart_rows.append(
                {
                    "fuel_group": fuel_group,
                    "month": month,
                    "month_name": month_name(month),
                    "gap_pct": row["median_absolute_pct"],
                    "gap_label": one_decimal_label(row["median_absolute_pct"]),
                    "units": row["units"],
                    "provisional": yes_or_no(provisional),
                    "dashed": yes_or_no(month >= Q23_DASHED_FROM_MONTH),
                    "end_name_pct": end_name,
                }
            )
    return chart_rows


def yes_or_no(flag):
    if flag:
        return "yes"
    return "no"


def wind_and_the_rest(rows, value_field):
    """The emphasised group's values and every other group's, checked to stay
    under the title's figure for them."""
    emphasised = []
    others = []
    for row in rows:
        if row["fuel_group"] == Q23_EMPHASISED_GROUP:
            emphasised.append(Decimal(row[value_field]))
        else:
            others.append(Decimal(row[value_field]))
    if not emphasised:
        raise ValueError(f"The saved output has no {Q23_EMPHASISED_GROUP} rows")
    return emphasised, others


def typical_title(chart_rows):
    """The title, once the data show wind furthest from plan."""
    wind, others = wind_and_the_rest(chart_rows, "typical_pct")
    for value in others:
        if value >= wind[0]:
            raise ValueError(f"A median gap of {value} breaks the title's claim")
    return Q23_TYPICAL_TITLE


def monthly_title(chart_rows):
    """The title, once the data show wind furthest from plan in every whole
    month."""
    whole_months = []
    for row in chart_rows:
        if row["provisional"] == "no":
            whole_months.append(row)
    by_month = {}
    for row in whole_months:
        by_month.setdefault(row["month"], []).append(row)
    for month, rows in by_month.items():
        wind, others = wind_and_the_rest(rows, "gap_pct")
        for value in others:
            if value >= wind[0]:
                raise ValueError(
                    f"A gap of {value} in {month} breaks the title's claim"
                )
    return Q23_MONTHLY_TITLE


def light_colour(fuel_group):
    light, _dark = FUEL_GROUP_COLOURS[fuel_group]
    return light


def fuel_group_colour(channel):
    """A mark's colour by fuel group, from the scheme the page registers for
    its colour mode; no legend, as the chart names each group beside it."""
    names = []
    for fuel_group in FUEL_GROUP_COLOURS:
        names.append(fuel_group_name(fuel_group))
    # Altair knows only Vega's own scheme names, so the name goes to Vega as
    # an expression.
    scheme = altair.ExprRef(expr=json.dumps(FUEL_GROUP_SCHEME))
    return channel(
        "fuel_group_name:N",
        scale=altair.Scale(domain=names, scheme=scheme),
        legend=None,
    )


def draw_typical_gaps(rows, title, path):
    figure, axes = new_figure(len(rows))
    for position, row in enumerate(rows):
        colour = light_colour(row["fuel_group"])
        typical = float(row["typical_pct"])
        if row["one_in_ten_pct"]:
            one_in_ten = float(row["one_in_ten_pct"])
            axes.barh(
                position,
                one_in_ten - typical,
                left=typical,
                height=0.15,
                color=colour,
                alpha=0.4,
            )
            bar_label(axes, position, one_in_ten, row["one_in_ten_label"])
        axes.barh(position, typical, height=0.5, color=colour)
        axes.annotate(
            row["typical_label"],
            (typical, position - 0.3),
            xytext=(LABEL_GAP_PIXELS, 0),
            textcoords="offset points",
            va="bottom",
            fontsize=8,
            color="#3d444d",
        )
    labels = []
    for row in rows:
        labels.append(fuel_group_name(row["fuel_group"]))
    row_labels(axes, labels)
    axes.set_xlim(0, Q23_TYPICAL_AXIS_TOP)
    axes.set_xlabel("Gap between plan and meter, % of capacity")
    png_title(axes, title, [Q23_TYPICAL_READING])
    handles = [
        Line2D([], [], color=COMPARISON_COLOUR, linewidth=8, label=Q23_KEY_TYPICAL),
        Line2D(
            [],
            [],
            color=COMPARISON_COLOUR,
            alpha=0.4,
            linewidth=3,
            label=Q23_KEY_ONE_IN_TEN,
        ),
    ]
    save_figure(figure, path, handles)


def write_q23_typical_chart(rows, output):
    name = "q23_typical_gap_by_fuel_group"
    chart_rows = typical_gap_rows(rows)
    title = typical_title(chart_rows)
    columns = list(chart_rows[0])
    csv_name = write_chart_csv(output, name, BMRS_ATTRIBUTION, columns, chart_rows)
    draw_typical_gaps(chart_rows, title, output / f"{name}.png")

    data = csv_data(csv_name, ["typical_pct", "one_in_ten_pct"])
    # Largest gap first, as the CSV; the first layer holds only the groups
    # with a pale line, so the order is given.
    names_in_order = []
    for row in chart_rows:
        names_in_order.append(fuel_group_name(row["fuel_group"]))
    fuel_group = row_axis("fuel_group_name", names_in_order)
    gap_axis = altair.X(
        "typical_pct:Q",
        scale=altair.Scale(domain=[0, Q23_TYPICAL_AXIS_TOP], nice=False),
        axis=axis_title(
            [
                "Gap between plan and meter, % of capacity",
                "further right: further from plan",
            ]
        ),
    )
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("typical_label:N", title="Median gap"),
        altair.Tooltip("one_in_ten_label:N", title="90th percentile"),
        altair.Tooltip("fewest_units:N", title="Units, fewest in a month"),
    ]
    keys = {
        Q23_KEY_TYPICAL: key_mark(COMPARISON_COLOUR, COMPARISON_COLOUR, BAR_SYMBOL),
        Q23_KEY_ONE_IN_TEN: key_mark(PALE_KEY_COLOUR, PALE_KEY_COLOUR, THIN_BAR_SYMBOL),
    }
    has_pale_line = "isValid(datum.one_in_ten_pct)"
    pale_line = keyed(
        altair.Chart()
        .transform_filter(has_pale_line)
        .mark_bar(height=6, opacity=0.4, cornerRadiusEnd=3),
        Q23_KEY_ONE_IN_TEN,
    ).encode(
        x=gap_axis,
        x2="one_in_ten_pct:Q",
        y=fuel_group,
        fill=fuel_group_colour(altair.Fill),
        tooltip=tooltip,
    )
    bars = keyed(
        altair.Chart().mark_bar(height=16, cornerRadiusEnd=4), Q23_KEY_TYPICAL
    ).encode(
        x=gap_axis,
        y=fuel_group,
        fill=fuel_group_colour(altair.Fill),
        tooltip=tooltip,
    )
    # The bar's value sits above a pale line that runs on from its end.
    typical_labels = (
        altair.Chart()
        .mark_text(
            align="left",
            dx=LABEL_GAP_PIXELS,
            baseline=altair.ExprRef(expr=f"{has_pale_line} ? 'bottom' : 'middle'"),
            dy=altair.ExprRef(expr=f"{has_pale_line} ? -5 : 0"),
        )
        .encode(x="typical_pct:Q", y=fuel_group, text="typical_label:N")
    )
    one_in_ten_labels = (
        altair.Chart()
        .transform_filter(has_pale_line)
        .mark_text(align="left", dx=LABEL_GAP_PIXELS)
        .encode(x="one_in_ten_pct:Q", y=fuel_group, text="one_in_ten_label:N")
    )
    key = keyed(
        drawn_once().mark_point(opacity=0, filled=True), Q23_KEY_TYPICAL
    ).encode(
        color=altair.Color(
            f"{KEY_FIELD}:N", scale=key_scale(keys, "fill"), legend=key_legend()
        ),
        shape=key_shape(keys),
    )
    chart = (
        altair.layer(
            pale_line,
            bars,
            typical_labels,
            one_in_ten_labels,
            key,
            data=data,
        )
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES)
        )
        .properties(
            **page_layout(title, Q23_TYPICAL_READING, len(chart_rows) * ROW_STEP_PIXELS)
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


def draw_monthly_gaps(rows, title, path):
    figure, axes = pyplot.subplots(figsize=(FIGURE_WIDTH_INCHES, 4.5))
    quiet_frame(axes)
    axes.grid(axis="y", color=GRID_COLOUR, linewidth=0.8)
    last = len(Q23_CHART_MONTHS) - 1
    axes.axvspan(last - 0.5, last + 0.4, color=COMPARISON_COLOUR, alpha=0.15)
    axes.annotate(
        "provisional",
        (last - 0.5, Q23_MONTHLY_AXIS_TOP),
        xytext=(-2, -2),
        textcoords="offset points",
        ha="right",
        va="top",
        fontsize=8,
        fontstyle="italic",
        color="#3d444d",
    )
    by_group = {}
    for row in rows:
        by_group.setdefault(row["fuel_group"], []).append(row)
    for fuel_group, group_rows in by_group.items():
        colour = light_colour(fuel_group)
        width = 2.5 if fuel_group == Q23_EMPHASISED_GROUP else 1.5
        values = []
        for row in group_rows:
            values.append(float(row["gap_pct"]))
        axes.plot(range(last), values[:last], color=colour, linewidth=width)
        axes.plot(
            [last - 1, last],
            values[last - 1 :],
            color=colour,
            linewidth=width,
            linestyle=(0, (3, 2)),
        )
        axes.plot(
            last, values[last], "o", markerfacecolor="white", markeredgecolor=colour
        )
        for position, row in enumerate(group_rows):
            if not row["end_name_pct"]:
                continue
            end_name = float(row["end_name_pct"])
            axes.plot(
                [position + 0.1, position + 0.3],
                [values[position], end_name],
                color=colour,
            )
            axes.annotate(
                fuel_group_name(fuel_group),
                (position + 0.3, end_name),
                xytext=(LABEL_GAP_PIXELS, 0),
                textcoords="offset points",
                va="center",
                fontsize=8,
                fontweight="bold" if fuel_group == Q23_EMPHASISED_GROUP else "normal",
                color="#3d444d",
            )
    month_labels = []
    for month in Q23_CHART_MONTHS:
        month_labels.append(month_name(month).replace(" ", "\n"))
    axes.set_xticks(range(len(Q23_CHART_MONTHS)), month_labels, fontsize=7)
    axes.set_xlim(-0.5, last + 2)
    axes.set_ylim(0, Q23_MONTHLY_AXIS_TOP)
    axes.set_ylabel("Median gap, % of capacity")
    png_title(axes, title, [Q23_MONTHLY_READING])
    save_figure(figure, path)


def utc_date(day):
    year, month, date = day.split("-")
    return altair.DateTime(year=int(year), month=int(month), date=int(date), utc=True)


def write_q23_monthly_chart(rows, output):
    name = "q23_monthly_gap_by_fuel_group"
    chart_rows = monthly_gap_rows(rows)
    title = monthly_title(chart_rows)
    columns = list(chart_rows[0])
    csv_name = write_chart_csv(output, name, BMRS_ATTRIBUTION, columns, chart_rows)
    draw_monthly_gaps(chart_rows, title, output / f"{name}.png")

    data = altair.UrlData(
        url=f"{SPEC_DATA_FOLDER}/{csv_name}",
        format=altair.CsvDataFormat(
            type="csv",
            parse={
                "month": "utc:'%Y-%m-%d'",
                "gap_pct": "number",
                "end_name_pct": "number",
            },
        ),
    )
    month_ticks = []
    for month in Q23_CHART_MONTHS:
        month_ticks.append(utc_date(month))
    # The year under September and January.
    month_label = (
        "indexof(['01', '09'], utcFormat(datum.value, '%m')) >= 0"
        " ? [utcFormat(datum.value, '%b'), utcFormat(datum.value, '%Y')]"
        " : utcFormat(datum.value, '%b')"
    )
    # The scale and axis are set once, on the whole months' lines.
    month_axis = altair.X(
        "month:T",
        scale=altair.Scale(type="utc", nice=False, padding=Q23_TIME_PADDING_PIXELS),
        axis=altair.Axis(
            values=month_ticks,
            labelExpr=month_label,
            labelOverlap="parity",
            title=None,
            grid=False,
        ),
    )
    month = altair.X("month:T")
    gap = altair.Y(
        "gap_pct:Q",
        scale=altair.Scale(domain=[0, Q23_MONTHLY_AXIS_TOP], nice=False),
        axis=altair.Axis(
            values=list(range(0, Q23_MONTHLY_AXIS_TOP + 1, 2)),
            title=["Median gap, % of capacity", "higher: further from plan"],
        ),
    )
    end_name = altair.Y("end_name_pct:Q")
    colour = fuel_group_colour(altair.Stroke)
    emphasised = f"datum.fuel_group == {json.dumps(Q23_EMPHASISED_GROUP)}"
    line_width = altair.condition(emphasised, altair.value(3), altair.value(2))
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("month_name:N", title="Month"),
        altair.Tooltip("gap_label:N", title="Median gap, %"),
        altair.Tooltip("units:N", title="Units"),
    ]
    is_whole_month = "datum.provisional == 'no'"
    is_provisional = "datum.provisional == 'yes'"
    band_start = utc_date(Q23_BAND_FROM)
    band = (
        drawn_once()
        .mark_rect(fill=BAND_FILL)
        .encode(
            x=altair.XDatum(band_start, type="temporal"),
            x2=altair.X2Datum(utc_date(Q23_BAND_TO)),
            y=altair.value(0),
            y2=altair.value("height"),
        )
        .properties(name="provisional band")
    )
    band_word = (
        drawn_once()
        .mark_text(align="right", baseline="top", dx=-2, dy=4, fontStyle="italic")
        .encode(
            x=altair.XDatum(band_start, type="temporal"),
            y=altair.value(0),
            text=altair.value("provisional"),
        )
    )
    whole_months = (
        altair.Chart()
        .transform_filter(is_whole_month)
        .mark_line(strokeCap="round", strokeJoin="round")
        .encode(
            x=month_axis, y=gap, stroke=colour, strokeWidth=line_width, tooltip=tooltip
        )
    )
    dashed_end = (
        altair.Chart()
        .transform_filter("datum.dashed == 'yes'")
        .mark_line(strokeDash=[4, 3])
        .encode(x=month, y=gap, stroke=colour, strokeWidth=line_width)
    )
    to_its_name = (
        altair.Chart()
        .transform_filter(is_provisional)
        .mark_rule(xOffset=7, x2Offset=15, strokeWidth=1.5)
        .encode(
            x=month,
            y=gap,
            x2=altair.X2("month"),
            y2=altair.Y2("end_name_pct"),
            stroke=colour,
        )
        .properties(name="connector")
    )
    hollow_end = (
        altair.Chart()
        .transform_filter(is_provisional)
        .mark_point(filled=False, size=60, strokeWidth=2, opacity=1)
        .encode(x=month, y=gap, stroke=colour, tooltip=tooltip)
    )
    names = (
        altair.Chart()
        .transform_filter(is_provisional)
        .mark_text(
            align="left",
            xOffset=18,
            fontWeight=altair.ExprRef(expr=f"{emphasised} ? 600 : 400"),
        )
        .encode(x=month, y=end_name, text="fuel_group_name:N")
    )
    # Every month's value on hover, over the line drawn for it.
    hover = (
        altair.Chart()
        .mark_point(opacity=0, size=260, filled=True)
        .encode(x=month, y=gap, stroke=colour, tooltip=tooltip)
    )
    chart = (
        altair.layer(
            band,
            band_word,
            whole_months,
            dashed_end,
            to_its_name,
            hollow_end,
            names,
            hover,
            data=data,
        )
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES)
        )
        .properties(
            **page_layout(title, Q23_MONTHLY_READING, Q23_MONTHLY_HEIGHT_PIXELS)
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


# Q24: the share NESO flags as managing the network


def flagged_share_rows(rows):
    chart_rows = []
    for row in matching_rows(rows, "section", "2"):
        flagged = Decimal(row["so_flagged_volume_pct"])
        chart_rows.append(
            {
                "fuel_group": row["fuel_group"],
                "acceptance_volume_mwh": row["acceptance_volume_mwh"],
                "flagged_pct": str(flagged),
                "not_flagged_pct": str(Decimal(100) - flagged),
            }
        )
    return sorted(chart_rows, key=most_flagged_first)


def most_flagged_first(row):
    return (-Decimal(row["flagged_pct"]), row["fuel_group"])


def write_q24_chart(rows, output):
    name = "q24_flagged_share_by_fuel_group"
    chart_rows = flagged_share_rows(rows)
    columns = list(chart_rows[0])
    csv_name = write_chart_csv(output, name, BMRS_ATTRIBUTION, columns, chart_rows)

    figure, axes = new_figure(len(chart_rows))
    stacked_bars(axes, chart_rows, Q24_PARTS, [RESULT_COLOUR, REST_COLOUR], 1)
    labels = []
    for position, row in enumerate(chart_rows):
        flagged = float(row["flagged_pct"])
        axes.annotate(
            f"{flagged:.0f}%",
            (flagged, position),
            xytext=(-LABEL_GAP_PIXELS, 0),
            textcoords="offset points",
            ha="right",
            va="center",
            fontsize=8,
            color=INSIDE_LABEL_COLOUR,
        )
        volume = float(row["acceptance_volume_mwh"]) / 1_000_000
        labels.append(f"{fuel_group_name(row['fuel_group'])}|{volume:.1f} TWh")
    row_labels(axes, labels)
    axes.set_xlim(0, 100)
    axes.set_xticks(SHARE_TICKS)
    axes.set_xlabel(Q24_AXIS_TITLE)
    png_title(axes, Q24_TITLE, ["22 August 2025 to 30 September 2026"])
    save_figure(figure, output / f"{name}.png", legend_handles(axes))

    data = csv_data(
        csv_name, ["acceptance_volume_mwh", "flagged_pct", "not_flagged_pct"]
    )
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("part_name:N", title="Part"),
        altair.Tooltip("scaled:Q", title="Share, %", format=".1f"),
        altair.Tooltip("volume_twh:Q", title="Energy moved, TWh", format=".1f"),
    ]
    bars = stacked_parts(
        "label",
        Q24_PARTS,
        [RESULT_COLOUR, REST_COLOUR],
        [Q24_AXIS_TITLE],
        tooltip,
        1,
    ).encode(
        x=altair.X(
            "scaled:Q",
            stack="zero",
            scale=altair.Scale(domain=[0, 100]),
            axis=altair.Axis(values=SHARE_TICKS, title=[Q24_AXIS_TITLE]),
        )
    )
    shares = (
        altair.Chart()
        .transform_calculate(share_label="format(datum.flagged_pct, '.0f') + '%'")
        .mark_text(align="right", dx=-LABEL_GAP_PIXELS, color=INSIDE_LABEL_COLOUR)
        .encode(x="flagged_pct:Q", y=row_axis("label"), text="share_label:N")
    )
    chart = (
        altair.layer(bars, shares, data=data)
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES)
        )
        .transform_calculate(volume_twh="datum.acceptance_volume_mwh / 1000000")
        .transform_calculate(
            label="datum.fuel_group_name + '|' + format(datum.volume_twh, '.1f') + ' TWh'"
        )
        .properties(
            **page_layout(
                Q24_TITLE, Q24_READING, len(chart_rows) * TWO_LINE_ROW_STEP_PIXELS
            )
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


# Q25: how many instructions are short


def short_share(row):
    return percentage(Decimal(row["under_10_minutes"]), Decimal(row["n"]))


def measure_rows(rows, measure):
    by_fuel_group = {}
    for row in matching_rows(rows, "section", "2"):
        if row["measure"] == measure:
            by_fuel_group[row["fuel_group"]] = row
    if not by_fuel_group:
        raise ValueError(f"The saved output has no section 2 row for {measure!r}")
    return by_fuel_group


def short_instruction_rows(corrected_rows, first_read_rows):
    """Per fuel group, most short first: the share of instructions under 10
    minutes joined with their follow-ons and on their own, from the
    corrected output, and the joined share as first read."""
    joined = measure_rows(corrected_rows, Q25_JOINED_MEASURE)
    own_span = measure_rows(corrected_rows, Q25_OWN_SPAN_MEASURE)
    first_read = measure_rows(first_read_rows, Q25_JOINED_MEASURE)
    chart_rows = []
    for fuel_group, joined_row in joined.items():
        chart_rows.append(
            {
                "fuel_group": fuel_group,
                "instructions": joined_row["n"],
                "short_joined_pct": fixed(short_share(joined_row), 6),
                "short_on_its_own_pct": fixed(short_share(own_span[fuel_group]), 6),
                "short_joined_as_first_read_pct": fixed(
                    short_share(first_read[fuel_group]), 6
                ),
            }
        )
    return sorted(chart_rows, key=most_short_first)


def most_short_first(row):
    return (-Decimal(row["short_joined_pct"]), row["fuel_group"])


def write_q25_chart(corrected_rows, first_read_rows, output):
    name = "q25_short_instructions_by_fuel_group"
    chart_rows = short_instruction_rows(corrected_rows, first_read_rows)
    columns = list(chart_rows[0])
    csv_name = write_chart_csv(output, name, BMRS_ATTRIBUTION, columns, chart_rows)

    figure, axes = new_figure(len(chart_rows))
    labels = []
    for position, row in enumerate(chart_rows):
        joined = float(row["short_joined_pct"])
        own = float(row["short_on_its_own_pct"])
        axes.plot(
            [joined, own], [position, position], color=COMPARISON_COLOUR, linewidth=1.5
        )
        axes.plot(
            own,
            position,
            "o",
            markerfacecolor="none",
            markeredgecolor=COMPARISON_COLOUR,
        )
        axes.plot(joined, position, "o", color=RESULT_COLOUR)
        axes.annotate(
            f"{joined:.2g}%",
            (joined, position),
            xytext=(0, 7),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color="#3d444d",
        )
        labels.append(fuel_group_name(row["fuel_group"]))
    row_labels(axes, labels)
    axes.set_xscale("log")
    axes.set_xlim(LOG_SHARE_TICKS[0], LOG_SHARE_TICKS[-1])
    axes.set_xticks(LOG_SHARE_TICKS, ["0.01%", "0.1%", "1%", "10%", "100%"])
    axes.set_xlabel("Instructions under 10 minutes, % (log scale)")
    png_title(
        axes,
        Q25_TITLE,
        [
            "Dot: joined with follow-on instructions, Elexon's test; ring: the "
            "instruction on its own; 22 August 2025 to 30 September 2026"
        ],
    )
    save_figure(figure, output / f"{name}.png")

    data = csv_data(
        csv_name,
        [
            "instructions",
            "short_joined_pct",
            "short_on_its_own_pct",
            "short_joined_as_first_read_pct",
        ],
    )
    fuel_group = row_axis("fuel_group_name")
    tooltip = [
        altair.Tooltip("fuel_group_name:N", title="Fuel group"),
        altair.Tooltip("instructions:Q", title="Instructions", format=","),
        altair.Tooltip("short_joined_pct:Q", title="Short, joined, %", format=".2r"),
        altair.Tooltip(
            "short_on_its_own_pct:Q", title="Short on its own, %", format=".2r"
        ),
    ]
    log_axis = altair.X(
        "short_joined_pct:Q",
        scale=altair.Scale(
            type="log", domain=[LOG_SHARE_TICKS[0], LOG_SHARE_TICKS[-1]]
        ),
        axis=altair.Axis(
            values=LOG_SHARE_TICKS,
            labelExpr="format(datum.value, '~r') + '%'",
            title=["Instructions under 10 minutes, % (log scale)"],
        ),
    )
    keys = {
        "Joined with follow-ons (Elexon's test)": key_mark(
            RESULT_COLOUR, RESULT_COLOUR, "circle"
        ),
        "Each instruction on its own": key_mark(COMPARISON_COLOUR, NO_FILL, "circle"),
    }
    link = (
        altair.Chart()
        .mark_rule(color=COMPARISON_COLOUR, strokeWidth=1.5)
        .encode(x=log_axis, x2="short_on_its_own_pct:Q", y=fuel_group)
        .properties(name="connector")
    )
    on_its_own = keyed(
        altair.Chart().mark_point(filled=False, size=70, strokeWidth=2, opacity=1),
        "Each instruction on its own",
    ).encode(
        x="short_on_its_own_pct:Q",
        y=fuel_group,
        tooltip=tooltip,
        **keyed_points(keys),
    )
    joined = keyed(
        altair.Chart().mark_point(filled=False, size=80, strokeWidth=2, opacity=1),
        "Joined with follow-ons (Elexon's test)",
    ).encode(
        x="short_joined_pct:Q", y=fuel_group, tooltip=tooltip, **keyed_points(keys)
    )
    joined_label = label_above("short_joined_pct:Q", fuel_group, "joined_label")
    chart = (
        altair.layer(link, on_its_own, joined, joined_label, data=data)
        .transform_calculate(
            fuel_group_name=display_name("fuel_group", FUEL_GROUP_NAMES),
            joined_label="format(datum.short_joined_pct, '.2r') + '%'",
        )
        .properties(
            **page_layout(
                Q25_TITLE, Q25_READING, len(chart_rows) * TWO_LINE_ROW_STEP_PIXELS
            )
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


# Q28: shortfall by size of the carbon miss


def miss_range(result):
    """The fifth's range of misses from the saved text, as (lower, upper)."""
    lower, upper = result.removeprefix(Q28_RANGE_PREFIX).split(" to ")
    return lower, upper


def fifth_label(quintile, quintile_count, result):
    """The fifth's range of misses; the last fifth's top edge is left off."""
    lower, upper = miss_range(result)
    if quintile == 1:
        return f"Smallest misses|{lower}–{upper} g"
    if quintile == quintile_count:
        return f"Largest misses|{lower} g and up"
    return f"{lower}–{upper} g"


def shortfall_index_rows(rows):
    """Per fifth of half-hours by the size of the carbon miss, the mean
    shortfall indexed to the smallest-miss fifth, with the ratio's interval
    on the largest-miss fifth."""
    fifths = matching_rows(rows, "item", Q28_QUINTILE_ITEM)
    base = Decimal(matching_rows(fifths, "quintile", "1")[0]["value"])
    low = Decimal(
        row_for(rows, "3", "bootstrap 90% interval for the ratio, low")["value"]
    )
    high = Decimal(
        row_for(rows, "3", "bootstrap 90% interval for the ratio, high")["value"]
    )
    chart_rows = []
    for row in fifths:
        quintile = int(row["quintile"])
        largest = quintile == len(fifths)
        index_low = ""
        index_high = ""
        if largest:
            index_low = fixed(Decimal(100) * low, 4)
            index_high = fixed(Decimal(100) * high, 4)
        label = fifth_label(quintile, len(fifths), row["result"])
        chart_rows.append(
            {
                "quintile": row["quintile"],
                "label": label,
                "misses": label.replace("|", ", "),
                "half_hours": row["half_hours"],
                "mean_shortfall_mwh": row["value"],
                "index": fixed(Decimal(100) * Decimal(row["value"]) / base, 4),
                "index_low": index_low,
                "index_high": index_high,
            }
        )
    return chart_rows


def draw_shortfall_index(rows, path):
    figure, axes = new_figure(len(rows))
    axes.axvline(Q28_NEEDED_INDEX, color=COMPARISON_COLOUR, linestyle="--", linewidth=1)
    axes.annotate(
        "needed for yes",
        (Q28_NEEDED_INDEX, len(rows) - 0.6),
        xytext=(-LABEL_GAP_PIXELS, 0),
        textcoords="offset points",
        ha="right",
        fontsize=8,
        color="#3d444d",
    )
    labels = []
    for position, row in enumerate(rows):
        index = float(row["index"])
        if row["index_low"]:
            axes.plot(
                [float(row["index_low"]), float(row["index_high"])],
                [position, position],
                color=RESULT_COLOUR,
                linewidth=1.5,
            )
        axes.plot(index, position, "o", color=RESULT_COLOUR)
        axes.annotate(
            f"{index:.1f}",
            (index, position),
            xytext=(0, 7),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color="#3d444d",
        )
        labels.append(row["label"])
    row_labels(axes, labels)
    axes.set_xlim(95, 130)
    axes.set_xlabel("Under-delivery not instructed, smallest-miss fifth = 100")
    png_title(
        axes,
        Q28_TITLE,
        [
            "Fifths of half-hours by the size of the stored carbon forecast's miss; "
            "the largest fifth with its 90% range"
        ],
    )
    save_figure(figure, path)


def write_q28_chart(rows, output):
    name = "q28_shortfall_index_by_miss_fifth"
    chart_rows = shortfall_index_rows(rows)
    columns = list(chart_rows[0])
    csv_name = write_chart_csv(
        output, name, BMRS_AND_CARBON_ATTRIBUTION, columns, chart_rows
    )
    draw_shortfall_index(chart_rows, output / f"{name}.png")

    data = csv_data(
        csv_name, ["mean_shortfall_mwh", "index", "index_low", "index_high"]
    )
    fifth = row_axis("label", column_of(chart_rows, "label"))
    height = len(chart_rows) * TWO_LINE_ROW_STEP_PIXELS
    index_scale = altair.Scale(domain=[95, 130], nice=False)
    index_axis = altair.X(
        "index:Q",
        scale=index_scale,
        axis=axis_title(
            [
                "Under-delivery not instructed,",
                "smallest-miss fifth = 100",
                "further right: more under-delivery",
            ]
        ),
    )
    needed_index = altair.XDatum(Q28_NEEDED_INDEX, scale=index_scale)
    keys = {
        "Mean under-delivery (index)": key_mark(RESULT_COLOUR, RESULT_COLOUR, "circle"),
        Q28_RANGE_KEY: key_mark(RESULT_COLOUR, NO_FILL, LINE_SYMBOL),
        f"Needed for yes ({Q28_NEEDED_INDEX})": key_mark(
            COMPARISON_COLOUR, NO_FILL, DASH_SYMBOL
        ),
    }
    tooltip = [
        altair.Tooltip("misses:N", title="Carbon miss"),
        altair.Tooltip("half_hours:Q", title="Half-hours", format=","),
        altair.Tooltip(
            "mean_shortfall_mwh:Q",
            title="Mean under-delivery, MWh",
            format=",.0f",
        ),
        altair.Tooltip("index:Q", title="Index", format=".1f"),
        altair.Tooltip("index_low:Q", title="90% range from", format=".1f"),
        altair.Tooltip("index_high:Q", title="90% range to", format=".1f"),
    ]
    needed = keyed(
        drawn_once().mark_rule(strokeDash=[5, 4]),
        f"Needed for yes ({Q28_NEEDED_INDEX})",
    ).encode(x=needed_index, stroke=key_stroke(keys))
    needed_label = (
        drawn_once()
        .mark_text(align="right", baseline="top", dx=-LABEL_GAP_PIXELS, dy=2)
        .encode(
            x=needed_index,
            y=altair.value(0),
            text=altair.value("needed for yes"),
        )
    )
    interval = keyed(
        altair.Chart()
        .transform_filter("isValid(datum.index_low)")
        .mark_rule(strokeWidth=2),
        Q28_RANGE_KEY,
    ).encode(
        x=altair.X("index_low:Q", scale=index_scale),
        x2="index_high:Q",
        y=fifth,
        stroke=key_stroke(keys),
    )
    dots = keyed(
        altair.Chart().mark_point(filled=False, size=80, strokeWidth=2, opacity=1),
        "Mean under-delivery (index)",
    ).encode(x=index_axis, y=fifth, tooltip=tooltip, **keyed_points(keys))
    dot_labels = label_above("index:Q", fifth, "index_label")
    chart = (
        altair.layer(needed, needed_label, interval, dots, dot_labels, data=data)
        .transform_calculate(index_label="format(datum.index, '.1f')")
        .properties(**page_layout(Q28_TITLE, Q28_READING, height))
    )
    write_spec(chart, output / f"{name}.vl.json")


# Q29: the stored forecast's miss by time of day and by month


def q29_direction():
    return f"datum.mean_error > 0 ? {json.dumps(DIRTIER)} : {json.dumps(CLEANER)}"


def direction_keys(symbol):
    return {
        DIRTIER: key_mark(DIRTIER_COLOUR, DIRTIER_COLOUR, symbol),
        CLEANER: key_mark(CLEANER_COLOUR, CLEANER_COLOUR, symbol),
    }


def sign_colour(value):
    if value > 0:
        return DIRTIER_COLOUR
    return CLEANER_COLOUR


def signed_bar_label(axes, position, value, text):
    """The value beside the bar's end: right of a positive bar, left of a
    negative one."""
    if value > 0:
        bar_label(axes, position, value, text)
        return
    axes.annotate(
        text,
        (value, position),
        xytext=(-LABEL_GAP_PIXELS, 0),
        textcoords="offset points",
        ha="right",
        va="center",
        fontsize=8,
        color="#3d444d",
    )


def write_q29_time_chart(rows, output):
    name = "q29_error_by_time_of_day"
    blocks = matching_rows(rows, "section", "3")
    (overall,) = matching_rows(rows, "section", "1")

    figure, axes = new_figure(len(blocks))
    labels = []
    for position, row in enumerate(blocks):
        error = float(row["mean_error"])
        axes.barh(position, error, height=0.6, color=sign_colour(error))
        signed_bar_label(axes, position, error, f"{error:+.1f}")
        labels.append(Q29_BLOCKS[row["item"]])
    row_labels(axes, labels)
    axes.axvline(0, color=COMPARISON_COLOUR, linewidth=0.8)
    axes.margins(x=0.2)
    axes.set_xlabel("Mean miss, gCO2/kWh (left: cleaner than forecast; right: dirtier)")
    png_title(
        axes,
        Q29_TIME_TITLE,
        [
            f"Six-hour blocks, UTC; mean size of the miss "
            f"{float(overall['mean_absolute_error']):.1f} g/kWh over "
            f"{int(overall['half_hours']):,} half-hours"
        ],
    )
    save_figure(figure, output / f"{name}.png")

    data = csv_data(Q29_RESULT, ["section", "mean_error", "mean_absolute_error"])
    position = {
        "x": altair.X(
            "mean_error:Q",
            scale=altair.Scale(padding=SIGNED_SCALE_PADDING_PIXELS),
            axis=axis_title(
                [
                    "Mean miss, grams of CO2 per kWh",
                    "left: cleaner than forecast · right: dirtier",
                ]
            ),
        ),
        "y": row_axis("block"),
    }
    keys = direction_keys(BAR_SYMBOL)
    tooltip = [
        altair.Tooltip("block_name:N", title="Time of day, UTC"),
        altair.Tooltip("half_hours:N", title="Half-hours"),
        altair.Tooltip("mean_error:Q", title="Mean miss, g/kWh", format="+.1f"),
        altair.Tooltip(
            "mean_absolute_error:Q",
            title="Mean size of the miss, g/kWh",
            format=".1f",
        ),
    ]
    bars = (
        altair.Chart()
        .mark_bar(cornerRadiusEnd=BAR_CORNER_RADIUS)
        .encode(fill=key_fill(keys), tooltip=tooltip, **position)
    )
    chart = (
        altair.layer(
            zero_line(), bars, value_labels(position, "mean_error", "+.1f"), data=data
        )
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=3))
        .transform_calculate(
            block=display_name("item", Q29_BLOCKS),
            **{KEY_FIELD: q29_direction()},
        )
        .transform_calculate(block_name="replace(datum.block, '|', ', ')")
        .properties(
            **page_layout(
                Q29_TIME_TITLE,
                Q29_TIME_READING,
                len(blocks) * TWO_LINE_ROW_STEP_PIXELS,
            )
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


def month_of(item):
    return item.removeprefix("settlement month ")[:7]


def is_part_month(item):
    return "partial" in item


MONTH_LABEL = (
    "['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', "
    "'Nov', 'Dec'][toNumber(slice(datum.label, 5, 7)) - 1] + ' ' + "
    "slice(datum.label, 2, 4)"
)


def write_q29_month_chart(rows, output):
    name = "q29_error_by_month"
    months = matching_rows(rows, "section", "2")

    figure, axes = pyplot.subplots(figsize=(FIGURE_WIDTH_INCHES, 4))
    quiet_frame(axes)
    axes.grid(axis="y", color=GRID_COLOUR, linewidth=0.8)
    positions = list(range(len(months)))
    errors = []
    for row in months:
        errors.append(float(row["mean_error"]))
    axes.axhline(0, color=COMPARISON_COLOUR, linewidth=0.8)
    axes.plot(positions, errors, color=COMPARISON_COLOUR, linewidth=1.5)
    month_labels = []
    for position, row in enumerate(months):
        colour = sign_colour(errors[position])
        face = colour
        if is_part_month(row["item"]):
            face = "white"
        axes.plot(
            position,
            errors[position],
            "o",
            markerfacecolor=face,
            markeredgecolor=colour,
        )
        month_labels.append(month_of(row["item"]))
    axes.set_xticks(positions, month_labels, rotation=90)
    axes.set_ylabel("Mean miss, gCO2/kWh")
    png_title(
        axes,
        Q29_MONTH_TITLE,
        ["Positive: dirtier than forecast. Hollow: part months"],
    )
    save_figure(figure, output / f"{name}.png")

    data = csv_data(Q29_RESULT, ["section", "mean_error", "mean_absolute_error"])
    month = altair.X(
        "month:O",
        title=None,
        axis=altair.Axis(labelExpr=MONTH_LABEL, labelAngle=0, labelOverlap="parity"),
    )
    error = altair.Y(
        "mean_error:Q",
        axis=axis_title(
            ["Mean miss, grams of CO2 per kWh", "above zero: dirtier than forecast"]
        ),
    )
    keys = direction_keys("circle")
    keys[PART_MONTH] = key_mark(COMPARISON_COLOUR, NO_FILL, "circle")
    tooltip = [
        altair.Tooltip("month:N", title="Month"),
        altair.Tooltip("half_hours:N", title="Half-hours"),
        altair.Tooltip("mean_error:Q", title="Mean miss, g/kWh", format="+.1f"),
        altair.Tooltip(
            "mean_absolute_error:Q",
            title="Mean size of the miss, g/kWh",
            format=".1f",
        ),
    ]
    zero = (
        drawn_once()
        .mark_rule(color=COMPARISON_COLOUR)
        .encode(y=altair.datum(0))
        .properties(name="zero line")
    )
    line = (
        altair.Chart()
        .mark_line(color=COMPARISON_COLOUR, strokeWidth=2)
        .encode(x=month, y=error)
        .properties(name="connector")
    )
    points = (
        altair.Chart()
        .mark_point(filled=False, size=70, strokeWidth=2, opacity=1)
        .encode(x=month, y=error, tooltip=tooltip, **keyed_points(keys))
    )
    part_month = "indexof(datum.item, 'partial') >= 0"
    chart = (
        altair.layer(zero, line, points, data=data)
        .transform_filter(altair.FieldEqualPredicate(field="section", equal=2))
        .transform_calculate(
            month="slice(datum.item, 17, 24)",
            **{
                KEY_FIELD: (
                    f"{part_month} ? {json.dumps(PART_MONTH)} : ({q29_direction()})"
                )
            },
        )
        .properties(
            **page_layout(Q29_MONTH_TITLE, Q29_MONTH_READING, LINE_CHART_HEIGHT_PIXELS)
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


# Q30: the four rank correlations


def correlation_rows(rows):
    chart_rows = []
    for correlation in Q30_CORRELATIONS:
        section = correlation["section"]
        chart_rows.append(
            {
                "label": correlation["label"],
                "measure": correlation["measure"],
                "rho": row_for(rows, section, correlation["value"])["value"],
                "low": row_for(rows, section, correlation["low"])["value"],
                "high": row_for(rows, section, correlation["high"])["value"],
            }
        )
    return chart_rows


def draw_correlations(rows, path):
    figure, axes = new_figure(len(rows))
    axes.axvline(0, color=COMPARISON_COLOUR, linewidth=0.8)
    labels = []
    for position, row in enumerate(rows):
        if row["label"].startswith(Q30_SIZE_LABEL_START):
            axes.fill_between(
                [-Q30_EXPECTED_SIZE, Q30_EXPECTED_SIZE],
                position - 0.35,
                position + 0.35,
                color=COMPARISON_COLOUR,
                alpha=0.15,
                linewidth=0,
            )
        rho = float(row["rho"])
        axes.plot(
            [float(row["low"]), float(row["high"])],
            [position, position],
            color=RESULT_COLOUR,
            linewidth=1.5,
        )
        axes.plot(rho, position, "o", color=RESULT_COLOUR)
        axes.annotate(
            f"{rho:+.2f}",
            (rho, position),
            xytext=(0, 7),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color="#3d444d",
        )
        labels.append(row["label"])
    row_labels(axes, labels)
    axes.set_xlim(-0.3, 0.3)
    axes.set_xlabel("Rank correlation with the stored carbon forecast's miss")
    png_title(
        axes,
        Q30_TITLE,
        [
            "With 90% ranges; shaded in the size rows: within ±0.2, the size "
            "expected before reading; the direction rows were expected to be "
            "negative"
        ],
    )
    save_figure(figure, path)


def write_q30_chart(rows, output):
    name = "q30_rank_correlations"
    chart_rows = correlation_rows(rows)
    columns = list(chart_rows[0])
    csv_name = write_chart_csv(
        output, name, BMRS_AND_CARBON_ATTRIBUTION, columns, chart_rows
    )
    draw_correlations(chart_rows, output / f"{name}.png")

    data = csv_data(csv_name, ["rho", "low", "high"])
    measure = row_axis("label", column_of(chart_rows, "label"))
    rho_scale = altair.Scale(domain=[-0.3, 0.3], nice=False)
    rho_axis = altair.X(
        "rho:Q",
        scale=rho_scale,
        axis=axis_title(["Rank correlation with", "the carbon forecast's miss"]),
    )
    tooltip = [
        altair.Tooltip("measure:N", title="Measure"),
        altair.Tooltip("rho:Q", title="Rank correlation", format="+.2f"),
        altair.Tooltip("low:Q", title="90% range from", format="+.2f"),
        altair.Tooltip("high:Q", title="90% range to", format="+.2f"),
    ]
    expected_label = f"Size expected: ±{Q30_EXPECTED_SIZE}"
    keys = {
        "Rank correlation": key_mark(RESULT_COLOUR, RESULT_COLOUR, "circle"),
        "90% range": key_mark(RESULT_COLOUR, NO_FILL, LINE_SYMBOL),
        expected_label: key_mark(COMPARISON_COLOUR, BAND_FILL, BAR_SYMBOL),
    }
    is_size_row = f"indexof(datum.label, {json.dumps(Q30_SIZE_LABEL_START)}) == 0"
    expected = keyed(
        altair.Chart().transform_filter(is_size_row).mark_rect(), expected_label
    ).encode(
        x=altair.XDatum(-Q30_EXPECTED_SIZE, scale=rho_scale),
        x2=altair.datum(Q30_EXPECTED_SIZE),
        y=measure,
        fill=key_fill(keys),
    )
    zero = (
        drawn_once()
        .mark_rule(color=COMPARISON_COLOUR)
        .encode(x=altair.XDatum(0, scale=rho_scale))
        .properties(name="zero line")
    )
    interval = keyed(altair.Chart().mark_rule(strokeWidth=2), "90% range").encode(
        x=altair.X("low:Q", scale=rho_scale),
        x2="high:Q",
        y=measure,
        stroke=key_stroke(keys),
    )
    dots = keyed(
        altair.Chart().mark_point(filled=False, size=80, strokeWidth=2, opacity=1),
        "Rank correlation",
    ).encode(x=rho_axis, y=measure, tooltip=tooltip, **keyed_points(keys))
    dot_labels = label_above("rho:Q", measure, "rho_label")
    chart = (
        altair.layer(expected, zero, interval, dots, dot_labels, data=data)
        .transform_calculate(rho_label="format(datum.rho, '+.2f')")
        .properties(
            **page_layout(
                Q30_TITLE, Q30_READING, len(chart_rows) * TWO_LINE_ROW_STEP_PIXELS
            )
        )
    )
    write_spec(chart, output / f"{name}.vl.json")


def write_charts(result_folder, output_folder):
    results = Path(result_folder)
    output = Path(output_folder)
    output.mkdir(parents=True, exist_ok=True)

    copy_saved_output(results, output, Q2_RESULT, BMRS_ATTRIBUTION)
    q2_by_prefix = copy_saved_output(
        results, output, Q2_BY_PREFIX_RESULT, BMRS_ATTRIBUTION
    )
    write_q2_chart(q2_by_prefix, output)
    q3 = copy_saved_output(results, output, Q3_RESULT, BMRS_ATTRIBUTION)
    copy_saved_output(results, output, Q3_COVERAGE_RESULT, BMRS_ATTRIBUTION)
    copy_saved_output(results, output, Q22_RESULT, BMRS_ATTRIBUTION)
    write_q3_chart(q3, output)
    q12 = copy_saved_output(results, output, Q12_RESULT, BMRS_ATTRIBUTION)
    write_q12_chart(q12, output)
    q5 = copy_saved_output(results, output, Q5_RESULT, BMRS_ATTRIBUTION)
    write_q5_chart(q5, output)
    copy_saved_output(results, output, Q6_RESULT, BMRS_ATTRIBUTION)
    q23 = copy_saved_output(results, output, Q23_RESULT, BMRS_ATTRIBUTION)
    write_q23_typical_chart(q23, output)
    write_q23_monthly_chart(q23, output)
    q24 = copy_saved_output(results, output, Q24_RESULT, BMRS_ATTRIBUTION)
    write_q24_chart(q24, output)
    q25 = copy_saved_output(results, output, Q25_RESULT, BMRS_ATTRIBUTION)
    q25_corrected = copy_saved_output(
        results, output, Q25_CORRECTED_RESULT, BMRS_ATTRIBUTION
    )
    write_q25_chart(q25_corrected, q25, output)
    q8_q28 = copy_saved_output(
        results, output, Q8_Q28_RESULT, BMRS_AND_CARBON_ATTRIBUTION
    )
    write_q28_chart(q8_q28, output)
    q29 = copy_saved_output(results, output, Q29_RESULT, CARBON_ATTRIBUTION)
    write_q29_time_chart(q29, output)
    write_q29_month_chart(q29, output)
    q30 = copy_saved_output(results, output, Q30_RESULT, BMRS_AND_CARBON_ATTRIBUTION)
    write_q30_chart(q30, output)


if __name__ == "__main__":
    write_charts(sys.argv[1], sys.argv[2])
