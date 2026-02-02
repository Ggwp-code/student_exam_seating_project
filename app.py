from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, jsonify, flash
import pandas as pd
import os
import qrcode
import pyotp
import qrcode.image.svg
import glob
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from functools import wraps
from io import BytesIO
from types import SimpleNamespace

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32).hex()

# Configure secure session settings
app.config.update(
    SESSION_COOKIE_SECURE=True,    # Only send cookies over HTTPS
    SESSION_COOKIE_HTTPONLY=True,  # Prevent client-side JS access
    SESSION_COOKIE_SAMESITE='Lax',  # CSRF protection
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),  # Session timeout
    SESSION_REFRESH_EACH_REQUEST=True  # Update session timestamp on each request
)

# PostgreSQL Configuration
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost/exam_seating')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize SQLAlchemy with Flask app
from models import db
db.init_app(app)

# Configuration
CSV_PATH = os.path.abspath('data/students.csv')
UPLOAD_FOLDER = os.path.abspath('static/uploads')
QR_FOLDER = os.path.abspath('static/qrcodes')
DB_PATH = os.path.abspath('data/system.db')

# Ensure directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)
os.makedirs('data', exist_ok=True)

def get_or_create_shared_totp_secret():
    """Get or create a shared TOTP secret for admin and teachers"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if there's already a shared secret in the system_config table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    
    cursor.execute('SELECT value FROM system_config WHERE key = ?', ('shared_totp_secret',))
    result = cursor.fetchone()
    
    if result:
        shared_secret = result[0]
    else:
        # Generate new shared secret
        shared_secret = pyotp.random_base32()
        cursor.execute('INSERT INTO system_config (key, value) VALUES (?, ?)', 
                      ('shared_totp_secret', shared_secret))
        conn.commit()
    
    conn.close()
    return shared_secret

def init_database():
    """Initialize SQLite database for system data"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            totp_secret TEXT,
            is_active INTEGER DEFAULT 1
        )
    ''')

    # Migration: Add email column if it doesn't exist
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN email TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS room_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_name TEXT NOT NULL UNIQUE,
            capacity INTEGER NOT NULL,
            max_subjects INTEGER,
            max_branches INTEGER,
            max_departments INTEGER DEFAULT 2,
            max_years INTEGER DEFAULT 2,
            allowed_years TEXT,
            allowed_branches TEXT,
            layout_columns INTEGER DEFAULT 6,
            layout_rows INTEGER DEFAULT 5
        )
    ''')

    # Migration: Add new columns if they don't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE room_configs ADD COLUMN max_departments INTEGER DEFAULT 2')
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        cursor.execute('ALTER TABLE room_configs ADD COLUMN max_years INTEGER DEFAULT 2')
    except sqlite3.OperationalError:
        pass  # Column already exists
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS teacher_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_username TEXT NOT NULL,
            room_name TEXT NOT NULL,
            UNIQUE (teacher_username)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS teacher_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_username TEXT NOT NULL,
            room_name TEXT NOT NULL,
            exam_date TEXT NOT NULL,
            exam_time TEXT NOT NULL,
            UNIQUE (teacher_username, exam_date, exam_time),
            UNIQUE (room_name, exam_date, exam_time)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')

    # Teacher preferences table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS teacher_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_username TEXT NOT NULL UNIQUE,
            preferred_times TEXT DEFAULT 'Morning,Afternoon,Evening',
            max_sessions_per_day INTEGER DEFAULT 2,
            unavailable_dates TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Swap requests table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS swap_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_username TEXT NOT NULL,
            target_username TEXT NOT NULL,
            requester_schedule_id INTEGER NOT NULL,
            target_schedule_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            reason TEXT,
            admin_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            reviewed_by TEXT,
            FOREIGN KEY (requester_schedule_id) REFERENCES teacher_schedule(id),
            FOREIGN KEY (target_schedule_id) REFERENCES teacher_schedule(id)
        )
    ''')

    # Notification queue table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_username TEXT NOT NULL,
            recipient_email TEXT,
            notification_type TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            scheduled_for TIMESTAMP,
            sent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            error_message TEXT
        )
    ''')

    # Get shared TOTP secret
    shared_secret = get_or_create_shared_totp_secret()
    
    # Add an admin user if not exists
    cursor.execute('SELECT * FROM users WHERE username = ?', ('admin',))
    if not cursor.fetchone():
        hashed_password = generate_password_hash('adminpass')
        cursor.execute('''
            INSERT INTO users (username, password_hash, role, totp_secret)
            VALUES (?, ?, ?, ?)
        ''', ('admin', hashed_password, 'admin', shared_secret))
    else:
        # Update existing admin to use shared secret
        cursor.execute('''
            UPDATE users SET totp_secret = ? WHERE username = ? AND role = ?
        ''', (shared_secret, 'admin', 'admin'))
    
    # Add default teacher accounts if they don't exist
    # Need at least 5 teachers for 3 rooms × 3 time slots with no-consecutive constraint
    default_teachers = [
        ('teacher1', 'teacher1@school.edu', 'teacher123'),
        ('teacher2', 'teacher2@school.edu', 'teacher123'),
        ('teacher3', 'teacher3@school.edu', 'teacher123'),
        ('teacher4', 'teacher4@school.edu', 'teacher123'),
        ('teacher5', 'teacher5@school.edu', 'teacher123'),
        ('teacher6', 'teacher6@school.edu', 'teacher123'),
    ]

    for teacher_data in default_teachers:
        cursor.execute('SELECT * FROM users WHERE username = ?', (teacher_data[0],))
        if not cursor.fetchone():
            hashed_password = generate_password_hash(teacher_data[2])
            cursor.execute('''
                INSERT INTO users (username, email, password_hash, role, totp_secret)
                VALUES (?, ?, ?, ?, ?)
            ''', (teacher_data[0], teacher_data[1], hashed_password, 'teacher', shared_secret))
            # Also create default preferences for this teacher
            cursor.execute('''
                INSERT OR IGNORE INTO teacher_preferences (teacher_username, preferred_times, max_sessions_per_day, unavailable_dates)
                VALUES (?, ?, ?, ?)
            ''', (teacher_data[0], 'Morning,Afternoon,Evening', 2, ''))

    # Add default room configurations if they don't exist
    default_rooms = [
        ('Room-A', 30, 15, 5, '2,3', 'CS,EC,ME', 6, 5),
        ('Room-B', 40, 15, 5, '2,3', 'CS,EC,ME', 8, 5),
        ('Room-C', 25, 10, 3, '2,3,4', 'CS,EC', 5, 5)
    ]
    
    for room_data in default_rooms:
        cursor.execute('SELECT * FROM room_configs WHERE room_name = ?', (room_data[0],))
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO room_configs 
                (room_name, capacity, max_subjects, max_branches, allowed_years, allowed_branches, layout_columns, layout_rows)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', room_data)
    
    conn.commit()
    conn.close()

