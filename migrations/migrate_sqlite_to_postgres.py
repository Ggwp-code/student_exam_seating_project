#!/usr/bin/env python3
"""
Migration script: SQLite to PostgreSQL for Exam Seating System

This script migrates existing data from SQLite (system.db) and CSV files
to the new PostgreSQL database schema.

Usage:
    1. Ensure PostgreSQL is running and database is created:
       createdb exam_seating

    2. Run the schema and triggers:
       psql -d exam_seating -f database/schema.sql
       psql -d exam_seating -f database/triggers.sql
       psql -d exam_seating -f database/procedures.sql
       psql -d exam_seating -f database/views.sql

    3. Set the DATABASE_URL environment variable:
       export DATABASE_URL="postgresql://user:password@localhost/exam_seating"

    4. Run this migration:
       python migrations/migrate_sqlite_to_postgres.py

Author: Migration for Exam Seating System
"""

import os
import sys
import sqlite3
import pandas as pd
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configuration
SQLITE_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'system.db')
CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'students.csv')
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://localhost/exam_seating')


def get_postgres_connection():
    """Get PostgreSQL connection using psycopg2."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Cannot connect to PostgreSQL: {e}")
        sys.exit(1)


def get_sqlite_connection():
    """Get SQLite connection."""
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"WARNING: SQLite database not found at {SQLITE_DB_PATH}")
        return None
    return sqlite3.connect(SQLITE_DB_PATH)


def migrate_users(sqlite_conn, pg_conn):
    """Migrate users from SQLite to PostgreSQL."""
    print("\n--- Migrating Users ---")

    if not sqlite_conn:
        print("Skipping: No SQLite database")
        return 0

    cursor = sqlite_conn.cursor()

    # Check which columns exist in the SQLite users table
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]

    # Build query based on available columns
    if 'is_active' in columns:
        cursor.execute("SELECT id, username, password_hash, role, totp_secret, is_active FROM users")
    else:
        cursor.execute("SELECT id, username, password_hash, role, totp_secret FROM users")

    users = cursor.fetchall()

    if not users:
        print("No users to migrate")
        return 0

    pg_cursor = pg_conn.cursor()
    migrated = 0
    has_is_active = 'is_active' in columns

    for user in users:
        if has_is_active:
            user_id, username, password_hash, role, totp_secret, is_active = user
        else:
            user_id, username, password_hash, role, totp_secret = user
            is_active = 1  # Default to active
        try:
            pg_cursor.execute("""
                INSERT INTO users (username, password_hash, role, totp_secret, is_active)
                VALUES (%s, %s, %s::user_role, %s, %s)
                ON CONFLICT (username) DO UPDATE SET
                    password_hash = EXCLUDED.password_hash,
                    role = EXCLUDED.role,
                    totp_secret = EXCLUDED.totp_secret,
                    is_active = EXCLUDED.is_active
                RETURNING id
            """, (username, password_hash, role, totp_secret, bool(is_active)))
            pg_conn.commit()
            migrated += 1
            print(f"  Migrated user: {username} ({role})")
        except Exception as e:
            print(f"  ERROR migrating user {username}: {e}")
            pg_conn.rollback()

    print(f"Migrated {migrated} users")
    return migrated


def migrate_rooms(sqlite_conn, pg_conn):
    """Migrate room configurations from SQLite to PostgreSQL."""
    print("\n--- Migrating Rooms ---")

    if not sqlite_conn:
        print("Skipping: No SQLite database")
        return 0

    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT room_name, capacity, max_subjects, max_branches,
               allowed_years, allowed_branches, layout_columns, layout_rows
        FROM room_configs
    """)
    rooms = cursor.fetchall()

    if not rooms:
        print("No rooms to migrate")
        return 0

    pg_cursor = pg_conn.cursor()
    migrated = 0

    for room in rooms:
        room_name, capacity, max_subjects, max_branches, allowed_years, allowed_branches, cols, rows = room
        try:
            # Parse allowed_years from comma-separated string to array
            years_array = None
            if allowed_years:
                years_array = [int(y.strip()) for y in allowed_years.split(',') if y.strip()]

            # Parse allowed_branches from comma-separated string to array
            branches_array = None
            if allowed_branches:
                branches_array = [b.strip() for b in allowed_branches.split(',') if b.strip()]

            pg_cursor.execute("""
                INSERT INTO rooms (room_name, capacity, max_subjects, max_branches,
                                   allowed_years, allowed_branches, layout_columns, layout_rows)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (room_name) DO UPDATE SET
                    capacity = EXCLUDED.capacity,
                    max_subjects = EXCLUDED.max_subjects,
                    max_branches = EXCLUDED.max_branches,
                    allowed_years = EXCLUDED.allowed_years,
                    allowed_branches = EXCLUDED.allowed_branches,
                    layout_columns = EXCLUDED.layout_columns,
                    layout_rows = EXCLUDED.layout_rows
                RETURNING id
            """, (room_name, capacity, max_subjects, max_branches,
                  years_array, branches_array, cols or 6, rows or 5))
            pg_conn.commit()
            migrated += 1
            print(f"  Migrated room: {room_name} (capacity: {capacity})")
        except Exception as e:
            print(f"  ERROR migrating room {room_name}: {e}")
            pg_conn.rollback()

    print(f"Migrated {migrated} rooms")
    return migrated


