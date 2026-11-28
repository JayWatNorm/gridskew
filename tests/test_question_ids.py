"""Every question a tracked file names is one the questions page holds."""

import html
import re
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parent.parent
QUESTIONS = REPOSITORY / "docs" / "research-questions.md"
ANSWERED = (1, 2, 3, 5, 6, 8, 10, 12, 22, 23, 24, 25, 28, 29, 30, 37)
RETIRED = (7, 11, 13, 14, 17, 18, 19, 20, 21)
# A question id as prose writes it (Q12) or as a file name does (q12_...),
# with the settlement-run suffix a variant of a question would carry. The
# suffix may follow any hyphen or dash, a minus sign or an underscore.
QUESTION_ID = re.compile(
    r"(?<![0-9A-Za-z])[Qq](?P<number>\d{1,2})"
    r"(?P<variant>[-_\u2010-\u2015\u2212]?[Rr](?:\d[Rr])?[Ff])?"
    r"(?![0-9A-Za-z])"
)
# The word and a number on one line, or wrapped onto the next.
QUESTION_BY_WORD = re.compile(
    r"\bquestions?(?:[ \t ]+|[ \t]*\n[ \t]*)\d", re.IGNORECASE
)
# Wording for a question without an answer, for a method fixed in advance
# and for a result on a register, held in one fixture file that the scans
# skip.
WORDING_PATTERNS = "tests/fixtures/public_wording_patterns.txt"


def wording_patterns():
    """Each pattern name in the wording file, with its patterns joined into
    one case-insensitive expression."""
    by_name = {}
    lines = (REPOSITORY / WORDING_PATTERNS).read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line or line.startswith("#"):
            continue
        name, pattern = line.split(" = ", 1)
        if name not in by_name:
            by_name[name] = []
        by_name[name].append(pattern)
    compiled = {}
    for name, patterns in by_name.items():
        compiled[name] = re.compile("|".join(patterns), re.IGNORECASE)
    return compiled


UNANSWERED_WORDING = wording_patterns()["unanswered"]
FIXED_IN_ADVANCE_WORDING = wording_patterns()["fixed_in_advance"]
ON_A_REGISTER_WORDING = wording_patterns()["on_a_register"]
# Saved outputs and the queries as run keep the row labels they were read
# with; one analysis writes those labels and one check expects them.
KEEP_THEIR_ROW_LABELS = (
    "docs/results/",
    "dbt/analyses/q1_forecast_drift_beside.sql",
    "tests/integration/check_one_look_analyses.py",
)