def run_postgres_migrations():
    """Run PostgreSQL migrations automatically on startup"""
    try:
        from sqlalchemy import text
        with app.app_context():
            # Check if section column exists in students table
            result = db.session.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'students' AND column_name = 'section'
                )
            """))
            section_exists = result.scalar()

            if not section_exists:
                print("[Migration] Adding 'section' column to students table...")
                db.session.execute(text("ALTER TABLE students ADD COLUMN section VARCHAR(10)"))
                db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_students_section ON students(section)"))
                db.session.commit()
                print("[Migration] Section column added successfully!")

            # Create section_exam_assignments table if not exists
            db.session.execute(text("""
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
                )
            """))
            db.session.commit()

            # Create indexes if not exist
            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_section_exam_dept ON section_exam_assignments(department_code)",
                "CREATE INDEX IF NOT EXISTS idx_section_exam_branch ON section_exam_assignments(branch)",
                "CREATE INDEX IF NOT EXISTS idx_section_exam_section ON section_exam_assignments(section)",
                "CREATE INDEX IF NOT EXISTS idx_section_exam_exam ON section_exam_assignments(exam_id)"
            ]:
                db.session.execute(text(idx_sql))
            db.session.commit()
            print("[Migration] PostgreSQL migrations completed successfully!")

    except Exception as e:
        print(f"[Migration] Warning: {e}")
        # Don't crash - migrations may have already been applied

def generate_seating_visualizations():
    """Generate seating visualizations from main.py logic"""
    try:
        import main
        print("[Visualization] Generating seating layouts...")
        main.main()
        print("[Visualization] Seating layouts generated!")
    except Exception as e:
        print(f"[Visualization] Could not generate layouts: {e}")

def sync_exams_from_csv():
    """Sync exams from CSV to PostgreSQL database and enroll students"""
    try:
        import pandas as pd
        from datetime import datetime
        from models import db, Exam, ExamTimeSlot, Student, ExamEnrollment

        csv_path = 'data/students.csv'
        if not os.path.exists(csv_path):
            print("[Sync] No CSV file found")
            return

        with app.app_context():
            df = pd.read_csv(csv_path)

            # Get unique exams (Subject + Date + Time combinations)
            unique_exams = df.groupby(['Subject', 'ExamDate', 'ExamTime']).size().reset_index(name='student_count')

            created_count = 0
            enrolled_count = 0

            for _, row in unique_exams.iterrows():
                subject = row['Subject']
                exam_date_str = row['ExamDate']
                exam_time = row['ExamTime']

                # Convert date string to Python date object
                exam_date = datetime.strptime(exam_date_str, '%Y-%m-%d').date()

                # Generate exam code (include time slot to make unique)
                time_abbrev = {'Morning': 'AM', 'Afternoon': 'PM', 'Evening': 'EV'}.get(exam_time, 'AM')
                exam_code = f"{subject.upper().replace(' ', '-')[:10]}-{exam_date_str.replace('-', '')}-{time_abbrev}"

                # Map time slot
                time_map = {
                    'Morning': ExamTimeSlot.MORNING,
                    'Afternoon': ExamTimeSlot.AFTERNOON,
                    'Evening': ExamTimeSlot.EVENING
                }
                exam_time_enum = time_map.get(exam_time, ExamTimeSlot.MORNING)

                # Check if exam already exists
                exam = Exam.query.filter_by(exam_code=exam_code).first()
                if not exam:
                    # Create exam
                    exam = Exam(
                        exam_code=exam_code,
                        name=f"{subject} Exam",
                        subject=subject,
                        exam_date=exam_date,
                        exam_time=exam_time_enum,
                        duration_minutes=180,
                        is_active=True
                    )
                    db.session.add(exam)
                    db.session.flush()  # Get the exam ID
                    created_count += 1

                # Get students for this exam from CSV
                exam_students = df[
                    (df['Subject'] == subject) &
                    (df['ExamDate'] == exam_date_str) &
                    (df['ExamTime'] == exam_time)
                ]

                for _, student_row in exam_students.iterrows():
                    student_id_str = str(student_row['StudentID'])

                    # Get or create student
                    student = Student.query.filter_by(student_id=student_id_str).first()
                    if not student:
                        student = Student(
                            student_id=student_id_str,
                            name=student_row.get('Name', 'Unknown'),
                            branch=student_row.get('Branch', ''),
                            section=student_row.get('Section', ''),
                            year=int(student_row.get('Year', 1)) if pd.notna(student_row.get('Year')) else None,
                            semester=int(student_row.get('Semester', 1)) if pd.notna(student_row.get('Semester')) else None
                        )
                        db.session.add(student)
                        db.session.flush()

                    # Check if enrollment exists
                    existing_enrollment = ExamEnrollment.query.filter_by(
                        student_id=student.id,
                        exam_id=exam.id
                    ).first()

                    if not existing_enrollment:
                        enrollment = ExamEnrollment(
                            student_id=student.id,
                            exam_id=exam.id
                        )
                        db.session.add(enrollment)
                        enrolled_count += 1

                # Update total_students count
                exam.total_students = ExamEnrollment.query.filter_by(exam_id=exam.id).count()

            db.session.commit()
            print(f"[Sync] Created {created_count} new exams, enrolled {enrolled_count} students")

    except Exception as e:
        print(f"[Sync] Error syncing exams: {e}")
        import traceback
        traceback.print_exc()

def get_rooms_config_from_db():
    """Get room configurations from database in the format expected by main.py"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT room_name, capacity, max_subjects, max_branches, allowed_years,
               allowed_branches, layout_columns, layout_rows, max_departments, max_years
        FROM room_configs ORDER BY room_name
    ''')
    rooms_data = cursor.fetchall()
    conn.close()

    rooms_config = []
    for row in rooms_data:
        room_config = {
            'room_name': row[0],
            'capacity': row[1],
            'max_subjects': row[2],
            'max_branches': row[3],
            'allowed_years': [int(y) for y in row[4].split(',') if y.strip()] if row[4] else [],
            'allowed_branches': row[5].split(',') if row[5] else [],
            'layout_columns': row[6] or 6,
            'layout_rows': row[7] or 5,
            'max_departments': row[8] if len(row) > 8 and row[8] else 2,
            'max_years': row[9] if len(row) > 9 and row[9] else 2
        }
        rooms_config.append(room_config)

    return rooms_config

# Decorator for login required
def require_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Decorator for admin required
def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or session['role'] != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Decorator for teacher required
def require_teacher(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or session['role'] != 'teacher':
            flash('Teacher access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def load_student_data():
    """Load student data from CSV file."""
    if os.path.exists(CSV_PATH):
        try:
            return pd.read_csv(CSV_PATH)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame({
        'StudentID': ['1001', '1002', '1003', '1004', '1005', '1006', '1007', '1008', '1009', '1010', '1011', '1012'],
        'Name': ['Alice Smith', 'Bob Johnson', 'Charlie Brown', 'Diana Prince', 'Eve Adams', 'Frank White', 'Grace Lee', 'Harry Kim', 'Ivy Green', 'Jack Black', 'Kevin Blue', 'Linda Red'],
        'Department': ['CSE', 'ECE', 'ME', 'CSE', 'ECE', 'ME', 'CSE', 'ECE', 'ME', 'CSE', 'ECE', 'ME'],
        'Branch': ['CS', 'EC', 'ME', 'CS', 'EC', 'ME', 'CS', 'EC', 'ME', 'CS', 'EC', 'ME'],
        'Batch': ['2022', '2022', '2022', '2023', '2023', '2023', '2022', '2022', '2023', '2023', '2022', '2023'],
        'Year': [2, 2, 2, 3, 3, 3, 2, 2, 3, 3, 2, 3],
        'Semester': [4, 4, 4, 6, 6, 6, 4, 4, 6, 6, 4, 6],
        'Subject': ['DSA', 'VLSI', 'Thermodynamics', 'AI', 'DSP', 'Fluid Mech', 'OS', 'Signals', 'Robotics', 'Networks', 'Embedded Sys', 'Compilers'],
        'ExamDate': ['2025-06-01', '2025-06-01', '2025-06-02', '2025-06-02', '2025-06-03', '2025-06-03', '2025-06-01', '2025-06-01', '2025-06-02', '2025-06-02', '2025-06-03', '2025-06-03'],
        'ExamTime': ['Morning', 'Morning', 'Afternoon', 'Afternoon', 'Morning', 'Morning', 'Afternoon', 'Afternoon', 'Morning', 'Morning', 'Afternoon', 'Afternoon'],
        'PhotoPath': [f'/static/uploads/student_{i}.jpg' for i in range(1, 13)],
        'Gender': ['M', 'F', 'M', 'F', 'M', 'F', 'M', 'F', 'M', 'F', 'M', 'F']
    })

# Initialize student data
df_students = load_student_data()

# Import functions from main.py
from main import (
    get_colored_groups, extract_student_metadata, assign_rooms_to_groups,
    assign_seats_in_room, create_index_page, create_simple_html_visualization
)

# Routes
@app.route('/')
def index():
    if 'logged_in' in session:
        if session['role'] == 'student':
            student_info = get_student_by_id(session['username'])
            if not student_info:
                session.clear()
                flash('Student record not found. Please register or contact admin.', 'warning')
                return redirect(url_for('login'))
            return redirect(url_for('student_dashboard', student_id=session['username']))
        elif session['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        elif session['role'] == 'teacher':
            return redirect(url_for('teacher_dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        totp_code = request.form.get('totp')

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ? AND role = ?', (username, role))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):
            # Only require 2FA for admin login
            if role == 'admin':
                if user[4]:  # user[4] is totp_secret
                    totp = pyotp.TOTP(user[4])
                    if not totp.verify(totp_code):
                        flash('Invalid 2FA code.', 'danger')
                        return render_template('enhanced_login.html')
                else:
                    flash('Admin account requires 2FA setup. Please contact support.', 'danger')
                    return render_template('enhanced_login.html')

            # For students, verify they exist in CSV
            if role == 'student':
                student_data = get_student_by_id(username)
                if not student_data:
                    flash('Student ID not found in system records. Please contact admin.', 'danger')
                    return render_template('enhanced_login.html')

            session['logged_in'] = True
            session['username'] = username
            session['role'] = role
            session['user_id'] = user[0]

            flash(f'Logged in as {role}!', 'success')
            if role == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif role == 'teacher':
                return redirect(url_for('teacher_dashboard'))
            elif role == 'student':
                return redirect(url_for('student_dashboard', student_id=username))
        else:
            flash('Invalid username, password, or role.', 'danger')
    return render_template('enhanced_login.html')

@app.route('/visualizations/<path:filename>')
@require_admin
def serve_visualization_file(filename):
    return send_from_directory('visualizations', filename)

@app.route('/seating_dashboard')
@require_admin
def seating_dashboard():
    return redirect(url_for('serve_visualization_file', filename='index.html'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Load available rooms from database
    cursor.execute('SELECT room_name, capacity FROM room_configs ORDER BY room_name')
    available_rooms = [{'room_name': row[0], 'capacity': row[1]} for row in cursor.fetchall()]

    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        role = request.form.get('role', 'student')
        assigned_room = request.form.get('assigned_room')
        student_id = request.form.get('student_id', '')  # Changed from full_name to student_id

        # Validation
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            conn.close()
            return render_template('register.html', available_rooms=available_rooms)

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            conn.close()
            return render_template('register.html', available_rooms=available_rooms)

        # Special validation for students
        if role == 'student':
            if not student_id:
                flash('Student ID is required for student registration.', 'danger')
                conn.close()
                return render_template('register.html', available_rooms=available_rooms)
            
            # Validate Student ID format (only alphanumeric, no spaces)
            if not student_id.replace('_', '').replace('-', '').isalnum():
                flash('Student ID can only contain letters, numbers, hyphens, and underscores (no spaces).', 'danger')
                conn.close()
                return render_template('register.html', available_rooms=available_rooms)
            
            # Check if student exists in CSV by Student ID
            student_data = get_student_by_id(student_id)
            if not student_data:
                flash(f'Student ID "{student_id}" not found in system records. Please contact admin to add your information first.', 'danger')
                conn.close()
                return render_template('register.html', available_rooms=available_rooms)
            
            # Use the Student ID as username (this ensures URL-safe usernames)
            username = str(student_id).strip()
            
            # Check if this student ID is already registered
            cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
            if cursor.fetchone():
                flash(f'Student with ID {username} is already registered. Please login instead.', 'danger')
                conn.close()
                return render_template('register.html', available_rooms=available_rooms)

        if role == 'teacher':
            if not assigned_room:
                flash('Please select a room for the teacher.', 'danger')
                conn.close()
                return render_template('register.html', available_rooms=available_rooms)

            # Check if room is already assigned to another teacher
            cursor.execute('SELECT teacher_username FROM teacher_rooms WHERE room_name = ?', (assigned_room,))
            existing_assignment = cursor.fetchone()
            if existing_assignment:
                flash(f'Room {assigned_room} is already assigned to teacher {existing_assignment[0]}.', 'danger')
                conn.close()
                return render_template('register.html', available_rooms=available_rooms)

        # Check for existing username (for non-students or if username was manually entered)
        if role != 'student':
            cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
            if cursor.fetchone():
                flash('Username already exists.', 'danger')
                conn.close()
                return render_template('register.html', available_rooms=available_rooms)

        hashed_password = generate_password_hash(password)

        try:
            if role == 'teacher':
                # Use shared TOTP secret for teacher
                shared_secret = get_or_create_shared_totp_secret()
                
                # Insert user with shared TOTP
                cursor.execute('''
                    INSERT INTO users (username, password_hash, role, totp_secret)
                    VALUES (?, ?, ?, ?)
                ''', (username, hashed_password, role, shared_secret))
                
                user_id = cursor.lastrowid
                
                # Assign room to teacher
                cursor.execute('''
                    INSERT INTO teacher_rooms (teacher_username, room_name)
                    VALUES (?, ?)
                ''', (username, assigned_room))
                
                conn.commit()
                
                # Generate QR code for 2FA setup using shared secret
                totp_uri = pyotp.utils.build_uri(shared_secret, "SharedAccount", "ExamSeatingSystem")
                qr_filename = f"shared_2fa_setup.svg"
                qr_filepath = os.path.join(QR_FOLDER, qr_filename)
                
                img = qrcode.make(totp_uri, image_factory=qrcode.image.svg.SvgImage)
                with open(qr_filepath, "wb") as f:
                    img.save(f)
                
                # Store setup info in session for display
                session['teacher_setup'] = {
                    'username': username,
                    'totp_secret': shared_secret,
                    'qr_path': url_for('static', filename=f'qrcodes/{qr_filename}'),
                    'assigned_room': assigned_room
                }
                
                flash('Teacher registered successfully! Please set up 2FA using the shared QR code below.', 'success')
                conn.close()
                return redirect(url_for('teacher_setup_2fa'))
                
            else:  # Student registration
                cursor.execute('''
                    INSERT INTO users (username, password_hash, role)
                    VALUES (?, ?, ?)
                ''', (username, hashed_password, role))
                
                conn.commit()
                flash(f'Student registration successful! Your username is {username} (Student ID). Please log in.', 'success')
                conn.close()
                return redirect(url_for('login'))
                
        except sqlite3.IntegrityError as e:
            flash(f'Registration failed: {str(e)}', 'danger')
            conn.rollback()
        except Exception as e:
            flash(f'An error occurred during registration: {str(e)}', 'danger')
            conn.rollback()

    conn.close()
    return render_template('register.html', available_rooms=available_rooms)

@app.route('/teacher_setup_2fa')
def teacher_setup_2fa():
    """Display 2FA setup page for newly registered teachers"""
    if 'teacher_setup' not in session:
        flash('No teacher setup information found.', 'danger')
        return redirect(url_for('register'))
    
    setup_info = session['teacher_setup']
    return render_template('teacher_setup_2fa.html', setup_info=setup_info)

@app.route('/complete_teacher_setup', methods=['POST'])
def complete_teacher_setup():
    """Verify 2FA setup and complete teacher registration"""
    if 'teacher_setup' not in session:
        flash('No teacher setup information found.', 'danger')
        return redirect(url_for('register'))
    
    setup_info = session['teacher_setup']
    verification_code = request.form.get('verification_code')
    
    if not verification_code:
        flash('Please enter the verification code from your authenticator app.', 'danger')
        return render_template('teacher_setup_2fa.html', setup_info=setup_info)
    
    # Verify the TOTP code
    totp = pyotp.TOTP(setup_info['totp_secret'])
    if totp.verify(verification_code):
        # Clear setup session
        session.pop('teacher_setup', None)
        flash('2FA setup completed successfully! You can now log in.', 'success')
        return redirect(url_for('login'))
    else:
        flash('Invalid verification code. Please try again.', 'danger')
        return render_template('teacher_setup_2fa.html', setup_info=setup_info)

@app.route('/admin_dashboard')
@require_admin
def admin_dashboard():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get shared TOTP secret to display QR code
    shared_secret = get_or_create_shared_totp_secret()
    qr_code_svg = None
    if shared_secret:
        totp_uri = pyotp.utils.build_uri(shared_secret, "SharedAccount", "ExamSeatingSystem")
        img = qrcode.make(totp_uri, image_factory=qrcode.image.svg.SvgImage)
        buffer = BytesIO()
        img.save(buffer)
        qr_code_svg = buffer.getvalue().decode('utf-8')

    admin_data = {
        'totp_secret': shared_secret,
        'qr_code_svg': qr_code_svg
    }

    # Fetch all users with their assigned rooms
    cursor.execute('''
        SELECT u.id, u.username, u.role, tr.room_name 
        FROM users u 
        LEFT JOIN teacher_rooms tr ON u.username = tr.teacher_username 
        ORDER BY u.role, u.username
    ''')
    user_rows = cursor.fetchall()
    users = []
    for row in user_rows:
        user_data = {
            'id': row[0],
            'username': row[1],
            'role': row[2],
            'email': f'{row[1]}@example.com',
            'assigned_room': row[3] if row[3] else 'N/A'
        }
        users.append(user_data)

    # Fetch room info from database
    cursor.execute('SELECT room_name, capacity FROM room_configs ORDER BY room_name')
    global_room_configs_from_db = [{'room_name': r[0], 'capacity': r[1]} for r in cursor.fetchall()]

    # Student metrics
    df = load_student_data()
    total_students = df['StudentID'].nunique() if not df.empty and 'StudentID' in df.columns else len(df)
    active_exams = df['Subject'].nunique() if not df.empty else 0
    exam_time_dict = df['ExamTime'].value_counts().to_dict() if not df.empty else {}
    exam_time_distribution = SimpleNamespace(**exam_time_dict)

    conn.close()

    return render_template(
        'admin_dashboard.html',
        username=session['username'],
        admin_data=admin_data,
        users=users,
        total_students=total_students,
        active_exams=active_exams,
        exam_time_distribution=exam_time_distribution,
        global_room_configs_from_db=global_room_configs_from_db
    )

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@require_admin
def admin_delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Get user info before deletion
        cursor.execute('SELECT username, role FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        
        if not user:
            flash('User not found.', 'danger')
            conn.close()
            return redirect(url_for('admin_dashboard'))
        
        username, role = user
        
        # Don't allow deleting the current admin
        if username == session['username'] and role == 'admin':
            flash('Cannot delete your own admin account.', 'danger')
            conn.close()
            return redirect(url_for('admin_dashboard'))
        
        # Delete teacher room assignment if exists
        if role == 'teacher':
            cursor.execute('DELETE FROM teacher_rooms WHERE teacher_username = ?', (username,))
        
        # Delete user
        cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        
        flash(f'User {username} deleted successfully.', 'success')
    except Exception as e:
        flash(f'Error deleting user: {str(e)}', 'danger')
        conn.rollback()
    
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/edit_user/<int:user_id>', methods=['GET', 'POST'])
@require_admin
def admin_edit_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        new_room = request.form.get('assigned_room')
        
        # Get user info
        cursor.execute('SELECT username, role FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        
        if not user:
            flash('User not found.', 'danger')
            conn.close()
            return redirect(url_for('admin_dashboard'))
        
        username, role = user
        
        if role == 'teacher':
            try:
                # Check if new room is already assigned to another teacher
                if new_room:
                    cursor.execute('SELECT teacher_username FROM teacher_rooms WHERE room_name = ? AND teacher_username != ?', 
                                 (new_room, username))
                    existing_assignment = cursor.fetchone()
                    if existing_assignment:
                        flash(f'Room {new_room} is already assigned to {existing_assignment[0]}.', 'danger')
                        conn.close()
                        return redirect(url_for('admin_edit_user', user_id=user_id))
                
                # Update room assignment
                cursor.execute('DELETE FROM teacher_rooms WHERE teacher_username = ?', (username,))
                if new_room:
                    cursor.execute('INSERT INTO teacher_rooms (teacher_username, room_name) VALUES (?, ?)', 
                                 (username, new_room))
                
                conn.commit()
                flash(f'Teacher {username} room assignment updated successfully.', 'success')
                conn.close()
                return redirect(url_for('admin_dashboard'))
                
            except Exception as e:
                flash(f'Error updating user: {str(e)}', 'danger')
                conn.rollback()
    
    # GET request - show edit form
    cursor.execute('''
        SELECT u.username, u.role, tr.room_name 
        FROM users u 
        LEFT JOIN teacher_rooms tr ON u.username = tr.teacher_username 
        WHERE u.id = ?
    ''', (user_id,))
    user_data = cursor.fetchone()
    
    if not user_data:
        flash('User not found.', 'danger')
        conn.close()
        return redirect(url_for('admin_dashboard'))
    
    # Get available rooms
    cursor.execute('SELECT room_name, capacity FROM room_configs ORDER BY room_name')
    available_rooms = [{'room_name': row[0], 'capacity': row[1]} for row in cursor.fetchall()]
    
    # Get assigned rooms to exclude current user's room
    cursor.execute('SELECT room_name, teacher_username FROM teacher_rooms WHERE teacher_username != ?', (user_data[0],))
    assigned_rooms = {row[0]: row[1] for row in cursor.fetchall()}
    
    conn.close()
    
    user_info = {
        'id': user_id,
        'username': user_data[0],
        'role': user_data[1],
        'assigned_room': user_data[2]
    }
    
    return render_template('admin_edit_user.html', 
                         user=user_info, 
                         available_rooms=available_rooms,
                         assigned_rooms=assigned_rooms)

@app.route('/admin/exam_schedule')
@require_admin
def admin_exam_schedule():
    df = load_student_data()
    if df.empty:
        flash('No student data loaded.', 'info')
        return render_template('admin_exam_schedule.html', exam_time_distribution={}, exam_date_subjects={})

    exam_time_distribution = df['ExamTime'].value_counts().to_dict()
    exam_date_subjects = df.groupby('ExamDate')['Subject'].apply(lambda x: x.tolist()).to_dict()

    return render_template('admin_exam_schedule.html',
                           exam_time_distribution=exam_time_distribution,
                           exam_date_subjects=exam_date_subjects)

@app.route('/admin/seating_rules')
@require_admin
def admin_seating_rules():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM room_configs ORDER BY room_name')
    rooms_data = cursor.fetchall()
    conn.close()

    room_constraints = []
    for room in rooms_data:
        room_constraints.append({
            'room_name': room[1],
            'capacity': room[2],
            'max_subjects': room[3],
            'max_branches': room[4],
            'allowed_years': room[5].split(',') if room[5] else [],
            'allowed_branches': room[6].split(',') if room[6] else []
        })
    return render_template('admin_seating_rules.html', room_constraints=room_constraints)

@app.route('/admin/rooms_config')
@require_admin
def admin_rooms_config():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get rooms data
    cursor.execute('SELECT * FROM room_configs ORDER BY room_name')
    rooms = cursor.fetchall()
    
    # Get users data with room assignments
    cursor.execute('''
        SELECT u.id, u.username, u.role, tr.room_name 
        FROM users u 
        LEFT JOIN teacher_rooms tr ON u.username = tr.teacher_username 
        ORDER BY u.role, u.username
    ''')
    user_rows = cursor.fetchall()
    users = []
    for row in user_rows:
        user_data = {
            'id': row[0],
            'username': row[1],
            'role': row[2],
            'assigned_room': row[3] if row[3] else None
        }
        users.append(user_data)
    
    conn.close()
    return render_template('admin_rooms_config.html', rooms=rooms, users=users)

@app.route('/admin/add_room_config', methods=['GET', 'POST'])
@require_admin
def admin_add_room_config():
    if request.method == 'POST':
        room_name = request.form['room_name']
        capacity = int(request.form['capacity'])
        max_subjects = request.form.get('max_subjects')
        max_branches = request.form.get('max_branches')
        max_departments = request.form.get('max_departments', 2)
        max_years = request.form.get('max_years', 2)
        allowed_years = request.form.getlist('allowed_years')
        allowed_branches = request.form.getlist('allowed_branches')
        layout_columns = int(request.form.get('layout_columns', 6))
        layout_rows = int(request.form.get('layout_rows', 5))

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO room_configs
                (room_name, capacity, max_subjects, max_branches, max_departments, max_years, allowed_years, allowed_branches, layout_columns, layout_rows)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (room_name, capacity,
                  max_subjects if max_subjects else None,
                  max_branches if max_branches else None,
                  int(max_departments) if max_departments else 2,
                  int(max_years) if max_years else 2,
                  ','.join(allowed_years),
                  ','.join(allowed_branches),
                  layout_columns, layout_rows))
            conn.commit()
            flash('Room configuration added successfully!', 'success')
        except sqlite3.IntegrityError:
            flash('Room name already exists.', 'danger')
        conn.close()
        return redirect(url_for('admin_rooms_config'))
    return render_template('admin_add_room_config.html')

@app.route('/admin/edit_room_config/<int:room_id>', methods=['GET', 'POST'])
@require_admin
def admin_edit_room_config(room_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if request.method == 'POST':
        capacity = int(request.form['capacity'])
        max_subjects = request.form.get('max_subjects')
        max_branches = request.form.get('max_branches')
        max_departments = request.form.get('max_departments', 2)
        max_years = request.form.get('max_years', 2)
        allowed_years = request.form.getlist('allowed_years')
        allowed_branches = request.form.getlist('allowed_branches')
        layout_columns = int(request.form.get('layout_columns', 6))
        layout_rows = int(request.form.get('layout_rows', 5))

        cursor.execute('''
            UPDATE room_configs SET
            capacity = ?, max_subjects = ?, max_branches = ?, max_departments = ?, max_years = ?,
            allowed_years = ?, allowed_branches = ?, layout_columns = ?, layout_rows = ?
            WHERE id = ?
        ''', (capacity,
              max_subjects if max_subjects else None,
              max_branches if max_branches else None,
              int(max_departments) if max_departments else 2,
              int(max_years) if max_years else 2,
              ','.join(allowed_years),
              ','.join(allowed_branches),
              layout_columns, layout_rows, room_id))
        conn.commit()
        flash('Room configuration updated successfully!', 'success')
        conn.close()
        return redirect(url_for('admin_rooms_config'))

    cursor.execute('SELECT * FROM room_configs WHERE id = ?', (room_id,))
    room = cursor.fetchone()
    conn.close()

    if room:
        room_dict = {
            'id': room[0],
            'room_name': room[1],
            'capacity': room[2],
            'max_subjects': room[3],
            'max_branches': room[4],
            'allowed_years': room[5].split(',') if room[5] else [],
            'allowed_branches': room[6].split(',') if room[6] else [],
            'layout_columns': room[7] if len(room) > 7 else 6,
            'layout_rows': room[8] if len(room) > 8 else 5
        }
        return render_template('admin_edit_room_config.html', room=room_dict)
    else:
        flash('Room not found.', 'danger')
        return redirect(url_for('admin_rooms_config'))

@app.route('/admin/delete_room_config/<int:room_id>', methods=['POST'])
@require_admin
def admin_delete_room_config(room_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Get room name for feedback
        cursor.execute('SELECT room_name FROM room_configs WHERE id = ?', (room_id,))
        room = cursor.fetchone()
        
        if not room:
            flash('Room not found.', 'danger')
            conn.close()
            return redirect(url_for('admin_rooms_config'))
        
        room_name = room[0]
        
        # Check if room is assigned to any teacher
        cursor.execute('SELECT teacher_username FROM teacher_rooms WHERE room_name = ?', (room_name,))
        assigned_teacher = cursor.fetchone()
        
        if assigned_teacher:
            flash(f'Cannot delete room {room_name}. It is assigned to teacher {assigned_teacher[0]}. Please reassign the teacher first.', 'danger')
            conn.close()
            return redirect(url_for('admin_rooms_config'))
        
        # Delete room configuration
        cursor.execute('DELETE FROM room_configs WHERE id = ?', (room_id,))
        conn.commit()
        
        flash(f'Room {room_name} deleted successfully.', 'success')
    except Exception as e:
        flash(f'Error deleting room: {str(e)}', 'danger')
        conn.rollback()
    
    conn.close()
    return redirect(url_for('admin_rooms_config'))

@app.route('/admin/view_all_rooms')
@require_admin
def admin_view_all_rooms():
    """View all rooms with their details and seating status"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, room_name, capacity, layout_columns, layout_rows,
               max_subjects, max_branches, allowed_years
        FROM room_configs ORDER BY room_name
    ''')
    rooms_raw = cursor.fetchall()
    conn.close()

    rooms = []
    total_capacity = 0
    rooms_with_seating = 0

    for room in rooms_raw:
        allowed_years = room[7].split(',') if room[7] else []
        room_name = room[1]
        # Check for session-specific visualization files (e.g., Room-A_20250620_Morning.html)
        session_files = glob.glob(f'visualizations/{room_name}_*.html')
        has_seating = len(session_files) > 0

        rooms.append({
            'id': room[0],
            'room_name': room_name,
            'capacity': room[2],
            'layout_columns': room[3] or 6,
            'layout_rows': room[4] or 5,
            'max_subjects': room[5] or 3,
            'max_branches': room[6] or 4,
            'allowed_years': allowed_years,
            'has_seating': has_seating,
            'session_count': len(session_files)
        })
        total_capacity += room[2]
        if has_seating:
            rooms_with_seating += 1

    return render_template('admin_view_all_rooms.html',
                           rooms=rooms,
                           total_capacity=total_capacity,
                           rooms_with_seating=rooms_with_seating)

@app.route('/admin/qr_codes')
@require_admin
def admin_qr_codes():
    """Manage QR codes for students"""
    # Get all students
    df = load_student_data()
    students = df.to_dict(orient='records') if not df.empty else []

    # Get unique departments
    departments = df['Department'].unique().tolist() if 'Department' in df.columns else []

    # Get existing QR code files
    qr_folder = 'static/qrcodes'
    existing_qr_files = []
    if os.path.exists(qr_folder):
        for filename in os.listdir(qr_folder):
            if filename.startswith('student_') and filename.endswith('_qr.svg'):
                student_id = filename.replace('student_', '').replace('_qr.svg', '')
                existing_qr_files.append({
                    'filename': filename,
                    'student_id': student_id,
                    'path': url_for('static', filename=f'qrcodes/{filename}')
                })

    # Get QR codes from session if just generated
    qr_codes = session.pop('generated_qr_codes', [])

    return render_template('admin_qr_codes.html',
                           students=students,
                           departments=departments,
                           qr_codes=qr_codes,
                           existing_qr_files=existing_qr_files,
                           existing_qr_count=len(existing_qr_files))

@app.route('/admin/qr_codes/generate', methods=['POST'])
@require_admin
def admin_generate_qr():
    """Generate QR code for a single student"""
    student_id = request.form.get('student_id')

    if not student_id:
        flash('Please select a student.', 'danger')
        return redirect(url_for('admin_qr_codes'))

    # Get student info
    df = load_student_data()
    student = df[df['StudentID'].astype(str) == str(student_id)]
    student_name = student.iloc[0]['Name'] if not student.empty else 'Unknown'

    qr_data = f"StudentID:{student_id}|ExamSystem"
    qr_filename = f"student_{student_id}_qr.svg"
    qr_filepath = os.path.join(QR_FOLDER, qr_filename)

    try:
        img = qrcode.make(qr_data, image_factory=qrcode.image.svg.SvgImage)
        with open(qr_filepath, "wb") as f:
            img.save(f)

        qr_url = url_for('static', filename=f'qrcodes/{qr_filename}')
        session['generated_qr_codes'] = [{
            'student_id': student_id,
            'name': student_name,
            'path': qr_url
        }]
        flash(f'QR Code generated successfully for {student_id}!', 'success')
    except Exception as e:
        flash(f'Error generating QR Code: {e}', 'danger')

    return redirect(url_for('admin_qr_codes'))

