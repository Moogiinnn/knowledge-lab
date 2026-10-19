"""Create a synthetic demo course for trying the UI. Never part of research exports.

The demo student's and demo teacher's passwords are generated and written to .local/demo-credentials.txt.
"""

import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import ensure_teacher_group
from apps.curriculum.models import Concept, ConceptEdge, Course, Enrollment, Question
from apps.curriculum.services import create_version, publish_version
from apps.grading.models import CodeTestCase

CONCEPTS = [
    ("array", "Массив", "STRUCTURE"),
    ("linked-list", "Холбоост жагсаалт", "STRUCTURE"),
    ("stack", "Стек", "STRUCTURE"),
    ("queue", "Дараалал", "STRUCTURE"),
    ("recursion", "Рекурс", "OTHER"),
    ("bfs", "Өргөнөөр хайх (BFS)", "ALGORITHM"),
    ("dfs", "Гүнээр хайх (DFS)", "ALGORITHM"),
]
EDGES = [("queue", "bfs", "PREREQUISITE"), ("stack", "dfs", "PREREQUISITE"), ("recursion", "dfs", "PREREQUISITE"),
         ("array", "linked-list", "RELATED")]  # fmt: skip
QUESTIONS = [
    {
        "code": "stack-order",
        "title": "Стекийн дараалал",
        "kind": "SHORT",
        "prompt": "Хоосон стек дээр push(1), push(2), push(3), pop() хийв. Дараагийн pop() ямар утга буцаах вэ?",
        "accepted": ["2"],
        "hints": ["Стек нь LIFO зарчмаар ажилладаг.", "Эхний pop() 3-ыг авсан."],
        "weights": {"stack": 1.0},
        "difficulty": 1,
    },
    {
        "code": "bfs-structure",
        "title": "BFS-ийн өгөгдлийн бүтэц",
        "kind": "SHORT",
        "prompt": "Өргөнөөр хайх (BFS) алгоритм дараагийн оройг хадгалахад ямар өгөгдлийн бүтэц ашигладаг вэ? (нэг үгээр)",
        "accepted": ["queue", "дараалал"],
        "hints": ["FIFO зарчимтай бүтэц."],
        "weights": {"queue": 0.6, "bfs": 0.4},
        "difficulty": 2,
    },
    {
        "code": "list-sum",
        "title": "Массивын нийлбэр",
        "kind": "CODE",
        "prompt": "Python дээр `total(xs)` функц бичиж, жагсаалтын элементүүдийн нийлбэрийг буцаа. "
        "Код тусгаарлагдсан орчинд тестээр шалгагдана.",
        "accepted": [],
        "hints": ["Давталтаар нэмж болно, эсвэл sum() ашигла."],
        "driver": "import sys, json\nprint(total(json.loads(sys.stdin.read())))",
        "tests": [("[1, 2, 3]", "6", False), ("[]", "0", False), ("[-5, 5, 10]", "10", True)],
        "weights": {"array": 1.0},
        "difficulty": 2,
    },
    {
        "code": "array-sum-c",
        "title": "C: Массивын нийлбэр",
        "kind": "CODE",
        "language": "C",
        "prompt": "C хэл дээр int sum(const int *a, int n) функц бичиж, n урттай массивын элементүүдийн нийлбэрийг "
        "буцаа. main() функцийг систем өөрөө нэмнэ: эхлээд n, дараа нь n тоо уншиж, таны функцийн хариуг хэвлэнэ.",
        "accepted": [],
        "hints": ["for давталтаар a[i]-г нэмж ол.", "n = 0 үед 0 буцаана."],
        "driver": "#include <stdio.h>\n"
        "int main(void) {\n"
        "    int n, a[1000];\n"
        "    if (scanf(\"%d\", &n) != 1 || n < 0 || n > 1000) return 1;\n"
        "    for (int i = 0; i < n; i++) if (scanf(\"%d\", &a[i]) != 1) return 1;\n"
        "    printf(\"%d\\n\", sum(a, n));\n"
        "    return 0;\n"
        "}",
        "tests": [("3\n1 2 3", "6", False), ("0", "0", False), ("3\n-5 5 10", "10", True)],
        "weights": {"array": 1.0},
        "difficulty": 2,
    },
    {
        "code": "bfs-shortest-cpp",
        "title": "C++: Хамгийн богино зам (BFS)",
        "kind": "CODE",
        "language": "CPP",
        "prompt": "ICPC маягийн бодлого, C++ дээр бүтэн програм бич. Эхний мөрөнд n (орой) ба m (ирмэг), дараагийн "
        "m мөрөнд чиглэлгүй ирмэг (u v), сүүлийн мөрөнд s ба t өгөгдөнө (оройнууд 1-ээс n). s-ээс t хүрэх хамгийн "
        "богино замын ирмэгийн тоог хэвлэ; зам байхгүй бол -1.",
        "accepted": [],
        "hints": ["Дараалал (queue) ашиглан BFS хий: эхлээд s-ийг хийнэ.", "dist массивыг -1-ээр эхлүүлж, зочилсныг тэмдэглэ."],
        "tests": [("4 4\n1 2\n2 3\n3 4\n1 3\n1 4", "2", False), ("3 1\n1 2\n1 3", "-1", False),
                  ("5 4\n1 2\n2 3\n3 4\n4 5\n5 1", "4", True)],  # fmt: skip
        "weights": {"queue": 0.4, "bfs": 0.6},
        "difficulty": 3,
    },
]


