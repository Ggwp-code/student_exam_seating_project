-- PostgreSQL Triggers for Exam Seating System
-- Run after schema.sql: psql -d exam_seating -f triggers.sql

-- ============================================
-- TIMESTAMP UPDATE TRIGGER
-- ============================================

CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply timestamp trigger to relevant tables
CREATE TRIGGER trg_students_timestamp
    BEFORE UPDATE ON students
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER trg_rooms_timestamp
    BEFORE UPDATE ON rooms
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER trg_users_timestamp
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER trg_exams_timestamp
    BEFORE UPDATE ON exams
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER trg_relationships_timestamp
    BEFORE UPDATE ON student_relationships
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

-- ============================================
-- AUDIT LOGGING TRIGGER
-- ============================================

-- Session variable to store current user context
-- Set via: SELECT set_config('app.current_user_id', '1', true);
-- Set via: SELECT set_config('app.current_username', 'admin', true);
-- Set via: SELECT set_config('app.session_id', 'abc123', true);

CREATE OR REPLACE FUNCTION audit_trigger_func()
RETURNS TRIGGER AS $$
DECLARE
    audit_user_id INTEGER;
    audit_username VARCHAR(100);
    audit_session VARCHAR(100);
    old_data JSONB;
    new_data JSONB;
    mapped_action audit_action;
BEGIN
    -- Get user context from session variables
    BEGIN
        audit_user_id := NULLIF(current_setting('app.current_user_id', true), '')::INTEGER;
    EXCEPTION WHEN OTHERS THEN
        audit_user_id := NULL;
    END;

    BEGIN
        audit_username := NULLIF(current_setting('app.current_username', true), '');
    EXCEPTION WHEN OTHERS THEN
        audit_username := 'system';
    END;

    BEGIN
        audit_session := NULLIF(current_setting('app.session_id', true), '');
    EXCEPTION WHEN OTHERS THEN
        audit_session := NULL;
    END;

    -- Build old/new data JSON and map TG_OP to our enum
    IF TG_OP = 'DELETE' THEN
        old_data := to_jsonb(OLD);
        new_data := NULL;
        mapped_action := 'DELETE';
    ELSIF TG_OP = 'INSERT' THEN
        old_data := NULL;
        new_data := to_jsonb(NEW);
        mapped_action := 'CREATE';
    ELSIF TG_OP = 'UPDATE' THEN
        old_data := to_jsonb(OLD);
        new_data := to_jsonb(NEW);
        mapped_action := 'UPDATE';
    END IF;

    -- Insert audit record
    INSERT INTO audit_logs (
        user_id,
        username,
        action,
        table_name,
        record_id,
        old_values,
        new_values,
        session_id
    ) VALUES (
        audit_user_id,
        COALESCE(audit_username, 'system'),
        mapped_action,
        TG_TABLE_NAME,
        COALESCE((NEW).id, (OLD).id),
        old_data,
        new_data,
        audit_session
    );

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    ELSE
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Apply audit trigger to key tables
CREATE TRIGGER audit_students
    AFTER INSERT OR UPDATE OR DELETE ON students
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

CREATE TRIGGER audit_rooms
    AFTER INSERT OR UPDATE OR DELETE ON rooms
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

CREATE TRIGGER audit_users
    AFTER INSERT OR UPDATE OR DELETE ON users
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

CREATE TRIGGER audit_exams
    AFTER INSERT OR UPDATE OR DELETE ON exams
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

CREATE TRIGGER audit_seating
    AFTER INSERT OR UPDATE OR DELETE ON seating_assignments
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

CREATE TRIGGER audit_relationships
    AFTER INSERT OR UPDATE OR DELETE ON student_relationships
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

CREATE TRIGGER audit_invigilators
    AFTER INSERT OR UPDATE OR DELETE ON invigilators
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

-- ============================================
-- SEATING VALIDATION TRIGGER
-- ============================================