@app.route('/admin/qr_codes/generate_bulk', methods=['POST'])
@require_admin
def admin_generate_bulk_qr():
    """Generate QR codes for multiple students"""
    department = request.form.get('department', 'all')

    df = load_student_data()
    if df.empty:
        flash('No students found in the system.', 'danger')
        return redirect(url_for('admin_qr_codes'))

    if department != 'all':
        df = df[df['Department'] == department]

    if df.empty:
        flash(f'No students found in department: {department}', 'danger')
        return redirect(url_for('admin_qr_codes'))

    generated_qr_codes = []
    success_count = 0

    for _, student in df.iterrows():
        student_id = str(student['StudentID'])
        student_name = student.get('Name', 'Unknown')

        qr_data = f"StudentID:{student_id}|ExamSystem"
        qr_filename = f"student_{student_id}_qr.svg"
        qr_filepath = os.path.join(QR_FOLDER, qr_filename)

        try:
            img = qrcode.make(qr_data, image_factory=qrcode.image.svg.SvgImage)
            with open(qr_filepath, "wb") as f:
                img.save(f)

            generated_qr_codes.append({
                'student_id': student_id,
                'name': student_name,
                'path': url_for('static', filename=f'qrcodes/{qr_filename}')
            })
            success_count += 1
        except Exception:
            continue

    session['generated_qr_codes'] = generated_qr_codes
    flash(f'Successfully generated {success_count} QR codes!', 'success')
    return redirect(url_for('admin_qr_codes'))

@app.route('/teacher_dashboard')
@require_login
def teacher_dashboard():
    # Load student data if needed
    df = load_student_data()
    students_data = df.to_dict(orient='records')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Fetch schedule for this teacher
    cursor.execute('''
        SELECT room_name, exam_date, exam_time
        FROM teacher_schedule
        WHERE teacher_username = ?
        ORDER BY exam_date,
            CASE exam_time
                WHEN 'Morning' THEN 1
                WHEN 'Afternoon' THEN 2
                WHEN 'Evening' THEN 3
            END
    ''', (session['username'],))

    schedule = []
    for row in cursor.fetchall():
        schedule.append({
            'room': row[0],
            'date': row[1],
            'time': row[2]
        })

    # Get upcoming sessions (next 3)
    upcoming = schedule[:3] if schedule else []

    conn.close()

    seating_plan_exists = 'final_seating_layout' in session and session['final_seating_layout'] is not None

    return render_template(
        'enhanced_teacher_dashboard.html',
        username=session['username'],
        students=students_data,
        schedule=schedule,
        upcoming=upcoming,
        total_sessions=len(schedule),
        seating_plan_exists=seating_plan_exists
    )

def auto_assign_teachers_to_schedule():
    """
    Auto-assign teachers to room schedules with constraints:
    - No consecutive invigilation sessions
    - Respect teacher preferences (unavailable dates, preferred times, max sessions per day)
    """
    import pandas as pd
    from collections import defaultdict

    csv_path = 'data/students.csv'
    if not os.path.exists(csv_path):
        return 0

    df = pd.read_csv(csv_path)

    # Get unique exam sessions (date + time)
    sessions = df.groupby(['ExamDate', 'ExamTime']).size().reset_index(name='count')

    # Define time slot order
    time_order = {'Morning': 0, 'Afternoon': 1, 'Evening': 2}
    sessions['time_order'] = sessions['ExamTime'].map(time_order)
    sessions = sessions.sort_values(['ExamDate', 'time_order'])

    # Get all teachers
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT username FROM users WHERE role = 'teacher'")
    teachers = [row[0] for row in cursor.fetchall()]

    if not teachers:
        conn.close()
        return 0

    # Get rooms that actually have students assigned for each session
    # by checking which visualization files exist
    import glob as glob_module
    viz_files = glob_module.glob('visualizations/Room-*_*_*.html')
    # Parse files to get (room, date, time) tuples
    rooms_by_session = {}  # (date, time) -> set of rooms
    for vf in viz_files:
        # Format: visualizations/Room-A_20260620_Morning.html
        fname = os.path.basename(vf).replace('.html', '')
        parts = fname.rsplit('_', 2)  # Split from right to handle Room-A, Room-B etc.
        if len(parts) == 3:
            room_name = parts[0]
            date_str = parts[1]  # e.g., "20260620"
            time_slot = parts[2]  # e.g., "Morning"
            # Convert date back to YYYY-MM-DD format
            if len(date_str) == 8:
                formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                session_key = (formatted_date, time_slot)
                if session_key not in rooms_by_session:
                    rooms_by_session[session_key] = set()
                rooms_by_session[session_key].add(room_name)

    # Load teacher preferences
    teacher_preferences = {}
    for teacher in teachers:
        cursor.execute('''
            SELECT preferred_times, max_sessions_per_day, unavailable_dates
            FROM teacher_preferences WHERE teacher_username = ?
        ''', (teacher,))
        row = cursor.fetchone()
        if row:
            teacher_preferences[teacher] = {
                'preferred_times': set(row[0].split(',')) if row[0] else {'Morning', 'Afternoon', 'Evening'},
                'max_sessions_per_day': row[1] if row[1] else 2,
                'unavailable_dates': set(row[2].split('\n')) if row[2] else set()
            }
        else:
            teacher_preferences[teacher] = {
                'preferred_times': {'Morning', 'Afternoon', 'Evening'},
                'max_sessions_per_day': 2,
                'unavailable_dates': set()
            }

    # Clear existing schedule
    cursor.execute("DELETE FROM teacher_schedule")

    # Track assignments: teacher -> list of (date, time_order)
    teacher_assignments = defaultdict(list)
    # Track sessions per teacher per day
    teacher_daily_sessions = defaultdict(lambda: defaultdict(int))
    # Track which teachers are assigned per session
    session_assignments = {}  # (date, time) -> set of teachers
    # Track unassigned room-sessions for reporting
    unassigned_slots = []

    assignments_made = 0

    # Convert sessions to list for easier processing
    session_list = sessions.to_dict('records')

    for i, session_row in enumerate(session_list):
        exam_date = session_row['ExamDate']
        exam_time = session_row['ExamTime']
        current_time_order = session_row['time_order']
        session_key = (exam_date, exam_time)

        # Find previous session (if exists) to check for consecutive
        prev_session = None
        if i > 0:
            prev = session_list[i - 1]
            if prev['ExamDate'] == exam_date and abs(prev['time_order'] - current_time_order) == 1:
                prev_session = (prev['ExamDate'], prev['ExamTime'])

        # Get teachers who were in the previous consecutive session
        busy_teachers = set()
        if prev_session and prev_session in session_assignments:
            busy_teachers = session_assignments[prev_session]

        session_assignments[session_key] = set()

        # Only assign teachers to rooms that actually have students for this session
        rooms_for_session = rooms_by_session.get(session_key, set())
        if not rooms_for_session:
            continue  # No rooms have students for this session

        for room in sorted(rooms_for_session):
            # Score each teacher: prefer those who are NOT busy from prev session
            # and have fewer total assignments
            best_teacher = None
            best_score = float('inf')

            for teacher in teachers:
                prefs = teacher_preferences[teacher]

                # Check if already assigned this session (can't be in 2 rooms at once)
                if teacher in session_assignments[session_key]:
                    continue

                # HARD CONSTRAINT: Skip teachers who were busy in the previous consecutive session
                # Teachers must NOT have consecutive invigilation sessions
                if teacher in busy_teachers:
                    continue

                # Check if teacher is unavailable on this date
                if exam_date in prefs['unavailable_dates'] or exam_date.strip() in prefs['unavailable_dates']:
                    continue

                # Check if teacher has reached max sessions for this day
                if teacher_daily_sessions[teacher][exam_date] >= prefs['max_sessions_per_day']:
                    continue

                # Calculate score: lower is better
                assignment_count = len(teacher_assignments[teacher])
                score = assignment_count

                # Penalty for non-preferred time slots (soft constraint)
                if exam_time not in prefs['preferred_times']:
                    score += 0.5  # Small penalty, still can be assigned but prefer others

                if score < best_score:
                    best_score = score
                    best_teacher = teacher

            if best_teacher:
                try:
                    cursor.execute('''
                        INSERT INTO teacher_schedule (teacher_username, room_name, exam_date, exam_time)
                        VALUES (?, ?, ?, ?)
                    ''', (best_teacher, room, exam_date, exam_time))
                    teacher_assignments[best_teacher].append((exam_date, current_time_order))
                    teacher_daily_sessions[best_teacher][exam_date] += 1
                    session_assignments[session_key].add(best_teacher)
                    assignments_made += 1
                except sqlite3.IntegrityError:
                    pass  # Already assigned (shouldn't happen with our checks)
            else:
                # No teacher available (all are either already assigned or blocked by constraints)
                unassigned_slots.append(f"{room} on {exam_date} {exam_time}")

    conn.commit()
    conn.close()

    # Calculate total slots needed (count all rooms across all sessions)
    total_slots_needed = sum(len(rooms) for rooms in rooms_by_session.values())

    print(f"[Schedule] Teacher assignment summary (preferences and constraints enforced):")
    for teacher, slots in teacher_assignments.items():
        prefs = teacher_preferences.get(teacher, {})
        print(f"  - {teacher}: {len(slots)} sessions (max/day: {prefs.get('max_sessions_per_day', 2)})")

    if unassigned_slots:
        print(f"\n[Schedule] WARNING: {len(unassigned_slots)} room-sessions have NO teacher assigned!")
        print(f"[Schedule] You have {len(teachers)} teachers but need to cover {total_slots_needed} room-sessions.")
        print(f"[Schedule] With no-consecutive constraint and max 2 sessions/day, each teacher can cover ~2 slots/day.")
        print(f"[Schedule] RECOMMENDATION: Add more teachers or reduce rooms/time slots.")
        for slot in unassigned_slots[:5]:
            print(f"  - {slot}")
        if len(unassigned_slots) > 5:
            print(f"  ... and {len(unassigned_slots) - 5} more unassigned slots")

    return assignments_made

@app.route('/teacher/schedule')
def teacher_schedule():
    if 'username' not in session or session.get('role') != 'teacher':
        flash('Please login as teacher', 'danger')
        return redirect(url_for('login'))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get this teacher's schedule
    cursor.execute('''
        SELECT room_name, exam_date, exam_time
        FROM teacher_schedule
        WHERE teacher_username = ?
        ORDER BY exam_date,
            CASE exam_time
                WHEN 'Morning' THEN 1
                WHEN 'Afternoon' THEN 2
                WHEN 'Evening' THEN 3
            END
    ''', (session['username'],))

    schedule = []
    for row in cursor.fetchall():
        schedule.append({
            'room': row[0],
            'date': row[1],
            'time': row[2]
        })

    # Get unique dates sorted
    dates = sorted(list(set(item['date'] for item in schedule)))

    # Create grid structure: schedule_grid[time_slot][date] = room
    time_slots = ['Morning', 'Afternoon', 'Evening']
    schedule_grid = {slot: {} for slot in time_slots}

    for item in schedule:
        schedule_grid[item['time']][item['date']] = item['room']

    conn.close()

    return render_template(
        'teacher_schedule.html',
        username=session['username'],
        schedule=schedule,
        dates=dates,
        time_slots=time_slots,
        schedule_grid=schedule_grid
    )


@app.route('/teacher/download_schedule')
def download_teacher_schedule():
    """Download teacher's invigilation schedule as CSV"""
    if 'username' not in session or session.get('role') != 'teacher':
        flash('Please login as teacher', 'danger')
        return redirect(url_for('login'))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get this teacher's schedule with room details
    cursor.execute('''
        SELECT ts.room_name, ts.exam_date, ts.exam_time, rc.capacity, rc.layout_columns, rc.layout_rows
        FROM teacher_schedule ts
        LEFT JOIN room_configs rc ON ts.room_name = rc.room_name
        WHERE ts.teacher_username = ?
        ORDER BY ts.exam_date,
            CASE ts.exam_time
                WHEN 'Morning' THEN 1
                WHEN 'Afternoon' THEN 2
                WHEN 'Evening' THEN 3
            END
    ''', (session['username'],))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        flash('No schedule found to download', 'warning')
        return redirect(url_for('teacher_schedule'))

    # Create CSV in memory
    import io
    output = io.StringIO()

    # Write header
    output.write('Date,Time Slot,Room,Capacity,Layout (Columns x Rows),Report Time\n')

    # Time slot report times
    report_times = {
        'Morning': '8:30 AM',
        'Afternoon': '12:30 PM',
        'Evening': '4:30 PM'
    }

    for row in rows:
        room_name = row[0]
        exam_date = row[1]
        exam_time = row[2]
        capacity = row[3] if row[3] else 'N/A'
        layout = f"{row[4]}x{row[5]}" if row[4] and row[5] else 'N/A'
        report_time = report_times.get(exam_time, 'N/A')

        output.write(f'{exam_date},{exam_time},{room_name},{capacity},{layout},{report_time}\n')

    # Create response
    output.seek(0)
    from flask import Response

    filename = f"invigilation_schedule_{session['username']}.csv"

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# ============================================
# TEACHER PREFERENCES
# ============================================

@app.route('/teacher/preferences', methods=['GET', 'POST'])
def teacher_preferences():
    """View and update teacher preferences for invigilation scheduling"""
    if 'username' not in session or session.get('role') != 'teacher':
        flash('Please login as teacher', 'danger')
        return redirect(url_for('login'))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if request.method == 'POST':
        preferred_times = request.form.getlist('preferred_times')
        max_sessions = int(request.form.get('max_sessions_per_day', 2))
        unavailable_dates = request.form.get('unavailable_dates', '')

        # Upsert preferences
        cursor.execute('''
            INSERT INTO teacher_preferences (teacher_username, preferred_times, max_sessions_per_day, unavailable_dates, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(teacher_username) DO UPDATE SET
                preferred_times = excluded.preferred_times,
                max_sessions_per_day = excluded.max_sessions_per_day,
                unavailable_dates = excluded.unavailable_dates,
                updated_at = CURRENT_TIMESTAMP
        ''', (session['username'], ','.join(preferred_times), max_sessions, unavailable_dates))
        conn.commit()
        flash('Preferences saved successfully!', 'success')

    # Get current preferences
    cursor.execute('''
        SELECT preferred_times, max_sessions_per_day, unavailable_dates
        FROM teacher_preferences WHERE teacher_username = ?
    ''', (session['username'],))
    row = cursor.fetchone()

    preferences = {
        'preferred_times': row[0].split(',') if row and row[0] else ['Morning', 'Afternoon', 'Evening'],
        'max_sessions_per_day': row[1] if row else 2,
        'unavailable_dates': row[2] if row else ''
    }

    conn.close()
    return render_template('teacher_preferences.html',
                         username=session['username'],
                         preferences=preferences)


# ============================================
# SWAP REQUESTS SYSTEM
# ============================================

@app.route('/teacher/swap_requests')
def teacher_swap_requests():
    """View swap requests (sent and received)"""
    if 'username' not in session or session.get('role') != 'teacher':
        flash('Please login as teacher', 'danger')
        return redirect(url_for('login'))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get sent requests
    cursor.execute('''
        SELECT sr.id, sr.target_username, sr.status, sr.reason, sr.created_at,
               ts1.room_name as my_room, ts1.exam_date as my_date, ts1.exam_time as my_time,
               ts2.room_name as their_room, ts2.exam_date as their_date, ts2.exam_time as their_time
        FROM swap_requests sr
        JOIN teacher_schedule ts1 ON sr.requester_schedule_id = ts1.id
        JOIN teacher_schedule ts2 ON sr.target_schedule_id = ts2.id
        WHERE sr.requester_username = ?
        ORDER BY sr.created_at DESC
    ''', (session['username'],))
    sent_requests = cursor.fetchall()

    # Get received requests
    cursor.execute('''
        SELECT sr.id, sr.requester_username, sr.status, sr.reason, sr.created_at,
               ts1.room_name as their_room, ts1.exam_date as their_date, ts1.exam_time as their_time,
               ts2.room_name as my_room, ts2.exam_date as my_date, ts2.exam_time as my_time
        FROM swap_requests sr
        JOIN teacher_schedule ts1 ON sr.requester_schedule_id = ts1.id
        JOIN teacher_schedule ts2 ON sr.target_schedule_id = ts2.id
        WHERE sr.target_username = ?
        ORDER BY sr.created_at DESC
    ''', (session['username'],))
    received_requests = cursor.fetchall()

    # Get all other teachers for creating new swap request
    cursor.execute("SELECT username FROM users WHERE role = 'teacher' AND username != ?",
                  (session['username'],))
    other_teachers = [row[0] for row in cursor.fetchall()]

    # Get my schedule for swap options
    cursor.execute('''
        SELECT id, room_name, exam_date, exam_time
        FROM teacher_schedule
        WHERE teacher_username = ?
        ORDER BY exam_date, CASE exam_time WHEN 'Morning' THEN 1 WHEN 'Afternoon' THEN 2 ELSE 3 END
    ''', (session['username'],))
    my_schedule = cursor.fetchall()

    conn.close()
    return render_template('teacher_swap_requests.html',
                         username=session['username'],
                         sent_requests=sent_requests,
                         received_requests=received_requests,
                         other_teachers=other_teachers,
                         my_schedule=my_schedule)


@app.route('/teacher/swap_requests/create', methods=['POST'])
def create_swap_request():
    """Create a new swap request"""
    if 'username' not in session or session.get('role') != 'teacher':
        return jsonify({'error': 'Unauthorized'}), 401

    my_schedule_id = request.form.get('my_schedule_id')
    target_username = request.form.get('target_username')
    target_schedule_id = request.form.get('target_schedule_id')
    reason = request.form.get('reason', '')

    if not all([my_schedule_id, target_username, target_schedule_id]):
        flash('Please fill all required fields', 'danger')
        return redirect(url_for('teacher_swap_requests'))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Verify both schedules exist and belong to correct teachers
    cursor.execute('SELECT teacher_username FROM teacher_schedule WHERE id = ?', (my_schedule_id,))
    my_row = cursor.fetchone()
    cursor.execute('SELECT teacher_username FROM teacher_schedule WHERE id = ?', (target_schedule_id,))
    target_row = cursor.fetchone()

    if not my_row or my_row[0] != session['username']:
        flash('Invalid schedule selection', 'danger')
        conn.close()
        return redirect(url_for('teacher_swap_requests'))

    if not target_row or target_row[0] != target_username:
        flash('Invalid target schedule selection', 'danger')
        conn.close()
        return redirect(url_for('teacher_swap_requests'))

    # Check for existing pending request
    cursor.execute('''
        SELECT id FROM swap_requests
        WHERE requester_username = ? AND target_username = ?
        AND requester_schedule_id = ? AND target_schedule_id = ?
        AND status = 'pending'
    ''', (session['username'], target_username, my_schedule_id, target_schedule_id))

    if cursor.fetchone():
        flash('A similar swap request is already pending', 'warning')
        conn.close()
        return redirect(url_for('teacher_swap_requests'))

    # Create swap request
    cursor.execute('''
        INSERT INTO swap_requests (requester_username, target_username, requester_schedule_id,
                                   target_schedule_id, reason, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    ''', (session['username'], target_username, my_schedule_id, target_schedule_id, reason))

    # Create notification for target teacher
    cursor.execute('''
        INSERT INTO notifications (recipient_username, notification_type, subject, message)
        VALUES (?, 'swap_request', 'New Swap Request',
                ?)
    ''', (target_username, f'{session["username"]} has requested to swap an invigilation session with you.'))

    conn.commit()
    conn.close()
    flash('Swap request sent successfully!', 'success')
    return redirect(url_for('teacher_swap_requests'))


