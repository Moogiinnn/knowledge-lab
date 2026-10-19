"""The database itself rejects the data problems found in the v1 audit."""

import uuid

import pytest
from django.db import DatabaseError, connection, transaction

from apps.curriculum.models import Concept, ConceptEdge, Course, Question, QuestionConcept, QuestionVersion
from apps.curriculum.services import create_version, publish_version
from apps.events.models import SubmissionEvent

from .conftest import make_short_question


def check_deferred():
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


def draft(course, code="q-d"):
    q = Question.objects.create(course=course, code=code, title=code, kind="SHORT")
    return QuestionVersion.objects.create(question=q, version=1, prompt="p", accepted_answers=["1"])


def test_weights_may_be_written_row_by_row(course):
    v = draft(course)
    with transaction.atomic():
        for code, w in [("array", 0.5), ("stack", 0.3), ("queue", 0.2)]:
            QuestionConcept.objects.create(question_version=v, concept=Concept.objects.get(code=code), weight=w)
        check_deferred()


def test_weights_must_sum_to_one(course):
    v = draft(course)
    with pytest.raises(DatabaseError, match="WEIGHT_SUM"), transaction.atomic():
        QuestionConcept.objects.create(question_version=v, concept=Concept.objects.get(code="array"), weight=0.7)
        QuestionConcept.objects.create(question_version=v, concept=Concept.objects.get(code="stack"), weight=0.5)
        check_deferred()
    # A single concept with weight < 1 is also rejected (v1 only checked 2+ rows).
    with pytest.raises(DatabaseError, match="WEIGHT_SUM"), transaction.atomic():
        QuestionConcept.objects.create(question_version=v, concept=Concept.objects.get(code="array"), weight=0.3)
        check_deferred()


def test_concept_from_another_course_is_rejected(course):
    other = Course.objects.create(code="other", name="other")
    foreign = Concept.objects.create(course=other, code="array", name="array")
    v = draft(course)
    with pytest.raises(DatabaseError, match="CROSS_COURSE"), transaction.atomic():
        QuestionConcept.objects.create(question_version=v, concept=foreign, weight=1)
    with pytest.raises(DatabaseError, match="CROSS_COURSE"), transaction.atomic():
        ConceptEdge.objects.create(source=Concept.objects.get(course=course, code="array"), target=foreign)


def test_publishing_requires_mapping(course):
    v = draft(course)
    with pytest.raises(DatabaseError, match="NO_MAPPING"), transaction.atomic():
        publish_version(v)
        check_deferred()


def test_published_version_and_mapping_are_frozen(course):
    q = make_short_question(course)
    v = q.current_version
    with pytest.raises(DatabaseError, match="VERSION_FROZEN"), transaction.atomic():
        QuestionVersion.objects.filter(pk=v.pk).update(prompt="changed")
    with pytest.raises(DatabaseError, match="VERSION_FROZEN"), transaction.atomic():
        QuestionConcept.objects.filter(question_version=v).update(weight=0.5)
    with pytest.raises(DatabaseError, match="VERSION_FROZEN"), transaction.atomic():
        QuestionConcept.objects.filter(question_version=v).delete()
    # Retiring is the one allowed change.
    QuestionVersion.objects.filter(pk=v.pk).update(status="RETIRED")


def test_current_version_must_be_published_and_own(course):
    q1 = make_short_question(course, code="q1")
    q2 = Question.objects.create(course=course, code="q2", title="q2", kind="SHORT")
    with pytest.raises(DatabaseError, match="BAD_CURRENT_VERSION"), transaction.atomic():
        Question.objects.filter(pk=q2.pk).update(current_version=q1.current_version)
    v = create_version(q2, prompt="p", accepted_answers=["1"], concept_weights={"array": 1})
    with pytest.raises(DatabaseError, match="BAD_CURRENT_VERSION"), transaction.atomic():
        Question.objects.filter(pk=q2.pk).update(current_version=v)


def test_events_are_append_only(student, short_question, submit):
    event, _ = submit(student, short_question, "10")
    with pytest.raises(DatabaseError, match="APPEND_ONLY"), transaction.atomic():
        SubmissionEvent.objects.filter(pk=event.pk).update(score=0.5)
    with pytest.raises(DatabaseError, match="APPEND_ONLY"), transaction.atomic():
        event.answer.delete()
    # The ORM already refuses (PROTECT); raw SQL is stopped by the trigger.
    with pytest.raises(DatabaseError, match="APPEND_ONLY"), transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("DELETE FROM events_submissionevent WHERE event_id = %s", [event.pk])


def test_event_delete_needs_explicit_opt_in(student, short_question, submit):
    event, _ = submit(student, short_question, "10")
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SET LOCAL knowledge_lab.allow_event_delete = 'on'")
        event.answer.delete()
        SubmissionEvent.objects.filter(pk=event.pk).delete()
    assert not SubmissionEvent.objects.exists()


def test_event_must_reference_consistent_published_version(course, student, short_question, submit):
    event, _ = submit(student, short_question, "10")
    other = make_short_question(course, code="q-other")
    fields = {f.attname: getattr(event, f.attname) for f in SubmissionEvent._meta.concrete_fields}
    fields.update(
        event_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4(),
        attempt_index=99,
        question_version_id=other.current_version_id,
    )
    with pytest.raises(DatabaseError, match="BAD_EVENT_REF"), transaction.atomic():
        SubmissionEvent.objects.create(**fields)
