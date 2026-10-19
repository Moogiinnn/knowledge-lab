import uuid

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.curriculum.models import Concept, Course, Enrollment, Question
from apps.curriculum.services import create_version, publish_version
from apps.events.services import submit_answer


@pytest.fixture
def course(db):
    course = Course.objects.create(code="ds-2026", name="Өгөгдлийн бүтэц")
    for code in ["array", "stack", "queue", "bfs"]:
        Concept.objects.create(course=course, code=code, name=code)
    return course


def make_student(course, username, *, consent=True):
    user = get_user_model().objects.create_user(username=username, password="x-test-pass-123")
    Enrollment.objects.create(course=course, user=user, consent_given_at=timezone.now() if consent else None)
    return user


@pytest.fixture
def student(course):
    return make_student(course, "S001")


def make_short_question(course, code="q-sum", answers=("10",), weights=None, hints=()):
    question = Question.objects.create(course=course, code=code, title=code, kind=Question.Kind.SHORT)
    version = create_version(
        question,
        prompt="s = 1+2+3+4",
        accepted_answers=list(answers),
        concept_weights=weights or {"array": 0.6, "stack": 0.4},
        hints=list(hints),
    )
    publish_version(version)
    question.refresh_from_db()
    return question


@pytest.fixture
def short_question(course):
    return make_short_question(course)


@pytest.fixture
def submit():
    session = uuid.uuid4()

    def _submit(user, question, answer, key=None, **kwargs):
        return submit_answer(
            user=user,
            question=question,
            idempotency_key=key or uuid.uuid4(),
            answer=answer,
            study_session_id=session,
            **kwargs,
        )

    return _submit
