-- PostgreSQL Stored Procedures for Exam Seating System
-- Run after triggers.sql: psql -d exam_seating -f procedures.sql

-- ============================================
-- SEATING MANAGEMENT PROCEDURES
-- ============================================

-- Clear all seating assignments for an exam (with history snapshot)
CREATE OR REPLACE FUNCTION clear_exam_seating(
    p_exam_id INTEGER,
    p_save_history BOOLEAN DEFAULT TRUE,
    p_user_id INTEGER DEFAULT NULL
) RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
    current_version INTEGER;
    snapshot JSONB;
BEGIN
    -- Set audit context if user provided
    IF p_user_id IS NOT NULL THEN
        PERFORM set_audit_context(p_user_id);
    END IF;

    -- Save to history before clearing
    IF p_save_history THEN
        -- Get next version number
        SELECT COALESCE(MAX(version), 0) + 1 INTO current_version
        FROM seating_history WHERE exam_id = p_exam_id;

        -- Create snapshot of current seating
        SELECT jsonb_agg(
            jsonb_build_object(
                'student_id', sa.student_id,
                'room_id', sa.room_id,
                'seat_number', sa.seat_number,
                'seat_x', sa.seat_x,
                'seat_y', sa.seat_y,
                'color_group', sa.color_group
            )
        ) INTO snapshot
        FROM seating_assignments sa
        WHERE sa.exam_id = p_exam_id;

        IF snapshot IS NOT NULL THEN
            -- Mark all previous versions as inactive
            UPDATE seating_history SET is_active = FALSE WHERE exam_id = p_exam_id;

            -- Insert new history record
            INSERT INTO seating_history (
                exam_id, version, generated_by, total_students,
                rooms_used, snapshot, is_active, notes
            )
            SELECT
                p_exam_id,
                current_version,
                p_user_id,
                COUNT(DISTINCT student_id),
                COUNT(DISTINCT room_id),
                snapshot,
                FALSE,
                'Snapshot before clear'
            FROM seating_assignments WHERE exam_id = p_exam_id;
        END IF;
    END IF;

    -- Delete seating assignments
    DELETE FROM seating_assignments WHERE exam_id = p_exam_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Bulk insert seating assignments from JSON
CREATE OR REPLACE FUNCTION bulk_insert_seating(
    p_exam_id INTEGER,
    p_assignments JSONB,
    p_user_id INTEGER DEFAULT NULL,
    p_algorithm VARCHAR DEFAULT 'dsatur_ffd'
) RETURNS TABLE(inserted INTEGER, errors TEXT[]) AS $$
DECLARE
    rec JSONB;
    insert_count INTEGER := 0;
    error_list TEXT[] := ARRAY[]::TEXT[];
    start_time TIMESTAMP;
    end_time TIMESTAMP;
    current_version INTEGER;
BEGIN
    start_time := clock_timestamp();

    -- Set audit context
    IF p_user_id IS NOT NULL THEN
        PERFORM set_audit_context(p_user_id);
    END IF;

    -- Process each assignment
    FOR rec IN SELECT * FROM jsonb_array_elements(p_assignments)
    LOOP
        BEGIN
            INSERT INTO seating_assignments (
                exam_id, student_id, room_id, seat_number,
                seat_x, seat_y, color_group, assigned_by
            ) VALUES (
                p_exam_id,
                (rec->>'student_id')::INTEGER,
                (rec->>'room_id')::INTEGER,
                (rec->>'seat_number')::INTEGER,
                (rec->>'seat_x')::INTEGER,
                (rec->>'seat_y')::INTEGER,
                (rec->>'color_group')::INTEGER,
                p_user_id
            );
            insert_count := insert_count + 1;
        EXCEPTION WHEN OTHERS THEN
            error_list := array_append(error_list,
                format('Student %s: %s', rec->>'student_id', SQLERRM));
        END;
    END LOOP;

    end_time := clock_timestamp();

    -- Save to history
    SELECT COALESCE(MAX(version), 0) + 1 INTO current_version
    FROM seating_history WHERE exam_id = p_exam_id;

    INSERT INTO seating_history (
        exam_id, version, generated_by, total_students, rooms_used,
        algorithm_used, generation_time_ms, snapshot, is_active
    )
    SELECT
        p_exam_id,
        current_version,
        p_user_id,
        COUNT(DISTINCT student_id),
        COUNT(DISTINCT room_id),
        p_algorithm,
        EXTRACT(MILLISECONDS FROM (end_time - start_time))::INTEGER,
        p_assignments,
        TRUE
    FROM seating_assignments WHERE exam_id = p_exam_id;

    -- Mark previous versions inactive
    UPDATE seating_history
    SET is_active = FALSE
    WHERE exam_id = p_exam_id AND version < current_version;

    RETURN QUERY SELECT insert_count, error_list;
