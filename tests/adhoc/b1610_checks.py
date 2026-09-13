"""Historical B1610 reconnaissance used to design the poller.

Six questions the design depends on:
  1. how many days late does data actually arrive?
  2. where are the settlement run boundaries?
  3. does RF ever land inside a usable window?
  4. can a past run be requested explicitly, or only the current one?
  5. how much does the number actually move between runs?
  6. what does a market-wide day contain?

Question 5 is the one that decides whether II is usable or whether analysis has
to wait a year. Questions 1-4 only tell you which label is current.

This remains self-contained so its original API evidence can be reproduced
without using production poller behaviour.

Roughly 60 requests, one-hour windows, sleep(0.2) between. A few minutes.
"""

import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import requests

URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets/B1610/stream"
HEADERS = {"User-Agent": "gridskew/0.1 (+https://github.com/JayWatNorm/gridskew)"}
RUNS = ("II", "SF", "R1", "R2", "R3", "RF")


def fetch(from_date, to_date, run_type=None):
    params = {
        "from": from_date.strftime("%Y-%m-%dT%H:%MZ"),
        "to": to_date.strftime("%Y-%m-%dT%H:%MZ"),
    }
    if run_type:
        params["settlementRunType"] = run_type
    response = requests.get(URL, params=params, headers=HEADERS, timeout=60)
    response.raise_for_status()
    # parse_float=Decimal so quantity never touches binary floating point
    return json.loads(response.text, parse_float=Decimal)


def window(days_ago, hours=1):
    start = today - timedelta(days=days_ago)
    return start, start + timedelta(hours=hours)


now = datetime.now(timezone.utc)
today = now.replace(hour=0, minute=0, second=0, microsecond=0)
print(f"reference date {today:%Y-%m-%d}\n")

# ------------------------------------------------------------ 1. lag probe

print("1. publication lag")
print("   days ago     rows")

first_populated = None
for days_ago in range(1, 11):
    from_date, to_date = window(days_ago)
    rows = fetch(from_date, to_date)
    print(f"   {days_ago:>8}   {len(rows):>6,}")
    if rows and first_populated is None:
        first_populated = days_ago
    time.sleep(0.2)

if first_populated is None:
    raise SystemExit("\nno data in the last 10 days - widen the probe")

print(f"   -> first populated at {first_populated} days\n")

# --------------------------------------------------- 2. run type boundaries
# dense through the early transitions, coarser once runs are months apart,
# and out past a year to find RF. Published timescales are approximate and
# being changed by MHHS, so these boundaries come from measurement.

ages = list(range(7, 120, 7)) + list(range(120, 240, 15)) + list(range(240, 750, 30))

print(f"2. settlement run boundaries  ({len(ages)} requests)")
print("   days ago     rows   units   run types")

seen_at = {}
for days_ago in ages:
    from_date, to_date = window(days_ago)
    rows = fetch(from_date, to_date)
    runs = Counter(r["settlementRunType"] for r in rows)
    units = len({r["bmUnit"] for r in rows})
    for run in runs:
        seen_at.setdefault(run, []).append(days_ago)
    print(f"   {days_ago:>8}   {len(rows):>6,}   {units:>5,}   {dict(runs) or '-'}")
    time.sleep(0.2)

print("\n   run     first seen   last seen")
for run in RUNS:
    if run in seen_at:
        print(f"   {run:>3}   {min(seen_at[run]):>10}   {max(seen_at[run]):>9}")
    else:
        print(f"   {run:>3}   {'not seen':>10}   {'-':>9}")
print()

# ------------------------------------------ 3. can a past run be requested?
# the endpoint takes settlementRunType as a parameter, but the sweep above
# returned one run per date - so it may only serve the current one. If past
# runs ARE retrievable, question 5 can be answered today rather than in a year.

probe_age = min(300, max(ages))
print(f"3. explicit run retrieval, {today - timedelta(days=probe_age):%Y-%m-%d}")

snapshots = {}
for run in RUNS:
    from_date, to_date = window(probe_age)
    rows = fetch(from_date, to_date, run_type=run)
    print(f"   {run:>3}   {len(rows):>6,} rows")
    if rows:
        snapshots[run] = {
            (r["bmUnit"], r["settlementPeriod"]): r["quantity"] for r in rows
        }
    time.sleep(0.2)

