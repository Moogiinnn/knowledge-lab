"""Student UI details: prompt formatting, per-test chips, question navigation, editor assets."""

from django.urls import reverse

from apps.grading.code import grade_next
from apps.grading.runner import CaseResult
from apps.study.templatetags.study_format import prompt_format

from .conftest import make_short_question
from .test_code_grading import FakeRunner, code_question
from .test_study_views import client_for  # noqa: F401  (fixture)


def test_prompt_format_marks_inline_code_and_escapes_html():
    html = prompt_format("Функц `total(xs)` бич.\n<script>alert(1)</script> `a < b`")
    assert "<code>total(xs)</code>" in html and "<code>a &lt; b</code>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_code_page_shows_editor_language_and_test_chips(client_for, course, student, submit):
    q = code_question(course, cases=((1, "[1]", "1", False), (2, "[]", "0", True)))
    submit(student, q, "def total(xs): return 0")
    grade_next(FakeRunner({1: CaseResult(1, "ok", stdout="0"), 2: CaseResult(2, "ok", stdout="0")}))
    page = client_for(student).get(reverse("question", args=[q.pk])).content.decode()
    assert 'data-mode="python"' in page and "codemirror.js" in page and "solution.py" in page
    assert "Тест 1 ✗" in page and "Нууц тест ✓" in page
    assert "Сүүлийн оролдлого #1" in page and "буруу" in page


def test_c_page_uses_c_mode(client_for, course, student):
    q = code_question(course, code="c1", language="C")
    page = client_for(student).get(reverse("question", args=[q.pk])).content.decode()
    assert 'data-mode="text/x-csrc"' in page and "solution.c · C11" in page


def test_short_page_has_no_editor_and_navigation_links(client_for, course, student):
    first = make_short_question(course, code="a-first")
    second = make_short_question(course, code="b-second")
    client = client_for(student)
    page = client.get(reverse("question", args=[first.pk])).content.decode()
    assert "codemirror" not in page and "1/2" in page
    assert reverse("question", args=[second.pk]) in page
    page = client.get(reverse("question", args=[second.pk])).content.decode()
    assert reverse("question", args=[first.pk]) in page and "2/2" in page
