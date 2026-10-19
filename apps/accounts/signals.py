from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .study_session import start_study_session


@receiver(user_logged_in)
def new_study_session_on_login(sender, request, user, **kwargs):
    start_study_session(request)
