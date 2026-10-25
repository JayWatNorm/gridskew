"""Draw the README charts from a saved delivery result.

Reads the delivery-by-fuel-group result saved as CSV at its read (one row
per fuel group, the mart's columns) and writes, for each chart, a PNG and
the CSV of the values it shows. Nothing is queried. A rerun on the same
input writes identical files.

    python scripts/readme_charts.py <saved delivery result.csv> <output folder>
"""

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot  # noqa: E402

BMRS_ATTRIBUTION = (
    "Contains BMRS data © Elexon Limited copyright and database right 2026."
)
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
FIGURE_WIDTH_INCHES = 8
INCHES_PER_GROUP = 0.45
FIGURE_MARGIN_INCHES = 1.6
DPI = 100


def read_delivery(path):
    with Path(path).open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    cutoffs = set()
    for row in rows:
        cutoffs.add(row["analysis_read_until"])
    if len(cutoffs) != 1:
        raise ValueError(f"The result must come from one cutoff, not {sorted(cutoffs)}")
    return rows


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


def new_figure(group_count):
    height = FIGURE_MARGIN_INCHES + INCHES_PER_GROUP * group_count
    return pyplot.subplots(figsize=(FIGURE_WIDTH_INCHES, height))


def save_figure(figure, path):
    figure.tight_layout()
    figure.savefig(path, dpi=DPI)
    pyplot.close(figure)


def draw_deviation(rows, path):
    figure, axes = new_figure(len(rows))
    positions = list(range(len(rows)))
    for position, row in zip(positions, rows, strict=True):
        lower = float(row["p25_deviation_mwh"])
        upper = float(row["p75_deviation_mwh"])
        axes.plot([lower, upper], [position, position], color="#4c72b0", linewidth=4)
        axes.plot(float(row["median_deviation_mwh"]), position, "o", color="#1b2a4a")
    axes.axvline(0, color="#999999", linewidth=0.8)
    axes.set_yticks(positions, group_labels(rows))
    axes.invert_yaxis()
    axes.set_xlabel("Metered minus notified (PN) energy per unit-period, MWh")
    axes.set_title(
        "Median and interquartile range by fuel group\n" + describe_read(rows),
        fontsize=10,
    )
    save_figure(figure, path)


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
        axes.barh(position, 100 * float(share), color="#4c72b0")
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


def write_charts(delivery_csv, output_dir):
    rows = read_delivery(delivery_csv)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    by_metered_energy = sorted(rows, key=descending_by("abs_metered_mwh"))
    write_values(
        output / "q3_deviation_by_fuel_group.csv", by_metered_energy, Q3_COLUMNS
    )
    draw_deviation(by_metered_energy, output / "q3_deviation_by_fuel_group.png")

    by_deviation = sorted(rows, key=descending_by("abs_deviation_mwh"))
    write_values(
        output / "q12_instructed_share_by_fuel_group.csv", by_deviation, Q12_COLUMNS
    )
    draw_instructed_share(
        by_deviation, output / "q12_instructed_share_by_fuel_group.png"
    )


if __name__ == "__main__":
    write_charts(sys.argv[1], sys.argv[2])
