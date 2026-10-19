"""Load a simulated course (engine.sim_course output) into the system, through the real code paths.

Dress rehearsal only (SIMULATED DATA). The course is created with is_synthetic=True, so its
events never mix with research exports unless --include-synthetic is given. Steps:
  1. course, concepts and edges via `import_graph` (the CSV path the teacher's form uses),
  2. one SHORT question per simulated question (answer "ok", three hints), published,
  3. one user per simulated student, enrolled; a share of them decline research consent,
  4. every simulated attempt replayed with services.show_hint / services.submit_answer while
     the clock is set to the simulated time, so attempt_index, hint counts, timing, consent
     filtering and pseudonyms are produced by the same code as in a real semester.

    manage.py load_simulated_course data/sim-ds-120 --course sim-ds-120 --decline 0.1
"""

import csv
import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

import numpy as np
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.curriculum.models import ConsentLog, Course, Enrollment, Question
from apps.curriculum.services import create_version, publish_version, record_consent
from apps.events.services import show_hint, submit_answer

HINTS = ["Тодорхойлолтыг санаарай.", "Жишээн дээр гараар туршаад үз.", "Алхам бүрийг бичээд шалга."]


class Command(BaseCommand):
    help = "Load engine.sim_course output into a new synthetic course through the real services."

    def add_arguments(self, parser):
        parser.add_argument("folder", help="output folder of `python -m engine.sim_course`")
        parser.add_argument("--course", required=True, help="new course code")
        parser.add_argument("--decline", type=float, default=0.1, help="share of students declining consent")
        parser.add_argument("--seed", type=int, default=0)

    def handle(self, folder, course, decline, seed, **options):
        src = Path(folder)
        graph = json.loads((src / "graph.json").read_text(encoding="utf-8"))
        questions = json.loads((src / "questions.json").read_text(encoding="utf-8"))
        with open(src / "events.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if Course.objects.filter(code=course).exists():
            raise CommandError(f"course {course} already exists")
        rng = np.random.default_rng(seed)

        course_obj = Course.objects.create(
            code=course, name=f"[SIMULATED] Өгөгдлийн бүтэц ба алгоритм ({course})", is_synthetic=True,
            consent_text="ХИЙМЭЛ ӨГӨГДӨЛ. Бэлтгэл туршилтын курс, судалгаанд ашиглахгүй.",
        )  # fmt: skip
        self._import_graph(course, graph)

        by_code = {}
        for q in questions:
            question = Question.objects.create(course=course_obj, code=q["code"], title=q["title"], kind="SHORT")
            version = create_version(question, prompt=f"[SIMULATED] {q['title']}", concept_weights=q["concepts"],
                                     accepted_answers=["ok"], hints=HINTS, difficulty=q["difficulty"])  # fmt: skip
            publish_version(version)
            question.refresh_from_db()
            by_code[q["code"]] = question

        users, declined = {}, 0
        User = get_user_model()
        for sid in sorted({r["student_id"] for r in rows}):
            user = User.objects.create(username=f"{course}-{sid[-6:]}")
            user.set_unusable_password()
            user.save()
            enrollment = Enrollment.objects.create(course=course_obj, user=user)
            with mock.patch("django.utils.timezone.now", return_value=_time(rows[0]["timestamp"])):
                decision = ConsentLog.Decision.DECLINED if rng.random() < decline else ConsentLog.Decision.GIVEN
                record_consent(enrollment, decision)
            declined += decision == ConsentLog.Decision.DECLINED
            users[sid] = user

        shown: dict[tuple[str, str], int] = {}
        for n, r in enumerate(rows, start=1):
            user, question = users[r["student_id"]], by_code[r["question_id"]]
            key = (r["student_id"], r["question_id"])
            want = min(len(HINTS), shown.get(key, 0) + int(r["hint_count"]))
            with mock.patch("django.utils.timezone.now", return_value=_time(r["timestamp"])):
                for index in range(shown.get(key, 0), want):
                    show_hint(user=user, question=question, hint_index=index)
                shown[key] = want
                submit_answer(
                    user=user, question=question, idempotency_key=uuid.uuid4(),
                    answer="ok" if r["correct"] == "1" else "буруу",
                    study_session_id=uuid.UUID(r["session_id"]),
                    response_time_sec=int(r["response_time_sec"]), server_elapsed_sec=int(r["server_elapsed_sec"]),
                )  # fmt: skip
            if n % 2000 == 0:
                self.stdout.write(f"  {n}/{len(rows)} attempts")
        self.stdout.write(self.style.SUCCESS(
            f"{course}: {len(by_code)} questions, {len(users)} students ({declined} declined consent), "
            f"{len(rows)} attempts. SIMULATED DATA: export with --include-synthetic."))  # fmt: skip

    def _import_graph(self, course: str, graph: dict):
        with tempfile.TemporaryDirectory() as tmp:
            concepts, edges = Path(tmp) / "concepts.csv", Path(tmp) / "edges.csv"
            with open(concepts, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["code", "name", "kind"])
                w.writerows([c["code"], c["name"], c["kind"]] for c in graph["concepts"])
            with open(edges, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["source", "target", "relation", "confidence"])
                w.writerows([e["source"], e["target"], e["relation"], e.get("confidence") or ""] for e in graph["edges"])
            with transaction.atomic():
                call_command("import_graph", course, str(concepts), str(edges), stdout=self.stdout)


def _time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
