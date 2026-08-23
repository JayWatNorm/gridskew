# Ad-hoc checks

**These are not tests.** They are throwaway scripts used to answer a specific
question about the data, kept because the answer ended up in the documentation
and the working should be visible.

They are **excluded from pytest** by `norecursedirs = ["adhoc"]` in
`pyproject.toml`. That exclusion is load-bearing: these scripts **call the live
API at import time**, so if pytest ever collected one it would make real HTTP
requests during collection — including on a CI runner.

**Do not name anything in here `test_*.py`.** The exclusion covers the directory,
but the naming convention is the second line of defence.

## Running one

From the repository root, with the virtualenv active:

```bash
python -m tests.adhoc.pn_checks
```

They import from `ingestion/`, so they need the repo root on the path.

## What is here

| Script | Question it answered | Result |
|---|---|---|
| `pn_checks.py` | Are `bmUnit` and `nationalGridBmUnit` ever null? How many PN rows are zero-to-zero? | 2,450 of 132,728 rows have a null `bmUnit` and none has a null `nationalGridBmUnit`, which is why the primary key uses the National Grid identifier. 72% of rows are zero-to-zero, and 63% of units are zero all day. See `docs/sources/elexon/010_pn.md` |
| `qpn_checks.py` | Does QPN share PN's null pattern, and how often is it actually non-zero? | Same 2,450 null `bmUnit` rows, same zero nulls on `nationalGridBmUnit`, so the same key holds. **50 of 119,600 rows are non-zero — 0.04% — and all 50 belong to `T_WILCT-1`**, at a constant −60 MW. See `docs/sources/elexon/015_qpn.md` |
| `b1610_checks.py` | When does B1610 arrive, when does each settlement run publish, can earlier runs be recovered, and what does a market-wide day contain? | **Lag is 7 days, not the documented 5.** **~440,000 rows per settlement day across 9,177 units — 22x the previous estimate**, making B1610 the largest table in the project. Identifier pattern **inverted from PN**: `bmUnit` never null, `nationalGridBmUnitId` null on 71.8%. Run boundaries measured out to 720 days: **`RF` lands at ~420 days, not the ~7 months published.** Most importantly, **superseded runs are discarded and cannot be fetched retrospectively** — restatement is only observable forwards. See `docs/sources/elexon/020_b1610.md` |

## Fixture capture

`b1610_capture_fixture.py` is not a check — it **writes**
`tests/fixtures/elexon/b1610_stream.json`. A market-wide window is ~9,000 rows
per settlement period, far too large to commit, so it fetches a real window and
selects the smallest set of units covering every trait the tests need, reporting
coverage as it goes.

Re-run it rather than hand-editing the fixture. The moment a fixture is edited by
hand it stops recording what the API returned.

Several figures in the documentation were wrong before being measured, because
they were inferred rather than counted. A script that produced a number is
better evidence than a number in a sentence, and it can be re-run when the
answer might have changed.

If a script's finding stops being relevant, delete the script.