CREATE OR REPLACE FUNCTION validate_seating_assignment()
RETURNS TRIGGER AS $$
DECLARE
    room_capacity INTEGER;
    current_count INTEGER;
    room_cols INTEGER;
    room_rows INTEGER;
BEGIN
    -- Get room configuration
    SELECT capacity, layout_columns, layout_rows
    INTO room_capacity, room_cols, room_rows
    FROM rooms WHERE id = NEW.room_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Room with id % does not exist', NEW.room_id;
    END IF;

    -- Validate seat coordinates are within room layout
    IF NEW.seat_x >= room_cols THEN
        RAISE EXCEPTION 'Seat X coordinate (%) exceeds room columns (%)', NEW.seat_x, room_cols;
    END IF;

    IF NEW.seat_y >= room_rows THEN
        RAISE EXCEPTION 'Seat Y coordinate (%) exceeds room rows (%)', NEW.seat_y, room_rows;
    END IF;

    -- Check room capacity (excluding current assignment if updating)
    SELECT COUNT(*) INTO current_count
    FROM seating_assignments
    WHERE room_id = NEW.room_id
      AND exam_id = NEW.exam_id
      AND id != COALESCE(NEW.id, 0);

    IF current_count >= room_capacity THEN
        RAISE EXCEPTION 'Room % is at full capacity (%) for exam %',
            NEW.room_id, room_capacity, NEW.exam_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_seating
    BEFORE INSERT OR UPDATE ON seating_assignments
    FOR EACH ROW EXECUTE FUNCTION validate_seating_assignment();

-- ============================================
-- FRIEND ADJACENCY WARNING TRIGGER
-- ============================================

CREATE OR REPLACE FUNCTION check_friend_adjacency()
RETURNS TRIGGER AS $$
DECLARE
    friend_record RECORD;
    adjacent_friend RECORD;