@app.route('/teacher/swap_requests/<int:request_id>/respond', methods=['POST'])
def respond_swap_request(request_id):
    """Teacher responds to a swap request (accept/reject)"""
    if 'username' not in session or session.get('role') != 'teacher':
        return jsonify({'error': 'Unauthorized'}), 401

    action = request.form.get('action')
    if action not in ['accept', 'reject']:
        flash('Invalid action', 'danger')
        return redirect(url_for('teacher_swap_requests'))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Verify request exists and is for this teacher
    cursor.execute('''
        SELECT requester_username, target_username, status
        FROM swap_requests WHERE id = ?
    ''', (request_id,))
    row = cursor.fetchone()

    if not row or row[1] != session['username']:
        flash('Invalid request', 'danger')
        conn.close()
        return redirect(url_for('teacher_swap_requests'))

    if row[2] != 'pending':
        flash('This request has already been processed', 'warning')
        conn.close()
        return redirect(url_for('teacher_swap_requests'))

    if action == 'accept':
        # Update status to teacher_accepted, awaiting admin approval
        cursor.execute('''
            UPDATE swap_requests SET status = 'teacher_accepted'
            WHERE id = ?
        ''', (request_id,))

        # Notify requester
        cursor.execute('''
            INSERT INTO notifications (recipient_username, notification_type, subject, message)
            VALUES (?, 'swap_accepted', 'Swap Request Accepted',
                    ?)
        ''', (row[0], f'{session["username"]} has accepted your swap request. Awaiting admin approval.'))

    else:
        cursor.execute('''
            UPDATE swap_requests SET status = 'rejected'
            WHERE id = ?
        ''', (request_id,))

        # Notify requester
        cursor.execute('''
            INSERT INTO notifications (recipient_username, notification_type, subject, message)
            VALUES (?, 'swap_rejected', 'Swap Request Rejected',
                    ?)
        ''', (row[0], f'{session["username"]} has declined your swap request.'))

    conn.commit()
    conn.close()
    flash(f'Request {action}ed successfully!', 'success')
    return redirect(url_for('teacher_swap_requests'))


@app.route('/api/teacher/<username>/schedule')
def get_teacher_schedule_api(username):
    """API to get a teacher's schedule for swap selection"""
    if 'username' not in session or session.get('role') != 'teacher':
        return jsonify({'error': 'Unauthorized'}), 401

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, room_name, exam_date, exam_time
        FROM teacher_schedule
        WHERE teacher_username = ?
        ORDER BY exam_date, CASE exam_time WHEN 'Morning' THEN 1 WHEN 'Afternoon' THEN 2 ELSE 3 END
    ''', (username,))

    schedule = [{'id': row[0], 'room': row[1], 'date': row[2], 'time': row[3]}
                for row in cursor.fetchall()]

    conn.close()
    return jsonify(schedule)


# ============================================
# ADMIN SWAP MANAGEMENT
# ============================================

@app.route('/admin/swap_requests')
@require_admin
def admin_swap_requests():
    """Admin view of all swap requests"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT sr.id, sr.requester_username, sr.target_username, sr.status, sr.reason,
               sr.admin_notes, sr.created_at, sr.reviewed_at, sr.reviewed_by,
               ts1.room_name as req_room, ts1.exam_date as req_date, ts1.exam_time as req_time,
               ts2.room_name as tgt_room, ts2.exam_date as tgt_date, ts2.exam_time as tgt_time
        FROM swap_requests sr
        JOIN teacher_schedule ts1 ON sr.requester_schedule_id = ts1.id
        JOIN teacher_schedule ts2 ON sr.target_schedule_id = ts2.id
        ORDER BY
            CASE sr.status WHEN 'teacher_accepted' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
            sr.created_at DESC
    ''')

    requests = []
    for row in cursor.fetchall():
        requests.append({
            'id': row[0],
            'requester': row[1],
            'target': row[2],
            'status': row[3],
            'reason': row[4],
            'admin_notes': row[5],
            'created_at': row[6],
            'reviewed_at': row[7],
            'reviewed_by': row[8],
            'requester_session': {'room': row[9], 'date': row[10], 'time': row[11]},
            'target_session': {'room': row[12], 'date': row[13], 'time': row[14]}
        })

    conn.close()
    return render_template('admin_swap_requests.html', requests=requests)


@app.route('/admin/swap_requests/<int:request_id>/approve', methods=['POST'])
@require_admin
def approve_swap_request(request_id):
    """Admin approves a swap request and executes the swap"""
    admin_notes = request.form.get('admin_notes', '')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get the swap request details
    cursor.execute('''
        SELECT requester_username, target_username, requester_schedule_id, target_schedule_id, status
        FROM swap_requests WHERE id = ?
    ''', (request_id,))
    row = cursor.fetchone()

    if not row:
        flash('Swap request not found', 'danger')
        conn.close()
        return redirect(url_for('admin_swap_requests'))

    if row[4] != 'teacher_accepted':
        flash('This swap request is not ready for admin approval', 'warning')
        conn.close()
        return redirect(url_for('admin_swap_requests'))

    requester, target, req_sched_id, tgt_sched_id = row[0], row[1], row[2], row[3]

    # Execute the swap - update teacher_schedule entries
    cursor.execute('UPDATE teacher_schedule SET teacher_username = ? WHERE id = ?',
                  ('__TEMP_SWAP__', req_sched_id))
    cursor.execute('UPDATE teacher_schedule SET teacher_username = ? WHERE id = ?',
                  (requester, tgt_sched_id))
    cursor.execute('UPDATE teacher_schedule SET teacher_username = ? WHERE id = ?',
                  (target, req_sched_id))

    # Update swap request status
    cursor.execute('''
        UPDATE swap_requests
        SET status = 'approved', admin_notes = ?, reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ?
        WHERE id = ?
    ''', (admin_notes, session['username'], request_id))

    # Notify both teachers
    cursor.execute('''
        INSERT INTO notifications (recipient_username, notification_type, subject, message)
        VALUES (?, 'swap_approved', 'Swap Approved', 'Your swap request has been approved by admin. Your schedule has been updated.')
    ''', (requester,))
    cursor.execute('''
        INSERT INTO notifications (recipient_username, notification_type, subject, message)
        VALUES (?, 'swap_approved', 'Schedule Swapped', 'An invigilation swap affecting your schedule has been approved. Please check your updated schedule.')
    ''', (target,))

    conn.commit()
    conn.close()
    flash('Swap approved and executed successfully!', 'success')
    return redirect(url_for('admin_swap_requests'))


@app.route('/admin/swap_requests/<int:request_id>/reject', methods=['POST'])
@require_admin
def reject_swap_request(request_id):
    """Admin rejects a swap request"""
    admin_notes = request.form.get('admin_notes', '')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT requester_username, target_username FROM swap_requests WHERE id = ?
    ''', (request_id,))
    row = cursor.fetchone()

    if not row:
        flash('Swap request not found', 'danger')
        conn.close()
        return redirect(url_for('admin_swap_requests'))

    # Update status
    cursor.execute('''
        UPDATE swap_requests
        SET status = 'admin_rejected', admin_notes = ?, reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ?
        WHERE id = ?
    ''', (admin_notes, session['username'], request_id))

    # Notify both teachers
    for username in row:
        cursor.execute('''
            INSERT INTO notifications (recipient_username, notification_type, subject, message)
            VALUES (?, 'swap_rejected', 'Swap Request Rejected by Admin',
                    ?)
        ''', (username, f'The swap request has been rejected by admin. Reason: {admin_notes or "No reason provided"}'))

    conn.commit()
    conn.close()
    flash('Swap request rejected', 'success')
    return redirect(url_for('admin_swap_requests'))


# ============================================
# CONFLICT DETECTION DASHBOARD
# ============================================

@app.route('/admin/conflicts')
@require_admin
def admin_conflicts_dashboard():
    """Dashboard showing scheduling conflicts and issues"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    conflicts = {
        'room_capacity_issues': [],
        'teacher_overload': [],
        'coverage_gaps': [],
        'consecutive_violations': []
    }

    # 1. Check room capacity issues - rooms that might not fit all students
    csv_path = 'data/students.csv'
    if os.path.exists(csv_path):
        import pandas as pd
        df = pd.read_csv(csv_path)

        # Get students per session
        session_counts = df.groupby(['ExamDate', 'ExamTime']).size().reset_index(name='student_count')

        # Get total room capacity
        cursor.execute('SELECT SUM(capacity) FROM room_configs')
        total_capacity = cursor.fetchone()[0] or 0

        for _, row in session_counts.iterrows():
            if row['student_count'] > total_capacity:
                conflicts['room_capacity_issues'].append({
                    'date': row['ExamDate'],
                    'time': row['ExamTime'],
                    'students': row['student_count'],
                    'capacity': total_capacity,
                    'shortage': row['student_count'] - total_capacity
                })

    # 2. Check teacher overload - teachers with too many sessions per day
    cursor.execute('''
        SELECT teacher_username, exam_date, COUNT(*) as session_count
        FROM teacher_schedule
        GROUP BY teacher_username, exam_date
        HAVING session_count > 2
        ORDER BY session_count DESC
    ''')
    for row in cursor.fetchall():
        # Get teacher's max preference
        cursor.execute('''
            SELECT max_sessions_per_day FROM teacher_preferences WHERE teacher_username = ?
        ''', (row[0],))
        pref = cursor.fetchone()
        max_allowed = pref[0] if pref else 2

        if row[2] > max_allowed:
            conflicts['teacher_overload'].append({
                'teacher': row[0],
                'date': row[1],
                'sessions': row[2],
                'max_allowed': max_allowed
            })

    # 3. Check coverage gaps - sessions without enough teachers
    cursor.execute('SELECT COUNT(*) FROM room_configs')
    room_count = cursor.fetchone()[0] or 0

    if os.path.exists(csv_path):
        sessions = df.groupby(['ExamDate', 'ExamTime']).size().reset_index(name='count')
        for _, row in sessions.iterrows():
            cursor.execute('''
                SELECT COUNT(DISTINCT teacher_username)
                FROM teacher_schedule
                WHERE exam_date = ? AND exam_time = ?
            ''', (row['ExamDate'], row['ExamTime']))
            teacher_count = cursor.fetchone()[0]

            if teacher_count < room_count:
                conflicts['coverage_gaps'].append({
                    'date': row['ExamDate'],
                    'time': row['ExamTime'],
                    'rooms_needed': room_count,
                    'teachers_assigned': teacher_count,
                    'gap': room_count - teacher_count
                })

    # 4. Check consecutive session violations
    cursor.execute('''
        SELECT t1.teacher_username, t1.exam_date, t1.exam_time, t2.exam_time
        FROM teacher_schedule t1
        JOIN teacher_schedule t2 ON t1.teacher_username = t2.teacher_username
            AND t1.exam_date = t2.exam_date
        WHERE (t1.exam_time = 'Morning' AND t2.exam_time = 'Afternoon')
           OR (t1.exam_time = 'Afternoon' AND t2.exam_time = 'Evening')
        ORDER BY t1.teacher_username, t1.exam_date
    ''')
    for row in cursor.fetchall():
        conflicts['consecutive_violations'].append({
            'teacher': row[0],
            'date': row[1],
            'first_session': row[2],
            'second_session': row[3]
        })

    # Summary stats
    stats = {
        'total_teachers': 0,
        'total_sessions': 0,
        'total_rooms': room_count,
        'avg_sessions_per_teacher': 0
    }

    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'teacher'")
    stats['total_teachers'] = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM teacher_schedule")
    stats['total_sessions'] = cursor.fetchone()[0] or 0

    if stats['total_teachers'] > 0:
        stats['avg_sessions_per_teacher'] = round(stats['total_sessions'] / stats['total_teachers'], 1)

    # Teacher workload distribution
    cursor.execute('''
        SELECT teacher_username, COUNT(*) as session_count
        FROM teacher_schedule
        GROUP BY teacher_username
        ORDER BY session_count DESC
    ''')
    workload = [{'teacher': row[0], 'sessions': row[1]} for row in cursor.fetchall()]

    conn.close()
    return render_template('admin_conflicts_dashboard.html',
                         conflicts=conflicts,
                         stats=stats,
                         workload=workload)


# ============================================
# NOTIFICATION SYSTEM
# ============================================

@app.route('/admin/notifications')
@require_admin
def admin_notifications():
    """Admin view of notification queue and settings"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get recent notifications
    cursor.execute('''
        SELECT id, recipient_username, notification_type, subject, status, created_at, sent_at
        FROM notifications
        ORDER BY created_at DESC
        LIMIT 100
    ''')
    notifications = [{'id': row[0], 'recipient': row[1], 'type': row[2],
                     'subject': row[3], 'status': row[4], 'created': row[5], 'sent': row[6]}
                    for row in cursor.fetchall()]

    # Get notification settings
    cursor.execute("SELECT value FROM system_config WHERE key = 'email_enabled'")
    row = cursor.fetchone()
    email_enabled = row[0] == 'true' if row else False

    cursor.execute("SELECT value FROM system_config WHERE key = 'smtp_server'")
    row = cursor.fetchone()
    smtp_server = row[0] if row else ''

    conn.close()
    return render_template('admin_notifications.html',
                         notifications=notifications,
                         email_enabled=email_enabled,
                         smtp_server=smtp_server)


@app.route('/admin/notifications/settings', methods=['POST'])
@require_admin
def update_notification_settings():
    """Update email notification settings"""
    email_enabled = 'email_enabled' in request.form
    smtp_server = request.form.get('smtp_server', '')
    smtp_port = request.form.get('smtp_port', '587')
    smtp_username = request.form.get('smtp_username', '')
    smtp_password = request.form.get('smtp_password', '')
    sender_email = request.form.get('sender_email', '')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    settings = [
        ('email_enabled', 'true' if email_enabled else 'false'),
        ('smtp_server', smtp_server),
        ('smtp_port', smtp_port),
        ('smtp_username', smtp_username),
        ('sender_email', sender_email)
    ]

    # Only update password if provided
    if smtp_password:
        settings.append(('smtp_password', smtp_password))

    for key, value in settings:
        cursor.execute('''
            INSERT INTO system_config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        ''', (key, value))

    conn.commit()
    conn.close()
    flash('Notification settings updated!', 'success')
    return redirect(url_for('admin_notifications'))


@app.route('/admin/notifications/send_pending', methods=['POST'])
@require_admin
def send_pending_notifications():
    """Process and send pending notifications"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if email is enabled
    cursor.execute("SELECT value FROM system_config WHERE key = 'email_enabled'")
    row = cursor.fetchone()
    if not row or row[0] != 'true':
        flash('Email notifications are disabled', 'warning')
        conn.close()
        return redirect(url_for('admin_notifications'))

    # Get SMTP settings
    smtp_settings = {}
    for key in ['smtp_server', 'smtp_port', 'smtp_username', 'smtp_password', 'sender_email']:
        cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        smtp_settings[key] = row[0] if row else ''

    if not smtp_settings['smtp_server']:
        flash('SMTP server not configured', 'danger')
        conn.close()
        return redirect(url_for('admin_notifications'))

    # Get pending notifications with user emails
    cursor.execute('''
        SELECT n.id, n.recipient_username, u.email, n.subject, n.message
        FROM notifications n
        JOIN users u ON n.recipient_username = u.username
        WHERE n.status = 'pending' AND u.email IS NOT NULL AND u.email != ''
    ''')
    pending = cursor.fetchall()

    sent_count = 0
    error_count = 0

    for notif in pending:
        try:
            # Send email using smtplib
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg['From'] = smtp_settings['sender_email']
            msg['To'] = notif[2]
            msg['Subject'] = f"[ExamSeat] {notif[3]}"

            body = f"""
Hello {notif[1]},

{notif[4]}

---
This is an automated message from ExamSeat Invigilation System.
            """
            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP(smtp_settings['smtp_server'], int(smtp_settings['smtp_port']))
            server.starttls()
            if smtp_settings['smtp_username'] and smtp_settings['smtp_password']:
                server.login(smtp_settings['smtp_username'], smtp_settings['smtp_password'])
            server.sendmail(smtp_settings['sender_email'], notif[2], msg.as_string())
            server.quit()

            cursor.execute('''
                UPDATE notifications SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?
            ''', (notif[0],))
            sent_count += 1

        except Exception as e:
            cursor.execute('''
                UPDATE notifications SET status = 'failed', error_message = ? WHERE id = ?
            ''', (str(e), notif[0]))
            error_count += 1

    conn.commit()
    conn.close()

    if sent_count > 0:
        flash(f'Successfully sent {sent_count} notification(s)', 'success')
    if error_count > 0:
        flash(f'Failed to send {error_count} notification(s)', 'danger')
    if sent_count == 0 and error_count == 0:
        flash('No pending notifications to send', 'info')

    return redirect(url_for('admin_notifications'))


@app.route('/admin/notifications/schedule_reminders', methods=['POST'])
@require_admin
def schedule_exam_reminders():
    """Schedule reminder notifications for upcoming exams (24 hours before)"""
    from datetime import datetime, timedelta

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get tomorrow's date
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    # Get all teachers with sessions tomorrow
    cursor.execute('''
        SELECT DISTINCT ts.teacher_username, ts.room_name, ts.exam_date, ts.exam_time
        FROM teacher_schedule ts
        WHERE ts.exam_date = ?
    ''', (tomorrow,))

    reminders_created = 0
    for row in cursor.fetchall():
        # Check if reminder already exists
        cursor.execute('''
            SELECT id FROM notifications
            WHERE recipient_username = ? AND notification_type = 'exam_reminder'
            AND message LIKE ?
        ''', (row[0], f'%{row[1]}%{row[2]}%{row[3]}%'))

        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO notifications (recipient_username, notification_type, subject, message)
                VALUES (?, 'exam_reminder', 'Exam Invigilation Reminder',
                        ?)
            ''', (row[0], f'Reminder: You have invigilation duty tomorrow ({row[2]}) during {row[3]} session in {row[1]}. Please report 30 minutes before the exam.'))
            reminders_created += 1

    conn.commit()
    conn.close()

    flash(f'Scheduled {reminders_created} reminder notification(s) for tomorrow', 'success')
    return redirect(url_for('admin_notifications'))


