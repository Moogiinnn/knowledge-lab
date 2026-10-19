"""Programming language of CODE question versions (Python or C), frozen once published."""

from django.db import migrations, models

FUNCTION = r"""
CREATE OR REPLACE FUNCTION curriculum_guard_question_version() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'VERSION_FROZEN: a % question version cannot be deleted', OLD.status;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'DRAFT' THEN
        IF NEW.question_id <> OLD.question_id OR NEW.version <> OLD.version
           OR NEW.prompt <> OLD.prompt OR NEW.accepted_answers <> OLD.accepted_answers
           OR NEW.hints <> OLD.hints OR NEW.difficulty <> OLD.difficulty
           OR NEW.code_driver IS DISTINCT FROM OLD.code_driver
           OR NEW.language IS DISTINCT FROM OLD.language
           OR NEW.published_at IS DISTINCT FROM OLD.published_at
           OR NOT (NEW.status = OLD.status OR (OLD.status = 'PUBLISHED' AND NEW.status = 'RETIRED')) THEN
            RAISE EXCEPTION 'VERSION_FROZEN: question version % is %, create a new version instead', OLD.id, OLD.status;
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

PREVIOUS = FUNCTION.replace("           OR NEW.language IS DISTINCT FROM OLD.language\n", "")


class Migration(migrations.Migration):
    dependencies = [("curriculum", "0009_concept_kind_edge_confidence")]

    operations = [
        migrations.AddField(
            model_name="questionversion",
            name="language",
            field=models.CharField(choices=[("PYTHON", "Python"), ("C", "C")], default="PYTHON", max_length=10),
        ),
        migrations.RunSQL(FUNCTION, PREVIOUS),
    ]
