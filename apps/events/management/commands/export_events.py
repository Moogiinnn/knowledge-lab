import csv

from django.core.management.base import BaseCommand, CommandError

from apps.curriculum.models import Course
from apps.events.export import COLUMNS, export_rows


class Command(BaseCommand):
    help = "Write the research event log of one course as CSV (UTC, pseudonymised, replay order)."

    def add_arguments(self, parser):
        parser.add_argument("course", help="course code")
        parser.add_argument("--out", help="output file (default: stdout)")
        parser.add_argument("--include-synthetic", action="store_true")

    def handle(self, course, out=None, include_synthetic=False, **options):
        try:
            course_obj = Course.objects.get(code=course)
        except Course.DoesNotExist:
            raise CommandError(f"unknown course {course!r}") from None
        stream = open(out, "w", newline="", encoding="utf-8") if out else self.stdout
        try:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            n = 0
            for row in export_rows(course_obj, include_synthetic=include_synthetic):
                writer.writerow(row)
                n += 1
        finally:
            if out:
                stream.close()
        self.stderr.write(f"{n} events exported")
