-- PostgreSQL Schema for Exam Seating System
-- Run this file to create all tables: psql -d exam_seating -f schema.sql

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Drop existing types if recreating
DROP TYPE IF EXISTS user_role CASCADE;
DROP TYPE IF EXISTS exam_time_slot CASCADE;
DROP TYPE IF EXISTS relationship_type CASCADE;
DROP TYPE IF EXISTS audit_action CASCADE;

-- Enum types
CREATE TYPE user_role AS ENUM ('admin', 'teacher', 'student');
CREATE TYPE exam_time_slot AS ENUM ('Morning', 'Afternoon', 'Evening');
CREATE TYPE relationship_type AS ENUM ('friend', 'relative', 'same_hostel', 'same_room');
CREATE TYPE audit_action AS ENUM ('CREATE', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT', 'SEATING_GENERATED', 'EXPORT');

-- ============================================
-- CORE TABLES
-- ============================================

-- Departments table
CREATE TABLE IF NOT EXISTS departments (
    id SERIAL PRIMARY KEY,
    code VARCHAR(10) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Students table (migrated from CSV)
CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    student_id VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    branch VARCHAR(50),
    section VARCHAR(10),
    year INTEGER CHECK (year BETWEEN 1 AND 6),
    semester INTEGER CHECK (semester BETWEEN 1 AND 12),
    batch VARCHAR(20),
    email VARCHAR(200),
    phone VARCHAR(20),
    photo_path VARCHAR(500),
    gender CHAR(1) CHECK (gender IN ('M', 'F', 'O')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_students_department ON students(department_id);
CREATE INDEX IF NOT EXISTS idx_students_year ON students(year);
CREATE INDEX IF NOT EXISTS idx_students_student_id ON students(student_id);
CREATE INDEX IF NOT EXISTS idx_students_branch ON students(branch);
CREATE INDEX IF NOT EXISTS idx_students_section ON students(section);
CREATE INDEX IF NOT EXISTS idx_students_batch ON students(batch);

-- Section-Exam Assignments (for bulk enrollment by section)
CREATE TABLE IF NOT EXISTS section_exam_assignments (
    id SERIAL PRIMARY KEY,
    department_code VARCHAR(10) NOT NULL,
    branch VARCHAR(50) NOT NULL,
    section VARCHAR(10) NOT NULL,
    year INTEGER,
    semester INTEGER,
    exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(department_code, branch, section, exam_id)
);

CREATE INDEX IF NOT EXISTS idx_section_exam_dept ON section_exam_assignments(department_code);
CREATE INDEX IF NOT EXISTS idx_section_exam_branch ON section_exam_assignments(branch);
CREATE INDEX IF NOT EXISTS idx_section_exam_section ON section_exam_assignments(section);
CREATE INDEX IF NOT EXISTS idx_section_exam_exam ON section_exam_assignments(exam_id);

-- Rooms table (enhanced from room_configs)
CREATE TABLE IF NOT EXISTS rooms (
    id SERIAL PRIMARY KEY,
    room_name VARCHAR(50) NOT NULL UNIQUE,
    building VARCHAR(100),
    floor INTEGER,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    max_subjects INTEGER DEFAULT 15,
    max_branches INTEGER DEFAULT 5,
    allowed_years INTEGER[] DEFAULT ARRAY[1,2,3,4],
    allowed_branches TEXT[],
    layout_columns INTEGER DEFAULT 6 CHECK (layout_columns > 0),
    layout_rows INTEGER DEFAULT 5 CHECK (layout_rows > 0),
    has_ac BOOLEAN DEFAULT FALSE,
    has_projector BOOLEAN DEFAULT FALSE,
    has_cctv BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rooms_capacity ON rooms(capacity);
CREATE INDEX IF NOT EXISTS idx_rooms_building ON rooms(building);

-- Users table (enhanced)
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(200),
    password_hash VARCHAR(255) NOT NULL,
    role user_role NOT NULL,
    totp_secret VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMP,
    failed_login_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMP,
    student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_student ON users(student_id);

-- Invigilators table (teacher-room assignments)
CREATE TABLE IF NOT EXISTS invigilators (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
    is_primary BOOLEAN DEFAULT FALSE,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, room_id)
);

CREATE INDEX IF NOT EXISTS idx_invigilators_user ON invigilators(user_id);
CREATE INDEX IF NOT EXISTS idx_invigilators_room ON invigilators(room_id);

-- ============================================
-- EXAM TABLES
-- ============================================

-- Exams table (exam schedule master)
CREATE TABLE IF NOT EXISTS exams (
    id SERIAL PRIMARY KEY,
    exam_code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    subject VARCHAR(100) NOT NULL,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    exam_date DATE NOT NULL,
    exam_time exam_time_slot NOT NULL,
    duration_minutes INTEGER DEFAULT 180 CHECK (duration_minutes > 0),
    total_students INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_exams_date ON exams(exam_date);
CREATE INDEX IF NOT EXISTS idx_exams_department ON exams(department_id);
CREATE INDEX IF NOT EXISTS idx_exams_date_time ON exams(exam_date, exam_time);
CREATE INDEX IF NOT EXISTS idx_exams_subject ON exams(subject);

-- Student-Exam enrollment (many-to-many)
CREATE TABLE IF NOT EXISTS exam_enrollments (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(student_id, exam_id)
);

CREATE INDEX IF NOT EXISTS idx_enrollments_student ON exam_enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_exam ON exam_enrollments(exam_id);

-- ============================================
-- SEATING TABLES
-- ============================================

-- Seating assignments (persisted - replaces session storage)
CREATE TABLE IF NOT EXISTS seating_assignments (
    id SERIAL PRIMARY KEY,
    exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    seat_number INTEGER NOT NULL CHECK (seat_number > 0),
    seat_x INTEGER NOT NULL CHECK (seat_x >= 0),
    seat_y INTEGER NOT NULL CHECK (seat_y >= 0),
    color_group INTEGER,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    is_manual_override BOOLEAN DEFAULT FALSE,
    override_reason TEXT,
    UNIQUE(exam_id, student_id),
    UNIQUE(exam_id, room_id, seat_number)
);

CREATE INDEX IF NOT EXISTS idx_seating_exam ON seating_assignments(exam_id);
CREATE INDEX IF NOT EXISTS idx_seating_room ON seating_assignments(room_id);
CREATE INDEX IF NOT EXISTS idx_seating_student ON seating_assignments(student_id);
CREATE INDEX IF NOT EXISTS idx_seating_position ON seating_assignments(room_id, seat_x, seat_y);

-- ============================================
-- CHEAT PREVENTION TABLES
-- ============================================

-- Student relationships (friend pairs for cheat prevention)
CREATE TABLE IF NOT EXISTS student_relationships (
    id SERIAL PRIMARY KEY,
    student1_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    student2_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    relationship_type relationship_type DEFAULT 'friend',
    reported_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (student1_id < student2_id),
    UNIQUE(student1_id, student2_id)
);

CREATE INDEX IF NOT EXISTS idx_relationships_student1 ON student_relationships(student1_id);
CREATE INDEX IF NOT EXISTS idx_relationships_student2 ON student_relationships(student2_id);
CREATE INDEX IF NOT EXISTS idx_relationships_active ON student_relationships(is_active) WHERE is_active = TRUE;

-- Cheat detection flags (for analysis and future result-based detection)
CREATE TABLE IF NOT EXISTS cheat_detection_flags (
    id SERIAL PRIMARY KEY,
    exam_id INTEGER REFERENCES exams(id) ON DELETE CASCADE,
    student1_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    student2_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    flag_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) DEFAULT 'low' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    details JSONB,
    reviewed BOOLEAN DEFAULT FALSE,
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMP,
    resolution_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cheat_flags_exam ON cheat_detection_flags(exam_id);
CREATE INDEX IF NOT EXISTS idx_cheat_flags_severity ON cheat_detection_flags(severity);
CREATE INDEX IF NOT EXISTS idx_cheat_flags_reviewed ON cheat_detection_flags(reviewed);
CREATE INDEX IF NOT EXISTS idx_cheat_flags_type ON cheat_detection_flags(flag_type);

-- ============================================
-- AUDIT LOGGING TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username VARCHAR(100),
    action audit_action NOT NULL,
    table_name VARCHAR(100),
    record_id INTEGER,
    old_values JSONB,
    new_values JSONB,
    ip_address INET,
    user_agent TEXT,
    session_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_table ON audit_logs(table_name);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_record ON audit_logs(table_name, record_id);

-- Partition audit logs by month for better performance (optional)
-- CREATE INDEX IF NOT EXISTS idx_audit_created_month ON audit_logs(date_trunc('month', created_at));

-- ============================================
-- SYSTEM CONFIGURATION
-- ============================================

CREATE TABLE IF NOT EXISTS system_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

-- ============================================
-- SEATING HISTORY (for rollback capability)
-- ============================================

CREATE TABLE IF NOT EXISTS seating_history (
    id SERIAL PRIMARY KEY,
    exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    generated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    total_students INTEGER,
    rooms_used INTEGER,
    algorithm_used VARCHAR(50),
    generation_time_ms INTEGER,
    snapshot JSONB NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    notes TEXT,
    UNIQUE(exam_id, version)
);

CREATE INDEX IF NOT EXISTS idx_seating_history_exam ON seating_history(exam_id);
CREATE INDEX IF NOT EXISTS idx_seating_history_active ON seating_history(is_active);

-- ============================================
-- INSERT DEFAULT DATA
-- ============================================

-- Default departments
INSERT INTO departments (code, name) VALUES
    ('CS', 'Computer Science'),
    ('EC', 'Electronics & Communication'),
    ('ME', 'Mechanical Engineering'),
    ('CE', 'Civil Engineering'),
    ('EE', 'Electrical Engineering')
ON CONFLICT (code) DO NOTHING;

-- Default system config
INSERT INTO system_config (key, value, description) VALUES
    ('seating_algorithm', 'dsatur_ffd', 'Algorithm for seating: dsatur_ffd or backtracking'),
    ('friend_separation_enabled', 'true', 'Enable friend separation in seating'),
    ('section_separation_enabled', 'true', 'Separate students from same section'),
    ('audit_retention_days', '90', 'Days to keep audit logs'),
    ('max_login_attempts', '5', 'Max failed login attempts before lockout'),
    ('lockout_duration_minutes', '30', 'Account lockout duration in minutes')
ON CONFLICT (key) DO NOTHING;

-- ============================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================

COMMENT ON TABLE students IS 'Student master data - migrated from CSV';
COMMENT ON TABLE rooms IS 'Examination room configurations with layout and constraints';
COMMENT ON TABLE exams IS 'Exam schedule master - defines when and what exams occur';
COMMENT ON TABLE seating_assignments IS 'Persistent seat assignments - replaces volatile session storage';
COMMENT ON TABLE student_relationships IS 'Friend/relative pairs for cheat prevention - students should not sit adjacent';
COMMENT ON TABLE cheat_detection_flags IS 'Flags for suspicious patterns - hooks for future result analysis';
COMMENT ON TABLE audit_logs IS 'Complete audit trail of all system changes';
COMMENT ON TABLE seating_history IS 'Versioned snapshots of seating plans for rollback';

COMMENT ON COLUMN student_relationships.student1_id IS 'Always the smaller student ID (enforced by CHECK)';
COMMENT ON COLUMN seating_assignments.color_group IS 'Graph coloring group from DSatur algorithm';
COMMENT ON COLUMN cheat_detection_flags.details IS 'JSON details: seat positions, distances, similarity scores';
