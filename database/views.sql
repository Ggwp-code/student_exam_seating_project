-- PostgreSQL Views for Exam Seating System Analytics
-- Run after procedures.sql: psql -d exam_seating -f views.sql

-- ============================================
-- DASHBOARD SUMMARY VIEWS
-- ============================================

-- Overall system statistics
CREATE OR REPLACE VIEW v_system_stats AS
SELECT
    (SELECT COUNT(*) FROM students WHERE is_active = TRUE) AS total_students,
    (SELECT COUNT(*) FROM rooms WHERE is_active = TRUE) AS total_rooms,
    (SELECT SUM(capacity) FROM rooms WHERE is_active = TRUE) AS total_capacity,
    (SELECT COUNT(*) FROM exams WHERE is_active = TRUE) AS total_exams,
    (SELECT COUNT(*) FROM exams WHERE exam_date >= CURRENT_DATE AND is_active = TRUE) AS upcoming_exams,
    (SELECT COUNT(*) FROM users WHERE is_active = TRUE) AS total_users,
    (SELECT COUNT(*) FROM student_relationships WHERE is_active = TRUE) AS total_relationships,
    (SELECT COUNT(*) FROM cheat_detection_flags WHERE reviewed = FALSE) AS unreviewed_flags;

-- Today's exam summary
CREATE OR REPLACE VIEW v_todays_exams AS
SELECT
    e.id,
    e.exam_code,
    e.name,
    e.subject,
    d.name AS department_name,
    e.exam_time,
    e.duration_minutes,
    e.total_students,
    (SELECT COUNT(*) FROM seating_assignments sa WHERE sa.exam_id = e.id) AS seated_count,
    (SELECT COUNT(DISTINCT room_id) FROM seating_assignments sa WHERE sa.exam_id = e.id) AS rooms_used
FROM exams e
LEFT JOIN departments d ON d.id = e.department_id
WHERE e.exam_date = CURRENT_DATE AND e.is_active = TRUE
ORDER BY e.exam_time;

-- ============================================
-- EXAM ANALYTICS VIEWS
-- ============================================

-- Exam seating summary with room breakdown
CREATE OR REPLACE VIEW v_exam_seating_summary AS
SELECT
    e.id AS exam_id,
    e.exam_code,
    e.name AS exam_name,
    e.subject,
    e.exam_date,
    e.exam_time,
    d.name AS department,
    e.total_students AS enrolled,
    COUNT(sa.id) AS seated,
    COUNT(DISTINCT sa.room_id) AS rooms_used,
    STRING_AGG(DISTINCT r.room_name, ', ' ORDER BY r.room_name) AS room_list,
    ROUND(COUNT(sa.id)::NUMERIC / NULLIF(e.total_students, 0) * 100, 1) AS seating_pct
FROM exams e
LEFT JOIN departments d ON d.id = e.department_id
LEFT JOIN seating_assignments sa ON sa.exam_id = e.id
LEFT JOIN rooms r ON r.id = sa.room_id
WHERE e.is_active = TRUE
GROUP BY e.id, e.exam_code, e.name, e.subject, e.exam_date, e.exam_time, d.name, e.total_students
ORDER BY e.exam_date DESC, e.exam_time;

-- Exam schedule calendar view
CREATE OR REPLACE VIEW v_exam_calendar AS
SELECT
    exam_date,
    exam_time,
    COUNT(*) AS exam_count,
    SUM(total_students) AS total_students,
    STRING_AGG(exam_code || ': ' || subject, '; ' ORDER BY exam_code) AS exams
FROM exams
WHERE is_active = TRUE AND exam_date >= CURRENT_DATE
GROUP BY exam_date, exam_time
ORDER BY exam_date, exam_time;

-- ============================================
-- ROOM ANALYTICS VIEWS
-- ============================================

-- Room utilization summary
CREATE OR REPLACE VIEW v_room_utilization AS
SELECT
    r.id AS room_id,
    r.room_name,
    r.building,
    r.floor,
    r.capacity,
    r.has_ac,
    r.has_projector,
    r.has_cctv,
    COUNT(DISTINCT sa.exam_id) AS total_exams,
    COUNT(sa.id) AS total_seats_used,
    ROUND(
        COUNT(sa.id)::NUMERIC / NULLIF(COUNT(DISTINCT sa.exam_id) * r.capacity, 0) * 100,
        1
    ) AS avg_utilization_pct
FROM rooms r
LEFT JOIN seating_assignments sa ON sa.room_id = r.id
WHERE r.is_active = TRUE
GROUP BY r.id, r.room_name, r.building, r.floor, r.capacity, r.has_ac, r.has_projector, r.has_cctv
ORDER BY r.building, r.room_name;

-- Room availability for specific date
CREATE OR REPLACE VIEW v_room_availability_today AS
SELECT
    r.id AS room_id,
    r.room_name,
    r.capacity,
    e.exam_time,
    COALESCE(COUNT(sa.id), 0) AS students_assigned,
    r.capacity - COALESCE(COUNT(sa.id), 0) AS available_seats