def tracked_text_files():
    """Each file git tracks or would add that reads as UTF-8 text, by its
    repository path, with HTML entities decoded; the wording file, which
    holds the patterns the scans look for, is left out."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPOSITORY,
        capture_output=True,
        check=True,
    )
    texts = {}
    for name in listed.stdout.decode("utf-8").split("\0"):
        path = REPOSITORY / name
        if not name or not path.is_file() or name == WORDING_PATTERNS:
            continue
        try:
            texts[name] = html.unescape(path.read_bytes().decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return texts


def questions_not_on_the_page(text):
    """The question ids the text names that are neither answered nor retired."""
    unknown = []
    for named in QUESTION_ID.finditer(html.unescape(text)):
        number = int(named["number"])
        is_listed = number in ANSWERED or number in RETIRED
        if named["variant"] or not is_listed:
            unknown.append(named.group(0))
    return unknown


def files_naming_unlisted_questions(texts):
    """Each file that names a question off the page, with the ids it names."""
    unknown = {}
    for name, text in texts.items():
        named = questions_not_on_the_page(text)
        if named:
            unknown[name] = sorted(set(named))
    return unknown


def files_worded(wording, texts):
    """Each file holding the wording, with what it holds."""
    worded = {}
    for name, text in texts.items():
        found = wording.findall(text)
        if found:
            worded[name] = found
    return worded


def without_saved_row_labels(texts):
    kept = {}
    for name, text in texts.items():
        if not name.startswith(KEEP_THEIR_ROW_LABELS):
            kept[name] = text
    return kept


def numbers_matched(pattern, text):
    numbers = set()
    for number in re.findall(pattern, text):
        numbers.add(int(number))
    return numbers


def test_the_tracked_files_are_read_as_text():
    texts = tracked_text_files()

    assert "README.md" in texts
    assert "site/index.html" in texts
    assert "docs/research-questions.md" in texts
    assert "dbt/analyses/q8_q28_history.sql" in texts
    assert "tests/test_question_ids.py" in texts
    assert "docs/results/q29_error_by_month.png" not in texts
    assert WORDING_PATTERNS not in texts
    assert sorted(wording_patterns()) == [
        "fixed_in_advance",
        "on_a_register",
        "unanswered",
    ]


def test_the_listed_ids_are_the_questions_pages_own():
    page = QUESTIONS.read_text(encoding="utf-8")
    retired_table = page.split("\n## Retired\n", 1)[1].split("\n## ", 1)[0]

    assert numbers_matched(r"(?m)^### Q(\d+)\. ", page) == set(ANSWERED)
    assert numbers_matched(r"(?m)^\| Q(\d+) \|", retired_table) == set(RETIRED)


def test_a_question_id_outside_the_answered_and_retired_ones_is_recognised():
    answered_and_retired = "Q1, Q28's tie order, q8_q28_history.sql, Q7 and Q21."
    sample = (
        "Q" + "4, q" + "27_forecast, Q2" + "-RF, q2" + "rf_restatement and Q22" + "-RF"
    )

    assert questions_not_on_the_page(answered_and_retired) == []
    assert questions_not_on_the_page(sample) == [
        "Q" + "4",
        "q" + "27",
        "Q2" + "-RF",
        "q2" + "rf",
        "Q22" + "-RF",
    ]


@pytest.mark.parametrize(
    "separator",
    [
        pytest.param("-", id="hyphen"),
        pytest.param("‑", id="non-breaking-hyphen"),
        pytest.param("−", id="minus-sign"),
        pytest.param("_", id="underscore"),
        pytest.param("\u2013", id="en-dash"),
        pytest.param("", id="nothing"),
    ],
)
def test_a_variant_suffix_is_recognised_after_each_separator(separator):
    for suffix in ("RF", "R1RF", "r2rf"):
        variant = "Q2" + separator + suffix

        assert questions_not_on_the_page(f"the {variant} analysis") == [variant]


@pytest.mark.parametrize(
    "entity",
    [
        pytest.param("&minus;", id="minus-entity"),
        pytest.param("&ndash;", id="en-dash-entity"),
        pytest.param("&#8209;", id="non-breaking-hyphen-entity"),
        pytest.param("&#x2011;", id="hexadecimal-entity"),
    ],
)
def test_a_variant_suffix_is_recognised_behind_an_html_entity(entity):
    assert len(questions_not_on_the_page("Q22" + entity + "RF")) == 1


def test_a_settlement_run_named_after_an_answered_question_is_not_a_variant():
    assert questions_not_on_the_page("Q2 RF figures") == []


def test_no_tracked_file_names_a_question_the_questions_page_does_not_hold():
    assert files_naming_unlisted_questions(tracked_text_files()) == {}


def test_no_tracked_file_names_a_question_by_the_word_and_a_number():
    assert QUESTION_BY_WORD.findall(
        "see Question" + " 26, question" + "\n9 and Questions" + " 31 to 36"
    ) == [
        "Question" + " 2",
        "question" + "\n9",
        "Questions" + " 3",
    ]
    assert files_worded(QUESTION_BY_WORD, tracked_text_files()) == {}


def test_no_tracked_file_words_a_question_as_unanswered():
    assert files_worded(UNANSWERED_WORDING, tracked_text_files()) == {}


def test_only_saved_outputs_keep_the_fixed_in_advance_wording():
    files = without_saved_row_labels(tracked_text_files())

    assert files_worded(FIXED_IN_ADVANCE_WORDING, files) == {}


def test_only_saved_outputs_keep_the_on_a_register_wording():
    files = without_saved_row_labels(tracked_text_files())

    assert files_worded(ON_A_REGISTER_WORDING, files) == {}
