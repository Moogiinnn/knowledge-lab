"""Import a teacher-approved concept graph from the approval form (two CSV files).

concepts.csv  columns: code, name, kind[, description]
              kind: STRUCTURE / ALGORITHM / OTHER, or the form's letters Б / А / Ө, or empty
edges.csv     columns: source, target, relation, confidence[, initial_weight][, note]
              relation: PRE / PREREQUISITE, REL / RELATED, CONTROL, or "—" / "-" / empty (= no
              edge, row skipped). "?" (undecided) is an error: decide before importing.
              confidence: 1–3 or empty.

Everything is checked before anything is written, and the import runs in one transaction.
The graph is frozen once real data collection starts (preregistration §2), so the command
refuses to change a course that already has non-synthetic submission events unless
--force-after-collection is given (and that deviation must then be logged in §7).

    manage.py import_graph ds-2026 docs/graph_templates/concepts.csv docs/graph_templates/edges.csv --dry-run
"""

import csv
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.curriculum.graph import find_prerequisite_cycle, readiness_checks
from apps.curriculum.models import Concept, ConceptEdge, Course
from apps.events.models import SubmissionEvent

KIND_ALIASES = {
    "STRUCTURE": Concept.Kind.STRUCTURE, "Б": Concept.Kind.STRUCTURE,
    "ALGORITHM": Concept.Kind.ALGORITHM, "А": Concept.Kind.ALGORITHM,
    "OTHER": Concept.Kind.OTHER, "Ө": Concept.Kind.OTHER, "": "",
}  # fmt: skip
RELATION_ALIASES = {
    "PRE": ConceptEdge.Relation.PREREQUISITE, "PREREQUISITE": ConceptEdge.Relation.PREREQUISITE,
    "REL": ConceptEdge.Relation.RELATED, "RELATED": ConceptEdge.Relation.RELATED,
    "CONTROL": ConceptEdge.Relation.CONTROL, "CTRL": ConceptEdge.Relation.CONTROL,
}  # fmt: skip
NO_EDGE = {"", "-", "—", "–", "NONE"}


def read_rows(path: str, required: list[str]) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:  # utf-8-sig: Excel's BOM
        reader = csv.DictReader(fh)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in required if c not in header]
        if missing:
            raise CommandError(f"{Path(path).name}: missing columns {missing} (found {header})")
        return [
            {k.strip(): (v or "").strip() for k, v in row.items() if k} | {"_line": n}
            for n, row in enumerate(reader, start=2)
            if any((v or "").strip() for v in row.values())
        ]


