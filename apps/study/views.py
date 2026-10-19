"""Student-facing pages. All writes to the research log go through apps.events.services."""

import uuid
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.pseudonym import pseudonymous_id
from apps.accounts.study_session import current_study_session
from apps.curriculum.models import ConsentLog, Enrollment, Question
from apps.curriculum.services import record_consent
from apps.events.models import HintView, SubmissionEvent
from apps.events.queries import effective, with_final_grade
from apps.events.services import IdempotencyConflict, SubmissionError, show_hint, submit_answer
from apps.grading.code import case_passed
from apps.grading.runner import CaseResult

ASSISTANCE_CHOICES = [
    ("AI", "AI хэрэгсэл (ChatGPT г.м.)"),
    ("peer", "Найз, хамт олон"),
    ("teacher", "Багш, туслах"),
    ("material", "Ном, сурах бичиг, интернэт"),
    ("other", "Бусад"),
]


def _active_enrollments(user):
    return Enrollment.objects.filter(user=user, status=Enrollment.Status.ACTIVE).select_related("course")


def _enrollment_or_404(user, course):
    enrollment = _active_enrollments(user).filter(course=course).first()
    if enrollment is None:
        raise Http404
    return enrollment


def _question_or_404(user, question_id):
    question = get_object_or_404(
        Question.objects.select_related("course", "current_version"), pk=question_id, current_version__isnull=False
    )
    return question, _enrollment_or_404(user, question.course)


@login_required
def home(request):
    if request.user.is_staff:
        return redirect("admin:index")
    pseudonym = pseudonymous_id(request.user.pk)
    stats = {
        row["question"]: row
        for row in SubmissionEvent.objects.filter(student_pseudonym=pseudonym)
        .values("question")
        .annotate(attempts=Count("event_id"), last_at=Max("submitted_at"))
    }
    courses = []
    for enrollment in _active_enrollments(request.user).order_by("course__name"):
        questions = (
            enrollment.course.questions.filter(current_version__isnull=False)
            .select_related("current_version")
            .order_by("code")
        )
        courses.append(
            {
                "enrollment": enrollment,
                "questions": [{"question": q, "stats": stats.get(q.pk)} for q in questions],
            }
        )
    return render(request, "study/home.html", {"courses": courses})


@login_required
def consent(request, course_id):
    enrollment = get_object_or_404(_active_enrollments(request.user), course_id=course_id)
    if request.method == "POST":
        decision = request.POST.get("decision")
        allowed = {ConsentLog.Decision.GIVEN, ConsentLog.Decision.DECLINED, ConsentLog.Decision.WITHDRAWN}
        if decision not in allowed:
            messages.error(request, "Сонголтоо хийнэ үү.")
        else:
            record_consent(enrollment, decision)
            messages.success(request, "Таны сонголт хадгалагдлаа. Хүссэн үедээ өөрчилж болно.")
            return redirect("home")
    return render(request, "study/consent.html", {"enrollment": enrollment})


def _opened_key(question) -> str:
    return f"question_opened_at:{question.pk}"


def _mark_opened(request, question):
    """Remember when the student first opened this question since their last submit."""
    request.session.setdefault(_opened_key(question), timezone.now().isoformat())


def _server_elapsed(request, question) -> int | None:
    opened = request.session.get(_opened_key(question))
    if opened is None:
        return None
    seconds = (timezone.now() - datetime.fromisoformat(opened)).total_seconds()
    return max(0, round(seconds))


def _test_chips(event) -> list[dict]:
    """Pass/fail per test case of the event's latest sandbox run (hidden cases unnamed)."""
    run = event.code_runs.filter(infra_error="").order_by("-started_at").first()
    if run is None:
        return []
    cases = {c.order: c for c in event.question_version.test_cases.all()}
    chips = []
    for raw in run.results:
        case = cases.get(raw.get("order"))
        if case is not None:
            label = "Нууц тест" if case.is_hidden else f"Тест {case.order}"
            chips.append({"label": label, "passed": case_passed(case, CaseResult(**raw))})
    return chips


