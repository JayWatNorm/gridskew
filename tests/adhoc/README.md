# Ad-hoc checks

**These are not automated tests.** They are exploratory validation scripts used
to answer specific questions about the data. They are retained so documented
measurements remain reproducible.

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
| `b1610_checks.py` | When does B1610 arrive, when does each settlement run publish, can earlier runs be recovered, and what does a market-wide day contain? | First publication appears after five working days, normally 7 calendar days. A settlement day contains **~440,000 rows across 9,177 units**, making B1610 the largest table in the project. Identifier pattern is **inverted from PN**: `bmUnit` never null, `nationalGridBmUnitId` null on 71.8%. Run boundaries measured out to 720 days place `RF` at ~420 days. Most importantly, **superseded runs are discarded and cannot be fetched retrospectively** — restatement is only observable forwards. See `docs/sources/elexon/020_b1610.md` |

## Fixture capture

`b1610_capture_fixture.py` is not a check — it **writes**
`tests/fixtures/elexon/b1610_stream.json`. A market-wide window is ~9,000 rows
per settlement period, far too large to commit, so it fetches a real window and
selects the smallest set of units covering every trait the tests need, reporting
coverage as it goes.

Re-run it rather than hand-editing the fixture. The moment a fixture is edited by
hand it stops recording what the API returned.

A script that produced a number is better evidence than a number in a sentence,
and it can be re-run when the answer might have changed. Anything quoted as a
volume or a proportion in the documentation should be traceable to one of these.

If a script's finding stops being relevant, delete the script.
