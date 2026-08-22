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

## Why keep them

Several figures in the documentation were wrong before being measured, because
they were inferred rather than counted. A script that produced a number is
better evidence than a number in a sentence, and it can be re-run when the
answer might have changed.

If a script's finding stops being relevant, delete the script.
