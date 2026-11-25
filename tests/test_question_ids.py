"""Every question a tracked file names is one the questions page holds."""

import re
import subprocess
from pathlib import Path

REPOSITORY = Path(__file__).parent.parent
ANSWERED = (1, 2, 3, 5, 6, 8, 10, 12, 22, 23, 24, 25, 28, 29, 30)
RETIRED = (7, 11, 13, 14, 17, 18, 19, 20, 21)
# A question id as prose writes it (Q12) or as a file name does (q12_...),
# with the settlement-run suffix a variant of a question would carry.
QUESTION_ID = re.compile(
    r"(?<![0-9A-Za-z])[Qq](?P<number>\d{1,2})(?P<variant>-?[Rr](?:\d[Rr])?[Ff])?"
    r"(?![0-9A-Za-z])"
)
# Wording for a question without an answer, and for a method fixed in
# advance; each is built from its parts so this file does not hold it.
UNANSWERED_WORDING = re.compile(
    "|".join(
        [
            "not" + r"\s+" + "pursued",
            "still" + r"\s+" + "to" + r"\s+" + "come",
            "not" + r"\s+" + "answered" + r"\s+" + "here",
        ]
    ),
    re.IGNORECASE,
)
FIXED_IN_ADVANCE_WORDING = re.compile("pre" + "-" + "registered", re.IGNORECASE)
# Saved outputs and the queries as run keep the row labels they were read
# with; one analysis writes those labels and one check expects them.
KEEP_THEIR_ROW_LABELS = (
    "docs/results/",
    "dbt/analyses/q1_forecast_drift_beside.sql",
    "tests/integration/check_one_look_analyses.py",
)


def tracked_text_files():
    """Each file git tracks or would add that reads as UTF-8 text, by its
    repository path."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPOSITORY,
        capture_output=True,
        check=True,
    )
    texts = {}
    for name in listed.stdout.decode("utf-8").split("\0"):
        path = REPOSITORY / name
        if not name or not path.is_file():
            continue
        try:
            texts[name] = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
    return texts


def questions_not_on_the_page(text):
    """The question ids the text names that are neither answered nor retired."""
    unknown = []
    for named in QUESTION_ID.finditer(text):
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


def test_the_tracked_files_are_read_as_text():
    texts = tracked_text_files()

    assert "README.md" in texts
    assert "site/index.html" in texts
    assert "docs/research-questions.md" in texts
    assert "dbt/analyses/q8_q28_history.sql" in texts
    assert "tests/test_question_ids.py" in texts
    assert "docs/results/q29_error_by_month.png" not in texts


def test_a_question_id_outside_the_answered_and_retired_ones_is_recognised():
    answered_and_retired = "Q1, Q28's tie key, q8_q28_history.sql, Q7 and Q21."
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


def test_no_tracked_file_names_a_question_the_questions_page_does_not_hold():
    assert files_naming_unlisted_questions(tracked_text_files()) == {}


def test_no_tracked_file_words_a_question_as_unanswered():
    assert files_worded(UNANSWERED_WORDING, tracked_text_files()) == {}


def test_only_saved_outputs_keep_the_fixed_in_advance_wording():
    files = without_saved_row_labels(tracked_text_files())

    assert files_worded(FIXED_IN_ADVANCE_WORDING, files) == {}
