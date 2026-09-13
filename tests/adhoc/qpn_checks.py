from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ingestion.elexon.qpn_poller import fetch  # same shape; QPN poller not built yet

to_d = datetime(2026, 8, 22, tzinfo=timezone.utc)
rows = fetch(to_d - timedelta(days=1), to_d)  # point this at /QPN/stream

print(f"total rows        : {len(rows):,}")
print(f"null bmUnit       : {sum(1 for r in rows if not r['bmUnit']):,}")
print(f"null nationalGrid : {sum(1 for r in rows if not r['nationalGridBmUnit']):,}")

nonzero = [r for r in rows if r["levelFrom"] or r["levelTo"]]
print(f"non-zero rows     : {len(nonzero):,}  ({100 * len(nonzero) / len(rows):.1f}%)")

by_unit = defaultdict(list)
for r in nonzero:
    by_unit[r["bmUnit"] or r["nationalGridBmUnit"]].append(r)

print(f"units with any non-zero QPN: {len(by_unit)}")


def row_count(item):
    unit, rows = item
    return len(rows)


for unit, unit_rows in sorted(by_unit.items(), key=row_count, reverse=True)[:10]:
    levels = sorted({(row["levelFrom"], row["levelTo"]) for row in unit_rows})[:4]
    print(f"   {unit:16} {len(unit_rows):>4} rows   levels {levels}")
