from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ingestion.elexon.pn_poller import fetch

# null bm units

to_d = datetime(2026, 8, 22, tzinfo=timezone.utc)
rows = fetch(to_d - timedelta(days=1), to_d)

no_bm = [r for r in rows if not r["bmUnit"]]
no_ng = [r for r in rows if not r["nationalGridBmUnit"]]

print(f"total rows          : {len(rows):,}")
print(f"null bmUnit         : {len(no_bm):,}")
print(f"  distinct NG units : {sorted({r['nationalGridBmUnit'] for r in no_bm})}")
print(f"null nationalGrid   : {len(no_ng):,}")
print(
    f"both null           : {sum(1 for r in rows if not r['bmUnit'] and not r['nationalGridBmUnit']):,}"
)


zeros = sum(1 for r in rows if r["levelFrom"] == 0 and r["levelTo"] == 0)
print(f"{zeros:,} of {len(rows):,} rows are 0 to 0  ({100 * zeros / len(rows):.0f}%)")

# and how many units are zero for the ENTIRE day


by_unit = defaultdict(list)
for r in rows:
    by_unit[r["nationalGridBmUnit"]].append(r)
all_zero = sum(
    1
    for v in by_unit.values()
    if all(x["levelFrom"] == 0 and x["levelTo"] == 0 for x in v)
)
print(f"{all_zero:,} of {len(by_unit):,} units are zero all day")
