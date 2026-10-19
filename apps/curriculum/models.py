"""Course content: concepts, the prerequisite graph, and versioned questions.

Invariants enforced in the database (see migrations/0002_integrity_triggers.py):
- QuestionConcept weights of one QuestionVersion sum to 1 (checked at commit).
- A QuestionConcept's concept belongs to the same course as its question.
- A ConceptEdge connects two concepts of the same course.
- A non-DRAFT QuestionVersion and its concept mapping cannot be changed.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class Course(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    # Synthetic courses hold demo/pilot-generated data and are excluded from research exports.
    is_synthetic = models.BooleanField(default=False)
    consent_required = models.BooleanField(default=True)
    # Shown to students before they agree; must be the ethics-approved wording.
    consent_text = models.TextField(blank=True)
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "хичээл"
        verbose_name_plural = "хичээлүүд"

    def __str__(self):
        return self.code


class Enrollment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        WITHDRAWN = "WITHDRAWN"

    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="enrollments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="enrollments")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    # Research consent is separate from course membership: a student can study without
    # consenting, and their events are then excluded from exports.
    consent_given_at = models.DateTimeField(null=True, blank=True)
    consent_withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "бүртгэл"
        verbose_name_plural = "оюутны бүртгэл"
        constraints = [models.UniqueConstraint(fields=["course", "user"], name="enrollment_unique")]

    @property
    def has_research_consent(self) -> bool:
        if not self.course.consent_required:
            return True
        return self.consent_given_at is not None and self.consent_withdrawn_at is None

    @property
    def consent_decided(self) -> bool:
        """Declining is recorded as a withdrawal, so either timestamp means a decision."""
        if not self.course.consent_required:
            return True
        return self.consent_given_at is not None or self.consent_withdrawn_at is not None


class ConsentLog(models.Model):
    """Append-only record of every consent decision, with a hash of the text shown."""

    class Decision(models.TextChoices):
        GIVEN = "GIVEN"
        DECLINED = "DECLINED"
        WITHDRAWN = "WITHDRAWN"

    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="consent_log")
    decision = models.CharField(max_length=10, choices=Decision.choices)
    consent_text_sha256 = models.CharField(max_length=64)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "зөвшөөрлийн бүртгэл"
        verbose_name_plural = "зөвшөөрлийн бүртгэл"


class Concept(models.Model):
    class Kind(models.TextChoices):
        STRUCTURE = "STRUCTURE", "өгөгдлийн бүтэц"
        ALGORITHM = "ALGORITHM", "алгоритм"
        OTHER = "OTHER", "бусад"

    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="concepts")
    code = models.SlugField(max_length=60)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # Set by the teacher when the graph is approved; defines the groups of hypothesis H2.
    kind = models.CharField(max_length=10, choices=Kind.choices, blank=True, default="")

    class Meta:
        verbose_name = "concept"
        verbose_name_plural = "concept-ууд"
        constraints = [models.UniqueConstraint(fields=["course", "code"], name="concept_code_unique")]

    def __str__(self):
        return f"{self.course.code}:{self.code}"


class ConceptEdge(models.Model):
    """Directed edge from a prerequisite concept (source) to a target concept.

    The set of approved edges is the edge mask; `initial_weight` is W0. Learned weights
    belong to the engine's graph versions, not here.
    """

    class Relation(models.TextChoices):
        PREREQUISITE = "PREREQUISITE"
        RELATED = "RELATED"
        # Pairs the teacher judges unrelated, added on purpose as a negative control.
        CONTROL = "CONTROL"

    source = models.ForeignKey(Concept, on_delete=models.PROTECT, related_name="outgoing_edges")
    target = models.ForeignKey(Concept, on_delete=models.PROTECT, related_name="incoming_edges")
    relation = models.CharField(max_length=20, choices=Relation.choices, default=Relation.PREREQUISITE)
    initial_weight = models.FloatField(default=1.0)
    teacher_approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    # Teacher's confidence when approving: 1 = guess, 2 = fairly sure, 3 = very sure.
    confidence = models.PositiveSmallIntegerField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name = "concept холбоос"
        verbose_name_plural = "concept холбоосууд (граф)"
        constraints = [
            models.UniqueConstraint(fields=["source", "target", "relation"], name="edge_unique"),
            models.CheckConstraint(condition=~Q(source=F("target")), name="edge_no_self_loop"),
            models.CheckConstraint(
                condition=Q(initial_weight__gte=0) & Q(initial_weight__lte=1), name="edge_weight_range"
            ),
            models.CheckConstraint(
                condition=Q(confidence__isnull=True) | (Q(confidence__gte=1) & Q(confidence__lte=3)),
                name="edge_confidence_range",
            ),
        ]

    def clean(self):
        if self.source_id is None or self.target_id is None:
            return
        if self.source_id == self.target_id:
            raise ValidationError("Concept өөр рүүгээ холбогдох боломжгүй.")
        if self.source.course_id != self.target.course_id:
            raise ValidationError("Холбоосын хоёр concept нэг хичээлд байх ёстой.")
        if self.relation == self.Relation.PREREQUISITE and self.creates_cycle():
            raise ValidationError(
                f"{self.source.code} → {self.target.code} холбоос prerequisite графт цикл үүсгэнэ "
                f"({self.target.code}-оос {self.source.code} хүрэх зам аль хэдийн бий)."
            )

    def creates_cycle(self) -> bool:
        """True if target already reaches source through prerequisite edges."""
        edges = ConceptEdge.objects.filter(source__course_id=self.source.course_id, relation=self.Relation.PREREQUISITE)
        if self.pk:
            edges = edges.exclude(pk=self.pk)
        graph: dict[int, list[int]] = {}
        for src, dst in edges.values_list("source_id", "target_id"):
            graph.setdefault(src, []).append(dst)
        seen, stack = set(), [self.target_id]
        while stack:
            node = stack.pop()
            if node == self.source_id:
                return True
            if node not in seen:
                seen.add(node)
                stack.extend(graph.get(node, []))
        return False

    def __str__(self):
        return f"{self.source.code} -> {self.target.code}"


class Question(models.Model):
    class Kind(models.TextChoices):
        SHORT = "SHORT", "Богино хариулт"
        CODE = "CODE", "Код"

    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="questions")
    code = models.SlugField(max_length=40)
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    # The version students currently receive. Only a PUBLISHED version may be set here.
    current_version = models.ForeignKey(
        "QuestionVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        verbose_name = "бодлого"
        verbose_name_plural = "бодлогууд"
        constraints = [models.UniqueConstraint(fields=["course", "code"], name="question_code_unique")]

    def __str__(self):
        return f"{self.course.code}:{self.code}"


class QuestionVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        PUBLISHED = "PUBLISHED"
        RETIRED = "RETIRED"

    class Language(models.TextChoices):
        PYTHON = "PYTHON", "Python"
        C = "C", "C"
        CPP = "CPP", "C++"

    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    prompt = models.TextField()
    # SHORT: accepted answers, compared after whitespace/case normalisation.
    # CODE: unused; test cases live in grading.CodeTestCase.
    accepted_answers = models.JSONField(default=list, blank=True)
    hints = models.JSONField(default=list, blank=True)
    # CODE only: appended after the student's code before each test, e.g.
    #   import sys, json; print(total(json.loads(sys.stdin.read())))
    # Empty means the student writes a whole program reading stdin.
    code_driver = models.TextField(blank=True)
    # CODE only: the language the student writes and the tests run in.
    language = models.CharField(max_length=10, choices=Language.choices, default=Language.PYTHON)
    difficulty = models.PositiveSmallIntegerField(default=3)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "бодлогын хувилбар"
        verbose_name_plural = "бодлогын хувилбарууд"
        constraints = [
            models.UniqueConstraint(fields=["question", "version"], name="question_version_unique"),
            models.CheckConstraint(condition=Q(difficulty__gte=1) & Q(difficulty__lte=5), name="difficulty_range"),
            models.CheckConstraint(
                condition=Q(status="DRAFT") | Q(published_at__isnull=False), name="published_has_timestamp"
            ),
        ]

    def __str__(self):
        return f"{self.question} v{self.version}"


class QuestionConcept(models.Model):
    """a_qi: how much question version q depends on concept i. Weights per version sum to 1."""

    question_version = models.ForeignKey(QuestionVersion, on_delete=models.PROTECT, related_name="concept_links")
    concept = models.ForeignKey(Concept, on_delete=models.PROTECT, related_name="question_links")
    weight = models.FloatField()

    class Meta:
        verbose_name = "бодлого–concept жин"
        verbose_name_plural = "бодлого–concept жингүүд"
        constraints = [
            models.UniqueConstraint(fields=["question_version", "concept"], name="question_concept_unique"),
            models.CheckConstraint(condition=Q(weight__gt=0) & Q(weight__lte=1), name="question_concept_weight_range"),
        ]

    def __str__(self):
        return f"{self.concept.code} ({self.weight:g})"
