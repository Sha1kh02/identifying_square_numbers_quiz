"""Unit tests for the quiz logic and the CSV results store.

The tests cover the following:
 -the pure helper functions.
 -the behaviour of the generator and session classes.
 -the storage layer's handling of missing ordamaged files.
  
 Streamlit is never imported, so the whole suite runs in a headless CI job in under a second.
"""

from __future__ import annotations

import csv

import pytest

import quiz_logic as ql
from storage import FIELDNAMES, ResultsStore, StorageError


# --- Pure functions -------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 4, 9, 144, 10_000])
def test_is_square_accepts_perfect_squares(value: int) -> None:
    assert ql.is_square(value) is True


@pytest.mark.parametrize("value", [2, 3, 8, 10, 99, 9_999, -4])
def test_is_square_rejects_non_squares(value: int) -> None:
    assert ql.is_square(value) is False


def test_is_square_is_exact_for_large_values() -> None:
    """A float-based check fails here; math.isqrt does not."""
    assert ql.is_square(10**16) is True
    assert ql.is_square(10**16 + 1) is False


@pytest.mark.parametrize("value", ["16", 16.0, True, None])
def test_is_square_rejects_non_integers(value: object) -> None:
    with pytest.raises(TypeError):
        ql.is_square(value)  # type: ignore[arg-type]


def test_normalise_answer_strips_and_lowers() -> None:
    assert ql.normalise_answer("  YES  ") == "yes"
    assert ql.normalise_answer("Not   Sure") == "not sure"


def test_parse_integer_answer_reads_padded_input() -> None:
    assert ql.parse_integer_answer("  42 ") == 42


@pytest.mark.parametrize("value", ["", "   ", "seven", "4.5"])
def test_parse_integer_answer_rejects_bad_input(value: str) -> None:
    with pytest.raises(ql.InvalidAnswerError):
        ql.parse_integer_answer(value)


def test_score_percentage_rounds_to_one_decimal() -> None:
    assert ql.score_percentage(2, 3) == 66.7
    assert ql.score_percentage(0, 4) == 0.0
    assert ql.score_percentage(4, 4) == 100.0


@pytest.mark.parametrize(("correct", "total"), [(1, 0), (5, 4), (-1, 3)])
def test_score_percentage_rejects_impossible_scores(correct: int, total: int) -> None:
    with pytest.raises(ValueError):
        ql.score_percentage(correct, total)


@pytest.mark.parametrize(
    ("percentage", "expected"),
    [(100, "Confident"), (80, "Confident"), (60, "Competent"), (40, "Developing"), (0, "Needs support")],
)
def test_grade_band_boundaries(percentage: float, expected: str) -> None:
    assert ql.grade_band(percentage) == expected


# --- Question generation --------------------------------------------------


def test_generator_is_deterministic_with_a_seed() -> None:
    generator = ql.SquareNumberGenerator()
    first = generator.generate("medium", 8, seed=99)
    second = generator.generate("medium", 8, seed=99)
    assert [q.prompt for q in first] == [q.prompt for q in second]


def test_generator_covers_every_category() -> None:
    questions = ql.SquareNumberGenerator().generate("hard", 4, seed=1)
    assert {q.category for q in questions} == set(ql.CATEGORY_LABELS)


def test_generated_answers_are_correct() -> None:
    """Every generated question must be answerable from its own prompt."""
    for question in ql.SquareNumberGenerator().generate("hard", 40, seed=7):
        if question.category == "multiple_choice":
            squares = [o for o in question.options if ql.is_square(int(o))]
            assert squares == [question.correct_answer]
        if question.options:
            assert question.correct_answer in question.options


def test_generator_rejects_unknown_difficulty() -> None:
    with pytest.raises(ql.InvalidDifficultyError):
        ql.SquareNumberGenerator().generate("impossible", 4)


def test_generator_rejects_non_positive_count() -> None:
    with pytest.raises(ValueError):
        ql.SquareNumberGenerator().generate("easy", 0)


# --- Session behaviour ----------------------------------------------------


def build_session(count: int = 4) -> ql.QuizSession:
    """Helper returning a seeded session for the tests below."""
    questions = ql.SquareNumberGenerator().generate("easy", count, seed=5)
    return ql.QuizSession(participant="Test User", difficulty="easy", questions=questions)


def test_session_scores_all_correct_answers() -> None:
    session = build_session()
    while not session.is_complete:
        session.submit(session.current_question().correct_answer)
    assert session.correct_count == session.total
    assert session.score() == 100.0
    assert session.weakest_category() == "none"


def test_session_flags_the_weakest_category() -> None:
    session = build_session()
    first = session.current_question()
    session.submit("definitely wrong")
    while not session.is_complete:
        session.submit(session.current_question().correct_answer)
    assert session.weakest_category() == first.category


