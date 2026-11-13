"""Import questions (statement, concept weights, difficulty, test cases) from a folder.

    folder/problems.csv   columns: code, title, kind, difficulty, concepts[, language][, accepted][, hints][, note]
        kind        CODE or SHORT
        difficulty  1–5 (teacher's rating)
        concepts    "queue:0.4;bfs:0.6" (weights summing to 1) or "queue;bfs" (equal weights)
        language    CPP / C / PYTHON (CODE only; default CPP)
        accepted    accepted answers of a SHORT question, separated by |
        hints       hints in order, separated by |
    folder/<code>/statement.md           the statement students see (required)
    folder/<code>/tests/<name>.in, .out  test cases of a CODE question, in name order; files whose name
                                         starts with "sample" are shown to students, the others are hidden

Everything is checked before anything is written. A question that already has a published version
is skipped unless --new-version is given (a new version; events keep their old one). Versions are
created as DRAFT; --publish publishes them so students receive them.

    manage.py import_problems icpc-2027 docs/import_example --dry-run
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.curriculum.models import Concept, Course, Question, QuestionVersion
from apps.curriculum.services import create_version, publish_version
from apps.grading.models import CodeTestCase

from .import_graph import read_rows

LANGUAGES = {"CPP": "CPP", "C++": "CPP", "C": "C", "PYTHON": "PYTHON", "PY": "PYTHON"}
WEIGHT_TOLERANCE = 1e-3


def parse_concepts(text: str) -> dict[str, float]:
    parts = [p.strip() for p in text.split(";") if p.strip()]
    if not parts:
        raise ValueError("no concepts")
    if all(":" not in p for p in parts):
        return {p: 1 / len(parts) for p in parts}
    if not all(":" in p for p in parts):
        raise ValueError("give a weight for every concept or for none")
    weights = {}
    for p in parts:
        code, _, w = p.partition(":")
        weights[code.strip()] = float(w)
    return weights


def read_tests(folder: Path) -> list[tuple[str, str, bool]]:
    tests = []
    for inp in sorted(folder.glob("*.in")):
        out = inp.with_suffix(".out")
        if not out.exists():
            raise ValueError(f"{inp.name} has no {out.name}")
        tests.append((inp.read_text(encoding="utf-8"), out.read_text(encoding="utf-8").rstrip("\n"),
                      not inp.stem.lower().startswith("sample")))  # fmt: skip
    return tests


class Command(BaseCommand):
    help = "Import questions with statements, concept weights, difficulty and test cases from a folder."

    def add_arguments(self, parser):
        parser.add_argument("course", help="course code")
        parser.add_argument("folder", help="folder with problems.csv and one sub-folder per question")
        parser.add_argument("--dry-run", action="store_true", help="check and report, write nothing")
        parser.add_argument("--publish", action="store_true", help="publish the new versions")
        parser.add_argument("--new-version", action="store_true",
                            help="add a new version to questions that already have one")  # fmt: skip

    def handle(self, course, folder, dry_run, publish, new_version, **options):
        try:
            course_obj = Course.objects.get(code=course)
        except Course.DoesNotExist:
            raise CommandError(f"unknown course {course!r}") from None
        root = Path(folder)
        rows = read_rows(str(root / "problems.csv"), ["code", "title", "kind", "difficulty", "concepts"])
        known = set(Concept.objects.filter(course=course_obj).values_list("code", flat=True))
        errors, specs, seen = [], [], set()

        for r in rows:
            where = f"problems.csv:{r['_line']} ({r['code'] or '?'})"
            code, kind = r["code"], r["kind"].upper()
            if not code or len(code) > 40 or not all(ch.isalnum() or ch in "-_" for ch in code):
                errors.append(f"{where}: code must be 1–40 letters, digits, - or _")
            if code in seen:
                errors.append(f"{where}: duplicate code")
            seen.add(code)
            if kind not in Question.Kind.values:
                errors.append(f"{where}: kind must be CODE or SHORT")
            try:
                difficulty = int(r["difficulty"])
                if not 1 <= difficulty <= 5:
                    raise ValueError
            except ValueError:
                errors.append(f"{where}: difficulty must be a whole number 1–5")
                difficulty = 3
            try:
                weights = parse_concepts(r["concepts"])
                unknown = sorted(set(weights) - known)
                if unknown:
                    errors.append(f"{where}: concepts not in the course graph: {unknown} (import the graph first)")
                if any(w <= 0 or w > 1 for w in weights.values()) or abs(sum(weights.values()) - 1) > WEIGHT_TOLERANCE:
                    errors.append(f"{where}: concept weights must be in (0, 1] and sum to 1 (got {sum(weights.values()):.3f})")
            except ValueError as exc:
                errors.append(f"{where}: concepts {r['concepts']!r}: {exc}")
                weights = {}
            language = LANGUAGES.get((r.get("language") or "CPP").upper())
            if language is None:
                errors.append(f"{where}: language must be CPP, C or PYTHON")
            statement = root / code / "statement.md"
            prompt = statement.read_text(encoding="utf-8").strip() if statement.exists() else ""
            if not prompt:
                errors.append(f"{where}: missing or empty {code}/statement.md")
            accepted = [a.strip() for a in (r.get("accepted") or "").split("|") if a.strip()]
            hints = [h.strip() for h in (r.get("hints") or "").split("|") if h.strip()]
            tests = []
            if kind == "CODE":
                try:
                    tests = read_tests(root / code / "tests")
                except ValueError as exc:
                    errors.append(f"{where}: {exc}")
                if not tests:
                    errors.append(f"{where}: a CODE question needs test files in {code}/tests/")
            elif kind == "SHORT" and not accepted:
                errors.append(f"{where}: a SHORT question needs accepted answers")
            existing = Question.objects.filter(course=course_obj, code=code).first()
            if existing and existing.kind != kind:
                errors.append(f"{where}: question exists with kind {existing.kind}")
            specs.append({"code": code, "title": r["title"], "kind": kind, "difficulty": difficulty,
                          "weights": weights, "language": language, "prompt": prompt, "accepted": accepted,
                          "hints": hints, "tests": tests, "existing": existing})  # fmt: skip
        if errors:
            raise CommandError("nothing imported, fix these first:\n  " + "\n  ".join(errors))

        created = versions = skipped = 0
        with transaction.atomic():
            for s in specs:
                if s["existing"] and s["existing"].current_version_id and not new_version:
                    skipped += 1
                    self.stdout.write(f"  skip  {s['code']}: already published (use --new-version)")
                    continue
                question, new = Question.objects.get_or_create(
                    course=course_obj, code=s["code"], defaults={"title": s["title"], "kind": s["kind"]})  # fmt: skip
                created += new
                version = create_version(question, prompt=s["prompt"], concept_weights=s["weights"],
                                         accepted_answers=s["accepted"], hints=s["hints"], difficulty=s["difficulty"],
                                         language=s["language"] or QuestionVersion.Language.CPP)  # fmt: skip
                for order, (stdin, expected, hidden) in enumerate(s["tests"], start=1):
                    CodeTestCase.objects.create(question_version=version, order=order, stdin=stdin,
                                                expected_stdout=expected, is_hidden=hidden)  # fmt: skip
                if publish:
                    publish_version(version)
                versions += 1
                shown = sum(not h for _, _, h in s["tests"])
                self.stdout.write(f"  {'new ' if new else 'ver '} {s['code']}: v{version.version} "
                                  f"{'published' if publish else 'draft'}, difficulty {s['difficulty']}, "
                                  f"{len(s['weights'])} concepts, {len(s['tests'])} tests ({shown} shown)")  # fmt: skip
            self.stdout.write(f"questions: {created} created; versions: {versions} added; {skipped} skipped")
            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("dry run: nothing was written"))
