"""Capture a composite B1610 fixture from real data.

A market-wide window is ~9,000 rows per settlement period, far too large to
commit, and the endpoint's bmUnit filter is untested. So this fetches a real
window, picks a handful of units that between them cover every trait the tests
need, and writes their rows verbatim.

The window straddles a settlement-day boundary so periods 1 and 2 are both
present - period 1 is the only period where settlementDate disagrees with the
UTC date of halfHourEndTime, and that trap needs a test.

Writes tests/fixtures/elexon/b1610_stream.json. Review the coverage report
before committing, and record it in tests/fixtures/README.md as composite.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import requests

URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets/B1610/stream"
HEADERS = {"User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"}
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "elexon" / "b1610_stream.json"

LAG_DAYS = 14  # matches the II poll offset
MAX_UNITS = 6

TRAITS = {
    "null_ng": lambda r: r["nationalGridBmUnitId"] is None,
    "has_ng": lambda r: bool(r["nationalGridBmUnitId"]),
    "negative": lambda r: r["quantity"] < 0,
    "zero": lambda r: r["quantity"] == 0,
    "positive": lambda r: r["quantity"] > 0,
    "three_dp": lambda r: (
        r["quantity"] != 0 and -r["quantity"].as_tuple().exponent == 3
    ),
    "period_1": lambda r: r["settlementPeriod"] == 1,
    "period_2": lambda r: r["settlementPeriod"] == 2,
    # magnitude: the real range runs to +-700 MWh. A fixture topping out at
    # single digits cannot catch a numeric scale problem.
    "large": lambda r: abs(r["quantity"]) > 100,
    # identifier shapes: a parse that mishandles one format passes a fixture
    # containing only the other. pn_stream.json covers three shapes for the
    # same reason.
    "prefix_T": lambda r: r["bmUnit"].startswith("T_"),
    "prefix_E": lambda r: r["bmUnit"].startswith("E_"),
    "prefix_other": lambda r: not r["bmUnit"].startswith(("T_", "E_")),
}


def fetch(from_date, to_date):
    response = requests.get(
        URL,
        params={
            "from": from_date.strftime("%Y-%m-%dT%H:%MZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%MZ"),
        },
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()
    return json.loads(response.text, parse_float=Decimal)


def traits_of(rows):
    return {name for name, test in TRAITS.items() if any(test(r) for r in rows)}


today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
day = today - timedelta(days=LAG_DAYS)
start = day - timedelta(hours=2)

print(f"fetching {start:%Y-%m-%d %H:%M}Z +4h  (straddles the settlement-day boundary)")
rows = fetch(start, start + timedelta(hours=4))
print(f"  {len(rows):,} rows, {len({r['bmUnit'] for r in rows}):,} units\n")

if not rows:
    raise SystemExit("empty window - is LAG_DAYS inside the publication lag?")

by_unit = defaultdict(list)
for r in rows:
    by_unit[r["bmUnit"]].append(r)

# greedy: repeatedly take the unit covering the most traits still missing
wanted = set(TRAITS)
chosen = {}
while wanted and len(chosen) < MAX_UNITS:
    best, gain = None, set()
    for unit, unit_rows in by_unit.items():
        if unit in chosen:
            continue
        new = traits_of(unit_rows) & wanted
        if len(new) > len(gain):
            best, gain = unit, new
    if not best:
        break
    chosen[best] = by_unit[best]
    wanted -= gain

print("selected units")
for unit, unit_rows in chosen.items():
    ng = unit_rows[0]["nationalGridBmUnitId"] or "(null)"
    qty = sorted({r["quantity"] for r in unit_rows})
    print(
        f"  {unit:<14} ng={ng:<12} {len(unit_rows):>2} rows  qty {qty[0]} .. {qty[-1]}"
    )

print("\ntrait coverage")
covered = traits_of([r for rs in chosen.values() for r in rs])
for name in TRAITS:
    print(f"  {'OK ' if name in covered else '** MISSING **'} {name}")

selected = sorted(
    (r for rs in chosen.values() for r in rs),
    key=lambda r: (r["settlementDate"], r["settlementPeriod"], r["bmUnit"]),
)

# json.dumps cannot serialise Decimal, and float() would turn 0.000 into 0.0.
# Emit a sentinel, then unquote it, so quantities stay byte-for-byte as sent.
text = json.dumps(selected, indent=4, default=lambda o: f"@@{o}@@")
text = re.sub(r'"@@(-?[\d.]+)@@"', r"\1", text)

OUT.write_text(text + "\n", encoding="utf-8")
print(f"\nwrote {len(selected)} rows to {OUT}")

dates = sorted({r["settlementDate"] for r in selected})
periods = sorted({r["settlementPeriod"] for r in selected})
print(f"  settlement dates : {dates}")
print(f"  periods          : {periods}")
mismatch = [r for r in selected if r["settlementDate"] != r["halfHourEndTime"][:10]]
print(
    f"  local/UTC date mismatches : {len(mismatch)} "
    f"(periods {sorted({r['settlementPeriod'] for r in mismatch})})"
)