# ------------------------------------------------- 4. how much does it move?
# the actual question: is an early run trustworthy, or does it move enough to
# invalidate analysis built on it?

print("\n4. convergence between runs")

if len(snapshots) < 2:
    print("   only one run retrievable - restatement can only be observed forward.")
    print("   re-poll a fixed day monthly and diff, rather than measuring it now.")
else:
    final = snapshots[[r for r in RUNS if r in snapshots][-1]]
    final_name = [r for r in RUNS if r in snapshots][-1]
    total = sum(abs(q) for q in final.values())
    print(
        f"   baseline {final_name}, {len(final):,} unit-periods, "
        f"{total:,.1f} MWh absolute\n"
    )
    print("   comparing each retrievable run against the baseline:")

    for run in RUNS:
        if run not in snapshots or run == final_name:
            continue
        snap = snapshots[run]
        shared = set(snap) & set(final)
        # a percentage over an empty intersection reads as "identical" and
        # means nothing. Say so instead of printing 0.000%.
        if not shared:
            print(f"   {run:>3}   no overlapping unit-periods - nothing compared")
            continue
        delta = sum(abs(snap[k] - final[k]) for k in shared)
        differ = sum(1 for k in shared if snap[k] != final[k])
        if total:
            pct = 100 * delta / total
        else:
            pct = Decimal(0)
        print(
            f"   {run:>3}   {len(shared):>7,} shared   {differ:>9,} differ   "
            f"{delta:>12,.3f} MWh   {pct:>7.3f}%"
        )

    print("\n   a small percentage means early runs are usable and analysis")
    print("   need not wait. a large one means it must.")
    print("   'nothing compared' means the API no longer holds that run.")

# ------------------------------------------------------- 5. market-wide day

day_start = today - timedelta(days=first_populated)
print(f"\n5. market-wide day {day_start:%Y-%m-%d}, fetching...")
rows = fetch(day_start, day_start + timedelta(days=1))

print(f"\n   total rows            : {len(rows):,}")

no_bm = [r for r in rows if not r["bmUnit"]]
no_ng = [r for r in rows if not r["nationalGridBmUnitId"]]
print(f"   null bmUnit           : {len(no_bm):,}")
print(f"   null nationalGridId   : {len(no_ng):,}")
print(f"   distinct bm units     : {len({r['bmUnit'] for r in rows}):,}")
print(
    f"   distinct NG units     : {len({r['nationalGridBmUnitId'] for r in rows if r['nationalGridBmUnitId']}):,}"
)

print(
    f"\n   settlementRunType     : {dict(Counter(r['settlementRunType'] for r in rows))}"
)
print(f"   psrType               : {dict(Counter(r['psrType'] for r in rows))}")

qty = [r["quantity"] for r in rows]
zeros = sum(1 for q in qty if q == 0)
negs = sum(1 for q in qty if q < 0)
print(f"\n   quantity min / max    : {min(qty)} / {max(qty)}")
print(f"   zero rows             : {zeros:,}  ({100 * zeros / len(rows):.1f}%)")
print(f"   negative rows         : {negs:,}  ({100 * negs / len(rows):.1f}%)")
print(f"   decimal places        : {sorted({-q.as_tuple().exponent for q in qty})}")

by_date = defaultdict(set)
for r in rows:
    by_date[r["settlementDate"]].add(r["settlementPeriod"])
print(
    f"\n   periods per date      : {[(d, len(p)) for d, p in sorted(by_date.items())]}"
)

# ------------------------------------------------- 6. local vs UTC date check
# settlementDate is British local; halfHourEndTime is UTC. They can only
# disagree on periods 1 and 2, which sit BEFORE a UTC-aligned day window - so
# checking inside one day is vacuous. Straddle the boundary instead.

b_start = day_start - timedelta(days=1, hours=2)
print(f"\n6. boundary check, {b_start:%Y-%m-%d %H:%M}Z +4h")
b_rows = fetch(b_start, b_start + timedelta(hours=4))
mismatch = [r for r in b_rows if r["settlementDate"] != r["halfHourEndTime"][:10]]
print(f"   rows                  : {len(b_rows):,}")
print(f"   date mismatches       : {len(mismatch):,}")
print(f"   periods involved      : {sorted({r['settlementPeriod'] for r in mismatch})}")
print("   -> zero mismatches here would mean settlementDate is NOT local time")
