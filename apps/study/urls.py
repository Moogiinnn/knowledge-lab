from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("courses/<int:course_id>/consent/", views.consent, name="consent"),
    path("questions/<int:question_id>/", views.question_detail, name="question"),
    path("questions/<int:question_id>/submit/", views.submit, name="submit"),
    path("questions/<int:question_id>/hint/", views.hint, name="hint"),
]
