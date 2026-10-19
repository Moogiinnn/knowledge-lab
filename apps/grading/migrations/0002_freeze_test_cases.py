from django.db import migrations

FORWARD = r"""
CREATE FUNCTION grading_guard_test_case() RETURNS trigger AS $$
DECLARE
    vid bigint;
    v_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN vid := OLD.question_version_id; ELSE vid := NEW.question_version_id; END IF;
    SELECT status INTO v_status FROM curriculum_questionversion WHERE id = vid;
    IF v_status <> 'DRAFT' OR (TG_OP = 'UPDATE' AND OLD.question_version_id <> NEW.question_version_id) THEN
        RAISE EXCEPTION 'VERSION_FROZEN: test cases of a % question version cannot change', v_status;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER code_test_case_guard
    BEFORE INSERT OR UPDATE OR DELETE ON grading_codetestcase
    FOR EACH ROW EXECUTE FUNCTION grading_guard_test_case();
"""

BACKWARD = r"""
DROP TRIGGER IF EXISTS code_test_case_guard ON grading_codetestcase;
DROP FUNCTION IF EXISTS grading_guard_test_case();
"""


class Migration(migrations.Migration):
    dependencies = [("grading", "0001_initial"), ("curriculum", "0002_integrity_triggers")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
