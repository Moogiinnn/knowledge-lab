"""Include code_driver in the published-version immutability check."""

from django.db import migrations

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
           OR NEW.published_at IS DISTINCT FROM OLD.published_at
           OR NOT (NEW.status = OLD.status OR (OLD.status = 'PUBLISHED' AND NEW.status = 'RETIRED')) THEN
            RAISE EXCEPTION 'VERSION_FROZEN: question version % is %, create a new version instead', OLD.id, OLD.status;
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

PREVIOUS = FUNCTION.replace("           OR NEW.code_driver IS DISTINCT FROM OLD.code_driver\n", "")


class Migration(migrations.Migration):
    dependencies = [("curriculum", "0007_questionversion_code_driver")]
    operations = [migrations.RunSQL(FUNCTION, PREVIOUS)]
