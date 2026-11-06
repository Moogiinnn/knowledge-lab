"""Knowledge-state page data: DKG-AP fitted on a synthetic (demo or pilot) course's events.

Only synthetic courses: the preregistered study is observational, and showing model estimates
to teachers during data collection would change what is being measured. The research engine
(numpy, torch) is optional on the server; without it the page says how to install it.

The model is fitted on the course's exported events (consenting students only, as in the
research export) and cached by a hash of those events and the graph, so a new submission or
a graph change refits it on the next page view.
"""

import hashlib
import json
import threading
from dataclasses import dataclass, field

from apps.accounts.pseudonym import pseudonymous_id
from apps.events.export import export_rows, graph_dict

from .models import Course, Enrollment

EPOCHS = 15
RECENT_ATTEMPTS = 12

_cache: dict[int, tuple[str, "CourseModel"]] = {}
_lock = threading.Lock()


def engine_available() -> bool:
    try:
        import numpy  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class CourseModel:
    model: object  # engine.dkg.DKG, fitted
    data: object  # engine.data.Dataset
    names: dict[str, str]  # concept code → name
    edges: list[dict] = field(default_factory=list)
    k0: dict[str, float] = field(default_factory=dict)


def _fit(course: Course) -> CourseModel | None:
    from engine.data import dataset_from_rows
    from engine.dkg import DKG

    rows = list(export_rows(course, include_synthetic=True))
    graph = graph_dict(course)
    key = hashlib.sha256(json.dumps([rows, graph], sort_keys=True, default=str).encode()).hexdigest()
    with _lock:
        cached = _cache.get(course.pk)
        if cached and cached[0] == key:
            return cached[1]
        data = dataset_from_rows(rows, graph, source_sha256=key)
        if not data.interactions:
            return None
        dkg = DKG(epochs=EPOCHS, seed=0)
        dkg.fit(data)
        described = dkg.describe()
        result = CourseModel(
            model=dkg,
            data=data,
            names={c["code"]: c["name"] for c in graph["concepts"]},
            edges=described.get("edges", []),
            k0={code: c["k0"] for code, c in described["concepts"].items()},
        )
        _cache[course.pk] = (key, result)
        return result


def student_labels(course: Course) -> dict[str, str]:
    """Pseudonym → username. Shown only for synthetic courses, whose accounts are demo or pilot ones."""
    enrollments = Enrollment.objects.filter(course=course).select_related("user")
    return {pseudonymous_id(e.user_id): e.user.username for e in enrollments}


def course_knowledge(course: Course, student: str | None) -> dict:
    """Everything the page shows: edge weights, the students, and one student's states and recent attempts."""
    if not course.is_synthetic:
        raise PermissionError("knowledge states are shown for synthetic courses only")
    fitted = _fit(course)
    if fitted is None:
        return {"empty": True}
    labels = student_labels(course)
    counts: dict[str, list[int]] = {}
    for it in fitted.data.interactions:
        counts.setdefault(it.student, []).append(it.y)
    students = [{"id": s, "label": labels.get(s, s[:10] + "…"), "n": len(ys), "correct": sum(ys)}
                for s, ys in sorted(counts.items(), key=lambda kv: labels.get(kv[0], kv[0]))]  # fmt: skip
    if student not in counts:
        student = students[0]["id"]

    import torch

    sub = fitted.data.subset([it.idx for it in fitted.data.interactions if it.student == student])
    with torch.no_grad():
        trace = list(fitted.model.model.replay(sub, explain=True))
    state = dict(fitted.k0)
    touched: dict[str, int] = {}
    attempts = []
    for n, (it, p, exp) in enumerate(trace, start=1):
        state.update(exp["k_after"])
        for code in exp["k_after"]:
            touched[code] = n
        messages = [{"edge": m["edge"], "kind": m["kind"], "contribution": m["contribution"]}
                    for m in exp["messages"] if abs(m["contribution"]) >= 5e-4]  # fmt: skip
        attempts.append({
            "n": n, "question": it.question, "y": it.y, "p": float(p),
            "concepts": [{"code": c, "k_before": v["k_decayed"], "k_after": exp["k_after"][c]}
                         for c, v in exp["concepts"].items()],
            "messages": messages,
        })  # fmt: skip
    concepts = [{"code": code, "name": fitted.names.get(code, code), "k": state[code],
                 "pct": round(100 * state[code]), "last": touched.get(code), "prior": fitted.k0[code]}
                for code in fitted.data.concepts]  # fmt: skip
    return {
        "empty": False,
        "students": students,
        "student": student,
        "student_label": labels.get(student, student),
        "n_attempts": len(trace),
        "concepts": concepts,
        "attempts": list(reversed(attempts))[:RECENT_ATTEMPTS],
        "edges": fitted.edges,
        "n_events": len(fitted.data.interactions),
        "n_students": len(students),
        "ungraded": fitted.data.dropped.get("ungraded", 0),
    }