END;
$$ LANGUAGE plpgsql;

-- Restore seating from history version
CREATE OR REPLACE FUNCTION restore_seating_version(
    p_exam_id INTEGER,
    p_version INTEGER,
    p_user_id INTEGER DEFAULT NULL
) RETURNS INTEGER AS $$
DECLARE
    snapshot JSONB;
    restored_count INTEGER;
BEGIN
    -- Get snapshot from history
    SELECT sh.snapshot INTO snapshot
    FROM seating_history sh
    WHERE sh.exam_id = p_exam_id AND sh.version = p_version;

    IF snapshot IS NULL THEN
        RAISE EXCEPTION 'No seating history found for exam % version %', p_exam_id, p_version;
    END IF;

    -- Clear current seating (without saving history again)
    PERFORM clear_exam_seating(p_exam_id, FALSE, p_user_id);

    -- Restore from snapshot
    SELECT inserted INTO restored_count
    FROM bulk_insert_seating(p_exam_id, snapshot, p_user_id, 'restored');

    -- Update history to mark this version as active
    UPDATE seating_history SET is_active = FALSE WHERE exam_id = p_exam_id;
    UPDATE seating_history SET is_active = TRUE
    WHERE exam_id = p_exam_id AND version = p_version;

    RETURN restored_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- AUDIT LOG MANAGEMENT
-- ============================================

-- Clean up old audit logs
CREATE OR REPLACE FUNCTION cleanup_audit_logs(
    p_retention_days INTEGER DEFAULT 90
) RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM audit_logs
    WHERE created_at < CURRENT_TIMESTAMP - (p_retention_days || ' days')::INTERVAL;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Archive audit logs to separate table (optional)
CREATE OR REPLACE FUNCTION archive_audit_logs(
    p_before_date TIMESTAMP
) RETURNS INTEGER AS $$
DECLARE
    archived_count INTEGER;
BEGIN
    -- Create archive table if not exists
    CREATE TABLE IF NOT EXISTS audit_logs_archive (LIKE audit_logs INCLUDING ALL);

    -- Move old records to archive
    WITH moved AS (
        DELETE FROM audit_logs
        WHERE created_at < p_before_date
        RETURNING *
    )
    INSERT INTO audit_logs_archive SELECT * FROM moved;

    GET DIAGNOSTICS archived_count = ROW_COUNT;
    RETURN archived_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- STUDENT RELATIONSHIP MANAGEMENT
-- ============================================

-- Add or update student relationship
CREATE OR REPLACE FUNCTION upsert_relationship(
    p_student1_id INTEGER,
    p_student2_id INTEGER,
    p_type relationship_type DEFAULT 'friend',
    p_reported_by INTEGER DEFAULT NULL,
    p_notes TEXT DEFAULT NULL
) RETURNS INTEGER AS $$
DECLARE
    s1 INTEGER;
    s2 INTEGER;
    result_id INTEGER;
BEGIN
    -- Ensure consistent ordering
    IF p_student1_id < p_student2_id THEN
        s1 := p_student1_id;
        s2 := p_student2_id;
    ELSE
        s1 := p_student2_id;
        s2 := p_student1_id;
    END IF;

    -- Upsert relationship
    INSERT INTO student_relationships (
        student1_id, student2_id, relationship_type, reported_by, notes
    ) VALUES (s1, s2, p_type, p_reported_by, p_notes)
    ON CONFLICT (student1_id, student2_id) DO UPDATE SET
        relationship_type = EXCLUDED.relationship_type,
        notes = COALESCE(EXCLUDED.notes, student_relationships.notes),
        is_active = TRUE,
        updated_at = CURRENT_TIMESTAMP
    RETURNING id INTO result_id;

    RETURN result_id;