def test_session_rejects_a_blank_participant() -> None:
    questions = ql.SquareNumberGenerator().generate("easy", 2, seed=3)
    with pytest.raises(ValueError):
        ql.QuizSession(participant="   ", difficulty="easy", questions=questions)


def test_session_rejects_an_empty_question_list() -> None:
    with pytest.raises(ValueError):
        ql.QuizSession(participant="Test User", difficulty="easy", questions=[])


def test_session_raises_once_complete() -> None:
    session = build_session(count=1)
    session.submit(session.questions[0].correct_answer)
    with pytest.raises(ql.QuizError):
        session.current_question()


def test_free_text_question_validates_input() -> None:
    questions = [q for q in ql.SquareNumberGenerator().generate("easy", 4, seed=5) if not q.options]
    session = ql.QuizSession(participant="Test User", difficulty="easy", questions=questions)
    with pytest.raises(ql.InvalidAnswerError):
        session.submit("not a number")
    assert session.answered == 0


def test_record_matches_the_storage_columns() -> None:
    session = build_session()
    while not session.is_complete:
        session.submit(session.current_question().correct_answer)
    assert set(session.to_record()).issubset(set(FIELDNAMES))


# --- Storage --------------------------------------------------------------


def test_store_creates_the_file_with_headers(tmp_path) -> None:
    store = ResultsStore(tmp_path / "results.csv")
    store.ensure_file()
    with open(store.path, encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == list(FIELDNAMES)


def test_existing_empty_file_still_gets_headers(tmp_path) -> None:
    """An empty file left by an editor must not be mistaken for a ready one.

    Regression test: ensure_file originally checked only whether the path
    existed, so a empty CSV skipped the header write and the first saved
    attempt was consumed as the header row on read.
    """
    path = tmp_path / "results.csv"
    path.touch()
    store = ResultsStore(path)
    store.save({"participant": "Alex", "score_percent": 100.0})
    rows = store.load()
    assert len(rows) == 1
    assert rows[0]["participant"] == "Alex"


def test_headers_are_not_rewritten_for_a_populated_file(tmp_path) -> None:
    """Saving twice must not insert a second header row."""
    store = ResultsStore(tmp_path / "results.csv")
    store.save({"participant": "Alex", "score_percent": 100.0})
    store.save({"participant": "Sam", "score_percent": 50.0})
    with open(store.path, encoding="utf-8") as handle:
        lines = [line for line in handle.read().splitlines() if line.strip()]
    assert len(lines) == 3
    assert len(store.load()) == 2


def test_save_then_load_round_trip(tmp_path) -> None:
    store = ResultsStore(tmp_path / "results.csv")
    store.save({"participant": "Alex", "score_percent": 75.0, "weakest_category": "squaring"})
    rows = store.load()
    assert len(rows) == 1
    assert rows[0]["participant"] == "Alex"
    assert rows[0]["timestamp"]


def test_save_ignores_unknown_columns(tmp_path) -> None:
    store = ResultsStore(tmp_path / "results.csv")
    row = store.save({"participant": "Alex", "unexpected": "value"})
    assert "unexpected" not in row


def test_load_skips_damaged_rows(tmp_path) -> None:
    store = ResultsStore(tmp_path / "results.csv")
    store.save({"participant": "Alex", "score_percent": 50.0})
    with open(store.path, "a", encoding="utf-8") as handle:
        handle.write(",,,,,,,,\n")
    assert len(store.load()) == 1


def test_summary_handles_an_empty_file(tmp_path) -> None:
    summary = ResultsStore(tmp_path / "results.csv").summary()
    assert summary == {"attempts": 0, "average_score": 0.0, "most_missed": "none"}


def test_summary_averages_scores(tmp_path) -> None:
    store = ResultsStore(tmp_path / "results.csv")
    store.save({"participant": "Alex", "score_percent": 40.0, "weakest_category": "squaring"})
    store.save({"participant": "Sam", "score_percent": 60.0, "weakest_category": "squaring"})
    summary = store.summary()
    assert summary == {"attempts": 2, "average_score": 50.0, "most_missed": "squaring"}


def test_export_csv_includes_the_header(tmp_path) -> None:
    store = ResultsStore(tmp_path / "results.csv")
    store.save({"participant": "Alex", "score_percent": 90.0})
    exported = store.export_csv()
    assert exported.splitlines()[0] == ",".join(FIELDNAMES)


def test_unwritable_path_raises_storage_error(tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    store = ResultsStore(blocker / "results.csv")
    with pytest.raises(StorageError):
        store.ensure_file()