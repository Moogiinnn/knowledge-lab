"""Automatic grading of SHORT questions.

Every graded event records GRADER_VERSION so results produced by different grading
rules can be told apart later. Bump it whenever normalisation or matching changes.
"""

import unicodedata
from dataclasses import dataclass

GRADER_VERSION = "short-1"


@dataclass(frozen=True)
class Grade:
    correct: bool | None
    score: float | None
    tests_passed: int | None = None
    tests_total: int | None = None


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split()).casefold()


def grade_short(answer: str, accepted_answers: list[str]) -> Grade:
    if not accepted_answers:
        raise ValueError("A SHORT question version must have at least one accepted answer")
    accepted = {normalize(a) for a in accepted_answers}
    correct = normalize(answer) in accepted
    return Grade(correct=correct, score=1.0 if correct else 0.0)
