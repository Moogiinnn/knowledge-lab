"""The Teacher group and its permissions.

Teachers manage course content and grade, but cannot create, edit or delete events,
consent records or user accounts (editing users would let them grant themselves
superuser). Students are created by the researcher with `import_students`.
"""

from django.contrib.auth.models import Group, Permission

TEACHER_GROUP = "Багш"

TEACHER_PERMISSIONS = {
    "curriculum": {
        "course": ["view", "change"],
        "enrollment": ["view", "add", "change"],
        "concept": ["view", "add", "change", "delete"],
        "conceptedge": ["view", "add", "change", "delete"],
        "question": ["view", "add", "change"],
        "questionversion": ["view", "add", "change", "delete"],
        "questionconcept": ["view", "add", "change", "delete"],
        "consentlog": ["view"],
    },
    "events": {
        "submissionevent": ["view", "change"],  # change = may open the grading form
        "submissionanswer": ["view"],
        "graderevision": ["view", "add"],
        "hintview": ["view"],
    },
    "grading": {"codetestcase": ["view", "add", "change", "delete"]},
    "auth": {"user": ["view"]},
}


def ensure_teacher_group() -> Group:
    group, _ = Group.objects.get_or_create(name=TEACHER_GROUP)
    perms = []
    for app_label, models in TEACHER_PERMISSIONS.items():
        for model, actions in models.items():
            for action in actions:
                perms.append(Permission.objects.get(content_type__app_label=app_label, codename=f"{action}_{model}"))
    group.permissions.set(perms)
    return group