def _question_context(request, question, enrollment, *, answer="", error=None, assistance_flag="", types=()):
    pseudonym = pseudonymous_id(request.user.pk)
    version = question.current_version
    shown = set(
        HintView.objects.filter(student_pseudonym=pseudonym, question_version=version).values_list(
            "hint_index", flat=True
        )
    )
    history = with_final_grade(
        SubmissionEvent.objects.filter(student_pseudonym=pseudonym, question=question).select_related(
            "question_version"
        )
    ).order_by("-attempt_index")
    attempts = [
        {
            "event": e,
            "correct": effective(e, "correct"),
            "score": effective(e, "score"),
            "feedback": e.final_feedback or "",
        }
        for e in history
    ]
    siblings = list(
        Question.objects.filter(course=question.course, current_version__isnull=False)
        .order_by("code")
        .values_list("pk", flat=True)
    )
    position = siblings.index(question.pk)
    return {
        "question": question,
        "version": version,
        "enrollment": enrollment,
        "position": position + 1,
        "question_count": len(siblings),
        "previous_id": siblings[position - 1] if position > 0 else None,
        "next_id": siblings[position + 1] if position + 1 < len(siblings) else None,
        "latest": attempts[0] if attempts else None,
        "latest_tests": _test_chips(attempts[0]["event"]) if attempts and question.kind == Question.Kind.CODE else [],
        "hints": [{"index": i, "text": version.hints[i]} for i in sorted(shown)],
        "next_hint": len(shown) if len(shown) < len(version.hints) else None,
        "hint_total": len(version.hints),
        "attempts": attempts,
        # A fresh key per rendered form: resubmitting this form is a retry, a new
        # render (after success or conflict) is a new attempt.
        "idempotency_key": uuid.uuid4(),
        "answer": answer,
        "error": error,
        "assistance_choices": ASSISTANCE_CHOICES,
        "assistance_flag": assistance_flag,
        "assistance_types": set(types),
        "draft_key": f"kl-draft:{request.user.pk}:{question.pk}",
    }


@login_required
def question_detail(request, question_id):
    question, enrollment = _question_or_404(request.user, question_id)
    if not enrollment.consent_decided:
        return redirect("consent", course_id=question.course_id)
    _mark_opened(request, question)
    return render(request, "study/question.html", _question_context(request, question, enrollment))


def _parse_flag(value: str):
    return {"yes": True, "no": False}.get(value)


def _parse_seconds(value: str):
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return "invalid"  # recorded as timing_source=client_invalid


@login_required
@require_POST
def submit(request, question_id):
    question, enrollment = _question_or_404(request.user, question_id)
    if not enrollment.consent_decided:
        return redirect("consent", course_id=question.course_id)
    answer = request.POST.get("answer", "")
    flag_raw = request.POST.get("assistance_flag", "")
    types = request.POST.getlist("assistance_types")
    try:
        key = uuid.UUID(request.POST.get("idempotency_key", ""))
    except ValueError:
        key = None

    def rerender(error):
        ctx = _question_context(
            request, question, enrollment, answer=answer, error=error, assistance_flag=flag_raw, types=types
        )
        return render(request, "study/question.html", ctx, status=400)

    if key is None:
        return rerender("Маягт хүчингүй боллоо. Дахин илгээнэ үү.")
    try:
        event, created = submit_answer(
            user=request.user,
            question=question,
            idempotency_key=key,
            answer=answer,
            study_session_id=current_study_session(request),
            response_time_sec=_parse_seconds(request.POST.get("response_time_sec")),
            server_elapsed_sec=_server_elapsed(request, question),
            assistance_flag=_parse_flag(flag_raw),
            assistance_types=types,
        )
    except IdempotencyConflict:
        return rerender("Энэ маягтаар өөр хариулт аль хэдийн илгээгдсэн байна. Хариултаа шалгаад дахин илгээнэ үү.")
    except SubmissionError as exc:
        return rerender(_error_text(exc))
    if created:
        request.session.pop(_opened_key(question), None)
    return redirect(f"{reverse('question', args=[question.pk])}?saved={event.attempt_index}")


def _error_text(exc: SubmissionError) -> str:
    return {
        "invalid_submission": f"Хариулт хүлээн авах боломжгүй: {exc}",
        "not_enrolled": "Та энэ хичээлд бүртгэлгүй байна.",
        "question_not_available": "Энэ бодлого одоогоор нээлттэй биш байна.",
    }.get(exc.code, "Хадгалж чадсангүй. Дахин оролдоно уу.")


@login_required
@require_POST
def hint(request, question_id):
    question, _enrollment = _question_or_404(request.user, question_id)
    try:
        show_hint(user=request.user, question=question, hint_index=int(request.POST.get("hint_index", -1)))
    except (SubmissionError, ValueError):
        messages.error(request, "Энэ hint-ийг нээх боломжгүй.")
    return redirect("question", question_id=question.pk)