def migrate_students_from_csv(pg_conn):
    """Migrate students from CSV file to PostgreSQL."""
    print("\n--- Migrating Students from CSV ---")

    if not os.path.exists(CSV_PATH):
        print(f"Skipping: CSV file not found at {CSV_PATH}")
        return 0

    try:
        df = pd.read_csv(CSV_PATH)
    except Exception as e:
        print(f"ERROR reading CSV: {e}")
        return 0

    if df.empty:
        print("No students in CSV")
        return 0

    pg_cursor = pg_conn.cursor()
    migrated = 0
    department_cache = {}

    # Expected columns (flexible mapping)
    id_col = 'StudentID' if 'StudentID' in df.columns else 'student_id'
    name_col = 'Name' if 'Name' in df.columns else 'name'
    dept_col = 'Department' if 'Department' in df.columns else 'department'

    for _, row in df.iterrows():
        try:
            student_id = str(row.get(id_col, ''))
            name = row.get(name_col, f'Student-{student_id}')
            department = row.get(dept_col, 'Unknown')
            branch = row.get('Branch', row.get('Batch', 'Unknown'))
            year = row.get('Year', None)
            semester = row.get('Semester', None)
            batch = row.get('Batch', None)
            email = row.get('Email', None)
            phone = row.get('Phone', None)
            photo = row.get('PhotoPath', row.get('Photo', None))
            gender = row.get('Gender', None)

            if not student_id:
                continue

            # Get or create department
            if department not in department_cache:
                dept_code = department[:10] if department else 'UNK'
                pg_cursor.execute("""
                    INSERT INTO departments (code, name)
                    VALUES (%s, %s)
                    ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                """, (dept_code, department))
                dept_result = pg_cursor.fetchone()
                department_cache[department] = dept_result['id']
                pg_conn.commit()

            dept_id = department_cache.get(department)

            # Insert student
            pg_cursor.execute("""
                INSERT INTO students (student_id, name, department_id, branch, year,
                                      semester, batch, email, phone, photo_path, gender)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (student_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    department_id = EXCLUDED.department_id,
                    branch = EXCLUDED.branch,
                    year = EXCLUDED.year,
                    semester = EXCLUDED.semester,
                    batch = EXCLUDED.batch,
                    email = EXCLUDED.email,
                    phone = EXCLUDED.phone,
                    photo_path = EXCLUDED.photo_path,
                    gender = EXCLUDED.gender
                RETURNING id
            """, (student_id, name, dept_id, branch,
                  int(year) if year and pd.notna(year) else None,
                  int(semester) if semester and pd.notna(semester) else None,
                  str(batch) if batch and pd.notna(batch) else None,
                  email if email and pd.notna(email) else None,
                  str(phone) if phone and pd.notna(phone) else None,
                  photo if photo and pd.notna(photo) else None,
                  str(gender)[0].upper() if gender and pd.notna(gender) else None))
            pg_conn.commit()
            migrated += 1

        except Exception as e:
            print(f"  ERROR migrating student {row.get(id_col, 'Unknown')}: {e}")
            pg_conn.rollback()

    print(f"Migrated {migrated} students")
    return migrated