FROM rooms r
CROSS JOIN (VALUES ('Morning'), ('Afternoon'), ('Evening')) AS slots(exam_time)
LEFT JOIN exams e ON e.exam_date = CURRENT_DATE
    AND e.exam_time::TEXT = slots.exam_time
    AND e.is_active = TRUE
LEFT JOIN seating_assignments sa ON sa.room_id = r.id AND sa.exam_id = e.id
WHERE r.is_active = TRUE
GROUP BY r.id, r.room_name, r.capacity, e.exam_time, slots.exam_time
ORDER BY r.room_name, slots.exam_time;

-- ============================================
-- STUDENT ANALYTICS VIEWS
-- ============================================

-- Student exam schedule
CREATE OR REPLACE VIEW v_student_exam_schedule AS
SELECT
    s.id AS student_id,
    s.student_id AS student_code,
    s.name AS student_name,
    d.name AS department,
    s.year,
    s.branch,
    e.exam_code,
    e.subject,
    e.exam_date,
    e.exam_time,
    r.room_name,
    sa.seat_number,
    sa.seat_x,
    sa.seat_y
FROM students s
JOIN departments d ON d.id = s.department_id
JOIN exam_enrollments ee ON ee.student_id = s.id
JOIN exams e ON e.id = ee.exam_id
LEFT JOIN seating_assignments sa ON sa.student_id = s.id AND sa.exam_id = e.id
LEFT JOIN rooms r ON r.id = sa.room_id
WHERE s.is_active = TRUE AND e.is_active = TRUE
ORDER BY s.student_id, e.exam_date, e.exam_time;

-- Department-wise student count
CREATE OR REPLACE VIEW v_department_stats AS
SELECT
    d.id AS department_id,
    d.code AS department_code,
    d.name AS department_name,
    COUNT(s.id) AS total_students,
    COUNT(CASE WHEN s.year = 1 THEN 1 END) AS year_1,
    COUNT(CASE WHEN s.year = 2 THEN 1 END) AS year_2,
    COUNT(CASE WHEN s.year = 3 THEN 1 END) AS year_3,
    COUNT(CASE WHEN s.year = 4 THEN 1 END) AS year_4
FROM departments d
LEFT JOIN students s ON s.department_id = d.id AND s.is_active = TRUE
GROUP BY d.id, d.code, d.name
ORDER BY d.name;

-- ============================================
-- CHEAT DETECTION ANALYTICS
-- ============================================

-- Friend adjacency flags summary
CREATE OR REPLACE VIEW v_cheat_flags_summary AS
SELECT
    e.exam_code,
    e.name AS exam_name,
    e.exam_date,
    cdf.flag_type,
    cdf.severity,
    COUNT(*) AS flag_count,
    SUM(CASE WHEN cdf.reviewed THEN 1 ELSE 0 END) AS reviewed_count,
    SUM(CASE WHEN NOT cdf.reviewed THEN 1 ELSE 0 END) AS pending_count
FROM cheat_detection_flags cdf
JOIN exams e ON e.id = cdf.exam_id
GROUP BY e.exam_code, e.name, e.exam_date, cdf.flag_type, cdf.severity
ORDER BY e.exam_date DESC, cdf.severity DESC;

-- Detailed friend adjacency report
CREATE OR REPLACE VIEW v_friend_adjacency_details AS
SELECT
    cdf.id AS flag_id,
    e.exam_code,
    e.exam_date,
    s1.student_id AS student1_code,
    s1.name AS student1_name,
    s2.student_id AS student2_code,
    s2.name AS student2_name,
    r.room_name,
    cdf.details->>'seat1' AS seat1_info,
    cdf.details->>'seat2' AS seat2_info,
    cdf.severity,
    cdf.reviewed,
    cdf.resolution_notes,
    cdf.created_at
FROM cheat_detection_flags cdf
JOIN exams e ON e.id = cdf.exam_id
JOIN students s1 ON s1.id = cdf.student1_id
JOIN students s2 ON s2.id = cdf.student2_id
LEFT JOIN rooms r ON r.id = (cdf.details->>'room_id')::INTEGER
WHERE cdf.flag_type = 'friend_adjacent'
ORDER BY cdf.created_at DESC;

-- Student relationship network
CREATE OR REPLACE VIEW v_relationship_network AS
SELECT
    sr.id AS relationship_id,
    s1.student_id AS student1_code,
    s1.name AS student1_name,
    d1.name AS student1_dept,
    s2.student_id AS student2_code,
    s2.name AS student2_name,
    d2.name AS student2_dept,
    sr.relationship_type,
    sr.notes,
    u.username AS reported_by,
    sr.created_at
FROM student_relationships sr
JOIN students s1 ON s1.id = sr.student1_id
JOIN students s2 ON s2.id = sr.student2_id
LEFT JOIN departments d1 ON d1.id = s1.department_id
LEFT JOIN departments d2 ON d2.id = s2.department_id
LEFT JOIN users u ON u.id = sr.reported_by
WHERE sr.is_active = TRUE
ORDER BY sr.created_at DESC;

-- ============================================
-- AUDIT LOG VIEWS
-- ============================================

