import hashlib

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Concept, ConsentLog, Enrollment, Question, QuestionConcept, QuestionVersion


@transaction.atomic
def create_version(
    question: Question,
    *,
    prompt: str,
    concept_weights: dict[str, float],
    accepted_answers=None,
    hints=None,
    difficulty: int = 3,
    code_driver: str = "",
    language: str = QuestionVersion.Language.PYTHON,
) -> QuestionVersion:
    """New DRAFT version with its concept mapping (concept code -> weight, summing to 1)."""
    last = question.versions.aggregate(m=Max("version"))["m"] or 0
    version = QuestionVersion.objects.create(
        question=question,
        version=last + 1,
        prompt=prompt,
        accepted_answers=list(accepted_answers or []),
        hints=list(hints or []),
        difficulty=difficulty,
        code_driver=code_driver,
        language=language,
    )
    concepts = {c.code: c for c in Concept.objects.filter(course=question.course, code__in=concept_weights)}
    missing = set(concept_weights) - set(concepts)
    if missing:
        raise ValueError(f"unknown concepts in course {question.course.code}: {sorted(missing)}")
    QuestionConcept.objects.bulk_create(
        QuestionConcept(question_version=version, concept=concepts[code], weight=w)
        for code, w in concept_weights.items()
    )
    return version


@transaction.atomic
def publish_version(version: QuestionVersion) -> QuestionVersion:
    """Publish a DRAFT version and make it the one students receive. The previously
    current version is retired; events keep pointing at the version they were made on."""
    question = Question.objects.select_for_update().get(pk=version.question_id)
    previous = question.current_version
    version.status = QuestionVersion.Status.PUBLISHED
    version.published_at = timezone.now()
    version.save(update_fields=["status", "published_at"])
    question.current_version = version
    question.save(update_fields=["current_version"])
    if previous is not None and previous.pk != version.pk:
        previous.status = QuestionVersion.Status.RETIRED
        previous.save(update_fields=["status"])
    return version


@transaction.atomic
def record_consent(enrollment: Enrollment, decision: str) -> ConsentLog:
    """GIVEN sets consent; DECLINED and WITHDRAWN both clear it (excluded from exports)."""
    enrollment = Enrollment.objects.select_for_update().select_related("course").get(pk=enrollment.pk)
    now = timezone.now()
    if decision == ConsentLog.Decision.GIVEN:
        enrollment.consent_given_at, enrollment.consent_withdrawn_at = now, None
    elif decision in (ConsentLog.Decision.DECLINED, ConsentLog.Decision.WITHDRAWN):
        enrollment.consent_given_at, enrollment.consent_withdrawn_at = None, now
    else:
        raise ValueError(f"unknown consent decision {decision!r}")
    enrollment.save(update_fields=["consent_given_at", "consent_withdrawn_at"])
    text_hash = hashlib.sha256(enrollment.course.consent_text.encode()).hexdigest()
    return ConsentLog.objects.create(enrollment=enrollment, decision=decision, consent_text_sha256=text_hash)


@transaction.atomic
def copy_as_draft(version: QuestionVersion) -> QuestionVersion:
    """Start a new DRAFT version from an existing one (prompt, answers, hints, concept
    weights and code test cases). The source version is left untouched."""
    from apps.grading.models import CodeTestCase

    weights = {link.concept.code: link.weight for link in version.concept_links.select_related("concept")}
    draft = create_version(
        version.question,
        prompt=version.prompt,
        concept_weights=weights,
        accepted_answers=version.accepted_answers,
        hints=version.hints,
        difficulty=version.difficulty,
        code_driver=version.code_driver,
        language=version.language,
    )
    CodeTestCase.objects.bulk_create(
        CodeTestCase(
            question_version=draft,
            order=case.order,
            stdin=case.stdin,
            expected_stdout=case.expected_stdout,
            is_hidden=case.is_hidden,
        )
        for case in version.test_cases.all()
    )
    return draft


@transaction.atomic
def retire_version(version: QuestionVersion) -> None:
    """Stop offering a question: retire its published version and clear current_version."""
    question = Question.objects.select_for_update().get(pk=version.question_id)
    if question.current_version_id == version.pk:
        question.current_version = None
        question.save(update_fields=["current_version"])
    version.status = QuestionVersion.Status.RETIRED
    version.save(update_fields=["status"])
