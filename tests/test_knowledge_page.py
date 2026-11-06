"""Knowledge-state page: DKG-AP on synthetic courses only, never on a real course."""

import csv
import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.roles import ensure_teacher_group
from apps.curriculum.models import ConceptEdge, Course

from .conftest import make_short_question, make_student

pytest.importorskip("torch")

from engine import synthetic  # noqa: E402
from engine.data import dataset_from_rows, load_dataset  # noqa: E402
from engine.dkg import DKG  # noqa: E402


@pytest.fixture
def teacher_client(client, db):
    user = get_user_model().objects.create_user(username="t-know", password="x-test-pass-123", is_staff=True)
    user.groups.add(ensure_teacher_group())
    client.force_login(user)
    return client


def _with_answers(course, submit):
    queue, bfs = course.concepts.get(code="queue"), course.concepts.get(code="bfs")
    ConceptEdge.objects.create(source=queue, target=bfs, relation="PREREQUISITE", teacher_approved=True)
    q1 = make_short_question(course, "q-queue", answers=("3",), weights={"queue": 1.0})
    q2 = make_short_question(course, "q-bfs", answers=("5",), weights={"queue": 0.4, "bfs": 0.6})
    student = make_student(course, "demo-s1")
    for question, answer in [(q1, "1"), (q1, "3"), (q2, "5"), (q2, "2")]:
        submit(student, question, answer)
    return student


def test_synthetic_course_shows_states_and_messages(course, submit, teacher_client):
    Course.objects.filter(pk=course.pk).update(is_synthetic=True)
    course.refresh_from_db()
    _with_answers(course, submit)
    response = teacher_client.get(reverse("admin:curriculum_course_knowledge", args=[course.pk]))
    assert response.status_code == 200
    k = response.context["k"]
    assert not k["empty"] and k["n_events"] == 4 and k["student_label"] == "demo-s1"
    assert {c["code"] for c in k["concepts"]} == {"array", "stack", "queue", "bfs"}
    assert all(0.0 <= c["k"] <= 1.0 for c in k["concepts"])
    assert k["attempts"][0]["n"] == 4  # newest first
    kinds = {m["kind"] for a in k["attempts"] for m in a["messages"]}
    assert kinds <= {"evidence", "transfer"} and "transfer" in kinds  # queue→bfs carries transfer
    assert [e["edge"] for e in k["edges"]] == ["queue→bfs"]
    assert "demo-s1" in response.content.decode()


def test_real_course_is_refused(course, submit, teacher_client):
    _with_answers(course, submit)
    response = teacher_client.get(reverse("admin:curriculum_course_knowledge", args=[course.pk]))
    assert response.status_code == 200
    assert "k" not in response.context
    assert "зөвхөн хиймэл" in response.content.decode()


def test_student_cannot_open_the_page(course, client):
    student = make_student(course, "S-plain")
    client.force_login(student)
    response = client.get(reverse("admin:curriculum_course_knowledge", args=[course.pk]))
    assert response.status_code == 302  # to the admin login


def test_rows_in_memory_match_the_csv_loader(tmp_path):
    events, graph = synthetic.write(tmp_path, n_students=4, days=20, seed=1)
    with open(events, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    from_files = load_dataset(events, graph)
    in_memory = dataset_from_rows(rows, json.loads(graph.read_text(encoding="utf-8")))
    assert in_memory.interactions == from_files.interactions
    assert in_memory.edges == from_files.edges and in_memory.concepts == from_files.concepts


def test_explanation_reports_states_after_the_update(tmp_path):
    data = load_dataset(*synthetic.write(tmp_path, n_students=3, days=20, seed=2))
    dkg = DKG(epochs=1)
    dkg.fit(data)
    for it, _, exp in dkg.model.replay(data, explain=True):
        assert set(exp["concepts"]) <= set(exp["k_after"])
        assert {m["edge"].split("→")[1] for m in exp["messages"]} <= set(exp["k_after"])
        assert all(0.0 <= v <= 1.0 for v in exp["k_after"].values())