BEGIN
    -- Check if any friends are in adjacent seats (within 1 seat in any direction)
    FOR adjacent_friend IN
        SELECT sa.*, s.name as student_name
        FROM seating_assignments sa
        JOIN students s ON s.id = sa.student_id
        WHERE sa.exam_id = NEW.exam_id
          AND sa.room_id = NEW.room_id
          AND sa.student_id != NEW.student_id
          AND ABS(sa.seat_x - NEW.seat_x) <= 1
          AND ABS(sa.seat_y - NEW.seat_y) <= 1
          AND EXISTS (
              SELECT 1 FROM student_relationships sr
              WHERE sr.is_active = TRUE
                AND ((sr.student1_id = NEW.student_id AND sr.student2_id = sa.student_id)
                  OR (sr.student1_id = sa.student_id AND sr.student2_id = NEW.student_id))
          )
    LOOP
        -- Insert a cheat detection flag (warning, not blocking)
        INSERT INTO cheat_detection_flags (
            exam_id,
            student1_id,
            student2_id,
            flag_type,
            severity,
            details
        ) VALUES (
            NEW.exam_id,
            LEAST(NEW.student_id, adjacent_friend.student_id),
            GREATEST(NEW.student_id, adjacent_friend.student_id),
            'friend_adjacent',
            'medium',
            jsonb_build_object(
                'seat1', jsonb_build_object('x', NEW.seat_x, 'y', NEW.seat_y, 'seat_no', NEW.seat_number),
                'seat2', jsonb_build_object('x', adjacent_friend.seat_x, 'y', adjacent_friend.seat_y, 'seat_no', adjacent_friend.seat_number),
                'room_id', NEW.room_id,
                'auto_flagged', true
            )
        ) ON CONFLICT DO NOTHING;

        -- Log a notice (doesn't block the insert)
        RAISE NOTICE 'Warning: Friends seated adjacent - Student % near Student % in room %',
            NEW.student_id, adjacent_friend.student_id, NEW.room_id;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_check_friend_adjacency
    AFTER INSERT OR UPDATE ON seating_assignments
    FOR EACH ROW EXECUTE FUNCTION check_friend_adjacency();

-- ============================================
-- EXAM ENROLLMENT COUNT TRIGGER
-- ============================================

CREATE OR REPLACE FUNCTION update_exam_student_count()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE exams SET total_students = total_students + 1 WHERE id = NEW.exam_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE exams SET total_students = total_students - 1 WHERE id = OLD.exam_id;
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enrollment_count
    AFTER INSERT OR DELETE ON exam_enrollments
    FOR EACH ROW EXECUTE FUNCTION update_exam_student_count();

-- ============================================
-- USER LOGIN TRACKING
-- ============================================

CREATE OR REPLACE FUNCTION log_user_login()
RETURNS TRIGGER AS $$
BEGIN
    -- Only log if last_login actually changed
    IF OLD.last_login IS DISTINCT FROM NEW.last_login AND NEW.last_login IS NOT NULL THEN
        INSERT INTO audit_logs (
            user_id,
            username,
            action,
            table_name,
            record_id,
            new_values
        ) VALUES (
            NEW.id,
            NEW.username,
            'LOGIN',
            'users',
            NEW.id,
            jsonb_build_object('login_time', NEW.last_login)
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_user_login
    AFTER UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION log_user_login();

-- ============================================
-- RELATIONSHIP ORDERING ENFORCEMENT
-- ============================================

CREATE OR REPLACE FUNCTION enforce_relationship_ordering()
RETURNS TRIGGER AS $$
BEGIN
    -- Ensure student1_id is always less than student2_id
    IF NEW.student1_id > NEW.student2_id THEN
        -- Swap the values
        DECLARE
            temp INTEGER := NEW.student1_id;
        BEGIN
            NEW.student1_id := NEW.student2_id;
            NEW.student2_id := temp;
        END;
    END IF;

    -- Prevent self-relationships
    IF NEW.student1_id = NEW.student2_id THEN
        RAISE EXCEPTION 'Cannot create relationship between student and themselves';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_relationship_ordering
    BEFORE INSERT OR UPDATE ON student_relationships
    FOR EACH ROW EXECUTE FUNCTION enforce_relationship_ordering();

-- ============================================
-- HELPER FUNCTIONS
-- ============================================

-- Function to set audit context (call from application)
CREATE OR REPLACE FUNCTION set_audit_context(
    p_user_id INTEGER DEFAULT NULL,
    p_username VARCHAR DEFAULT NULL,
    p_session_id VARCHAR DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    IF p_user_id IS NOT NULL THEN
        PERFORM set_config('app.current_user_id', p_user_id::TEXT, true);
    END IF;
    IF p_username IS NOT NULL THEN
        PERFORM set_config('app.current_username', p_username, true);
    END IF;
    IF p_session_id IS NOT NULL THEN
        PERFORM set_config('app.session_id', p_session_id, true);
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Function to clear audit context
CREATE OR REPLACE FUNCTION clear_audit_context()
RETURNS VOID AS $$
BEGIN
    PERFORM set_config('app.current_user_id', '', true);
    PERFORM set_config('app.current_username', '', true);
    PERFORM set_config('app.session_id', '', true);
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- DOCUMENTATION
-- ============================================

COMMENT ON FUNCTION update_timestamp() IS 'Auto-updates updated_at column on row modification';
COMMENT ON FUNCTION audit_trigger_func() IS 'Logs all changes to audit_logs table with user context';
COMMENT ON FUNCTION validate_seating_assignment() IS 'Validates seat coordinates and room capacity';
COMMENT ON FUNCTION check_friend_adjacency() IS 'Flags friends seated adjacent - warning only, does not block';
COMMENT ON FUNCTION set_audit_context(INTEGER, VARCHAR, VARCHAR) IS 'Set user context for audit logging - call at session start';
COMMENT ON FUNCTION enforce_relationship_ordering() IS 'Ensures student1_id < student2_id for consistent relationship storage';
