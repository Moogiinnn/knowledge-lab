import time

from django.core.management.base import BaseCommand, CommandError

from apps.grading.code import grade_next
from apps.grading.runner import DockerRunner, InfrastructureError


class Command(BaseCommand):
    help = "Grade pending CODE submissions in Docker. Runs until stopped, or once with --once."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="grade the current queue and exit")
        parser.add_argument("--interval", type=float, default=3.0, help="seconds between polls when idle")

    def handle(self, once=False, interval=3.0, **options):
        runner = DockerRunner()
        graded = 0
        while True:
            try:
                event = grade_next(runner)
            except InfrastructureError as exc:
                if once:
                    raise CommandError(f"Docker unavailable: {exc}") from exc
                self.stderr.write(f"Docker unavailable, retrying in {interval * 10:g}s: {exc}")
                time.sleep(interval * 10)
                continue
            if event is not None:
                graded += 1
                self.stdout.write(f"graded {event.event_id} ({event.question.code} #{event.attempt_index})")
                continue
            if once:
                self.stdout.write(f"{graded} submissions graded")
                return
            time.sleep(interval)