END;
$$ LANGUAGE plpgsql;

-- Bulk import relationships from JSON array
CREATE OR REPLACE FUNCTION bulk_import_relationships(
    p_relationships JSONB,
    p_reported_by INTEGER DEFAULT NULL
) RETURNS TABLE(imported INTEGER, skipped INTEGER, errors TEXT[]) AS $$
DECLARE
    rec JSONB;
    import_count INTEGER := 0;
    skip_count INTEGER := 0;
    error_list TEXT[] := ARRAY[]::TEXT[];
    s1_id INTEGER;
    s2_id INTEGER;
BEGIN
    FOR rec IN SELECT * FROM jsonb_array_elements(p_relationships)
    LOOP
        BEGIN
            -- Try to find students by student_id (string identifier)
            SELECT id INTO s1_id FROM students WHERE student_id = rec->>'student1';
            SELECT id INTO s2_id FROM students WHERE student_id = rec->>'student2';

            IF s1_id IS NULL THEN
                skip_count := skip_count + 1;
                error_list := array_append(error_list,
                    format('Student not found: %s', rec->>'student1'));
                CONTINUE;
            END IF;

            IF s2_id IS NULL THEN
                skip_count := skip_count + 1;
                error_list := array_append(error_list,
                    format('Student not found: %s', rec->>'student2'));
                CONTINUE;
            END IF;

            PERFORM upsert_relationship(
                s1_id, s2_id,
                COALESCE((rec->>'type')::relationship_type, 'friend'),
                p_reported_by,
                rec->>'notes'
            );
            import_count := import_count + 1;

        EXCEPTION WHEN OTHERS THEN
            error_list := array_append(error_list,
                format('Error for %s-%s: %s', rec->>'student1', rec->>'student2', SQLERRM));
        END;
    END LOOP;

    RETURN QUERY SELECT import_count, skip_count, error_list;
END;
$$ LANGUAGE plpgsql;