class Command(BaseCommand):
    help = "Import a teacher-approved concept graph (concepts.csv, edges.csv) into a course."

    def add_arguments(self, parser):
        parser.add_argument("course", help="course code")
        parser.add_argument("concepts_csv")
        parser.add_argument("edges_csv")
        parser.add_argument("--dry-run", action="store_true", help="check and report, write nothing")
        parser.add_argument("--replace", action="store_true",
                            help="also delete this course's edges that are not in edges.csv")
        parser.add_argument("--force-after-collection", action="store_true",
                            help="allow changing a graph after real data collection started (log it!)")

    def handle(self, course, concepts_csv, edges_csv, dry_run, replace, force_after_collection, **options):
        try:
            course_obj = Course.objects.get(code=course)
        except Course.DoesNotExist:
            raise CommandError(f"unknown course {course!r}") from None
        collected = SubmissionEvent.objects.filter(course=course_obj, is_synthetic=False).exists()
        if collected and not force_after_collection and not dry_run:
            raise CommandError(
                f"course {course} already has real submission events; its graph is frozen "
                "(preregistration §2). Use --force-after-collection only for a logged deviation."
            )

        concept_rows = read_rows(concepts_csv, ["code", "name", "kind"])
        edge_rows = read_rows(edges_csv, ["source", "target", "relation", "confidence"])
        errors: list[str] = []

        concepts, seen_codes = {}, Counter(r["code"] for r in concept_rows)
        for r in concept_rows:
            where = f"concepts.csv:{r['_line']}"
            if seen_codes[r["code"]] > 1:
                errors.append(f"{where}: duplicate code {r['code']!r}")
            if not r["code"] or not r["name"]:
                errors.append(f"{where}: code and name are required")
            kind = KIND_ALIASES.get(r["kind"].upper())
            if kind is None:
                errors.append(f"{where}: unknown kind {r['kind']!r} (STRUCTURE/ALGORITHM/OTHER or Б/А/Ө)")
            concepts[r["code"]] = {"name": r["name"], "kind": kind or "", "description": r.get("description", "")}

        known = set(concepts) | set(Concept.objects.filter(course=course_obj).values_list("code", flat=True))
        edges, skipped = {}, 0
        for r in edge_rows:
            where = f"edges.csv:{r['_line']}"
            rel_raw = r["relation"].upper()
            if rel_raw in NO_EDGE:
                skipped += 1
                continue
            if rel_raw == "?":
                errors.append(f"{where}: {r['source']}→{r['target']} is still undecided ('?')")
                continue
            relation = RELATION_ALIASES.get(rel_raw)
            if relation is None:
                errors.append(f"{where}: unknown relation {r['relation']!r} (PRE/REL/CONTROL or —)")
                continue
            for end in ("source", "target"):
                if r[end] not in known:
                    errors.append(f"{where}: unknown concept {r[end]!r}")
            if r["source"] == r["target"]:
                errors.append(f"{where}: self loop {r['source']!r}")
            confidence = None
            if r["confidence"]:
                if r["confidence"] not in {"1", "2", "3"}:
                    errors.append(f"{where}: confidence must be 1, 2 or 3 (got {r['confidence']!r})")
                else:
                    confidence = int(r["confidence"])
            try:
                weight = float(r.get("initial_weight") or 1.0)
                if not 0 <= weight <= 1:
                    raise ValueError
            except ValueError:
                errors.append(f"{where}: initial_weight must be a number in [0, 1]")
                weight = 1.0
            key = (r["source"], r["target"], relation)
            if key in edges:
                errors.append(f"{where}: duplicate edge {r['source']}→{r['target']} ({relation})")
            edges[key] = {"confidence": confidence, "initial_weight": weight, "note": r.get("note", "")}

        prerequisites = {(s, t) for s, t, rel in edges if rel == ConceptEdge.Relation.PREREQUISITE}
        if not replace:  # edges already in the database stay, so they count for cycles too
            prerequisites |= set(ConceptEdge.objects.filter(
                source__course=course_obj, relation=ConceptEdge.Relation.PREREQUISITE,
            ).values_list("source__code", "target__code"))  # fmt: skip
        cycle = find_prerequisite_cycle(sorted(prerequisites))
        if cycle:
            errors.append("prerequisite cycle: " + " → ".join(cycle))
        if errors:
            raise CommandError("graph not imported, fix these first:\n  " + "\n  ".join(errors))

        with transaction.atomic():
            created_c = updated_c = created_e = updated_e = deleted_e = 0
            objs = {}
            for code, data in concepts.items():
                obj, created = Concept.objects.update_or_create(course=course_obj, code=code, defaults=data)
                objs[code] = obj
                created_c += created
                updated_c += not created
            for c in Concept.objects.filter(course=course_obj):
                objs.setdefault(c.code, c)
            now = timezone.now()
            keep = set()
            for (s, t, rel), data in edges.items():
                obj, created = ConceptEdge.objects.update_or_create(
                    source=objs[s], target=objs[t], relation=rel,
                    defaults={**data, "teacher_approved": True, "approved_at": now},
                )  # fmt: skip
                keep.add(obj.pk)
                created_e += created
                updated_e += not created
            if replace:
                stale = ConceptEdge.objects.filter(source__course=course_obj).exclude(pk__in=keep)
                deleted_e = stale.count()
                stale.delete()
            self.report(course_obj, created_c, updated_c, created_e, updated_e, deleted_e, skipped)
            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("dry run: nothing was written"))
            if collected and force_after_collection and not dry_run:
                self.stdout.write(self.style.WARNING(
                    "graph changed after data collection started: record this deviation in preregistration §7"))

    def report(self, course, created_c, updated_c, created_e, updated_e, deleted_e, skipped):
        concepts = list(Concept.objects.filter(course=course))
        edges = list(ConceptEdge.objects.filter(source__course=course, teacher_approved=True)
                     .select_related("source", "target"))  # fmt: skip
        self.stdout.write(f"concepts: {created_c} created, {updated_c} updated; edges: {created_e} created, "
                          f"{updated_e} updated, {deleted_e} deleted, {skipped} rows marked 'no edge'")  # fmt: skip
        for check in readiness_checks(concepts, edges):
            self.stdout.write(("  OK    " if check.ok else "  WARN  ") + check.en)
