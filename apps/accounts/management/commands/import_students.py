"""Create student accounts from a CSV with a `username` column and enroll them.

Use study IDs or university logins as usernames; do not put names in this system.
Generated passwords are written to a credentials CSV for the teacher to hand out.
Existing users are enrolled but their password is left unchanged.
"""

import csv
import re
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.curriculum.models import Course, Enrollment

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,40}$")


class Command(BaseCommand):
    help = "Create and enroll students from a CSV (column: username)."

    def add_arguments(self, parser):
        parser.add_argument("course", help="course code")
        parser.add_argument("csv_path")
        parser.add_argument("--out", help="credentials CSV (default: .local/credentials-<course>-<time>.csv)")

    @transaction.atomic
    def handle(self, course, csv_path, out=None, **options):
        try:
            course_obj = Course.objects.get(code=course)
        except Course.DoesNotExist:
            raise CommandError(f"unknown course {course!r}") from None
        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if "username" not in (reader.fieldnames or []):
                raise CommandError("CSV must have a 'username' column")
            usernames = [row["username"].strip() for row in reader if row["username"].strip()]
        bad = [u for u in usernames if not USERNAME_RE.match(u)]
        if bad:
            raise CommandError(f"invalid usernames (letters, digits, . _ - only): {bad}")
        if len(set(usernames)) != len(usernames):
            raise CommandError("duplicate usernames in CSV")

        User = get_user_model()
        credentials, enrolled = [], 0
        for username in usernames:
            user, created = User.objects.get_or_create(username=username)
            if created:
                password = secrets.token_urlsafe(9)
                user.set_password(password)
                user.save()
                credentials.append({"username": username, "password": password})
            _, new_enrollment = Enrollment.objects.get_or_create(course=course_obj, user=user)
            enrolled += new_enrollment

        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        out_path = Path(out) if out else settings.BASE_DIR / ".local" / f"credentials-{course}-{stamp}.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["username", "password"], lineterminator="\n")
            writer.writeheader()
            writer.writerows(credentials)
        self.stdout.write(
            f"{len(credentials)} new accounts, {enrolled} new enrollments. Credentials: {out_path}"
        )
