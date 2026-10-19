from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.roles import ensure_teacher_group


class Command(BaseCommand):
    help = "Create/update the Teacher group; optionally add staff users to it."

    def add_arguments(self, parser):
        parser.add_argument("--teacher", action="append", default=[], help="username to make a teacher")

    def handle(self, teacher, **options):
        group = ensure_teacher_group()
        for username in teacher:
            user = get_user_model().objects.get(username=username)
            user.is_staff = True
            user.save(update_fields=["is_staff"])
            user.groups.add(group)
            self.stdout.write(f"{username}: багш")
        self.stdout.write(f"Group «{group.name}»: {group.permissions.count()} permissions")
