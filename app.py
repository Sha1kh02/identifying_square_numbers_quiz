"""Streamlit interface for the numeracy training quiz.

This module is the presentation layer only. It reads input, calls into
``quiz_logic`` for every decision and ``storage`` for every read or write, and
renders whatever comes back. Keeping it thin is what allows the rest of the
project to be tested without launching a browser.

Run locally with::

    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from quiz_logic import (
    CATEGORY_LABELS,
    DIFFICULTY_LIMITS,
    InvalidAnswerError,
    QuizError,
    QuizSession,
    SquareNumberGenerator,
    grade_band,
)
from storage import ResultsStore, StorageError

QUESTION_COUNTS = (4, 8, 12)

st.set_page_config(page_title="Square Numbers Quiz", page_icon="\u25a0", layout="centered")


def get_store() -> ResultsStore:
    """Return the results store, created once per browser session."""
    if "store" not in st.session_state:
        st.session_state.store = ResultsStore()
    return st.session_state.store


def reset_session() -> None:
    """Clear the active quiz so the setup screen is shown again."""
    for key in ("session", "feedback", "saved"):
        st.session_state.pop(key, None)


def start_quiz(name: str, difficulty: str, count: int) -> None:
    """Build a new :class:`QuizSession` and store it in the Streamlit state.

    Validation failures are surfaced as an inline message rather than an
    exception page, so a mistyped name never loses the participant's place.
    """
    generator = SquareNumberGenerator()
    try:
        questions = generator.generate(difficulty=difficulty, count=count)
        st.session_state.session = QuizSession(
            participant=name,
            difficulty=difficulty,
            questions=questions,
            topic=generator.topic,
        )
    except (QuizError, ValueError) as exc:
        st.error(str(exc))
        return
    st.session_state.feedback = None
    st.session_state.saved = False


def render_setup() -> None:
    """Draw the screen shown before a quiz starts."""
    st.title("Square Numbers Quiz")
    st.write(
        "A short numeracy check covering square numbers. Answer the questions, "
        "get an explanation for each one, and your score is saved to the shared "
        "results file at the end."
    )

    name = st.text_input("Your name", placeholder="e.g. Alex Morgan")
    difficulty = st.select_slider(
        "Difficulty",
        options=list(DIFFICULTY_LIMITS),
        value="easy",
        help="Sets how large the numbers get. Easy stays inside the times tables.",
    )
    count = st.radio("Number of questions", QUESTION_COUNTS, horizontal=True)

    if st.button("Start quiz", type="primary"):
        if not name.strip():
            st.warning("Enter your name to start.")
        else:
            start_quiz(name, difficulty, int(count))
            st.rerun()


def render_question() -> None:
    """Draw the current question, handle the answer, and show feedback."""
    session: QuizSession = st.session_state.session
    st.progress(session.answered / session.total)
    st.caption(f"Question {session.answered + 1} of {session.total}")

    question = session.current_question()
    st.subheader(question.prompt)

    feedback = st.session_state.get("feedback")
    if feedback:
        # Feedback belongs to the question just answered, so show it and wait
        # for the participant to move on rather than jumping straight ahead.
        if feedback["correct"]:
            st.success("Correct.")
        else:
            st.error(f"Not quite. The answer is {feedback['answer']}.")
        st.info(feedback["explanation"])
        if st.button("Next question", type="primary"):
            st.session_state.feedback = None
            st.rerun()
        return

    if question.options:
        response = st.radio("Choose an answer", question.options, index=None)
    else:
        response = st.text_input("Your answer", placeholder="Whole numbers only")

    if st.button("Submit answer", type="primary"):
        if response is None:
            st.warning("Select an answer before submitting.")
            return
        try:
            was_correct = session.submit(response)
        except InvalidAnswerError as exc:
            st.warning(str(exc))
            return
        st.session_state.feedback = {
            "correct": was_correct,
            "answer": question.correct_answer,
            "explanation": question.explanation,
        }
        st.rerun()


def render_results() -> None:
    """Draw the end-of-quiz summary and save the attempt once."""
    session: QuizSession = st.session_state.session
    score = session.score()

    st.title("Your result")
    left, right = st.columns(2)
    left.metric("Score", f"{score}%")
    right.metric("Band", grade_band(score))
    st.write(f"{session.correct_count} correct out of {session.total}.")

    weakest = session.weakest_category()
    if weakest == "none":
        st.success("Every question correct. Nothing to review.")
    else:
        st.warning(f"Area to review: {CATEGORY_LABELS.get(weakest, weakest)}")

    if not st.session_state.get("saved"):
        try:
            get_store().save(session.to_record())
            st.session_state.saved = True
        except StorageError as exc:
            st.error(f"Result not saved. {exc}")

    with st.expander("Review your answers"):
        for index, (question, given, was_correct) in enumerate(session.responses, start=1):
            mark = "Correct" if was_correct else "Incorrect"
            st.markdown(f"**{index}. {question.prompt}**")
            st.caption(f"{mark} \u2014 you answered '{given}'. {question.explanation}")

    if st.button("Take it again"):
        reset_session()
        st.rerun()


def render_history() -> None:
    """Draw the saved attempts and the export control."""
    store = get_store()
    try:
        rows = store.load()
        summary = store.summary()
    except StorageError as exc:
        st.error(f"Results could not be loaded. {exc}")
        return

    if not rows:
        st.write("No attempts recorded yet. Finish a quiz and it will appear here.")
        return

    first, second, third = st.columns(3)
    first.metric("Attempts", summary["attempts"])
    second.metric("Average score", f"{summary['average_score']}%")
    third.metric(
        "Most missed",
        CATEGORY_LABELS.get(str(summary["most_missed"]), str(summary["most_missed"])),
    )

    st.dataframe(rows, hide_index=True)
    st.download_button(
        "Download results as CSV",
        data=store.export_csv(),
        file_name="quiz_results.csv",
        mime="text/csv",
    )


def main() -> None:
    """Route between the quiz and the results view."""
    quiz_tab, results_tab = st.tabs(["Quiz", "Results"])

    with quiz_tab:
        session = st.session_state.get("session")
        if session is None:
            render_setup()
        elif session.is_complete:
            render_results()
        else:
            render_question()

    with results_tab:
        render_history()


if __name__ == "__main__":
    main()
