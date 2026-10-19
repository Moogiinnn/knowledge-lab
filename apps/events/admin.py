"""Teacher view of the research log.

Events are read-only. Grading happens by adding a GradeRevision inline; previous
revisions stay visible and cannot be edited or deleted.
"""

from django.contrib import admin
from django.db.models import Exists, OuterRef
from django.utils.html import format_html

from apps.grading.models import CodeRun

from .models import GradeRevision, SubmissionAnswer, SubmissionEvent
from .queries import effective, with_final_grade


class NeedsGradingFilter(admin.SimpleListFilter):
    title = "үнэлгээ"
    parameter_name = "needs_grading"

    def lookups(self, request, model_admin):
        return [("yes", "Үнэлэх шаардлагатай"), ("no", "Үнэлэгдсэн")]

    def queryset(self, request, queryset):
        revised = Exists(GradeRevision.objects.filter(event=OuterRef("pk")))
        pending = queryset.filter(grading_status=SubmissionEvent.GradingStatus.PENDING)
        if self.value() == "yes":
            return pending.exclude(revised)
        if self.value() == "no":
            return queryset.exclude(pk__in=pending.exclude(revised).values("pk"))
        return queryset


class SubmissionAnswerInline(admin.StackedInline):
    model = SubmissionAnswer
    fields = ["answer_block"]
    readonly_fields = ["answer_block"]
    can_delete = False
    verbose_name = verbose_name_plural = "хариулт"

    @admin.display(description="")
    def answer_block(self, obj):
        return format_html('<pre style="white-space:pre-wrap;margin:0">{}</pre>', obj.text)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class CodeRunInline(admin.TabularInline):
    model = CodeRun
    fields = ["started_at", "grader_version", "summary", "infra_error"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    verbose_name_plural = "кодын ажиллуулалтууд (sandbox)"

    @admin.display(description="тест бүрийн төлөв")
    def summary(self, obj):
        return ", ".join(f"{r['order']}:{r['status']} {r['duration_ms']}ms" for r in obj.results) or "—"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class GradeRevisionInline(admin.TabularInline):
    model = GradeRevision
    fields = ["correct", "score", "tests_passed", "tests_total", "feedback", "reason", "source", "revised_by",
              "revised_at"]
    extra = 1
    can_delete = False
    verbose_name = "дүнгийн засвар"
    verbose_name_plural = "дүнгийн засварууд (хамгийн сүүлийнх нь эцсийн дүн)"

    def get_readonly_fields(self, request, obj=None):
        return ["source", "revised_by", "revised_at"]

    def has_change_permission(self, request, obj=None):
        # Existing revisions are shown but never edited: add a new one instead.
        return False


@admin.register(SubmissionEvent)
class SubmissionEventAdmin(admin.ModelAdmin):
    list_display = ["submitted_at", "question", "attempt_index", "student_pseudonym", "grading_status",
                    "auto_result", "final_result", "hint_count", "response_time_sec"]
    list_filter = [NeedsGradingFilter, "course", "grading_status", "is_synthetic", "question"]
    search_fields = ["student_pseudonym", "question__code", "event_id"]
    date_hierarchy = "submitted_at"
    ordering = ["-submitted_at"]
    inlines = [SubmissionAnswerInline, CodeRunInline, GradeRevisionInline]
    list_select_related = ["question", "course"]

    def get_queryset(self, request):
        return with_final_grade(super().get_queryset(request))

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in SubmissionEvent._meta.concrete_fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="автомат дүн")
    def auto_result(self, obj):
        return _result(obj.correct, obj.score)

    @admin.display(description="эцсийн дүн")
    def final_result(self, obj):
        return _result(effective(obj, "correct"), effective(obj, "score"))

    def save_model(self, request, obj, form, change):
        """Events are append-only (DB trigger); the change form only adds revisions."""

    def save_formset(self, request, form, formset, change):
        if formset.model is GradeRevision:
            for revision in formset.save(commit=False):
                if revision.pk is None:
                    revision.source = "teacher"
                    revision.revised_by = request.user
                    revision.save()
            return
        super().save_formset(request, form, formset, change)


def _result(correct, score):
    if correct is None:
        return "хүлээгдэж байна"
    return f"{'зөв' if correct else 'буруу'} ({score:g})"
