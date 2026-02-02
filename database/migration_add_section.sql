-- Migration: Add section column and section_exam_assignments table
-- Run this: psql -d exam_seating -f database/migration_add_section.sql

-- Add section column to students table if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'students' AND column_name = 'section'
    ) THEN
        ALTER TABLE students ADD COLUMN section VARCHAR(10);
        CREATE INDEX IF NOT EXISTS idx_students_section ON students(section);
        RAISE NOTICE 'Added section column to students table';
    ELSE
        RAISE NOTICE 'Section column already exists in students table';
    END IF;
END $$;

-- Create section_exam_assignments table if it doesn't exist
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

-- Verify the changes
SELECT 'Migration completed successfully!' as status;
