"""CSV persistence for completed quiz attempts.

All file access lives here. ``quiz_logic.py`` produces a plain dictionary and
this module is the only thing that knows where that dictionary is written, what
the column order is, and how to recover when the file is missing or damaged.

CSV was chosen over a database because the results are a small, flat, append-only
log that staff outside the project need to open in Excel without any tooling.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_RESULTS_PATH = Path("quiz_results.csv")

FIELDNAMES: tuple[str, ...] = (
    "timestamp",
    "participant",
    "topic",
    "difficulty",
    "questions",
    "correct",
    "score_percent",
    "band",
    "weakest_category",
)


class StorageError(Exception):
    """Raised when results cannot be read from or written to disk."""


class ResultsStore:
    """Reads and writes quiz attempts to a CSV file.

    Args:
        path: Location of the CSV file. Defaults to ``quiz_results.csv`` in the
            working directory.

    Examples:
        >>> store = ResultsStore("attempts.csv")   # doctest: +SKIP
        >>> store.save({"participant": "Sam"})     # doctest: +SKIP
    """

    def __init__(self, path: str | os.PathLike[str] = DEFAULT_RESULTS_PATH) -> None:
        self.path = Path(path)

    def ensure_file(self) -> None:
        """Write the CSV header row unless the file already has content.

        Checks the file size rather than only its existence. An empty file left
        behind by an editor or a fresh checkout would otherwise be treated as
        ready, the header would never be written, and the first saved attempt
        would silently become the header row when the file is read back.

        Raises:
            StorageError: If the file cannot be created.
        """
        try:
            if self.path.exists() and self.path.stat().st_size > 0:
                return
        except OSError as exc:
            raise StorageError(f"Could not inspect {self.path}: {exc}") from exc
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=FIELDNAMES).writeheader()
        except OSError as exc:
            raise StorageError(f"Could not create {self.path}: {exc}") from exc

    def save(self, record: dict[str, object]) -> dict[str, object]:
        """Append one attempt to the CSV and return the row that was written.

        A UTC timestamp is added here rather than in the quiz logic, so that
        the logic layer stays free of side effects and remains easy to test.

        Args:
            record: The dictionary produced by ``QuizSession.to_record``.

        Returns:
            The complete row, including the generated timestamp.

        Raises:
            StorageError: If the file cannot be written.
        """
        self.ensure_file()
        row = {name: "" for name in FIELDNAMES}
        row["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Unknown keys are ignored rather than written, so a change to the quiz
        # logic cannot silently corrupt the column layout.
        row.update({key: value for key, value in record.items() if key in FIELDNAMES})
        try:
            with self.path.open("a", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=FIELDNAMES).writerow(row)
        except OSError as exc:
            raise StorageError(f"Could not write to {self.path}: {exc}") from exc
        return row

    def load(self) -> list[dict[str, str]]:
        """Return every stored attempt, newest last.

        Rows that do not match the expected columns are skipped rather than
        raising, so one hand-edited line cannot take the whole history down.

        Raises:
            StorageError: If the file cannot be read.
        """
        self.ensure_file()
        try:
            with self.path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                return [row for row in reader if row.get("participant")]
        except OSError as exc:
            raise StorageError(f"Could not read {self.path}: {exc}") from exc

    def export_csv(self) -> str:
        """Return the stored attempts as a CSV string for download."""
        rows = self.load()
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in FIELDNAMES})
        return buffer.getvalue()

    def summary(self) -> dict[str, object]:
        """Aggregate the stored attempts for the dashboard.

        Returns:
            A dictionary with the attempt count, the mean score and the
            category missed most often. All values are safe defaults when no
            attempts have been recorded yet.
        """
        rows = self.load()
        if not rows:
            return {"attempts": 0, "average_score": 0.0, "most_missed": "none"}

        scores: list[float] = []
        for row in rows:
            try:
                scores.append(float(row["score_percent"]))
            except (KeyError, TypeError, ValueError):
                continue

        misses: dict[str, int] = {}
        for row in rows:
            category = row.get("weakest_category", "none")
            if category and category != "none":
                misses[category] = misses.get(category, 0) + 1

        return {
            "attempts": len(rows),
            "average_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
            "most_missed": max(misses, key=lambda key: misses[key]) if misses else "none",
        }