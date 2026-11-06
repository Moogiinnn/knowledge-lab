"""Teacher admin for course content.

Published question versions are read-only here as well as in the database: changes
go through "copy as new draft" and "publish". Consent can only be changed by the
student; the admin shows it read-only.
"""

import csv

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Count, Max, Q
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from apps.events.export import COLUMNS, export_rows
from apps.grading.models import CodeTestCase

from . import knowledge
from .graph import NODE_H, NODE_W, layout, readiness_checks
from .models import Concept, ConceptEdge, ConsentLog, Course, Enrollment, Question, QuestionConcept, QuestionVersion
from .services import copy_as_draft, publish_version, retire_version

WEIGHT_TOLERANCE = 1e-6


# --- Courses and enrollments -------------------------------------------------------


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "is_synthetic", "consent_required", "enrollment_count", "graph_link",
                    "knowledge_link"]  # fmt: skip
    search_fields = ["code", "name"]
    actions = ["export_research_csv"]

    @admin.display(description="concept граф")
    def graph_link(self, obj):
        return format_html('<a href="{}">Граф харах</a>', reverse("admin:curriculum_course_graph", args=[obj.pk]))

    @admin.display(description="мэдлэгийн төлөв")
    def knowledge_link(self, obj):
        if not obj.is_synthetic:
            return "—"
        url = reverse("admin:curriculum_course_knowledge", args=[obj.pk])
        return format_html('<a href="{}">DKG-AP харах</a>', url)

    def get_urls(self):
        view = self.admin_site.admin_view(self.graph_view)
        knowledge = self.admin_site.admin_view(self.knowledge_view)
        return [
            path("<int:course_id>/graph/", view, name="curriculum_course_graph"),
            path("<int:course_id>/knowledge/", knowledge, name="curriculum_course_knowledge"),
            *super().get_urls(),
        ]

    def knowledge_view(self, request, course_id):
        """DKG-AP knowledge states and edge messages, for synthetic (demo/pilot) courses only."""
        if not (request.user.has_perm("curriculum.view_concept") and request.user.has_perm("curriculum.view_enrollment")):
            raise PermissionDenied
        course = get_object_or_404(Course, pk=course_id)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": f"{course.name}: мэдлэгийн төлөв (DKG-AP)",
            "course": course,
            "engine": knowledge.engine_available(),
        }
        if course.is_synthetic and context["engine"]:
            context["k"] = knowledge.course_knowledge(course, request.GET.get("student"))
        return TemplateResponse(request, "admin/curriculum/course/knowledge.html", context)

    def graph_view(self, request, course_id):
        """Read-only drawing of the course's concept graph, with the hypotheses' preconditions."""
        if not (request.user.has_perm("curriculum.view_concept") and request.user.has_perm("curriculum.view_conceptedge")):
            raise PermissionDenied
        course = get_object_or_404(Course, pk=course_id)
        concepts = list(Concept.objects.filter(course=course).order_by("code"))
        edges = list(ConceptEdge.objects.filter(source__course=course).select_related("source", "target")
                     .order_by("relation", "source__code", "target__code"))  # fmt: skip
        published = Q(question_links__question_version__status=QuestionVersion.Status.PUBLISHED)
        questions = dict(Concept.objects.filter(course=course).annotate(
            n=Count("question_links__question_version__question", filter=published, distinct=True)).values_list("id", "n"))  # fmt: skip
        drawing = layout(concepts, edges, questions)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": f"{course.name}: concept граф",
            "course": course,
            "g": drawing,
            "node_w": NODE_W,
            "node_h": NODE_H,
            "checks": readiness_checks(concepts, [e for e in edges if e.teacher_approved]),
            "edges": edges,
            "unapproved": sum(not e.teacher_approved for e in edges),
            "kinds": Concept.Kind,
        }
        return TemplateResponse(request, "admin/curriculum/course/graph.html", context)

    @admin.display(description="оюутан")
    def enrollment_count(self, obj):
        return obj.enrollments.filter(status=Enrollment.Status.ACTIVE).count()

    @admin.action(description="Судалгааны CSV татах (зөвшөөрөлтэй оюутнууд)", permissions=["export"])
    def export_research_csv(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Нэг хичээл сонгоно уу.", messages.ERROR)
            return None
        course = queryset.get()
        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="events-{course.code}-{stamp}.csv"'
        writer = csv.DictWriter(response, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(export_rows(course))
        return response

    def has_export_permission(self, request):
        return request.user.has_perm("events.view_submissionevent")


class ConsentLogInline(admin.TabularInline):
    model = ConsentLog
    fields = ["decision", "decided_at", "consent_text_sha256"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ["user", "course", "status", "consent_state", "enrolled_at"]
    list_filter = ["course", "status"]
    search_fields = ["user__username"]
    autocomplete_fields = ["user"]
    readonly_fields = ["enrolled_at", "consent_given_at", "consent_withdrawn_at"]
    inlines = [ConsentLogInline]

    @admin.display(description="судалгааны зөвшөөрөл")
    def consent_state(self, obj):
        if obj.has_research_consent:
            return "зөвшөөрсөн"
        return "татгалзсан/цуцалсан" if obj.consent_decided else "сонгоогүй"

    def has_delete_permission(self, request, obj=None):
        return False  # set status=WITHDRAWN instead; enrollments anchor consent history


# --- Concepts and the prerequisite graph -------------------------------------------


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "kind", "course", "prerequisites", "dependents"]
    list_filter = ["course", "kind"]
    search_fields = ["code", "name"]

    @admin.display(description="урьдчилсан мэдлэг")
    def prerequisites(self, obj):
        return ", ".join(e.source.code for e in obj.incoming_edges.select_related("source")) or "—"

    @admin.display(description="дараагийн concept")
    def dependents(self, obj):
        return ", ".join(e.target.code for e in obj.outgoing_edges.select_related("target")) or "—"


@admin.register(ConceptEdge)
class ConceptEdgeAdmin(admin.ModelAdmin):
    list_display = ["source", "target", "relation", "confidence", "initial_weight", "teacher_approved", "approved_at"]
    list_filter = ["source__course", "relation", "teacher_approved"]
    search_fields = ["source__code", "target__code"]
    autocomplete_fields = ["source", "target"]
    readonly_fields = ["approved_at"]
    actions = ["approve"]

    def save_model(self, request, obj, form, change):
        if obj.teacher_approved and obj.approved_at is None:
            obj.approved_at = timezone.now()
        if not obj.teacher_approved:
            obj.approved_at = None
        super().save_model(request, obj, form, change)

    @admin.action(description="Сонгосон холбоосуудыг батлах")
    def approve(self, request, queryset):
        n = queryset.filter(teacher_approved=False).update(teacher_approved=True, approved_at=timezone.now())
        self.message_user(request, f"{n} холбоос батлагдлаа.")


# --- Questions and versions --------------------------------------------------------


class VersionInline(admin.TabularInline):
    model = QuestionVersion
    fields = ["version", "status", "difficulty", "published_at", "created_at"]
    readonly_fields = fields
    extra = 0
    show_change_link = True
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ["code", "title", "kind", "course", "current_version_label"]
    list_filter = ["course", "kind"]
    search_fields = ["code", "title"]
    readonly_fields = ["current_version", "new_version_link"]
    inlines = [VersionInline]

    def get_readonly_fields(self, request, obj=None):
        # course and kind define what existing events mean; fix them once created.
        return [*self.readonly_fields, "course", "kind"] if obj else self.readonly_fields

    @admin.display(description="одоогийн хувилбар")
    def current_version_label(self, obj):
        return f"v{obj.current_version.version}" if obj.current_version else "нийтлэгдээгүй"

    @admin.display(description="шинэ хувилбар")
    def new_version_link(self, obj):
        if obj is None or obj.pk is None:
            return "Бодлогоо хадгалсны дараа хувилбар нэмнэ."
        url = reverse("admin:curriculum_questionversion_add") + f"?question={obj.pk}"
        return format_html('<a class="button" href="{}">Шинэ ноорог хувилбар үүсгэх</a>', url)


class QuestionConceptFormSet(BaseInlineFormSet):
    """Concept weights of one version: same course as the question, summing to 1.
    Checked here so the teacher sees a form error instead of a commit-time DB error."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        question = getattr(self.instance, "question", None)
        total, rows, seen = 0.0, 0, set()
        for form in self.forms:
            data = form.cleaned_data
            if not data or data.get("DELETE"):
                continue
            concept = data["concept"]
            if question is not None and concept.course_id != question.course_id:
                raise ValidationError(f"«{concept.code}» нь энэ бодлогын хичээлд хамаарахгүй.")
            if concept.pk in seen:
                raise ValidationError(f"«{concept.code}» давхардсан байна.")
            seen.add(concept.pk)
            total += data["weight"]
            rows += 1
        if rows and abs(total - 1) > WEIGHT_TOLERANCE:
            raise ValidationError(f"Concept-ийн жингийн нийлбэр 1 байх ёстой (одоо {total:g}).")


class QuestionConceptInline(admin.TabularInline):
    model = QuestionConcept
    formset = QuestionConceptFormSet
    extra = 1
    autocomplete_fields = ["concept"]
    verbose_name = "concept-ийн жин (a_qi)"
    verbose_name_plural = "concept-ийн жин (a_qi). Нийлбэр нь 1"


class CodeTestCaseInline(admin.TabularInline):
    model = CodeTestCase
    extra = 0
    fields = ["order", "stdin", "expected_stdout", "is_hidden"]


def _lines(value) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


class QuestionVersionForm(forms.ModelForm):
    accepted_answers_text = forms.CharField(
        label="Зөв хариултууд",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Мөр бүрт нэг хувилбар. Харьцуулахдаа хоосон зай, том жижиг үсгийг үл тооно. Зөвхөн SHORT бодлогод.",
    )
    hints_text = forms.CharField(
        label="Hint-үүд",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Мөр бүрт нэг hint. Оюутанд дарааллаар нээгдэнэ.",
    )

    class Meta:
        model = QuestionVersion
        fields = ["question", "prompt", "difficulty", "language", "code_driver"]
        labels = {"code_driver": "Driver код (CODE)", "language": "Програмчлалын хэл (CODE)"}
        help_texts = {
            "code_driver": "Оюутны кодын араас залгагдана. Python жишээ: import sys, json; "
            "print(total(json.loads(sys.stdin.read()))). C жишээ: stdin уншиж оюутны функцийг дууддаг main(). "
            "Хоосон бол оюутан stdin уншдаг бүтэн програм бичнэ.",
            "language": "C кодыг gcc (C11) хөрвүүлж, дараа нь тест бүрээр ажиллуулна.",
        }
        widgets = {"code_driver": forms.Textarea(attrs={"rows": 4, "style": "font-family: monospace"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["accepted_answers_text"].initial = "\n".join(self.instance.accepted_answers)
            self.fields["hints_text"].initial = "\n".join(self.instance.hints)
        if "language" in self.fields:
            self.fields["language"].required = False  # SHORT forms do not show it; empty means Python

    def clean_language(self):
        return self.cleaned_data.get("language") or QuestionVersion.Language.PYTHON

    def clean(self):
        cleaned = super().clean()
        question = cleaned.get("question") or getattr(self.instance, "question", None)
        answers = _lines(cleaned.get("accepted_answers_text"))
        if question is not None and question.kind == Question.Kind.SHORT and not answers:
            self.add_error("accepted_answers_text", "SHORT бодлогод дор хаяж нэг зөв хариулт оруулна.")
        if question is not None and question.kind == Question.Kind.SHORT and cleaned.get("code_driver"):
            self.add_error("code_driver", "Driver код зөвхөн CODE бодлогод хэрэглэгдэнэ.")
        if question is not None and question.kind == Question.Kind.SHORT:
            cleaned["language"] = QuestionVersion.Language.PYTHON  # meaningless for SHORT; keep the default
        self.instance.accepted_answers = answers
        self.instance.hints = _lines(cleaned.get("hints_text"))
        return cleaned


@admin.register(QuestionVersion)
class QuestionVersionAdmin(admin.ModelAdmin):
    form = QuestionVersionForm
    list_display = ["question", "version", "status", "language", "difficulty", "concept_summary", "published_at"]
    list_filter = ["status", "question__course"]
    search_fields = ["question__code", "question__title"]
    inlines = [QuestionConceptInline, CodeTestCaseInline]
    actions = ["publish", "copy_draft", "retire"]
    frozen_fields = ["question", "version", "status", "prompt", "difficulty", "answers_display",
                     "hints_display", "language", "code_driver", "published_at", "created_at"]

    def is_frozen(self, obj) -> bool:
        return obj is not None and obj.status != QuestionVersion.Status.DRAFT

    def get_fields(self, request, obj=None):
        if self.is_frozen(obj):
            if obj.question.kind == Question.Kind.SHORT:
                return [f for f in self.frozen_fields if f not in ("language", "code_driver")]
            return self.frozen_fields
        fields = ["question", "prompt", "difficulty", "accepted_answers_text", "hints_text", "language", "code_driver"]
        if obj is None:
            return fields
        if obj.question.kind == Question.Kind.SHORT:
            fields.remove("code_driver")
            fields.remove("language")
        return ["version", "status", *fields]

    def get_readonly_fields(self, request, obj=None):
        if self.is_frozen(obj):
            return self.frozen_fields
        return ["question", "version", "status"] if obj else []

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not self.is_frozen(obj)

    def get_inlines(self, request, obj):
        inlines = [QuestionConceptInline]
        question = obj.question if obj else None
        if question is None or question.kind == Question.Kind.CODE:
            inlines.append(CodeTestCaseInline)
        return inlines

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if self.is_frozen(obj):
            for inline in instances:
                inline.max_num = 0
                inline.can_delete = False
                inline.has_add_permission = lambda *a, **k: False
                inline.has_change_permission = lambda *a, **k: False
                inline.has_delete_permission = lambda *a, **k: False
        return instances

    def save_model(self, request, obj, form, change):
        if not change:
            last = QuestionVersion.objects.filter(question=obj.question).aggregate(m=Max("version"))["m"]
            obj.version = (last or 0) + 1
        super().save_model(request, obj, form, change)

    @admin.display(description="зөв хариултууд")
    def answers_display(self, obj):
        return format_html_join(", ", "<code>{}</code>", ((a,) for a in obj.accepted_answers)) or "—"

    @admin.display(description="hint-үүд")
    def hints_display(self, obj):
        return format_html_join("", "<div>{}. {}</div>", ((i + 1, h) for i, h in enumerate(obj.hints))) or "—"

    @admin.display(description="concept (a_qi)")
    def concept_summary(self, obj):
        links = obj.concept_links.select_related("concept").order_by("-weight")
        return ", ".join(f"{link.concept.code} {link.weight:g}" for link in links) or "—"

    @admin.action(description="Нийтлэх (оюутанд энэ хувилбар харагдана)")
    def publish(self, request, queryset):
        for version in queryset.select_related("question"):
            if version.status != QuestionVersion.Status.DRAFT:
                self.message_user(request, f"{version}: зөвхөн ноорог хувилбарыг нийтэлнэ.", messages.WARNING)
                continue
            if version.question.kind == Question.Kind.SHORT and not version.accepted_answers:
                self.message_user(request, f"{version}: зөв хариулт оруулаагүй.", messages.ERROR)
                continue
            try:
                with transaction.atomic():
                    publish_version(version)
                    with transaction.get_connection().cursor() as cur:
                        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")  # surface weight errors here
            except DatabaseError as exc:
                self.message_user(request, f"{version}: нийтэлж чадсангүй. {_db_reason(exc)}", messages.ERROR)
            else:
                self.message_user(request, f"{version} нийтлэгдлээ.", messages.SUCCESS)

    @admin.action(description="Шинэ ноорог болгон хуулах")
    def copy_draft(self, request, queryset):
        for version in queryset.select_related("question"):
            draft = copy_as_draft(version)
            self.message_user(request, f"{draft} ноорог үүслээ.", messages.SUCCESS)

    @admin.action(description="Хаах (оюутанд харагдахгүй болно)")
    def retire(self, request, queryset):
        for version in queryset.filter(status=QuestionVersion.Status.PUBLISHED):
            retire_version(version)
            self.message_user(request, f"{version} хаагдлаа.", messages.SUCCESS)


def _db_reason(exc: DatabaseError) -> str:
    text = str(exc)
    if "NO_MAPPING" in text:
        return "Concept-ийн жин оруулаагүй эсвэл нийлбэр нь 1 биш байна."
    if "WEIGHT_SUM" in text:
        return "Concept-ийн жингийн нийлбэр 1 биш байна."
    return text.splitlines()[0]


@admin.register(ConsentLog)
class ConsentLogAdmin(admin.ModelAdmin):
    list_display = ["enrollment", "decision", "decided_at"]
    list_filter = ["decision", "enrollment__course"]
    readonly_fields = ["enrollment", "decision", "decided_at", "consent_text_sha256"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