@app.route('/teacher/notifications')
def teacher_notifications():
    """Teacher view of their notifications"""
    if 'username' not in session or session.get('role') != 'teacher':
        flash('Please login as teacher', 'danger')
        return redirect(url_for('login'))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, notification_type, subject, message, status, created_at
        FROM notifications
        WHERE recipient_username = ?
        ORDER BY created_at DESC
        LIMIT 50
    ''', (session['username'],))

    notifications = [{'id': row[0], 'type': row[1], 'subject': row[2],
                     'message': row[3], 'status': row[4], 'created': row[5]}
                    for row in cursor.fetchall()]

    conn.close()
    return render_template('teacher_notifications.html',
                         username=session['username'],
                         notifications=notifications)


def get_student_seating_info(student_id):
    """
    Get student's room and seat assignment from the exports CSV files.
    Returns: dict with 'room', 'seat_no', 'seat_x', 'seat_y' or None if not found
    """
    exports_dir = 'exports'

    if not os.path.exists(exports_dir):
        return None

    csv_files = glob.glob(os.path.join(exports_dir, "*_seating.csv"))
    if not csv_files:
        return None

    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)
            if 'StudentID' in df.columns:
                df['StudentID'] = df['StudentID'].astype(str)
                student_row = df[df['StudentID'] == str(student_id)]

                if not student_row.empty:
                    row_data = student_row.iloc[0]
                    # Handle both old and new column naming conventions
                    seat_no = row_data.get('SeatNo', row_data.get('Seat_No', 'Unknown'))
                    seat_x = row_data.get('Position_X', row_data.get('Seat_X', 'Unknown'))
                    seat_y = row_data.get('Position_Y', row_data.get('Seat_Y', 'Unknown'))
                    return {
                        'room': row_data.get('Room', 'Unknown'),
                        'seat_no': seat_no,
                        'seat_x': seat_x,
                        'seat_y': seat_y
                    }
        except Exception:
            continue

    return None

def get_student_seating_for_session(student_id, exam_date, exam_time):
    """
    Get student's seating for a specific exam session (date + time).
    Looks for session-specific export files like Room-A_20250620_Morning_seating.csv
    """
    exports_dir = 'exports'

    if not os.path.exists(exports_dir):
        return None

    # Create session key (same format as main.py)
    safe_date = exam_date.replace('-', '') if exam_date else ''
    session_pattern = f"*_{safe_date}_{exam_time}_seating.csv"

    csv_files = glob.glob(os.path.join(exports_dir, session_pattern))

    # Also check for general seating files
    if not csv_files:
        csv_files = glob.glob(os.path.join(exports_dir, "*_seating.csv"))

    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)
            if 'StudentID' in df.columns:
                df['StudentID'] = df['StudentID'].astype(str)
                student_row = df[df['StudentID'] == str(student_id)]

                if not student_row.empty:
                    row_data = student_row.iloc[0]
                    seat_no = row_data.get('SeatNo', row_data.get('Seat_No', 'Unknown'))
                    seat_x = row_data.get('Position_X', row_data.get('Seat_X', 'Unknown'))
                    seat_y = row_data.get('Position_Y', row_data.get('Seat_Y', 'Unknown'))
                    return {
                        'room': row_data.get('Room', 'Unknown'),
                        'seat_no': seat_no,
                        'seat_x': seat_x,
                        'seat_y': seat_y
                    }
        except Exception:
            continue

    return None

def get_room_seating_data(room_name):
    """Get all students assigned to a specific room."""
    exports_dir = 'exports'
    csv_file = os.path.join(exports_dir, f"{room_name}_seating.csv")

    if os.path.exists(csv_file):
        try:
            return pd.read_csv(csv_file)
        except Exception:
            return None
    return None


def refresh_seating_exports():
    """Regenerate all seating CSV exports from current session data."""
    final_seating_layout = session.get('final_seating_layout')
    student_metadata = session.get('student_metadata')

    if not final_seating_layout or not student_metadata:
        return False

    exports_dir = 'exports'
    os.makedirs(exports_dir, exist_ok=True)

    for room_name, room_seats in final_seating_layout.items():
        if not room_seats:
            continue

        room_data = []
        for seat in room_seats:
            student_id = seat['student_id']
            info = student_metadata.get(student_id, {})
            room_data.append({
                'StudentID': student_id,
                'Name': info.get('Name', 'Unknown'),
                'Department': info.get('Department', 'Unknown'),
                'Branch': info.get('Branch', 'Unknown'),
                'Year': info.get('Year', 'Unknown'),
                'Subject': info.get('Subject', 'Unknown'),
                'ExamDate': info.get('ExamDate', 'Unknown'),
                'ExamTime': info.get('ExamTime', 'Unknown'),
                'Room': room_name,
                'Seat_X': seat['x'],
                'Seat_Y': seat['y'],
                'Seat_No': seat['seat_no']
            })

        if room_data:
            df = pd.DataFrame(room_data)
            csv_path = os.path.join(exports_dir, f"{room_name}_seating.csv")
            df.to_csv(csv_path, index=False)

    return True

@app.route('/student_dashboard/<student_id>')
@require_login
def student_dashboard(student_id):
    if session['role'] == 'student' and session['username'] != student_id:
        flash('Access denied. You can only view your own dashboard.', 'danger')
        return redirect(url_for('student_dashboard', student_id=session['username']))

    # Get student info from CSV (for profile)
    student_info = get_student_by_id(student_id)

    if not student_info:
        session.clear()
        flash('Student record not found in system. Please contact admin or register again.', 'warning')
        return redirect(url_for('login'))

    # Get ALL exam records for this student
    all_exams = get_all_student_exams(student_id)

    # Placeholder for QR code generation
    qr_path = None
    if 'qr_code_data' in session and session['qr_code_data'].get('student_id') == student_id:
        qr_path = session['qr_code_data'].get('path')

    # Build exam list with seating info for each exam
    exams = []
    for exam_record in all_exams:
        exam_date = exam_record.get('ExamDate', '')
        exam_time = exam_record.get('ExamTime', '')

        # Get seating info for this specific exam session
        seating_info = get_student_seating_for_session(student_id, exam_date, exam_time)

        room_info = 'TBD'
        seat_info = 'TBD'
        if seating_info:
            room_info = seating_info['room']
            seat_info = f"#Seat {seating_info['seat_no']} (Position: {seating_info['seat_x']}, {seating_info['seat_y']})"

        exams.append({
            'subject': exam_record.get('Subject', 'N/A'),
            'department': exam_record.get('Department', 'N/A'),
            'date': exam_date,
            'time': exam_time,
            'room': room_info,
            'seat_no': seat_info
        })

    # Get first seating info for backward compatibility
    seating_info = get_student_seating_info(student_id) if exams else None

    return render_template('student_dashboard.html',
                           name=student_info.get('Name', 'Student'),
                           student_id=student_id,
                           photo_path=student_info.get('PhotoPath'),
                           qr_path=qr_path,
                           exams=exams,
                           seating_info=seating_info)


def get_student_by_id(student_id):
    """Get student info from CSV by StudentID (first record for profile)."""
    try:
        df = pd.read_csv(CSV_PATH)
        if 'Branch' not in df.columns and 'Batch' in df.columns:
            df['Branch'] = df['Batch']

        df['StudentID'] = df['StudentID'].astype(str)
        student = df[df['StudentID'] == str(student_id)]

        if not student.empty:
            return student.iloc[0].to_dict()
        return None
    except Exception:
        return None

def get_all_student_exams(student_id):
    """Get ALL exam records for a student from CSV."""
    try:
        df = pd.read_csv(CSV_PATH)
        if 'Branch' not in df.columns and 'Batch' in df.columns:
            df['Branch'] = df['Batch']

        df['StudentID'] = df['StudentID'].astype(str)
        student_exams = df[df['StudentID'] == str(student_id)]

        if not student_exams.empty:
            return student_exams.to_dict('records')
        return []
    except Exception:
        return []

@app.route('/generate_qr_code/<student_id>', methods=['POST'])
@require_login
def generate_qr_code(student_id):
    # Ensure student_id is for the logged-in student or admin is requesting
    if session['role'] == 'student' and session['username'] != student_id:
        flash('Unauthorized QR code generation request.', 'danger')
        return redirect(url_for('student_dashboard', student_id=session['username']))

    qr_data = f"StudentID:{student_id}|ExamSystem"
    qr_filename = f"student_{student_id}_qr.svg"
    qr_filepath = os.path.join(QR_FOLDER, qr_filename)

    try:
        img = qrcode.make(qr_data, image_factory=qrcode.image.svg.SvgImage)
        with open(qr_filepath, "wb") as f:
            img.save(f)
        qr_url = url_for('static', filename=f'qrcodes/{qr_filename}')
        session['qr_code_data'] = {'student_id': student_id, 'path': qr_url}
        flash('QR Code generated successfully!', 'success')
    except Exception as e:
        flash(f'Error generating QR Code: {e}', 'danger')

    if session['role'] == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('student_dashboard', student_id=student_id))

@app.route('/process_seating_plan', methods=['POST'])
@require_teacher
def process_seating_plan():
    global df_students

    uploaded_file = request.files.get('student_data_file')
    if uploaded_file and uploaded_file.filename != '':
        file_path = os.path.join(UPLOAD_FOLDER, 'students.csv')
        uploaded_file.save(file_path)
        df_students = pd.read_csv(file_path)
        flash('Student data uploaded and reloaded successfully!', 'success')
    else:
        flash('Using existing student data.', 'info')
        df_students = load_student_data()

    if df_students.empty:
        flash('No student data available to generate seating plan.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    # Load room configurations from database (dynamic)
    current_rooms_config = get_rooms_config_from_db()
    
    if not current_rooms_config:
        flash('No room configurations found. Please configure rooms in the admin dashboard.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    try:
        # Step 1: Extract student metadata
        student_metadata = extract_student_metadata(df_students)
        print("✅ Student metadata extracted.")

        # Step 2: Get colored groups (conflict resolution)
        colored_groups = get_colored_groups(student_metadata)
        print(f"✅ Generated {len(colored_groups)} conflict-free groups.")

        # Step 3: Assign rooms to groups
        room_assignments = assign_rooms_to_groups(colored_groups, student_metadata, current_rooms_config)
        print("✅ Rooms assigned to groups.")

        # Step 4: Assign seats within rooms
        final_seating_layout = assign_seats_in_room(room_assignments, student_metadata, {r['room_name']:r for r in current_rooms_config})
        print("✅ Seats assigned within rooms.")

        # Store results in session
        session['final_seating_layout'] = final_seating_layout
        session['student_metadata'] = student_metadata
        session['rooms_config_for_seating'] = current_rooms_config

        # Step 5: Automatically generate CSV exports
        print("🔄 Generating CSV exports...")
        exports_dir = 'exports'
        os.makedirs(exports_dir, exist_ok=True)
        
        exported_rooms = []
        for room_name, seats in final_seating_layout.items():
            if seats:  # Only export rooms with students
                room_data = []
                for seat in seats:
                    student_id = seat['student_id']
                    info = student_metadata.get(student_id, {})
                    room_data.append({
                        'StudentID': student_id,
                        'Name': info.get('Name', 'Unknown'),
                        'Department': info.get('Department', 'Unknown'),
                        'Branch': info.get('Branch', 'Unknown'),
                        'Year': info.get('Year', 'Unknown'),
                        'Subject': info.get('Subject', 'Unknown'),
                        'ExamDate': info.get('ExamDate', 'Unknown'),
                        'ExamTime': info.get('ExamTime', 'Unknown'),
                        'Room': room_name,
                        'Seat_X': seat['x'],
                        'Seat_Y': seat['y'],
                        'Seat_No': seat['seat_no']
                    })
                
                if room_data:
                    df_export = pd.DataFrame(room_data)
                    csv_path = os.path.join(exports_dir, f"{room_name}_seating.csv")
                    df_export.to_csv(csv_path, index=False)
                    exported_rooms.append(room_name)
                    print(f"✅ Exported {room_name} with {len(room_data)} students")
        
        print(f"✅ Generated CSV exports for {len(exported_rooms)} rooms: {exported_rooms}")
        flash(f'Seating plan generated successfully! CSV exports created for {len(exported_rooms)} rooms.', 'success')
        return redirect(url_for('view_seating_results'))

    except Exception as e:
        flash(f'Error generating seating plan: {e}', 'danger')
        return redirect(url_for('teacher_dashboard'))

@app.route('/api/student_seating/<student_id>')
@require_login
def api_get_student_seating(student_id):
    """
    API endpoint to get student's seating information
    """
    # Security check - students can only access their own data
    if session['role'] == 'student' and session['username'] != student_id:
        return jsonify({'error': 'Access denied'}), 403
    
    seating_info = get_student_seating_info(student_id)
    
    if seating_info:
        return jsonify({
            'success': True,
            'student_id': student_id,
            'seating_info': seating_info
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Seating assignment not found'
        })

@app.route('/api/refresh_seating_exports', methods=['POST'])
@require_teacher
def api_refresh_seating_exports():
    """
    API endpoint to refresh/regenerate seating CSV exports
    Only teachers can trigger this
    """
    success = refresh_seating_exports()
    
    if success:
        return jsonify({
            'success': True,
            'message': 'Seating exports refreshed successfully'
        })
    else:
        return jsonify({
            'success': False,
            'message': 'No seating data available to export'
        })

@app.route('/api/room_students/<room_name>')
@require_login  
def api_get_room_students(room_name):
    """
    API endpoint to get all students in a specific room
    """
    room_data = get_room_seating_data(room_name)
    
    if room_data is not None:
        students = room_data.to_dict('records')
        return jsonify({
            'success': True,
            'room_name': room_name,
            'students': students,
            'total_students': len(students)
        })
    else:
        return jsonify({
            'success': False,
            'message': f'No seating data found for room {room_name}'
        })

@app.route('/generate_seating_exports', methods=['POST'])
@require_teacher
def generate_seating_exports():
    """
    Manually trigger generation of seating CSV exports
    """
    success = refresh_seating_exports()
    
    if success:
        flash('Seating exports generated successfully!', 'success')
    else:
        flash('No seating data available. Please generate a seating plan first.', 'warning')
    
    return redirect(url_for('view_seating_results'))

@app.route('/view_seating_results')
@require_teacher
def view_seating_results():
    final_seating_layout = session.get('final_seating_layout')
    student_metadata = session.get('student_metadata')
    rooms_config_for_seating = session.get('rooms_config_for_seating')

    if not final_seating_layout or not student_metadata or not rooms_config_for_seating:
        flash('No seating plan found. Please generate one first.', 'info')
        return redirect(url_for('teacher_dashboard'))

    # Generate HTML visualizations and collect links
    visualization_links = []
    output_dir = 'visualizations'
    os.makedirs(output_dir, exist_ok=True)

    for room_name, seats in final_seating_layout.items():
        room_config = next((r for r in rooms_config_for_seating if r['room_name'] == room_name), None)
        if room_config and seats:
            html_content = create_simple_html_visualization(
                room_name=room_name,
                seating_arrangement=seats,
                metadata=student_metadata,
                room_config=room_config
            )
            html_filename = f"{room_name}.html"
            with open(os.path.join(output_dir, html_filename), "w") as f:
                f.write(html_content)
            visualization_links.append({'room_name': room_name, 'url': url_for('static_html', filename=html_filename)})

    # Create index page
    room_names_list = [link['room_name'] for link in visualization_links]
    if room_names_list:
        index_html_content = create_index_page(room_names_list, final_seating_layout, student_metadata)
        with open(os.path.join(output_dir, "index.html"), "w") as f:
            f.write(index_html_content)
        visualization_links.append({'room_name': 'Overall Dashboard', 'url': url_for('static_html', filename='index.html')})

    return render_template('seating_results.html', visualization_links=visualization_links)

@app.route('/static_html/<path:filename>')
def static_html(filename):
    return send_from_directory('visualizations', filename)

@app.route('/export_room_csv/<room_name>')
@require_teacher
def export_room_csv(room_name):
    final_seating_layout = session.get('final_seating_layout')
    student_metadata = session.get('student_metadata')

    if not final_seating_layout or not student_metadata:
        flash('No seating plan available to export.', 'danger')
        return redirect(url_for('view_seating_results'))

    room_seats = final_seating_layout.get(room_name)
    if not room_seats:
        flash(f'No seating information for {room_name}.', 'info')
        return redirect(url_for('view_seating_results'))

    room_data = []
    for seat in room_seats:
        student_id = seat['student_id']
        info = student_metadata.get(student_id, {})
        room_data.append({
            'StudentID': student_id,
            'Name': info.get('Name', 'Unknown'),
            'Department': info.get('Department', 'Unknown'),
            'Branch': info.get('Branch', 'Unknown'),
            'Year': info.get('Year', 'Unknown'),
            'Subject': info.get('Subject', 'Unknown'),
            'ExamDate': info.get('ExamDate', 'Unknown'),
            'ExamTime': info.get('ExamTime', 'Unknown'),
            'Room': room_name,
            'Seat_X': seat['x'],
            'Seat_Y': seat['y'],
            'Seat_No': seat['seat_no']
        })

    df = pd.DataFrame(room_data)
    exports_dir = 'exports'
    os.makedirs(exports_dir, exist_ok=True)
    csv_path = os.path.join(exports_dir, f"{room_name}_seating.csv")
    df.to_csv(csv_path, index=False)

    return send_from_directory(exports_dir, f"{room_name}_seating.csv", as_attachment=True)

@app.route('/get_student_details/<student_id>')
@require_login
def get_student_details(student_id):
    metadata = session.get('student_metadata')
    if not metadata:
        df = load_student_data()
        metadata = extract_student_metadata(df)
        session['student_metadata'] = metadata

    student_info = metadata.get(student_id)
    if student_info:
        return jsonify(student_info)
    return jsonify({'error': 'Student not found'}), 404

@app.route('/room_config/<room_id>', methods=['GET', 'POST'])
@require_admin
def room_config(room_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        max_subjects = request.form.get('max_subjects', type=int)
        max_branches = request.form.get('max_branches', type=int)
        allowed_years = ','.join(request.form.getlist('allowed_years'))
        allowed_branches = ','.join(request.form.getlist('allowed_branches'))
        
        cursor.execute('''
            UPDATE room_configs
            SET max_subjects = ?, max_branches = ?, allowed_years = ?, allowed_branches = ?
            WHERE id = ?
        ''', (max_subjects, max_branches, allowed_years, allowed_branches, room_id))
        conn.commit()
        flash('Room constraints updated successfully', 'success')
    
    cursor.execute('SELECT * FROM room_configs WHERE id = ?', (room_id,))
    room = cursor.fetchone()
    conn.close()
    
    return render_template('room_config.html',
                         room=room,
                         year_options=range(1, 5),
                         branch_options=['CS', 'EE', 'ME', 'CE'])

@app.route('/api/room/constraints/<room_name>')
@require_admin
def get_room_constraints(room_name):
    """API endpoint for room constraints"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT max_subjects, max_branches, allowed_years, allowed_branches 
        FROM room_configs 
        WHERE room_name = ?
    ''', (room_name,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return jsonify({
            'max_subjects': result[0],
            'max_branches': result[1],
            'allowed_years': result[2].split(',') if result[2] else [],
            'allowed_branches': result[3].split(',') if result[3] else []
        })
    return jsonify({'error': 'Room not found'}), 404

# ============================================
# STUDENT RELATIONSHIPS ROUTES (PostgreSQL)
# ============================================

@app.route('/admin/relationships')
@require_admin
def admin_relationships():
    """View and manage student relationships for cheat prevention."""
    try:
        from models import db, StudentRelationship, Student

        relationships = db.session.query(
            StudentRelationship,
            Student
        ).join(
            Student, Student.id == StudentRelationship.student1_id
        ).filter(
            StudentRelationship.is_active == True
        ).order_by(StudentRelationship.created_at.desc()).all()

        # Get all students for dropdown
        students = Student.query.filter_by(is_active=True).order_by(Student.name).all()

        return render_template('admin_relationships.html',
                             relationships=relationships,
                             students=students)
    except ImportError:
        flash('PostgreSQL models not configured. Using SQLite fallback.', 'warning')
        return render_template('admin_relationships.html',
                             relationships=[],
                             students=[],
                             fallback=True)

@app.route('/admin/relationships/add', methods=['POST'])
@require_admin
def add_relationship():
    """Add a student relationship."""
    try:
        from models import db, StudentRelationship, RelationshipType

        student1_id = request.form.get('student1_id', type=int)
        student2_id = request.form.get('student2_id', type=int)
        rel_type = request.form.get('relationship_type', 'friend')
        notes = request.form.get('notes', '')

        if student1_id == student2_id:
            flash('Cannot create relationship between same student.', 'danger')
            return redirect(url_for('admin_relationships'))

        rel_type_enum = RelationshipType(rel_type)
        user_id = session.get('user_id')

        # Verify user exists before using as reported_by
        from models import User
        if user_id and not User.query.get(user_id):
            user_id = None

        StudentRelationship.add_relationship(
            student1_id, student2_id,
            rel_type=rel_type_enum,
            reported_by=user_id,
            notes=notes
        )
        db.session.commit()

        flash('Relationship added successfully.', 'success')
    except Exception as e:
        flash(f'Error adding relationship: {str(e)}', 'danger')

    return redirect(url_for('admin_relationships'))

@app.route('/admin/relationships/bulk', methods=['POST'])
@require_admin
def bulk_import_relationships():
    """Bulk import relationships from CSV."""
    try:
        from models import db, StudentRelationship, Student, RelationshipType

        if 'csv_file' not in request.files:
            flash('No file uploaded.', 'danger')
            return redirect(url_for('admin_relationships'))

        file = request.files['csv_file']
        if file.filename == '':
            flash('No file selected.', 'danger')
            return redirect(url_for('admin_relationships'))

        # Read CSV
        import_df = pd.read_csv(file)
        required_cols = ['student1_id', 'student2_id']

        if not all(col in import_df.columns for col in required_cols):
            flash('CSV must have student1_id and student2_id columns.', 'danger')
            return redirect(url_for('admin_relationships'))

        imported = 0
        errors = []
        user_id = session.get('user_id')

        # Verify user exists before using as reported_by
        from models import User
        if user_id and not User.query.get(user_id):
            user_id = None

        for _, row in import_df.iterrows():
            try:
                # Look up students by student_id string
                s1 = Student.query.filter_by(student_id=str(row['student1_id'])).first()
                s2 = Student.query.filter_by(student_id=str(row['student2_id'])).first()

                if not s1 or not s2:
                    errors.append(f"Student not found: {row['student1_id']} or {row['student2_id']}")
                    continue

                rel_type = RelationshipType(row.get('type', 'friend'))
                notes = row.get('notes', '')

                StudentRelationship.add_relationship(
                    s1.id, s2.id,
                    rel_type=rel_type,
                    reported_by=user_id,
                    notes=notes
                )
                imported += 1
            except Exception as e:
                errors.append(str(e))

        db.session.commit()
        flash(f'Imported {imported} relationships. {len(errors)} errors.', 'success')

    except Exception as e:
        flash(f'Error importing relationships: {str(e)}', 'danger')

    return redirect(url_for('admin_relationships'))

@app.route('/admin/relationships/delete/<int:rel_id>', methods=['POST'])
@require_admin
def delete_relationship(rel_id):
    """Deactivate a student relationship."""
    try:
        from models import db, StudentRelationship

        rel = StudentRelationship.query.get(rel_id)
        if rel:
            rel.is_active = False
            db.session.commit()
            flash('Relationship removed.', 'success')
        else:
            flash('Relationship not found.', 'danger')
    except Exception as e:
        flash(f'Error removing relationship: {str(e)}', 'danger')

    return redirect(url_for('admin_relationships'))

# ============================================
# AUDIT LOGS ROUTES (PostgreSQL)
# ============================================

@app.route('/admin/audit_logs')
@require_admin
def admin_audit_logs():
    """View audit logs with filtering."""
    try:
        from models import db, AuditLog, AuditAction

        # Get filter parameters
        action_filter = request.args.get('action')
        table_filter = request.args.get('table')
        page = request.args.get('page', 1, type=int)
        per_page = 50

        query = AuditLog.query.order_by(AuditLog.created_at.desc())

        if action_filter:
            query = query.filter(AuditLog.action == AuditAction(action_filter))
        if table_filter:
            query = query.filter(AuditLog.table_name == table_filter)

        # Paginate
        logs = query.paginate(page=page, per_page=per_page, error_out=False)

        # Get unique tables for filter dropdown
        tables = db.session.query(AuditLog.table_name).distinct().all()
        table_names = [t[0] for t in tables if t[0]]

        return render_template('admin_audit_logs.html',
                             logs=logs,
                             actions=[a.value for a in AuditAction],
                             tables=table_names,
                             current_action=action_filter,
                             current_table=table_filter)
    except ImportError:
        flash('PostgreSQL models not configured.', 'warning')
        return render_template('admin_audit_logs.html',
                             logs=None,
                             actions=[],
                             tables=[],
                             fallback=True)

# ============================================
# ANALYTICS DASHBOARD ROUTES (PostgreSQL)
# ============================================

@app.route('/admin/analytics')
@require_admin
def admin_analytics():
    """Analytics dashboard with statistics."""
    try:
        from models import db

        # Execute views to get analytics data
        system_stats = db.session.execute(
            db.text("SELECT * FROM v_system_stats")
        ).fetchone()

        todays_exams = db.session.execute(
            db.text("SELECT * FROM v_todays_exams")
        ).fetchall()

        room_util = db.session.execute(
            db.text("SELECT * FROM v_room_utilization LIMIT 10")
        ).fetchall()

        dept_stats = db.session.execute(
            db.text("SELECT * FROM v_department_stats")
        ).fetchall()

        cheat_summary = db.session.execute(
            db.text("SELECT * FROM v_cheat_flags_summary ORDER BY exam_date DESC LIMIT 10")
        ).fetchall()

        recent_activity = db.session.execute(
            db.text("SELECT * FROM v_recent_audit_activity LIMIT 20")
        ).fetchall()

        return render_template('admin_analytics.html',
                             system_stats=system_stats,
                             todays_exams=todays_exams,
                             room_utilization=room_util,
                             department_stats=dept_stats,
                             cheat_summary=cheat_summary,
                             recent_activity=recent_activity)
    except Exception as e:
        flash(f'Analytics not available: {str(e)}', 'warning')
        return render_template('admin_analytics.html',
                             system_stats=None,
                             fallback=True)

@app.route('/api/analytics/exam/<int:exam_id>')
@require_admin
def api_exam_analytics(exam_id):
    """API endpoint for exam-specific analytics."""
    try:
        from models import db, Exam

        exam = Exam.query.get(exam_id)
        if not exam:
            return jsonify({'error': 'Exam not found'}), 404

        stats = exam.get_seating_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analytics/room_utilization')
@require_admin
def api_room_utilization():
    """API endpoint for room utilization data."""
    try:
        from models import db

        start_date = request.args.get('start_date', '2025-01-01')
        end_date = request.args.get('end_date', '2025-12-31')

        result = db.session.execute(
            db.text("SELECT * FROM get_room_utilization(:start, :end)"),
            {'start': start_date, 'end': end_date}
        ).fetchall()

        data = [dict(row._mapping) for row in result]
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================
# CHEAT FLAG REVIEW ROUTES (PostgreSQL)
# ============================================

@app.route('/admin/cheat_flags')
@require_admin
def admin_cheat_flags():
    """Review cheat detection flags."""
    try:
        from models import db, CheatDetectionFlag, Exam, Student

        reviewed = request.args.get('reviewed', 'false') == 'true'

        flags = db.session.query(
            CheatDetectionFlag, Exam, Student
        ).join(
            Exam, Exam.id == CheatDetectionFlag.exam_id
        ).join(
            Student, Student.id == CheatDetectionFlag.student1_id
        ).filter(
            CheatDetectionFlag.reviewed == reviewed
        ).order_by(
            CheatDetectionFlag.created_at.desc()
        ).limit(100).all()

        return render_template('admin_cheat_flags.html',
                             flags=flags,
                             showing_reviewed=reviewed)
    except Exception as e:
        flash(f'Error loading flags: {str(e)}', 'warning')
        return render_template('admin_cheat_flags.html',
                             flags=[],
                             fallback=True)

@app.route('/admin/cheat_flags/review/<int:flag_id>', methods=['POST'])
@require_admin
def review_cheat_flag(flag_id):
    """Mark a cheat flag as reviewed."""
    try:
        from models import db, CheatDetectionFlag

        flag = CheatDetectionFlag.query.get(flag_id)
        if flag:
            notes = request.form.get('notes', '')
            flag.mark_reviewed(session.get('user_id'), notes)
            db.session.commit()
            flash('Flag marked as reviewed.', 'success')
        else:
            flash('Flag not found.', 'danger')
    except Exception as e:
        flash(f'Error reviewing flag: {str(e)}', 'danger')

    return redirect(url_for('admin_cheat_flags'))

# ============================================
# EXAM CRUD ROUTES (PostgreSQL)
# ============================================

@app.route('/admin/exams')
@require_admin
def admin_exams():
    """List all exams with filtering."""
    try:
        from models import db, Exam, Department, ExamTimeSlot

        # Get filter parameters
        dept_filter = request.args.get('department')
        date_filter = request.args.get('date')
        page = request.args.get('page', 1, type=int)
        per_page = 20

        query = Exam.query.filter_by(is_active=True).order_by(Exam.exam_date.desc())

        if dept_filter:
            query = query.filter(Exam.department_id == int(dept_filter))
        if date_filter:
            query = query.filter(Exam.exam_date == date_filter)

        exams = query.paginate(page=page, per_page=per_page, error_out=False)
        departments = Department.query.order_by(Department.name).all()

        return render_template('admin_exams.html',
                             exams=exams,
                             departments=departments,
                             time_slots=[e.value for e in ExamTimeSlot],
                             current_dept=dept_filter,
                             current_date=date_filter)
    except Exception as e:
        flash(f'Error loading exams: {str(e)}', 'danger')
        return render_template('admin_exams.html', exams=None, fallback=True)


@app.route('/admin/exams/create-test-all-students', methods=['POST'])
@require_admin
def create_test_exam_all_students():
    """Create a test exam that includes ALL students for testing purposes."""
    try:
        from models import db, Exam, ExamTimeSlot
        from datetime import datetime, timedelta

        # Get form data or use defaults
        subject = request.form.get('subject', 'Test Exam - All Students')
        exam_date_str = request.form.get('exam_date')
        exam_time = request.form.get('exam_time', 'Morning')

        # Default to tomorrow if no date provided
        if not exam_date_str:
            exam_date = (datetime.now() + timedelta(days=1)).date()
            exam_date_str = exam_date.strftime('%Y-%m-%d')
        else:
            exam_date = datetime.strptime(exam_date_str, '%Y-%m-%d').date()

        # Generate unique exam code
        timestamp = datetime.now().strftime('%H%M%S')
        exam_code = f"TEST-ALL-{timestamp}"

        # Map time slot
        time_map = {
            'Morning': ExamTimeSlot.MORNING,
            'Afternoon': ExamTimeSlot.AFTERNOON,
            'Evening': ExamTimeSlot.EVENING
        }
        exam_time_enum = time_map.get(exam_time, ExamTimeSlot.MORNING)

        # Create exam in PostgreSQL
        with app.app_context():
            exam = Exam(
                exam_code=exam_code,
                name=subject,
                subject=subject,
                exam_date=exam_date,
                exam_time=exam_time_enum,
                duration_minutes=180,
                is_active=True
            )
            db.session.add(exam)
            db.session.commit()

        # Get all unique students from CSV and add this exam for each
        df = pd.read_csv(CSV_PATH)
        unique_students = df.drop_duplicates(subset=['StudentID']).copy()

        # Create new rows for all students with this exam
        new_rows = []
        for _, student in unique_students.iterrows():
            new_row = {
                'StudentID': student['StudentID'],
                'Name': student['Name'],
                'Department': student.get('Department', 'CS'),
                'Branch': student.get('Branch', 'CSE'),
                'Section': student.get('Section', 'A'),
                'Year': student.get('Year', 2),
                'Semester': student.get('Semester', 4),
                'Subject': subject,
                'ExamDate': exam_date_str,
                'ExamTime': exam_time
            }
            new_rows.append(new_row)

        # Append new rows to CSV
        new_df = pd.DataFrame(new_rows)
        df = pd.concat([df, new_df], ignore_index=True)
        df.to_csv(CSV_PATH, index=False)

        flash(f'Test exam created! {len(new_rows)} students enrolled in "{subject}" on {exam_date_str} ({exam_time}). Run seating algorithm to generate layouts.', 'success')

    except Exception as e:
        flash(f'Error creating test exam: {str(e)}', 'danger')

    return redirect(url_for('admin_exams'))


@app.route('/admin/sync_exams', methods=['POST'])
@require_admin
def admin_sync_exams():
    """Manually trigger sync of exams and students from CSV to database"""
    try:
        sync_exams_from_csv()
        flash('Exams and students synced from CSV successfully!', 'success')
    except Exception as e:
        flash(f'Error syncing: {str(e)}', 'danger')
    return redirect(url_for('admin_exams'))


@app.route('/admin/generate_seating', methods=['POST'])
@require_admin
def admin_generate_seating():
    """Manually trigger seating layout generation"""
    try:
        generate_seating_visualizations()
        flash('Seating layouts generated successfully! Check the seating dashboard.', 'success')
    except Exception as e:
        flash(f'Error generating seating: {str(e)}', 'danger')
    return redirect(url_for('admin_exams'))


@app.route('/admin/exams/add', methods=['GET', 'POST'])
@require_admin
def add_exam():
    """Create a new exam."""
    try:
        from models import db, Exam, Department, ExamTimeSlot

        if request.method == 'POST':
            exam_code = request.form.get('exam_code', '').strip()
            name = request.form.get('name', '').strip()
            subject = request.form.get('subject', '').strip()
            department_id = request.form.get('department_id', type=int)
            exam_date = request.form.get('exam_date')
            exam_time = request.form.get('exam_time')
            duration = request.form.get('duration_minutes', 180, type=int)

            if not all([exam_code, name, subject, exam_date, exam_time]):
                flash('Please fill all required fields.', 'danger')
                departments = Department.query.order_by(Department.name).all()
                return render_template('admin_exam_form.html',
                                     departments=departments,
                                     time_slots=[e.value for e in ExamTimeSlot],
                                     exam=None)

            # Check for duplicate exam_code
            existing = Exam.query.filter_by(exam_code=exam_code).first()
            if existing:
                flash('Exam code already exists.', 'danger')
                departments = Department.query.order_by(Department.name).all()
                return render_template('admin_exam_form.html',
                                     departments=departments,
                                     time_slots=[e.value for e in ExamTimeSlot],
                                     exam=None)

            exam = Exam(
                exam_code=exam_code,
                name=name,
                subject=subject,
                department_id=department_id if department_id else None,
                exam_date=datetime.strptime(exam_date, '%Y-%m-%d').date(),
                exam_time=ExamTimeSlot(exam_time),
                duration_minutes=duration,
                created_by=session.get('user_id')
            )
            db.session.add(exam)
            db.session.commit()
            flash(f'Exam "{name}" created successfully.', 'success')
            return redirect(url_for('admin_exams'))

        departments = Department.query.order_by(Department.name).all()
        return render_template('admin_exam_form.html',
                             departments=departments,
                             time_slots=[e.value for e in ExamTimeSlot],
                             exam=None)
    except Exception as e:
        flash(f'Error creating exam: {str(e)}', 'danger')
        return redirect(url_for('admin_exams'))


@app.route('/admin/exams/<int:exam_id>')
@require_admin
def view_exam(exam_id):
    """View exam details with enrollments and seating."""
    try:
        from models import db, Exam, ExamEnrollment, SeatingAssignment, Student

        exam = Exam.query.get_or_404(exam_id)

        # Get enrolled students
        enrollments = db.session.query(
            ExamEnrollment, Student
        ).join(Student).filter(
            ExamEnrollment.exam_id == exam_id
        ).order_by(Student.name).all()

        # Get seating assignments
        assignments = db.session.query(
            SeatingAssignment, Student
        ).join(Student).filter(
            SeatingAssignment.exam_id == exam_id
        ).order_by(SeatingAssignment.room_id, SeatingAssignment.seat_number).all()

        stats = exam.get_seating_stats()

        return render_template('admin_exam_detail.html',
                             exam=exam,
                             enrollments=enrollments,
                             assignments=assignments,
                             stats=stats)
    except Exception as e:
        flash(f'Error loading exam: {str(e)}', 'danger')
        return redirect(url_for('admin_exams'))


@app.route('/admin/exams/<int:exam_id>/edit', methods=['GET', 'POST'])
@require_admin
def edit_exam(exam_id):
    """Edit an existing exam."""
    try:
        from models import db, Exam, Department, ExamTimeSlot

        exam = Exam.query.get_or_404(exam_id)

        if request.method == 'POST':
            exam.name = request.form.get('name', '').strip()
            exam.subject = request.form.get('subject', '').strip()
            exam.department_id = request.form.get('department_id', type=int) or None
            exam.exam_date = datetime.strptime(request.form.get('exam_date'), '%Y-%m-%d').date()
            exam.exam_time = ExamTimeSlot(request.form.get('exam_time'))
            exam.duration_minutes = request.form.get('duration_minutes', 180, type=int)

            db.session.commit()
            flash(f'Exam "{exam.name}" updated successfully.', 'success')
            return redirect(url_for('view_exam', exam_id=exam_id))

        departments = Department.query.order_by(Department.name).all()
        return render_template('admin_exam_form.html',
                             departments=departments,
                             time_slots=[e.value for e in ExamTimeSlot],
                             exam=exam)
    except Exception as e:
        flash(f'Error updating exam: {str(e)}', 'danger')
        return redirect(url_for('admin_exams'))


@app.route('/admin/exams/<int:exam_id>/delete', methods=['POST'])
@require_admin
def delete_exam(exam_id):
    """Soft delete an exam."""
    try:
        from models import db, Exam

        exam = Exam.query.get_or_404(exam_id)
        exam.is_active = False
        db.session.commit()
        flash(f'Exam "{exam.name}" deleted.', 'success')
    except Exception as e:
        flash(f'Error deleting exam: {str(e)}', 'danger')

    return redirect(url_for('admin_exams'))


@app.route('/admin/exams/<int:exam_id>/enroll', methods=['POST'])
@require_admin
def enroll_students(exam_id):
    """Bulk enroll students in an exam."""
    try:
        from models import db, Exam, ExamEnrollment, Student

        exam = Exam.query.get_or_404(exam_id)
        student_ids = request.form.getlist('student_ids')

        enrolled = 0
        for sid in student_ids:
            student = Student.query.get(int(sid))
            if student:
                existing = ExamEnrollment.query.filter_by(
                    exam_id=exam_id, student_id=student.id
                ).first()
                if not existing:
                    enrollment = ExamEnrollment(exam_id=exam_id, student_id=student.id)
                    db.session.add(enrollment)
                    enrolled += 1

        exam.total_students = ExamEnrollment.query.filter_by(exam_id=exam_id).count()
        db.session.commit()
        flash(f'Enrolled {enrolled} students in exam.', 'success')
    except Exception as e:
        flash(f'Error enrolling students: {str(e)}', 'danger')

    return redirect(url_for('view_exam', exam_id=exam_id))


# ============================================
# SEATING HISTORY MANAGEMENT (PostgreSQL)
# ============================================

@app.route('/admin/exams/<int:exam_id>/history')
@require_admin
def exam_seating_history(exam_id):
    """View seating history versions for an exam."""
    try:
        from models import db, Exam, SeatingHistory

        exam = Exam.query.get_or_404(exam_id)
        history = SeatingHistory.query.filter_by(exam_id=exam_id).order_by(
            SeatingHistory.version.desc()
        ).all()

        return render_template('admin_seating_history.html',
                             exam=exam,
                             history=history)
    except Exception as e:
        flash(f'Error loading history: {str(e)}', 'danger')
        return redirect(url_for('view_exam', exam_id=exam_id))


@app.route('/admin/exams/<int:exam_id>/history/<int:version>')
@require_admin
def view_seating_version(exam_id, version):
    """View a specific seating history version."""
    try:
        from models import db, Exam, SeatingHistory

        exam = Exam.query.get_or_404(exam_id)
        history = SeatingHistory.query.filter_by(
            exam_id=exam_id, version=version
        ).first_or_404()

        return render_template('admin_seating_version.html',
                             exam=exam,
                             history=history,
                             snapshot=history.snapshot)
    except Exception as e:
        flash(f'Error loading version: {str(e)}', 'danger')
        return redirect(url_for('exam_seating_history', exam_id=exam_id))


@app.route('/admin/exams/<int:exam_id>/history/<int:version>/restore', methods=['POST'])
@require_admin
def restore_seating_version(exam_id, version):
    """Restore a previous seating arrangement."""
    try:
        from models import db, Exam, SeatingHistory, SeatingAssignment

        exam = Exam.query.get_or_404(exam_id)
        history = SeatingHistory.query.filter_by(
            exam_id=exam_id, version=version
        ).first_or_404()

        # Clear current assignments
        SeatingAssignment.query.filter_by(exam_id=exam_id).delete()

        # Restore from snapshot
        for assignment in history.snapshot.get('assignments', []):
            sa = SeatingAssignment(
                exam_id=exam_id,
                student_id=assignment['student_id'],
                room_id=assignment['room_id'],
                seat_number=assignment['seat_number'],
                seat_x=assignment['seat_x'],
                seat_y=assignment['seat_y'],
                color_group=assignment.get('color_group'),
                assigned_by=session.get('user_id'),
                is_manual_override=True,
                override_reason=f'Restored from version {version}'
            )
            db.session.add(sa)

        # Mark this version as active
        SeatingHistory.query.filter_by(exam_id=exam_id).update({'is_active': False})
        history.is_active = True
        db.session.commit()

        flash(f'Restored seating arrangement to version {version}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error restoring version: {str(e)}', 'danger')

    return redirect(url_for('exam_seating_history', exam_id=exam_id))


@app.route('/admin/exams/<int:exam_id>/save-snapshot', methods=['POST'])
@require_admin
def save_seating_snapshot(exam_id):
    """Save current seating as a new history version."""
    try:
        from models import db, Exam, SeatingHistory, SeatingAssignment
        import time

        exam = Exam.query.get_or_404(exam_id)
        start_time = time.time()

        # Get current assignments
        assignments = SeatingAssignment.query.filter_by(exam_id=exam_id).all()
        if not assignments:
            flash('No seating assignments to save.', 'warning')
            return redirect(url_for('view_exam', exam_id=exam_id))

        # Get next version number
        max_version = db.session.query(db.func.max(SeatingHistory.version)).filter_by(
            exam_id=exam_id
        ).scalar() or 0

        # Build snapshot
        snapshot = {
            'assignments': [{
                'student_id': a.student_id,
                'room_id': a.room_id,
                'seat_number': a.seat_number,
                'seat_x': a.seat_x,
                'seat_y': a.seat_y,
                'color_group': a.color_group
            } for a in assignments],
            'rooms': list(set(a.room_id for a in assignments))
        }

        gen_time = int((time.time() - start_time) * 1000)
        notes = request.form.get('notes', '')

        history = SeatingHistory(
            exam_id=exam_id,
            version=max_version + 1,
            generated_by=session.get('user_id'),
            total_students=len(assignments),
            rooms_used=len(snapshot['rooms']),
            algorithm_used='manual_snapshot',
            generation_time_ms=gen_time,
            snapshot=snapshot,
            is_active=True,
            notes=notes
        )

        # Deactivate previous versions
        SeatingHistory.query.filter_by(exam_id=exam_id).update({'is_active': False})
        db.session.add(history)
        db.session.commit()

        flash(f'Saved seating snapshot as version {max_version + 1}.', 'success')
    except Exception as e:
        flash(f'Error saving snapshot: {str(e)}', 'danger')

    return redirect(url_for('exam_seating_history', exam_id=exam_id))


# ============================================
# EXCEL IMPORT/EXPORT ROUTES
# ============================================

@app.route('/admin/import/students', methods=['GET', 'POST'])
@require_admin
def import_students_excel():
    """Import students from Excel file."""
    try:
        from models import db, Student, Department

        if request.method == 'POST':
            if 'excel_file' not in request.files:
                flash('No file uploaded.', 'danger')
                return redirect(request.url)

            file = request.files['excel_file']
            if file.filename == '':
                flash('No file selected.', 'danger')
                return redirect(request.url)

            # Read Excel or CSV
            if file.filename.endswith('.xlsx') or file.filename.endswith('.xls'):
                df = pd.read_excel(file)
            elif file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                flash('Unsupported file format. Use .xlsx, .xls, or .csv', 'danger')
                return redirect(request.url)

            # Required columns
            required = ['StudentID', 'Name']
            missing = [c for c in required if c not in df.columns]
            if missing:
                flash(f'Missing required columns: {", ".join(missing)}', 'danger')
                return redirect(request.url)

            imported = 0
            updated = 0
            errors = []

            for _, row in df.iterrows():
                try:
                    student_id = str(row['StudentID']).strip()
                    name = str(row['Name']).strip()

                    # Find or create department
                    dept_id = None
                    if 'Department' in row and pd.notna(row['Department']):
                        dept_code = str(row['Department']).strip()
                        dept = Department.query.filter_by(code=dept_code).first()
                        if dept:
                            dept_id = dept.id

                    existing = Student.query.filter_by(student_id=student_id).first()
                    if existing:
                        existing.name = name
                        existing.department_id = dept_id
                        existing.branch = str(row.get('Branch', '')).strip() if pd.notna(row.get('Branch')) else None
                        existing.section = str(row.get('Section', '')).strip() if pd.notna(row.get('Section')) else None
                        existing.year = int(row['Year']) if 'Year' in row and pd.notna(row['Year']) else None
                        existing.semester = int(row['Semester']) if 'Semester' in row and pd.notna(row['Semester']) else None
                        existing.batch = str(row.get('Batch', '')).strip() if pd.notna(row.get('Batch')) else None
                        existing.email = str(row.get('Email', '')).strip() if pd.notna(row.get('Email')) else None
                        existing.gender = str(row.get('Gender', '')).strip()[0].upper() if pd.notna(row.get('Gender')) else None
                        updated += 1
                    else:
                        student = Student(
                            student_id=student_id,
                            name=name,
                            department_id=dept_id,
                            branch=str(row.get('Branch', '')).strip() if pd.notna(row.get('Branch')) else None,
                            section=str(row.get('Section', '')).strip() if pd.notna(row.get('Section')) else None,
                            year=int(row['Year']) if 'Year' in row and pd.notna(row['Year']) else None,
                            semester=int(row['Semester']) if 'Semester' in row and pd.notna(row['Semester']) else None,
                            batch=str(row.get('Batch', '')).strip() if pd.notna(row.get('Batch')) else None,
                            email=str(row.get('Email', '')).strip() if pd.notna(row.get('Email')) else None,
                            gender=str(row.get('Gender', '')).strip()[0].upper() if pd.notna(row.get('Gender')) else None
                        )
                        db.session.add(student)
                        imported += 1
                except Exception as e:
                    errors.append(f"Row {_}: {str(e)}")

            db.session.commit()
            flash(f'Imported {imported} new students, updated {updated}. {len(errors)} errors.', 'success')
            if errors:
                for err in errors[:5]:
                    flash(err, 'warning')

            return redirect(url_for('admin_import_export'))

        return render_template('admin_import_students.html')
    except Exception as e:
        flash(f'Import error: {str(e)}', 'danger')
        return redirect(url_for('admin_import_export'))


@app.route('/admin/import/exams', methods=['GET', 'POST'])
@require_admin
def import_exams_excel():
    """Import exams from Excel file."""
    try:
        from models import db, Exam, Department, ExamTimeSlot

        if request.method == 'POST':
            if 'excel_file' not in request.files:
                flash('No file uploaded.', 'danger')
                return redirect(request.url)

            file = request.files['excel_file']
            if file.filename == '':
                flash('No file selected.', 'danger')
                return redirect(request.url)

            if file.filename.endswith('.xlsx') or file.filename.endswith('.xls'):
                df = pd.read_excel(file)
            elif file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                flash('Unsupported file format.', 'danger')
                return redirect(request.url)

            required = ['ExamCode', 'Name', 'Subject', 'ExamDate', 'ExamTime']
            missing = [c for c in required if c not in df.columns]
            if missing:
                flash(f'Missing required columns: {", ".join(missing)}', 'danger')
                return redirect(request.url)

            imported = 0
            errors = []

            for _, row in df.iterrows():
                try:
                    exam_code = str(row['ExamCode']).strip()

                    existing = Exam.query.filter_by(exam_code=exam_code).first()
                    if existing:
                        errors.append(f"Exam {exam_code} already exists")
                        continue

                    dept_id = None
                    if 'Department' in row and pd.notna(row['Department']):
                        dept = Department.query.filter_by(code=str(row['Department']).strip()).first()
                        if dept:
                            dept_id = dept.id

                    exam_date = pd.to_datetime(row['ExamDate']).date()
                    exam_time = ExamTimeSlot(str(row['ExamTime']).strip())

                    exam = Exam(
                        exam_code=exam_code,
                        name=str(row['Name']).strip(),
                        subject=str(row['Subject']).strip(),
                        department_id=dept_id,
                        exam_date=exam_date,
                        exam_time=exam_time,
                        duration_minutes=int(row.get('Duration', 180)) if pd.notna(row.get('Duration')) else 180,
                        created_by=session.get('user_id')
                    )
                    db.session.add(exam)
                    imported += 1
                except Exception as e:
                    errors.append(f"Row {_}: {str(e)}")

            db.session.commit()
            flash(f'Imported {imported} exams. {len(errors)} errors.', 'success')

            return redirect(url_for('admin_exams'))

        return render_template('admin_import_exams.html')
    except Exception as e:
        flash(f'Import error: {str(e)}', 'danger')
        return redirect(url_for('admin_import_export'))


@app.route('/admin/export/students')
@require_admin
def export_students_excel():
    """Export all students to Excel."""
    try:
        from models import Student, Department
        import io

        students = Student.query.filter_by(is_active=True).order_by(Student.student_id).all()

        data = []
        for s in students:
            dept_code = ''
            if s.department_id:
                dept = Department.query.get(s.department_id)
                dept_code = dept.code if dept else ''

            data.append({
                'StudentID': s.student_id,
                'Name': s.name,
                'Department': dept_code,
                'Branch': s.branch or '',
                'Year': s.year or '',
                'Semester': s.semester or '',
                'Batch': s.batch or '',
                'Email': s.email or '',
                'Gender': s.gender or ''
            })

        df = pd.DataFrame(data)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Students')
        output.seek(0)

        return send_from_directory(
            directory='.',
            path='students_export.xlsx',
            as_attachment=True
        ) if False else (
            output.getvalue(),
            200,
            {
                'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'Content-Disposition': 'attachment; filename=students_export.xlsx'
            }
        )
    except Exception as e:
        flash(f'Export error: {str(e)}', 'danger')
        return redirect(url_for('admin_import_export'))


@app.route('/admin/export/exams')
@require_admin
def export_exams_excel():
    """Export all exams to Excel."""
    try:
        from models import Exam, Department
        import io

        exams = Exam.query.filter_by(is_active=True).order_by(Exam.exam_date).all()

        data = []
        for e in exams:
            dept_code = ''
            if e.department_id:
                dept = Department.query.get(e.department_id)
                dept_code = dept.code if dept else ''

            data.append({
                'ExamCode': e.exam_code,
                'Name': e.name,
                'Subject': e.subject,
                'Department': dept_code,
                'ExamDate': e.exam_date.isoformat(),
                'ExamTime': e.exam_time.value,
                'Duration': e.duration_minutes,
                'TotalStudents': e.total_students
            })

        df = pd.DataFrame(data)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Exams')
        output.seek(0)

        return (
            output.getvalue(),
            200,
            {
                'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'Content-Disposition': 'attachment; filename=exams_export.xlsx'
            }
        )
    except Exception as e:
        flash(f'Export error: {str(e)}', 'danger')
        return redirect(url_for('admin_import_export'))


@app.route('/admin/export/seating/<int:exam_id>')
@require_admin
def export_seating_excel(exam_id):
    """Export seating arrangement for an exam to Excel."""
    try:
        from models import db, Exam, SeatingAssignment, Student, Room
        import io

        exam = Exam.query.get_or_404(exam_id)
        assignments = db.session.query(
            SeatingAssignment, Student, Room
        ).join(Student).join(Room).filter(
            SeatingAssignment.exam_id == exam_id
        ).order_by(Room.room_name, SeatingAssignment.seat_number).all()

        data = []
        for a, s, r in assignments:
            data.append({
                'StudentID': s.student_id,
                'Name': s.name,
                'Room': r.room_name,
                'SeatNumber': a.seat_number,
                'SeatX': a.seat_x,
                'SeatY': a.seat_y,
                'ColorGroup': a.color_group or ''
            })

        df = pd.DataFrame(data)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Seating')
        output.seek(0)

        filename = f'{exam.exam_code}_seating.xlsx'
        return (
            output.getvalue(),
            200,
            {
                'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'Content-Disposition': f'attachment; filename={filename}'
            }
        )
    except Exception as e:
        flash(f'Export error: {str(e)}', 'danger')
        return redirect(url_for('view_exam', exam_id=exam_id))


@app.route('/admin/import-export')
@require_admin
def admin_import_export():
    """Import/Export dashboard."""
    return render_template('admin_import_export.html')


@app.route('/admin/import/full', methods=['GET', 'POST'])
@require_admin
def import_full_data():
    """
    Import comprehensive data: Students + Exams + Enrollments in one CSV/Excel file.
    Expected columns: StudentID, Name, Department, Branch, Section, Year, Semester, Subject, ExamDate, ExamTime
    This creates students, exams, and enrollments automatically.
    """
    try:
        from models import db, Student, Department, Exam, ExamEnrollment, ExamTimeSlot

        if request.method == 'POST':
            if 'data_file' not in request.files:
                flash('No file uploaded.', 'danger')
                return redirect(request.url)

            file = request.files['data_file']
            if file.filename == '':
                flash('No file selected.', 'danger')
                return redirect(request.url)

            # Read file
            if file.filename.endswith('.xlsx') or file.filename.endswith('.xls'):
                df = pd.read_excel(file)
            elif file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                flash('Unsupported file format. Use .xlsx, .xls, or .csv', 'danger')
                return redirect(request.url)

            # Required columns
            required = ['StudentID', 'Name', 'Subject', 'ExamDate', 'ExamTime']
            missing = [c for c in required if c not in df.columns]
            if missing:
                flash(f'Missing required columns: {", ".join(missing)}', 'danger')
                return redirect(request.url)

            students_created = 0
            students_updated = 0
            exams_created = 0
            enrollments_created = 0
            errors = []

            # First pass: Create/update all students
            unique_students = df.drop_duplicates(subset=['StudentID'])
            for _, row in unique_students.iterrows():
                try:
                    student_id = str(row['StudentID']).strip()
                    name = str(row['Name']).strip()

                    # Find or create department
                    dept_id = None
                    dept_code = str(row.get('Department', 'CS')).strip() if pd.notna(row.get('Department')) else 'CS'
                    dept = Department.query.filter_by(code=dept_code).first()
                    if not dept:
                        dept = Department(code=dept_code, name=dept_code)
                        db.session.add(dept)
                        db.session.flush()
                    dept_id = dept.id

                    existing = Student.query.filter_by(student_id=student_id).first()
                    if existing:
                        existing.name = name
                        existing.department_id = dept_id
                        existing.branch = str(row.get('Branch', '')).strip() if pd.notna(row.get('Branch')) else None
                        existing.section = str(row.get('Section', '')).strip() if pd.notna(row.get('Section')) else None
                        existing.year = int(row['Year']) if 'Year' in row and pd.notna(row['Year']) else None
                        existing.semester = int(row['Semester']) if 'Semester' in row and pd.notna(row['Semester']) else None
                        students_updated += 1
                    else:
                        student = Student(
                            student_id=student_id,
                            name=name,
                            department_id=dept_id,
                            branch=str(row.get('Branch', '')).strip() if pd.notna(row.get('Branch')) else None,
                            section=str(row.get('Section', '')).strip() if pd.notna(row.get('Section')) else None,
                            year=int(row['Year']) if 'Year' in row and pd.notna(row['Year']) else None,
                            semester=int(row['Semester']) if 'Semester' in row and pd.notna(row['Semester']) else None
                        )
                        db.session.add(student)
                        students_created += 1
                except Exception as e:
                    errors.append(f"Student {row.get('StudentID')}: {str(e)}")

            db.session.flush()

            # Second pass: Create exams and enrollments
            exam_groups = df.groupby(['Subject', 'ExamDate', 'ExamTime'])
            for (subject, exam_date_str, exam_time), group in exam_groups:
                try:
                    # Parse exam date
                    if isinstance(exam_date_str, str):
                        exam_date = pd.to_datetime(exam_date_str).date()
                    else:
                        exam_date = exam_date_str

                    # Generate exam code
                    time_abbrev = {'Morning': 'AM', 'Afternoon': 'PM', 'Evening': 'EV'}.get(exam_time, 'AM')
                    exam_code = f"{subject.upper().replace(' ', '-')[:10]}-{exam_date.strftime('%Y%m%d')}-{time_abbrev}"

                    # Map time slot
                    time_map = {
                        'Morning': ExamTimeSlot.MORNING,
                        'Afternoon': ExamTimeSlot.AFTERNOON,
                        'Evening': ExamTimeSlot.EVENING
                    }
                    exam_time_enum = time_map.get(exam_time, ExamTimeSlot.MORNING)

                    # Find or create exam
                    exam = Exam.query.filter_by(exam_code=exam_code).first()
                    if not exam:
                        exam = Exam(
                            exam_code=exam_code,
                            name=f"{subject} Exam",
                            subject=subject,
                            exam_date=exam_date,
                            exam_time=exam_time_enum,
                            duration_minutes=180,
                            is_active=True,
                            created_by=session.get('user_id')
                        )
                        db.session.add(exam)
                        db.session.flush()
                        exams_created += 1

                    # Enroll students in this exam
                    for _, student_row in group.iterrows():
                        student_id_str = str(student_row['StudentID']).strip()
                        student = Student.query.filter_by(student_id=student_id_str).first()
                        if student:
                            existing_enrollment = ExamEnrollment.query.filter_by(
                                student_id=student.id,
                                exam_id=exam.id
                            ).first()

                            if not existing_enrollment:
                                enrollment = ExamEnrollment(
                                    student_id=student.id,
                                    exam_id=exam.id
                                )
                                db.session.add(enrollment)
                                enrollments_created += 1

                    # Update exam total
                    exam.total_students = ExamEnrollment.query.filter_by(exam_id=exam.id).count()

                except Exception as e:
                    errors.append(f"Exam {subject} on {exam_date_str}: {str(e)}")

            db.session.commit()

            flash(f'Import complete! Students: {students_created} new, {students_updated} updated. '
                  f'Exams: {exams_created} created. Enrollments: {enrollments_created} created.', 'success')

            if errors:
                for err in errors[:5]:
                    flash(err, 'warning')
                if len(errors) > 5:
                    flash(f'... and {len(errors) - 5} more errors', 'warning')

            return redirect(url_for('admin_exams'))

        return render_template('admin_import_full.html')

    except Exception as e:
        db.session.rollback()
        flash(f'Import error: {str(e)}', 'danger')
        import traceback
        traceback.print_exc()
        return redirect(url_for('admin_import_export'))


@app.route('/admin/students/add-by-section', methods=['GET', 'POST'])
@require_admin
def add_students_by_section():
    """Add multiple students at once by specifying section details."""
    try:
        from models import db, Student, Department

        if request.method == 'POST':
            dept_code = request.form.get('department', 'CS')
            branch = request.form.get('branch', '')
            section = request.form.get('section', '')
            year = int(request.form.get('year', 3))
            semester = int(request.form.get('semester', 6))
            student_data = request.form.get('student_data', '')

            if not student_data.strip():
                flash('Please provide student data.', 'danger')
                return redirect(request.url)

            # Get or create department
            dept = Department.query.filter_by(code=dept_code).first()
            if not dept:
                dept = Department(code=dept_code, name=dept_code)
                db.session.add(dept)
                db.session.flush()

            # Parse student data (format: StudentID,Name per line)
            lines = student_data.strip().split('\n')
            created = 0
            updated = 0
            errors = []

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(',', 1)
                if len(parts) < 2:
                    errors.append(f"Invalid format: {line}")
                    continue

                student_id = parts[0].strip()
                name = parts[1].strip()

                existing = Student.query.filter_by(student_id=student_id).first()
                if existing:
                    existing.name = name
                    existing.department_id = dept.id
                    existing.branch = branch
                    existing.section = section
                    existing.year = year
                    existing.semester = semester
                    updated += 1
                else:
                    student = Student(
                        student_id=student_id,
                        name=name,
                        department_id=dept.id,
                        branch=branch,
                        section=section,
                        year=year,
                        semester=semester
                    )
                    db.session.add(student)
                    created += 1

            db.session.commit()
            flash(f'Added {created} new students, updated {updated}. {len(errors)} errors.', 'success')
            return redirect(url_for('admin_students'))

        # GET request - show form
        departments = Department.query.all()
        return render_template('admin_add_students_section.html', departments=departments)

    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('admin_students'))