class Command(BaseCommand):
    help = "Create (or reset the password of) the synthetic demo course and demo student."

    def add_arguments(self, parser):
        parser.add_argument("--questions-only", action="store_true",
                            help="add missing demo questions; keep the demo accounts and their passwords")  # fmt: skip

    @transaction.atomic
    def handle(self, **options):
        course, _ = Course.objects.get_or_create(
            code="demo-ds",
            defaults={
                "name": "Демо: Өгөгдлийн бүтэц",
                "is_synthetic": True,
                "consent_text": "ДЕМО. Энэ бол туршилтын хичээл. Энд оруулсан өгөгдөл судалгаанд ашиглагдахгүй.",
            },
        )
        if not course.is_synthetic:
            raise SystemExit("demo-ds exists but is not synthetic; refusing to touch it")

        concepts = {}
        for code, name, kind in CONCEPTS:
            concepts[code], _ = Concept.objects.update_or_create(
                course=course, code=code, defaults={"name": name, "kind": kind}
            )
        for src, dst, relation in EDGES:
            ConceptEdge.objects.get_or_create(
                source=concepts[src],
                target=concepts[dst],
                relation=relation,
                defaults={"teacher_approved": True, "approved_at": timezone.now()},
            )

        for spec in QUESTIONS:
            question, created = Question.objects.get_or_create(
                course=course, code=spec["code"], defaults={"title": spec["title"], "kind": spec["kind"]}
            )
            current = question.current_version
            needs_tests = spec.get("tests") and (current is None or not current.test_cases.exists())
            if current is None or needs_tests:
                version = create_version(
                    question,
                    prompt=spec["prompt"],
                    accepted_answers=spec["accepted"],
                    hints=spec["hints"],
                    concept_weights=spec["weights"],
                    difficulty=spec["difficulty"],
                    code_driver=spec.get("driver", ""),
                    language=spec.get("language", "PYTHON"),
                )
                for order, (stdin, expected, hidden) in enumerate(spec.get("tests", []), start=1):
                    CodeTestCase.objects.create(
                        question_version=version, order=order, stdin=stdin, expected_stdout=expected, is_hidden=hidden
                    )
                publish_version(version)

        if options.get("questions_only"):
            self.stdout.write(f"demo questions ready in {course.code}; accounts unchanged")
            return

        password = secrets.token_urlsafe(12)
        user, _ = get_user_model().objects.get_or_create(username="demo-student")
        user.set_password(password)
        user.save()
        Enrollment.objects.get_or_create(course=course, user=user)

        teacher_password = secrets.token_urlsafe(12)
        teacher, _ = get_user_model().objects.get_or_create(username="demo-teacher")
        teacher.is_staff = True
        teacher.set_password(teacher_password)
        teacher.save()
        teacher.groups.add(ensure_teacher_group())

        creds = settings.BASE_DIR / ".local" / "demo-credentials.txt"
        creds.parent.mkdir(exist_ok=True)
        creds.write_text(
            f"student: demo-student / {password}\nteacher: demo-teacher / {teacher_password}\n", encoding="utf-8"
        )
        self.stdout.write(f"Demo course ready. Credentials written to {creds}")
