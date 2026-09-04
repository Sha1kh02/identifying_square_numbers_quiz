"""Core quiz logic for the numeracy training tool.

This module holds every rule the quiz needs and deliberately contains no
Streamlit code and no file access. Keeping it isolated means the logic can be
unit tested on its own, and it keeps the presentation layer (``app.py``) and
the persistence layer (``storage.py``) free of business rules.

The generator classes are the extension point: ``QuestionGenerator`` defines
the contract and ``SquareNumberGenerator`` supplies the first topic. A second
topic (for example a policy or procedure quiz) can be added later by writing
one more subclass, without touching the session, storage or interface code.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# Upper bound of the number pool used for each difficulty tier. Easy keeps the
# candidates inside the times tables most people recall; hard pushes past the
# point where the answer can be recognised without working it out.
DIFFICULTY_LIMITS: dict[str, int] = {"easy": 12, "medium": 25, "hard": 50}

# Human-readable labels for the question categories, used in the weak-area
# feedback shown at the end of a session.
CATEGORY_LABELS: dict[str, str] = {
    "recognition": "Recognising a square number",
    "multiple_choice": "Picking the square from a set",
    "square_root": "Finding a square root",
    "squaring": "Squaring a number",
}


class QuizError(Exception):
    """Base class for every error raised by the quiz domain logic."""


class InvalidDifficultyError(QuizError):
    """Raised when a difficulty outside :data:`DIFFICULTY_LIMITS` is requested."""


class InvalidAnswerError(QuizError):
    """Raised when a submitted answer cannot be interpreted."""


def is_square(n: int) -> bool:
    """Return ``True`` when ``n`` is a perfect square.

    Uses :func:`math.isqrt`, which works in integer arithmetic and therefore
    avoids the floating point rounding errors that ``int(n ** 0.5)`` produces
    for large values.

    Args:
        n: The integer to test.

    Returns:
        ``True`` if a whole number squared gives ``n``, otherwise ``False``.
        Negative numbers always return ``False``.

    Raises:
        TypeError: If ``n`` is not an integer.

    Examples:
        >>> is_square(49)
        True
        >>> is_square(50)
        False
    """
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError(f"is_square expects an integer, got {type(n).__name__}")
    if n < 0:
        return False
    root = math.isqrt(n)
    return root * root == n


def normalise_answer(raw: object) -> str:
    """Reduce a raw answer to a comparable form.

    Trims surrounding whitespace, collapses internal runs of whitespace and
    lowercases the result, so that ``" Yes "`` and ``"yes"`` are treated as the
    same response.

    Args:
        raw: Any value; it is converted to ``str`` first.

    Returns:
        The normalised string.
    """
    return " ".join(str(raw).strip().lower().split())


def parse_integer_answer(raw: object) -> int:
    """Convert a typed answer into an integer.

    Args:
        raw: The value entered by the participant.

    Returns:
        The parsed integer.

    Raises:
        InvalidAnswerError: If the value is blank or is not a whole number.
    """
    text = normalise_answer(raw)
    if not text:
        raise InvalidAnswerError("Enter an answer before submitting.")
    try:
        return int(text)
    except ValueError as exc:
        raise InvalidAnswerError(f"'{raw}' is not a whole number.") from exc


def score_percentage(correct: int, total: int) -> float:
    """Return the score as a percentage rounded to one decimal place.

    Args:
        correct: Number of questions answered correctly.
        total: Number of questions asked.

    Returns:
        The percentage score.

    Raises:
        ValueError: If ``total`` is not positive, or ``correct`` falls outside
            the range ``0..total``.
    """
    if total <= 0:
        raise ValueError("total must be greater than zero")
    if not 0 <= correct <= total:
        raise ValueError("correct must be between 0 and total")
    return round(correct / total * 100, 1)


def grade_band(percentage: float) -> str:
    """Map a percentage onto a plain-language competency band."""
    if not 0 <= percentage <= 100:
        raise ValueError("percentage must be between 0 and 100")
    if percentage >= 80:
        return "Confident"
    if percentage >= 60:
        return "Competent"
    if percentage >= 40:
        return "Developing"
    return "Needs support"


@dataclass(frozen=True)
class Question:
    """A single quiz question and its accepted answer.

    Attributes:
        prompt: The text shown to the participant.
        correct_answer: The expected answer, compared after normalisation.
        options: Choices offered. Empty for free-text questions.
        category: Key from :data:`CATEGORY_LABELS`, used for weak-area feedback.
        explanation: Shown after answering, so the quiz teaches as well as tests.
    """

    prompt: str
    correct_answer: str
    options: tuple[str, ...]
    category: str
    explanation: str

    def check(self, response: object) -> bool:
        """Return ``True`` when ``response`` matches the expected answer."""
        return normalise_answer(response) == normalise_answer(self.correct_answer)


class QuestionGenerator(ABC):
    """Contract that every quiz topic must satisfy."""

    topic: str = "Untitled topic"

    @abstractmethod
    def generate(self, difficulty: str, count: int, seed: int | None = None) -> list[Question]:
        """Build a list of questions for one session."""

    @staticmethod
    def _limit(difficulty: str) -> int:
        """Look up the number ceiling for a difficulty tier."""
        key = normalise_answer(difficulty)
        if key not in DIFFICULTY_LIMITS:
            raise InvalidDifficultyError(
                f"Unknown difficulty '{difficulty}'. Choose one of: "
                + ", ".join(DIFFICULTY_LIMITS)
            )
        return DIFFICULTY_LIMITS[key]


class SquareNumberGenerator(QuestionGenerator):
    """Produces square-number questions in four styles.

    Passing a ``seed`` makes generation deterministic, which is what allows the
    unit tests to assert on the exact questions produced.
    """

    topic = "Square numbers"

    def generate(self, difficulty: str, count: int, seed: int | None = None) -> list[Question]:
        """Return ``count`` questions drawn from the four categories in turn.

        Args:
            difficulty: One of ``easy``, ``medium`` or ``hard``.
            count: How many questions to produce; must be positive.
            seed: Optional seed. The same seed and arguments always give the
                same questions.

        Returns:
            A list of :class:`Question` objects.

        Raises:
            InvalidDifficultyError: If ``difficulty`` is not recognised.
            ValueError: If ``count`` is not positive.
        """
        if count <= 0:
            raise ValueError("count must be greater than zero")
        limit = self._limit(difficulty)
        rng = random.Random(seed)

        builders = (
            self._recognition_question,
            self._multiple_choice_question,
            self._square_root_question,
            self._squaring_question,
        )
        # Cycling through the builders guarantees a spread of categories even
        # on a short quiz, rather than leaving the mix to chance.
        return [builders[i % len(builders)](rng, limit) for i in range(count)]

    def _recognition_question(self, rng: random.Random, limit: int) -> Question:
        """Ask whether a given number is a square number."""
        if rng.random() < 0.5:
            value = rng.randint(1, limit) ** 2
        else:
            value = rng.randint(2, limit**2)
            while is_square(value):
                value = rng.randint(2, limit**2)
        answer = "Yes" if is_square(value) else "No"
        if is_square(value):
            explanation = f"{math.isqrt(value)} x {math.isqrt(value)} = {value}."
        else:
            lower = math.isqrt(value)
            explanation = (
                f"{value} sits between {lower}\u00b2 = {lower**2} and "
                f"{lower + 1}\u00b2 = {(lower + 1) ** 2}, so it is not square."
            )
        return Question(
            prompt=f"Is {value} a square number?",
            correct_answer=answer,
            options=("Yes", "No"),
            category="recognition",
            explanation=explanation,
        )

    def _multiple_choice_question(self, rng: random.Random, limit: int) -> Question:
        """Ask which of four numbers is the square number."""
        root = rng.randint(2, limit)
        target = root**2
        distractors: set[int] = set()
        while len(distractors) < 3:
            candidate = target + rng.choice([-3, -2, -1, 1, 2, 3, 5, 7])
            if candidate > 0 and candidate != target and not is_square(candidate):
                distractors.add(candidate)
        options = [target, *distractors]
        rng.shuffle(options)
        return Question(
            prompt="Which of these is a square number?",
            correct_answer=str(target),
            options=tuple(str(option) for option in options),
            category="multiple_choice",
            explanation=f"{root} x {root} = {target}. The others fall between two squares.",
        )

    def _square_root_question(self, rng: random.Random, limit: int) -> Question:
        """Ask for the square root of a perfect square."""
        root = rng.randint(2, limit)
        return Question(
            prompt=f"What is the square root of {root**2}?",
            correct_answer=str(root),
            options=(),
            category="square_root",
            explanation=f"{root} x {root} = {root**2}.",
        )

    def _squaring_question(self, rng: random.Random, limit: int) -> Question:
        """Ask for the square of a number."""
        value = rng.randint(2, limit)
        return Question(
            prompt=f"What is {value} squared?",
            correct_answer=str(value**2),
            options=(),
            category="squaring",
            explanation=f"{value} x {value} = {value**2}.",
        )


@dataclass
class QuizSession:
    """Tracks one participant's progress through a set of questions.

    The session owns no I/O. ``app.py`` drives it and ``storage.py`` writes the
    record it produces, which keeps each layer independently testable.
    """

    participant: str
    difficulty: str
    questions: list[Question]
    topic: str = SquareNumberGenerator.topic
    responses: list[tuple[Question, str, bool]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.questions:
            raise ValueError("A session needs at least one question")
        if not normalise_answer(self.participant):
            raise ValueError("Participant name cannot be blank")

    @property
    def total(self) -> int:
        """Number of questions in the session."""
        return len(self.questions)

    @property
    def answered(self) -> int:
        """Number of questions answered so far."""
        return len(self.responses)

    @property
    def is_complete(self) -> bool:
        """Whether every question has been answered."""
        return self.answered >= self.total

    @property
    def correct_count(self) -> int:
        """Number of correct answers so far."""
        return sum(1 for _, _, was_correct in self.responses if was_correct)

    def current_question(self) -> Question:
        """Return the question awaiting an answer.

        Raises:
            QuizError: If the session is already complete.
        """
        if self.is_complete:
            raise QuizError("The quiz is already complete.")
        return self.questions[self.answered]

    def submit(self, response: object) -> bool:
        """Record an answer to the current question and report whether it was right.

        Free-text questions are validated as integers first, so a typo raises
        :class:`InvalidAnswerError` rather than being silently marked wrong.
        """
        question = self.current_question()
        if not question.options:
            parse_integer_answer(response)
        was_correct = question.check(response)
        self.responses.append((question, normalise_answer(response), was_correct))
        return was_correct

    def score(self) -> float:
        """Percentage score across the questions answered so far."""
        return score_percentage(self.correct_count, self.answered or self.total)

    def weakest_category(self) -> str:
        """Return the category with the most wrong answers.

        Returns ``"none"`` when nothing was answered incorrectly. Ties are
        broken by the order categories first appeared, keeping the result
        deterministic.
        """
        misses: dict[str, int] = {}
        for question, _, was_correct in self.responses:
            if not was_correct:
                misses[question.category] = misses.get(question.category, 0) + 1
        if not misses:
            return "none"
        return max(misses, key=lambda category: misses[category])

    def to_record(self) -> dict[str, object]:
        """Flatten the finished session into a row ready for the CSV store."""
        return {
            "participant": normalise_answer(self.participant).title(),
            "topic": self.topic,
            "difficulty": normalise_answer(self.difficulty),
            "questions": self.answered,
            "correct": self.correct_count,
            "score_percent": self.score(),
            "band": grade_band(self.score()),
            "weakest_category": self.weakest_category(),
        }
