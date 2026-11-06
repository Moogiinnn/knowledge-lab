import json

from django.core.management.base import BaseCommand, CommandError

from apps.curriculum.models import Course
from apps.events.export import graph_dict


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
        text = json.dumps(graph_dict(course_obj), ensure_ascii=False, indent=2)
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(text)
        else:
            self.stdout.write(text)
