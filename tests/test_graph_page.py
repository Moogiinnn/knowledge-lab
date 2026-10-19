import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.roles import ensure_teacher_group
from apps.curriculum.graph import NODE_H, layout
from apps.curriculum.models import Concept, ConceptEdge, Course

PASSWORD = "x-test-pass-123"


def make_graph(db_course, codes, edges):
    concepts = {c: Concept.objects.create(course=db_course, code=c, name=c.upper()) for c in codes}
    for s, t, rel in edges:
        ConceptEdge.objects.create(source=concepts[s], target=concepts[t], relation=rel, teacher_approved=True)
    return list(Concept.objects.filter(course=db_course)), list(
        ConceptEdge.objects.filter(source__course=db_course).select_related("source", "target"))  # fmt: skip


@pytest.fixture
def gcourse(db):
    return Course.objects.create(code="g1", name="Graph course")


def test_rows_follow_prerequisite_depth(gcourse):
    concepts, edges = make_graph(gcourse, ["array", "stack", "queue", "bfs", "dfs", "lonely"], [
        ("array", "stack", "PREREQUISITE"), ("stack", "dfs", "PREREQUISITE"),
        ("queue", "bfs", "PREREQUISITE"), ("array", "queue", "RELATED"),
    ])  # fmt: skip
    g = layout(concepts, edges)
    level = {n.concept.code: n.level for n in g.nodes}
    assert level == {"array": 0, "queue": 0, "stack": 1, "bfs": 1, "dfs": 2, "lonely": -1}
    y = {n.concept.code: n.y for n in g.nodes}
    assert y["array"] < y["stack"] < y["dfs"] < y["lonely"]
    assert g.cycle is None and len(g.edges) == 4
    assert all(0 <= n.x and n.x + 168 <= g.width and n.y + NODE_H <= g.height for n in g.nodes)


def test_cycle_is_reported_and_drawn(gcourse):
    concepts, edges = make_graph(gcourse, ["a", "b", "c"], [])
    ids = {c.code: c for c in concepts}
    # The model's clean() refuses cycles, but bulk writes could still create one.
    ConceptEdge.objects.bulk_create([ConceptEdge(source=ids[s], target=ids[t], teacher_approved=True)
                                     for s, t in [("a", "b"), ("b", "c"), ("c", "a")]])  # fmt: skip
    edges = list(ConceptEdge.objects.filter(source__course=gcourse).select_related("source", "target"))
    g = layout(concepts, edges)
    assert g.cycle and g.cycle[0] == g.cycle[-1]
    assert all(n.in_cycle for n in g.nodes)


def test_empty_course(gcourse):
    g = layout([], [])
    assert g.nodes == [] and g.edges == []


@pytest.fixture
def teacher_client(client, db):
    user = get_user_model().objects.create_user(username="t-graph", password=PASSWORD, is_staff=True)
    user.groups.add(ensure_teacher_group())
    client.force_login(user)
    return client


def test_teacher_sees_graph_page(teacher_client, gcourse):
    make_graph(gcourse, ["queue", "bfs"], [("queue", "bfs", "PREREQUISITE")])
    Concept.objects.filter(code="bfs").update(kind=Concept.Kind.ALGORITHM)
    response = teacher_client.get(reverse("admin:curriculum_course_graph", args=[gcourse.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "<svg" in html and "queue → bfs · PREREQUISITE" in html
    assert "k-ALGORITHM" in html and "Concept 2 (шаардлага 20–40)" in html
    changelist = teacher_client.get(reverse("admin:curriculum_course_changelist")).content.decode()
    assert reverse("admin:curriculum_course_graph", args=[gcourse.pk]) in changelist


def test_graph_page_needs_permission(client, gcourse):
    user = get_user_model().objects.create_user(username="plain-staff", password=PASSWORD, is_staff=True)
    client.force_login(user)
    assert client.get(reverse("admin:curriculum_course_graph", args=[gcourse.pk])).status_code == 403
    client.logout()
    response = client.get(reverse("admin:curriculum_course_graph", args=[gcourse.pk]))
    assert response.status_code == 302 and "login" in response["Location"]