# ============================================
# EXAM RESULTS HOOKS (for future implementation)
# ============================================

@app.route('/admin/exams/<int:exam_id>/results', methods=['GET', 'POST'])
@require_admin
def exam_results(exam_id):
    """Manage exam results - hooks for cheat detection."""
    try:
        from models import db, Exam, ExamEnrollment, Student, CheatDetectionFlag

        exam = Exam.query.get_or_404(exam_id)

        if request.method == 'POST':
            # Import results from file
            if 'results_file' in request.files:
                file = request.files['results_file']
                if file.filename.endswith(('.xlsx', '.xls', '.csv')):
                    if file.filename.endswith('.csv'):
                        df = pd.read_csv(file)
                    else:
                        df = pd.read_excel(file)

                    # Expected columns: StudentID, Score
                    if 'StudentID' in df.columns and 'Score' in df.columns:
                        # Store results and run similarity detection
                        # This is a hook for future implementation
                        results_data = df.to_dict('records')

                        # Detect suspicious patterns
                        suspicious = detect_suspicious_scores(exam_id, results_data)

                        for s in suspicious:
                            flag = CheatDetectionFlag(
                                exam_id=exam_id,
                                student1_id=s['student1_id'],
                                student2_id=s['student2_id'],
                                flag_type='similar_answers',
                                severity=s['severity'],
                                details=s['details']
                            )
                            db.session.add(flag)

                        db.session.commit()
                        flash(f'Results imported. {len(suspicious)} suspicious patterns detected.', 'success')
                    else:
                        flash('File must have StudentID and Score columns.', 'danger')

        # Get current enrollments with any existing flags
        enrollments = db.session.query(ExamEnrollment, Student).join(Student).filter(
            ExamEnrollment.exam_id == exam_id
        ).all()

        flags = CheatDetectionFlag.query.filter_by(exam_id=exam_id).all()

        return render_template('admin_exam_results.html',
                             exam=exam,
                             enrollments=enrollments,
                             flags=flags)
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('view_exam', exam_id=exam_id))


