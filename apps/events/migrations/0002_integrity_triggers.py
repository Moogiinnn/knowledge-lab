"""Database-level invariants for the research log.

SubmissionEvent and GradeRevision are append-only. Deleting rows (retention, consent
withdrawal) requires opting in for the transaction with
    SET LOCAL knowledge_lab.allow_event_delete = 'on';
so it can only happen deliberately, never through an ordinary ORM call.
"""

from django.db import migrations

FORWARD = r"""
CREATE FUNCTION events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('knowledge_lab.allow_event_delete', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'APPEND_ONLY: % on % is not allowed; record a GradeRevision instead', TG_OP, TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER submission_event_append_only
    BEFORE UPDATE OR DELETE ON events_submissionevent
    FOR EACH ROW EXECUTE FUNCTION events_append_only();
CREATE TRIGGER submission_answer_append_only
    BEFORE UPDATE OR DELETE ON events_submissionanswer
    FOR EACH ROW EXECUTE FUNCTION events_append_only();
CREATE TRIGGER grade_revision_append_only
    BEFORE UPDATE OR DELETE ON events_graderevision
    FOR EACH ROW EXECUTE FUNCTION events_append_only();

-- An event references a PUBLISHED version of its own question, in its own course.
CREATE FUNCTION events_check_event_refs() RETURNS trigger AS $$
DECLARE
    v_question bigint;
    v_status text;
    q_course bigint;
BEGIN
    SELECT question_id, status INTO v_question, v_status
      FROM curriculum_questionversion WHERE id = NEW.question_version_id;
    SELECT course_id INTO q_course FROM curriculum_question WHERE id = NEW.question_id;
    IF v_question <> NEW.question_id THEN
        RAISE EXCEPTION 'BAD_EVENT_REF: version % does not belong to question %', NEW.question_version_id, NEW.question_id;
    END IF;
    IF v_status <> 'PUBLISHED' THEN
        RAISE EXCEPTION 'BAD_EVENT_REF: question version % is %, not PUBLISHED', NEW.question_version_id, v_status;
    END IF;
    IF q_course <> NEW.course_id THEN
        RAISE EXCEPTION 'BAD_EVENT_REF: question % is not in course %', NEW.question_id, NEW.course_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER submission_event_refs
    BEFORE INSERT ON events_submissionevent
    FOR EACH ROW EXECUTE FUNCTION events_check_event_refs();
"""

BACKWARD = r"""
DROP TRIGGER IF EXISTS submission_event_refs ON events_submissionevent;
DROP TRIGGER IF EXISTS grade_revision_append_only ON events_graderevision;
DROP TRIGGER IF EXISTS submission_answer_append_only ON events_submissionanswer;
DROP TRIGGER IF EXISTS submission_event_append_only ON events_submissionevent;
DROP FUNCTION IF EXISTS events_check_event_refs();
DROP FUNCTION IF EXISTS events_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("events", "0001_initial"), ("curriculum", "0002_integrity_triggers")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
