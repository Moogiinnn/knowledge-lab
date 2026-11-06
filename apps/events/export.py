"""Research export: one row per SubmissionEvent, ordered for replay.

Only events of students who consented (or of courses that do not require consent) are
included; synthetic events only on request. No user ids, answers or Django session keys.
"""

import json

from apps.accounts.pseudonym import pseudonymous_id
from apps.curriculum.models import ConceptEdge, Course, Enrollment

from .models import SubmissionEvent
from .queries import effective, with_final_grade

COLUMNS = [
    "event_id",
    "student_id",
    "course_id",
    "session_id",
    "timestamp",
    "question_id",
    "question_version",
    "difficulty",
    "attempt_index",
    "correct",
    "score",
    "tests_passed",
    "tests_total",
    "auto_correct",
    "auto_score",
    "grading_status",
    "grader_version",
    "grade_revisions",
    "response_time_sec",
    "timing_source",
    "server_elapsed_sec",
    "hint_count",
    "assistance_flag",
    "assistance_types",
    "concepts",
    "is_synthetic",
]


def consenting_pseudonyms(course: Course) -> set[str]:
    enrollments = Enrollment.objects.filter(course=course).select_related("course")
    return {pseudonymous_id(e.user_id) for e in enrollments if e.has_research_consent}


def _bool(value):
    return "" if value is None else int(value)


def export_rows(course: Course, *, include_synthetic: bool = False):
    allowed = consenting_pseudonyms(course)
    events = (
        with_final_grade(SubmissionEvent.objects.filter(course=course, student_pseudonym__in=allowed))
        .select_related("course", "question", "question_version")
        .order_by("submitted_at", "event_id")
    )
    if not include_synthetic:
        events = events.filter(is_synthetic=False)

    for e in events.iterator(chunk_size=2000):
        revised = e.final_source is not None
        score = effective(e, "score")
        yield {
            "event_id": str(e.event_id),
            "student_id": e.student_pseudonym,
            "course_id": e.course.code,
            "session_id": str(e.study_session_id),
            "timestamp": e.submitted_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "question_id": e.question.code,
            "question_version": e.question_version.version,
            "difficulty": e.question_version.difficulty,
            "attempt_index": e.attempt_index,
            "correct": _bool(effective(e, "correct")),
            "score": "" if score is None else score,
            "tests_passed": _nullable(effective(e, "tests_passed")),
            "tests_total": _nullable(effective(e, "tests_total")),
            "auto_correct": _bool(e.correct),
            "auto_score": "" if e.score is None else e.score,
            "grading_status": f"REVISED:{e.final_source}" if revised else e.grading_status,
            "grader_version": e.grader_version,
            "grade_revisions": e.revision_count,
            "response_time_sec": _nullable(e.response_time_sec),
            "timing_source": e.timing_source,
            "server_elapsed_sec": _nullable(e.server_elapsed_sec),
            "hint_count": e.hint_count,
            "assistance_flag": _bool(e.assistance_flag),
            "assistance_types": ";".join(e.assistance_types),
            "concepts": json.dumps(e.concept_snapshot, ensure_ascii=False, separators=(",", ":")),
            "is_synthetic": int(e.is_synthetic),
        }


def _nullable(value):
    return "" if value is None else value


def graph_dict(course: Course) -> dict:
    """The course's concepts and concept edges, as `export_graph` writes them for the engine."""
    edges = ConceptEdge.objects.filter(source__course=course).select_related("source", "target")
    return {
        "course": course.code,
        "concepts": [{"code": c.code, "name": c.name, "kind": c.kind} for c in course.concepts.order_by("code")],
        "edges": [
            {
                "source": e.source.code,
                "target": e.target.code,
                "relation": e.relation,
                "initial_weight": e.initial_weight,
                "approved": e.teacher_approved,
                "confidence": e.confidence,
            }
            for e in edges.order_by("source__code", "target__code")
        ],
    }