def detect_suspicious_scores(exam_id, results):
    """
    Hook function for detecting suspicious score patterns.
    This can be expanded with ML-based similarity detection.
    """
    from models import SeatingAssignment

    suspicious = []

    # Get adjacent pairs from seating
    adjacencies = {}
    assignments = SeatingAssignment.query.filter_by(exam_id=exam_id).all()

    for a in assignments:
        adjacents = a.get_adjacent_students()
        for adj in adjacents:
            pair = tuple(sorted([a.student_id, adj.student_id]))
            if pair not in adjacencies:
                adjacencies[pair] = {'distance': 1}

    # Compare scores for adjacent students
    scores = {str(r['StudentID']): r['Score'] for r in results}

    for (s1, s2), info in adjacencies.items():
        from models import Student
        st1 = Student.query.get(s1)
        st2 = Student.query.get(s2)

        if st1 and st2:
            score1 = scores.get(st1.student_id)
            score2 = scores.get(st2.student_id)

            if score1 is not None and score2 is not None:
                # Simple similarity check - can be enhanced
                diff = abs(float(score1) - float(score2))
                if diff < 2:  # Very similar scores
                    suspicious.append({
                        'student1_id': s1,
                        'student2_id': s2,
                        'severity': 'medium' if diff < 1 else 'low',
                        'details': {
                            'score1': score1,
                            'score2': score2,
                            'difference': diff,
                            'were_adjacent': True
                        }
                    })

    return suspicious


