"""Database-level invariants for curriculum data.

These live in PostgreSQL rather than in Python so that admin edits, shell scripts and
raw SQL all obey them. Each RAISE message starts with a stable tag used in tests.
"""

from django.db import migrations

FORWARD = r"""
-- 1. Concept weights of a question version sum to 1. Deferred to commit so a mapping
--    can be written row by row inside one transaction.
CREATE FUNCTION curriculum_check_weight_sum() RETURNS trigger AS $$
DECLARE
    vid bigint := COALESCE(NEW.question_version_id, OLD.question_version_id);
    total double precision;
    n integer;
BEGIN
    SELECT COALESCE(SUM(weight), 0), COUNT(*) INTO total, n
      FROM curriculum_questionconcept WHERE question_version_id = vid;
    IF n > 0 AND ABS(total - 1) > 1e-6 THEN
        RAISE EXCEPTION 'WEIGHT_SUM: concept weights of question version % sum to %, expected 1', vid, total;
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER question_concept_weight_sum
    AFTER INSERT OR UPDATE OR DELETE ON curriculum_questionconcept
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION curriculum_check_weight_sum();

-- 2. A question concept link stays inside the question's course, and only a DRAFT
--    version's mapping may change.
CREATE FUNCTION curriculum_check_question_concept() RETURNS trigger AS $$
DECLARE
    v_status text;
    q_course bigint;
    c_course bigint;
    row_ curriculum_questionconcept%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN row_ := OLD; ELSE row_ := NEW; END IF;
    SELECT v.status, q.course_id INTO v_status, q_course
      FROM curriculum_questionversion v JOIN curriculum_question q ON q.id = v.question_id
     WHERE v.id = row_.question_version_id;
    IF v_status <> 'DRAFT' THEN
        RAISE EXCEPTION 'VERSION_FROZEN: concept mapping of a % question version cannot change', v_status;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.question_version_id <> NEW.question_version_id THEN
        RAISE EXCEPTION 'VERSION_FROZEN: a concept link cannot move to another question version';
    END IF;
    IF TG_OP <> 'DELETE' THEN
        SELECT course_id INTO c_course FROM curriculum_concept WHERE id = NEW.concept_id;
        IF c_course <> q_course THEN
            RAISE EXCEPTION 'CROSS_COURSE: concept % is not in the question''s course', NEW.concept_id;
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER question_concept_guard
    BEFORE INSERT OR UPDATE OR DELETE ON curriculum_questionconcept
    FOR EACH ROW EXECUTE FUNCTION curriculum_check_question_concept();

-- 3. Published versions are immutable. The only allowed change is PUBLISHED -> RETIRED.
CREATE FUNCTION curriculum_guard_question_version() RETURNS trigger AS $$
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
           OR NEW.published_at IS DISTINCT FROM OLD.published_at
           OR NOT (NEW.status = OLD.status OR (OLD.status = 'PUBLISHED' AND NEW.status = 'RETIRED')) THEN
            RAISE EXCEPTION 'VERSION_FROZEN: question version % is %, create a new version instead', OLD.id, OLD.status;
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER question_version_guard
    BEFORE UPDATE OR DELETE ON curriculum_questionversion
    FOR EACH ROW EXECUTE FUNCTION curriculum_guard_question_version();

-- 4. A version cannot leave DRAFT without a complete concept mapping.
CREATE FUNCTION curriculum_check_version_mapping() RETURNS trigger AS $$
DECLARE
    total double precision;
    n integer;
BEGIN
    IF NEW.status = 'DRAFT' THEN RETURN NULL; END IF;
    SELECT COALESCE(SUM(weight), 0), COUNT(*) INTO total, n
      FROM curriculum_questionconcept WHERE question_version_id = NEW.id;
    IF n = 0 OR ABS(total - 1) > 1e-6 THEN
        RAISE EXCEPTION 'NO_MAPPING: question version % needs concept weights summing to 1 before publishing', NEW.id;
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER question_version_mapping
    AFTER INSERT OR UPDATE OF status ON curriculum_questionversion
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION curriculum_check_version_mapping();

-- 5. Question.current_version points to a PUBLISHED version of the same question.
CREATE FUNCTION curriculum_check_current_version() RETURNS trigger AS $$
DECLARE
    v_question bigint;
    v_status text;
BEGIN
    IF NEW.current_version_id IS NULL THEN RETURN NEW; END IF;
    SELECT question_id, status INTO v_question, v_status
      FROM curriculum_questionversion WHERE id = NEW.current_version_id;
    IF v_question <> NEW.id OR v_status <> 'PUBLISHED' THEN
        RAISE EXCEPTION 'BAD_CURRENT_VERSION: current version must be a PUBLISHED version of question %', NEW.id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER question_current_version_guard
    BEFORE INSERT OR UPDATE OF current_version_id ON curriculum_question
    FOR EACH ROW EXECUTE FUNCTION curriculum_check_current_version();

-- 6. Edges connect concepts of the same course.
CREATE FUNCTION curriculum_check_edge_course() RETURNS trigger AS $$
BEGIN
    IF (SELECT course_id FROM curriculum_concept WHERE id = NEW.source_id)
       <> (SELECT course_id FROM curriculum_concept WHERE id = NEW.target_id) THEN
        RAISE EXCEPTION 'CROSS_COURSE: edge endpoints must be in the same course';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER concept_edge_course_guard
    BEFORE INSERT OR UPDATE ON curriculum_conceptedge
    FOR EACH ROW EXECUTE FUNCTION curriculum_check_edge_course();
"""

BACKWARD = r"""
DROP TRIGGER IF EXISTS concept_edge_course_guard ON curriculum_conceptedge;
DROP TRIGGER IF EXISTS question_current_version_guard ON curriculum_question;
DROP TRIGGER IF EXISTS question_version_mapping ON curriculum_questionversion;
DROP TRIGGER IF EXISTS question_version_guard ON curriculum_questionversion;
DROP TRIGGER IF EXISTS question_concept_guard ON curriculum_questionconcept;
DROP TRIGGER IF EXISTS question_concept_weight_sum ON curriculum_questionconcept;
DROP FUNCTION IF EXISTS curriculum_check_edge_course();
DROP FUNCTION IF EXISTS curriculum_check_current_version();
DROP FUNCTION IF EXISTS curriculum_check_version_mapping();
DROP FUNCTION IF EXISTS curriculum_guard_question_version();
DROP FUNCTION IF EXISTS curriculum_check_question_concept();
DROP FUNCTION IF EXISTS curriculum_check_weight_sum();
"""


class Migration(migrations.Migration):
    dependencies = [("curriculum", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