def migrate_teacher_rooms(sqlite_conn, pg_conn):
    """Migrate teacher-room assignments to invigilators table."""
    print("\n--- Migrating Teacher-Room Assignments ---")

    if not sqlite_conn:
        print("Skipping: No SQLite database")
        return 0

    cursor = sqlite_conn.cursor()
    try:
        cursor.execute("SELECT teacher_username, room_name FROM teacher_rooms")
        assignments = cursor.fetchall()
    except sqlite3.OperationalError:
        print("No teacher_rooms table found")
        return 0

    if not assignments:
        print("No teacher-room assignments to migrate")
        return 0

    pg_cursor = pg_conn.cursor()
    migrated = 0

    for teacher_username, room_name in assignments:
        try:
            # Find user and room IDs
            pg_cursor.execute("SELECT id FROM users WHERE username = %s", (teacher_username,))
            user_result = pg_cursor.fetchone()
            if not user_result:
                print(f"  Skipping: User {teacher_username} not found")
                continue

            pg_cursor.execute("SELECT id FROM rooms WHERE room_name = %s", (room_name,))
            room_result = pg_cursor.fetchone()
            if not room_result:
                print(f"  Skipping: Room {room_name} not found")
                continue

            pg_cursor.execute("""
                INSERT INTO invigilators (user_id, room_id, is_primary)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (user_id, room_id) DO NOTHING
            """, (user_result['id'], room_result['id']))
            pg_conn.commit()
            migrated += 1
            print(f"  Assigned {teacher_username} to {room_name}")

        except Exception as e:
            print(f"  ERROR assigning {teacher_username} to {room_name}: {e}")
            pg_conn.rollback()

    print(f"Migrated {migrated} teacher-room assignments")
    return migrated


def migrate_system_config(sqlite_conn, pg_conn):
    """Migrate system configuration from SQLite to PostgreSQL."""
    print("\n--- Migrating System Configuration ---")

    if not sqlite_conn:
        print("Skipping: No SQLite database")
        return 0

    cursor = sqlite_conn.cursor()
    try:
        cursor.execute("SELECT key, value FROM system_config")
        configs = cursor.fetchall()
    except sqlite3.OperationalError:
        print("No system_config table found")
        return 0

    if not configs:
        print("No configurations to migrate")
        return 0

    pg_cursor = pg_conn.cursor()
    migrated = 0

    for key, value in configs:
        try:
            # Skip shared_totp_secret as it will be regenerated
            if key == 'shared_totp_secret':
                continue

            pg_cursor.execute("""
                INSERT INTO system_config (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, (key, value))
            pg_conn.commit()
            migrated += 1
            print(f"  Migrated config: {key}")

        except Exception as e:
            print(f"  ERROR migrating config {key}: {e}")
            pg_conn.rollback()

    print(f"Migrated {migrated} configuration items")
    return migrated


def verify_migration(pg_conn):
    """Verify migration by counting records."""
    print("\n--- Verification ---")

    pg_cursor = pg_conn.cursor()

    tables = ['users', 'rooms', 'students', 'departments', 'invigilators', 'system_config']

    for table in tables:
        pg_cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
        result = pg_cursor.fetchone()
        print(f"  {table}: {result['count']} records")


def main():
    """Main migration function."""
    print("=" * 60)
    print("Exam Seating System - SQLite to PostgreSQL Migration")
    print("=" * 60)
    print(f"\nSource SQLite: {SQLITE_DB_PATH}")
    print(f"Source CSV: {CSV_PATH}")
    print(f"Target PostgreSQL: {DATABASE_URL}")
    print("\nStarting migration...")

    # Get connections
    sqlite_conn = get_sqlite_connection()
    pg_conn = get_postgres_connection()

    try:
        # Run migrations in order
        migrate_users(sqlite_conn, pg_conn)
        migrate_rooms(sqlite_conn, pg_conn)
        migrate_students_from_csv(pg_conn)
        migrate_teacher_rooms(sqlite_conn, pg_conn)
        migrate_system_config(sqlite_conn, pg_conn)

        # Verify
        verify_migration(pg_conn)

        print("\n" + "=" * 60)
        print("Migration completed successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        if sqlite_conn:
            sqlite_conn.close()
        pg_conn.close()


if __name__ == '__main__':
    main()