# ============================================
# EXAM & SECTION MANAGEMENT ROUTES
# ============================================

@app.route('/admin/manage_exams')
@require_admin
def admin_manage_exams():
    """Admin page to manage exams and assign sections to exams"""
    from models import Exam, SectionExamAssignment, Student, Department
    import json

    # Get all exams from PostgreSQL
    try:
        exams = Exam.query.filter_by(is_active=True).order_by(Exam.exam_date.desc()).all()
    except Exception:
        exams = []

    # Get section assignments
    try:
        assignments = SectionExamAssignment.query.all()
    except Exception:
        assignments = []

    # Get unique sections from student data (CSV fallback + PostgreSQL)
    df = load_student_data()
    sections = []
    sections_json = []
    departments = []

    if not df.empty:
        # Get unique department/branch/section combinations with UNIQUE student count
        if all(col in df.columns for col in ['Department', 'Branch', 'Section']):
            # First get unique students per section (drop duplicate StudentID within section)
            unique_students = df.drop_duplicates(subset=['StudentID', 'Department', 'Branch', 'Section'])
            grouped = unique_students.groupby(['Department', 'Branch', 'Section', 'Year']).agg(
                student_count=('StudentID', 'nunique')
            ).reset_index()

            for _, row in grouped.iterrows():
                section_info = {
                    'department': row['Department'],
                    'branch': row['Branch'],
                    'section': row['Section'],
                    'year': int(row['Year']) if pd.notna(row['Year']) else 0,
                    'student_count': int(row['student_count'])
                }
                sections.append(section_info)
                sections_json.append(section_info)

            departments = df['Department'].unique().tolist()

    # Group sections by department-branch for the template
    sections_grouped = {}
    for sec in sections:
        key = f"{sec['department']}-{sec['branch']}"
        if key not in sections_grouped:
            sections_grouped[key] = []
        sections_grouped[key].append(sec)

    return render_template('admin_manage_exams.html',
                          exams=exams,
                          assignments=assignments,
                          sections=sections,
                          sections_grouped=sections_grouped,
                          sections_json=json.dumps(sections_json),
                          departments=departments)


@app.route('/admin/create_exam', methods=['POST'])
@require_admin
def admin_create_exam():
    """Create a new exam"""
    from models import db, Exam, ExamTimeSlot

    exam_code = request.form.get('exam_code', '').strip()
    exam_name = request.form.get('exam_name', '').strip()
    subject = request.form.get('subject', '').strip()
    exam_date = request.form.get('exam_date')
    exam_time = request.form.get('exam_time', 'Morning')
    duration = int(request.form.get('duration', 180))

    if not all([exam_code, exam_name, subject, exam_date]):
        flash('All fields are required.', 'danger')
        return redirect(url_for('admin_exams'))

    try:
        # Convert exam_time string to enum
        time_slot = ExamTimeSlot(exam_time)

        new_exam = Exam(
            exam_code=exam_code,
            name=exam_name,
            subject=subject,
            exam_date=datetime.strptime(exam_date, '%Y-%m-%d').date(),
            exam_time=time_slot,
            duration_minutes=duration,
            is_active=True
        )
        db.session.add(new_exam)
        db.session.commit()
        flash(f'Exam "{exam_code}" created successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating exam: {str(e)}', 'danger')

    return redirect(url_for('admin_exams'))


@app.route('/admin/assign_section_to_exam', methods=['POST'])
@require_admin
def admin_assign_section_to_exam():
    """Assign a section to an exam and enroll all students in that section"""
    from models import db, SectionExamAssignment, ExamEnrollment, Student, Exam

    department = request.form.get('department', '').strip()
    branch = request.form.get('branch', '').strip()
    section = request.form.get('section', '').strip()
    exam_id = request.form.get('exam_id')

    if not all([department, branch, section, exam_id]):
        flash('All fields are required.', 'danger')
        return redirect(url_for('admin_manage_exams'))

    try:
        exam_id = int(exam_id)

        # Check if assignment already exists
        existing = SectionExamAssignment.query.filter_by(
            department_code=department,
            branch=branch,
            section=section,
            exam_id=exam_id
        ).first()

        if existing:
            flash('This section is already assigned to this exam.', 'warning')
            return redirect(url_for('admin_manage_exams'))

        # Create section assignment
        assignment = SectionExamAssignment(
            department_code=department,
            branch=branch,
            section=section,
            exam_id=exam_id
        )
        db.session.add(assignment)

        # Get students from this section (from CSV)
        df = load_student_data()
        if not df.empty and all(col in df.columns for col in ['Department', 'Branch', 'Section', 'StudentID']):
            section_students = df[
                (df['Department'] == department) &
                (df['Branch'] == branch) &
                (df['Section'] == section)
            ]['StudentID'].unique()

            enrolled_count = 0
            for student_id in section_students:
                # First check if student exists in PostgreSQL, if not create them
                student = Student.query.filter_by(student_id=str(student_id)).first()
                if not student:
                    # Get student info from CSV
                    student_info = df[df['StudentID'] == student_id].iloc[0]
                    student = Student(
                        student_id=str(student_id),
                        name=student_info.get('Name', 'Unknown'),
                        branch=branch,
                        section=section,
                        year=int(student_info.get('Year', 1)) if pd.notna(student_info.get('Year')) else None,
                        semester=int(student_info.get('Semester', 1)) if pd.notna(student_info.get('Semester')) else None
                    )
                    db.session.add(student)
                    db.session.flush()  # Get the ID

                # Check if enrollment exists
                existing_enrollment = ExamEnrollment.query.filter_by(
                    student_id=student.id,
                    exam_id=exam_id
                ).first()

                if not existing_enrollment:
                    enrollment = ExamEnrollment(
                        student_id=student.id,
                        exam_id=exam_id
                    )
                    db.session.add(enrollment)
                    enrolled_count += 1

            # Update exam's total_students count
            exam = Exam.query.get(exam_id)
            if exam:
                exam.total_students = ExamEnrollment.query.filter_by(exam_id=exam_id).count()

            db.session.commit()
            flash(f'Section {department}-{branch}-{section} assigned to exam. {enrolled_count} students enrolled.', 'success')
        else:
            db.session.commit()
            flash(f'Section assigned but no students found in CSV data.', 'warning')

    except Exception as e:
        db.session.rollback()
        flash(f'Error assigning section: {str(e)}', 'danger')

    return redirect(url_for('admin_manage_exams'))


@app.route('/admin/assign_sections_bulk', methods=['POST'])
@require_admin
def admin_assign_sections_bulk():
    """Assign multiple sections to an exam at once"""
    from models import db, SectionExamAssignment, ExamEnrollment, Student, Exam

    exam_id = request.form.get('exam_id')
    sections_data = request.form.getlist('sections')  # List of "dept|branch|section" strings

    if not exam_id or not sections_data:
        flash('Please select an exam and at least one section.', 'danger')
        return redirect(url_for('admin_manage_exams'))

    try:
        exam_id = int(exam_id)
        exam = Exam.query.get(exam_id)
        if not exam:
            flash('Exam not found.', 'danger')
            return redirect(url_for('admin_manage_exams'))

        df = load_student_data()
        total_assigned = 0
        total_enrolled = 0
        skipped = 0

        for section_str in sections_data:
            parts = section_str.split('|')
            if len(parts) != 3:
                continue
            department, branch, section = parts

            # Check if assignment already exists
            existing = SectionExamAssignment.query.filter_by(
                department_code=department,
                branch=branch,
                section=section,
                exam_id=exam_id
            ).first()

            if existing:
                skipped += 1
                continue

            # Create section assignment
            assignment = SectionExamAssignment(
                department_code=department,
                branch=branch,
                section=section,
                exam_id=exam_id
            )
            db.session.add(assignment)
            total_assigned += 1

            # Enroll students from this section
            if not df.empty and all(col in df.columns for col in ['Department', 'Branch', 'Section', 'StudentID']):
                section_students = df[
                    (df['Department'] == department) &
                    (df['Branch'] == branch) &
                    (df['Section'] == section)
                ]['StudentID'].unique()

                for student_id in section_students:
                    student = Student.query.filter_by(student_id=str(student_id)).first()
                    if not student:
                        student_info = df[df['StudentID'] == student_id].iloc[0]
                        student = Student(
                            student_id=str(student_id),
                            name=student_info.get('Name', 'Unknown'),
                            branch=branch,
                            section=section,
                            year=int(student_info.get('Year', 1)) if pd.notna(student_info.get('Year')) else None,
                            semester=int(student_info.get('Semester', 1)) if pd.notna(student_info.get('Semester')) else None
                        )
                        db.session.add(student)
                        db.session.flush()

                    existing_enrollment = ExamEnrollment.query.filter_by(
                        student_id=student.id,
                        exam_id=exam_id
                    ).first()

                    if not existing_enrollment:
                        enrollment = ExamEnrollment(
                            student_id=student.id,
                            exam_id=exam_id
                        )
                        db.session.add(enrollment)
                        total_enrolled += 1

        # Update exam's total_students count
        exam.total_students = ExamEnrollment.query.filter_by(exam_id=exam_id).count()
        db.session.commit()

        # Also add students to CSV for seating generation
        csv_rows_added = 0
        try:
            csv_df = pd.read_csv(CSV_PATH)
            new_rows = []

            # Get exam details for CSV
            exam_date_str = exam.exam_date.strftime('%Y-%m-%d')
            exam_time_str = exam.exam_time.value

            for section_str in sections_data:
                parts = section_str.split('|')
                if len(parts) != 3:
                    continue
                department, branch, section = parts

                # Get students from this section
                section_students = df[
                    (df['Department'] == department) &
                    (df['Branch'] == branch) &
                    (df['Section'] == section)
                ].drop_duplicates(subset=['StudentID'])

                for _, student_row in section_students.iterrows():
                    # Check if this student-exam combination already exists in CSV
                    existing = csv_df[
                        (csv_df['StudentID'] == student_row['StudentID']) &
                        (csv_df['Subject'] == exam.subject) &
                        (csv_df['ExamDate'] == exam_date_str) &
                        (csv_df['ExamTime'] == exam_time_str)
                    ]

                    if existing.empty:
                        new_row = {
                            'StudentID': student_row['StudentID'],
                            'Name': student_row.get('Name', 'Unknown'),
                            'Department': student_row.get('Department', department),
                            'Branch': student_row.get('Branch', branch),
                            'Section': student_row.get('Section', section),
                            'Year': student_row.get('Year', 2),
                            'Semester': student_row.get('Semester', 4),
                            'Subject': exam.subject,
                            'ExamDate': exam_date_str,
                            'ExamTime': exam_time_str
                        }
                        new_rows.append(new_row)
                        csv_rows_added += 1

            if new_rows:
                new_df = pd.DataFrame(new_rows)
                csv_df = pd.concat([csv_df, new_df], ignore_index=True)
                csv_df.to_csv(CSV_PATH, index=False)
        except Exception as csv_e:
            print(f"[Warning] Could not update CSV: {csv_e}")

        msg = f'{total_assigned} sections assigned, {total_enrolled} students enrolled.'
        if csv_rows_added > 0:
            msg += f' ({csv_rows_added} rows added to CSV for seating.)'
        if skipped > 0:
            msg += f' ({skipped} sections already assigned, skipped.)'
        flash(msg, 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error assigning sections: {str(e)}', 'danger')

    return redirect(url_for('admin_manage_exams'))


@app.route('/admin/delete_exam/<int:exam_id>')
@require_admin
def admin_delete_exam(exam_id):
    """Delete an exam"""
    from models import db, Exam

    try:
        exam = Exam.query.get(exam_id)
        if exam:
            db.session.delete(exam)
            db.session.commit()
            flash(f'Exam "{exam.exam_code}" deleted successfully.', 'success')
        else:
            flash('Exam not found.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting exam: {str(e)}', 'danger')

    return redirect(url_for('admin_exams'))


@app.route('/admin/remove_section_assignment/<int:assignment_id>')
@require_admin
def admin_remove_section_assignment(assignment_id):
    """Remove a section-exam assignment"""
    from models import db, SectionExamAssignment, ExamEnrollment, Student, Exam

    try:
        assignment = SectionExamAssignment.query.get(assignment_id)
        if assignment:
            # Remove enrollments for students in this section
            df = load_student_data()
            if not df.empty:
                section_students = df[
                    (df['Department'] == assignment.department_code) &
                    (df['Branch'] == assignment.branch) &
                    (df['Section'] == assignment.section)
                ]['StudentID'].unique()

                for student_id in section_students:
                    student = Student.query.filter_by(student_id=str(student_id)).first()
                    if student:
                        ExamEnrollment.query.filter_by(
                            student_id=student.id,
                            exam_id=assignment.exam_id
                        ).delete()

            # Update exam's total_students count
            exam = Exam.query.get(assignment.exam_id)
            if exam:
                exam.total_students = ExamEnrollment.query.filter_by(exam_id=assignment.exam_id).count()

            db.session.delete(assignment)
            db.session.commit()
            flash('Section assignment removed and students unenrolled.', 'success')
        else:
            flash('Assignment not found.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error removing assignment: {str(e)}', 'danger')

    return redirect(url_for('admin_manage_exams'))


@app.route('/admin/view_exam_sections/<int:exam_id>')
@require_admin
def admin_view_exam_sections(exam_id):
    """View sections assigned to an exam"""
    from models import Exam, SectionExamAssignment, ExamEnrollment, Student

    exam = Exam.query.get_or_404(exam_id)
    assignments = SectionExamAssignment.query.filter_by(exam_id=exam_id).all()

    # Get enrolled students
    enrollments = ExamEnrollment.query.filter_by(exam_id=exam_id).all()
    students = []
    for enrollment in enrollments:
        student = Student.query.get(enrollment.student_id)
        if student:
            students.append({
                'student_id': student.student_id,
                'name': student.name,
                'branch': student.branch,
                'section': student.section
            })

    return render_template('admin_view_exam_sections.html',
                          exam=exam,
                          assignments=assignments,
                          students=students)


@app.route('/api/sections')
@require_login
def api_get_sections():
    """API to get sections based on filters"""
    department = request.args.get('department')
    branch = request.args.get('branch')

    df = load_student_data()
    if df.empty:
        return jsonify([])

    filtered = df.copy()
    if department:
        filtered = filtered[filtered['Department'] == department]
    if branch:
        filtered = filtered[filtered['Branch'] == branch]

    if 'Section' in filtered.columns:
        sections = filtered['Section'].unique().tolist()
        return jsonify(sections)
    return jsonify([])


@app.route('/api/branches')
@require_login
def api_get_branches():
    """API to get branches for a department"""
    department = request.args.get('department')

    df = load_student_data()
    if df.empty:
        return jsonify([])

    if department:
        filtered = df[df['Department'] == department]
        if 'Branch' in filtered.columns:
            branches = filtered['Branch'].unique().tolist()
            return jsonify(branches)
    return jsonify([])


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# Initialize database
init_database()

# Run PostgreSQL migrations automatically
run_postgres_migrations()

# Sync exams from CSV to PostgreSQL
sync_exams_from_csv()

# Auto-assign teachers to exam schedule
assignments = auto_assign_teachers_to_schedule()
if assignments > 0:
    print(f"[Schedule] Auto-assigned teachers to {assignments} room-sessions")

# Generate visualizations on startup (optional - can be slow)
# Uncomment the line below to auto-generate on every startup:
# generate_seating_visualizations()

if __name__ == '__main__':
    # Generate visualizations when running directly
    generate_seating_visualizations()
    app.run(debug=True)