-- Recent audit activity
CREATE OR REPLACE VIEW v_recent_audit_activity AS
SELECT
    al.id,
    al.username,
    al.action,
    al.table_name,
    al.record_id,
    al.created_at,
    CASE
        WHEN al.action = 'LOGIN' THEN 'User logged in'
        WHEN al.action = 'LOGOUT' THEN 'User logged out'
        WHEN al.action = 'CREATE' THEN 'Created ' || al.table_name || ' #' || al.record_id
        WHEN al.action = 'UPDATE' THEN 'Updated ' || al.table_name || ' #' || al.record_id
        WHEN al.action = 'DELETE' THEN 'Deleted ' || al.table_name || ' #' || al.record_id
        WHEN al.action = 'SEATING_GENERATED' THEN 'Generated seating'
        WHEN al.action = 'EXPORT' THEN 'Exported data'
        ELSE al.action::TEXT
    END AS activity_description
FROM audit_logs al
ORDER BY al.created_at DESC
LIMIT 100;

-- Audit activity by user
CREATE OR REPLACE VIEW v_audit_by_user AS
SELECT
    COALESCE(al.username, 'system') AS username,
    al.action,
    COUNT(*) AS action_count,
    MAX(al.created_at) AS last_action
FROM audit_logs al
WHERE al.created_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY al.username, al.action
ORDER BY al.username, action_count DESC;

-- Daily audit summary
CREATE OR REPLACE VIEW v_audit_daily_summary AS
SELECT
    DATE(created_at) AS activity_date,
    action,
    COUNT(*) AS count
FROM audit_logs
WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY DATE(created_at), action
ORDER BY activity_date DESC, count DESC;

-- ============================================
-- INVIGILATOR VIEWS
-- ============================================

-- Invigilator assignments
CREATE OR REPLACE VIEW v_invigilator_assignments AS
SELECT
    u.id AS user_id,
    u.username,
    u.email,
    r.room_name,
    r.building,
    r.capacity,
    i.is_primary,
    i.assigned_at
FROM invigilators i
JOIN users u ON u.id = i.user_id
JOIN rooms r ON r.id = i.room_id
WHERE u.is_active = TRUE AND r.is_active = TRUE
ORDER BY r.room_name, i.is_primary DESC;

-- ============================================
-- MATERIALIZED VIEWS (for heavy analytics)
-- ============================================

-- Monthly exam statistics (refresh periodically)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_monthly_exam_stats AS
SELECT
    DATE_TRUNC('month', e.exam_date) AS month,
    COUNT(DISTINCT e.id) AS total_exams,
    SUM(e.total_students) AS total_students,
    COUNT(DISTINCT sa.room_id) AS rooms_used,
    ROUND(AVG(e.total_students), 1) AS avg_students_per_exam
FROM exams e
LEFT JOIN seating_assignments sa ON sa.exam_id = e.id
WHERE e.is_active = TRUE
GROUP BY DATE_TRUNC('month', e.exam_date)
ORDER BY month DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_monthly_stats_month ON mv_monthly_exam_stats(month);

-- Department exam load (refresh daily)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_department_exam_load AS
SELECT
    d.id AS department_id,
    d.name AS department_name,
    DATE_TRUNC('month', e.exam_date) AS month,
    COUNT(DISTINCT e.id) AS exam_count,
    COUNT(DISTINCT ee.student_id) AS students_examined
FROM departments d
LEFT JOIN exams e ON e.department_id = d.id AND e.is_active = TRUE
LEFT JOIN exam_enrollments ee ON ee.exam_id = e.id
GROUP BY d.id, d.name, DATE_TRUNC('month', e.exam_date)
ORDER BY d.name, month DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_dept_load ON mv_department_exam_load(department_id, month);

-- ============================================
-- REFRESH FUNCTION FOR MATERIALIZED VIEWS
-- ============================================

CREATE OR REPLACE FUNCTION refresh_analytics_views()
RETURNS VOID AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_exam_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_department_exam_load;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- DOCUMENTATION
-- ============================================

COMMENT ON VIEW v_system_stats IS 'Overall system statistics for dashboard';
COMMENT ON VIEW v_todays_exams IS 'Summary of exams scheduled for today';
COMMENT ON VIEW v_exam_seating_summary IS 'Exam seating completion status with room details';
COMMENT ON VIEW v_room_utilization IS 'Room usage statistics across all exams';
COMMENT ON VIEW v_student_exam_schedule IS 'Complete exam schedule for each student with seating';
COMMENT ON VIEW v_cheat_flags_summary IS 'Summary of cheat detection flags by exam';
COMMENT ON VIEW v_friend_adjacency_details IS 'Detailed view of friend adjacency violations';
COMMENT ON VIEW v_relationship_network IS 'All active student relationships for admin review';
COMMENT ON VIEW v_recent_audit_activity IS 'Last 100 audit log entries with descriptions';
COMMENT ON MATERIALIZED VIEW mv_monthly_exam_stats IS 'Monthly aggregated exam statistics - refresh with refresh_analytics_views()';
