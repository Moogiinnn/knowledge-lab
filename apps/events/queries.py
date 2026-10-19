from django.db.models import Count, OuterRef, QuerySet, Subquery

from .models import GradeRevision


def with_final_grade(events: QuerySet) -> QuerySet:
    """Annotate final_* from the latest GradeRevision (None when never revised)."""
    latest = GradeRevision.objects.filter(event=OuterRef("pk")).order_by("-revised_at", "-id")
    return events.annotate(
        final_correct=Subquery(latest.values("correct")[:1]),
        final_score=Subquery(latest.values("score")[:1]),
        final_tests_passed=Subquery(latest.values("tests_passed")[:1]),
        final_tests_total=Subquery(latest.values("tests_total")[:1]),
        final_source=Subquery(latest.values("source")[:1]),
        final_feedback=Subquery(latest.values("feedback")[:1]),
        revision_count=Count("revisions"),
    )


def effective(event, field: str):
    """The final value of `field` (correct, score, tests_passed, tests_total)."""
    if event.final_source is not None:
        return getattr(event, f"final_{field}")
    return getattr(event, field)
