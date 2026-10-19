import json

from django.core.management.base import BaseCommand, CommandError

from apps.curriculum.models import ConceptEdge, Course


class Command(BaseCommand):
    help = "Write a course's concepts and concept edges as JSON for the research engine."

    def add_arguments(self, parser):
        parser.add_argument("course", help="course code")
        parser.add_argument("--out", help="output file (default: stdout)")

    def handle(self, course, out=None, **options):
        try:
            course_obj = Course.objects.get(code=course)
        except Course.DoesNotExist:
            raise CommandError(f"unknown course {course!r}") from None
        edges = ConceptEdge.objects.filter(source__course=course_obj).select_related("source", "target")
        graph = {
            "course": course_obj.code,
            "concepts": [{"code": c.code, "name": c.name, "kind": c.kind}
                         for c in course_obj.concepts.order_by("code")],
            "edges": [
                {
                    "source": e.source.code,
                    "target": e.target.code,
                    "relation": e.relation,
                    "initial_weight": e.initial_weight,
                    "approved": e.teacher_approved,
                    "confidence": e.confidence,
                }
                for e in edges.order_by("source__code", "target__code")
            ],
        }
        text = json.dumps(graph, ensure_ascii=False, indent=2)
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(text)
        else:
            self.stdout.write(text)