-- Get all friends for a student
CREATE OR REPLACE FUNCTION get_student_friends(p_student_id INTEGER)
RETURNS TABLE(
    friend_id INTEGER,
    friend_student_id VARCHAR,
    friend_name VARCHAR,
    relationship relationship_type
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        CASE WHEN sr.student1_id = p_student_id THEN sr.student2_id ELSE sr.student1_id END,
        s.student_id,
        s.name,
        sr.relationship_type
    FROM student_relationships sr
    JOIN students s ON s.id = CASE
        WHEN sr.student1_id = p_student_id THEN sr.student2_id
        ELSE sr.student1_id
    END
    WHERE (sr.student1_id = p_student_id OR sr.student2_id = p_student_id)
      AND sr.is_active = TRUE;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- EXAM ENROLLMENT PROCEDURES
-- ============================================

-- Bulk enroll students in exam
CREATE OR REPLACE FUNCTION bulk_enroll_students(
    p_exam_id INTEGER,
    p_student_ids INTEGER[]
) RETURNS INTEGER AS $$
DECLARE
    enrolled_count INTEGER := 0;
    sid INTEGER;
BEGIN
    FOREACH sid IN ARRAY p_student_ids
    LOOP
        BEGIN
            INSERT INTO exam_enrollments (exam_id, student_id)
            VALUES (p_exam_id, sid)
            ON CONFLICT (student_id, exam_id) DO NOTHING;

            IF FOUND THEN
                enrolled_count := enrolled_count + 1;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            -- Skip invalid student IDs
            CONTINUE;
        END;
    END LOOP;

    RETURN enrolled_count;
END;
$$ LANGUAGE plpgsql;

-- Enroll students by criteria (department, year, etc.)
CREATE OR REPLACE FUNCTION enroll_students_by_criteria(
    p_exam_id INTEGER,
    p_department_id INTEGER DEFAULT NULL,
    p_year INTEGER DEFAULT NULL,
    p_branch VARCHAR DEFAULT NULL
) RETURNS INTEGER AS $$
DECLARE
    enrolled_count INTEGER;
BEGIN
    WITH to_enroll AS (
        SELECT id FROM students
        WHERE is_active = TRUE
          AND (p_department_id IS NULL OR department_id = p_department_id)
          AND (p_year IS NULL OR year = p_year)
          AND (p_branch IS NULL OR branch = p_branch)
    )
    INSERT INTO exam_enrollments (exam_id, student_id)
    SELECT p_exam_id, id FROM to_enroll
    ON CONFLICT (student_id, exam_id) DO NOTHING;

    GET DIAGNOSTICS enrolled_count = ROW_COUNT;
    RETURN enrolled_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- STATISTICS AND REPORTING
-- ============================================

-- Get exam seating statistics
CREATE OR REPLACE FUNCTION get_exam_seating_stats(p_exam_id INTEGER)
RETURNS TABLE(
    total_enrolled BIGINT,
    total_seated BIGINT,
    rooms_used BIGINT,
    friend_adjacencies BIGINT,
    avg_students_per_room NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT COUNT(*) FROM exam_enrollments WHERE exam_id = p_exam_id),
        (SELECT COUNT(*) FROM seating_assignments WHERE exam_id = p_exam_id),
        (SELECT COUNT(DISTINCT room_id) FROM seating_assignments WHERE exam_id = p_exam_id),
        (SELECT COUNT(*) FROM cheat_detection_flags
         WHERE exam_id = p_exam_id AND flag_type = 'friend_adjacent'),
        (SELECT ROUND(AVG(cnt), 2) FROM (
            SELECT COUNT(*) as cnt FROM seating_assignments
            WHERE exam_id = p_exam_id GROUP BY room_id
        ) sub);
END;
$$ LANGUAGE plpgsql;

-- Get room utilization for date range
CREATE OR REPLACE FUNCTION get_room_utilization(
    p_start_date DATE,
    p_end_date DATE
) RETURNS TABLE(
    room_id INTEGER,
    room_name VARCHAR,
    capacity INTEGER,
    total_exams BIGINT,
    total_students BIGINT,
    utilization_pct NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        r.id,
        r.room_name,
        r.capacity,
        COUNT(DISTINCT e.id),
        COUNT(sa.id),
        ROUND(
            (COUNT(sa.id)::NUMERIC / NULLIF(COUNT(DISTINCT e.id) * r.capacity, 0)) * 100,
            2
        )
    FROM rooms r
    LEFT JOIN seating_assignments sa ON sa.room_id = r.id
    LEFT JOIN exams e ON e.id = sa.exam_id
        AND e.exam_date BETWEEN p_start_date AND p_end_date
    WHERE r.is_active = TRUE
    GROUP BY r.id, r.room_name, r.capacity
    ORDER BY r.room_name;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- DOCUMENTATION
-- ============================================

COMMENT ON FUNCTION clear_exam_seating(INTEGER, BOOLEAN, INTEGER) IS
    'Clear all seating for an exam, optionally saving to history first';

COMMENT ON FUNCTION bulk_insert_seating(INTEGER, JSONB, INTEGER, VARCHAR) IS
    'Bulk insert seating assignments from JSON array with error handling';

COMMENT ON FUNCTION restore_seating_version(INTEGER, INTEGER, INTEGER) IS
    'Restore seating from a historical snapshot version';

COMMENT ON FUNCTION cleanup_audit_logs(INTEGER) IS
    'Delete audit logs older than specified retention days';

COMMENT ON FUNCTION upsert_relationship(INTEGER, INTEGER, relationship_type, INTEGER, TEXT) IS
    'Add or update student relationship with automatic ID ordering';

COMMENT ON FUNCTION bulk_import_relationships(JSONB, INTEGER) IS
    'Import relationships from JSON array using student_id strings';

COMMENT ON FUNCTION get_student_friends(INTEGER) IS
    'Get all active friend relationships for a student';

COMMENT ON FUNCTION get_exam_seating_stats(INTEGER) IS
    'Get statistics for exam seating including friend adjacency count';
