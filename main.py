import pandas as pd
import os
import sqlite3
from conflict_graph import get_colored_groups, extract_student_metadata
from room_assignment import assign_rooms_to_groups
from seat_layout import assign_seats_in_room
from visualization import create_simple_html_visualization

# PostgreSQL connection string (matches app.py)
POSTGRESQL_URI = os.environ.get('DATABASE_URL', 'postgresql://postgres:admin@localhost:5432/exam_seating')


def load_exam_data_from_postgresql():
    """
    Load exam enrollment data from PostgreSQL database.
    Returns a DataFrame in the same format as the CSV-based system.
    """
    try:
        import psycopg2
        from urllib.parse import urlparse

        # Parse connection string
        result = urlparse(POSTGRESQL_URI)
        conn = psycopg2.connect(
            dbname=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port or 5432
        )

        # Query to get all enrolled students with their exam info
        query = """
        SELECT
            s.student_id as "StudentID",
            s.name as "Name",
            COALESCE(d.code, 'CS') as "Department",
            COALESCE(s.branch, 'Unknown') as "Branch",
            COALESCE(s.section, 'A') as "Section",
            COALESCE(s.year, 2) as "Year",
            COALESCE(s.semester, 4) as "Semester",
            e.subject as "Subject",
            e.exam_date::text as "ExamDate",
            e.exam_time as "ExamTime",
            COALESCE(s.photo_path, '/static/uploads/default.jpg') as "PhotoPath",
            COALESCE(s.gender, 'U') as "Gender"
        FROM exam_enrollments ee
        JOIN students s ON s.id = ee.student_id
        JOIN exams e ON e.id = ee.exam_id
        LEFT JOIN departments d ON d.id = s.department_id
        WHERE e.is_active = true
        ORDER BY e.exam_date, e.exam_time, s.student_id
        """

        df = pd.read_sql(query, conn)
        conn.close()

        print(f"[PostgreSQL] Loaded {len(df)} exam entries from database")
        return df

    except Exception as e:
        print(f"[PostgreSQL] Error loading from database: {e}")
        return None


def load_friend_relationships():
    """Load friend relationships from PostgreSQL for seating constraints."""
    try:
        import psycopg2
        from urllib.parse import urlparse

        result = urlparse(POSTGRESQL_URI)
        conn = psycopg2.connect(
            dbname=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port or 5432
        )

        query = """
        SELECT
            s1.student_id as student1,
            s2.student_id as student2,
            sr.relationship_type
        FROM student_relationships sr
        JOIN students s1 ON s1.id = sr.student1_id
        JOIN students s2 ON s2.id = sr.student2_id
        WHERE sr.is_active = true
        """

        cursor = conn.cursor()
        cursor.execute(query)
        relationships = cursor.fetchall()
        conn.close()

        # Build a dict of friends for each student
        friends_map = {}
        for s1, s2, rel_type in relationships:
            if s1 not in friends_map:
                friends_map[s1] = []
            if s2 not in friends_map:
                friends_map[s2] = []
            friends_map[s1].append(s2)
            friends_map[s2].append(s1)

        return friends_map

    except Exception as e:
        print(f"[PostgreSQL] Could not load friend relationships: {e}")
        return {}


def get_rooms_config_from_db(db_path='data/system.db'):
    """Get room configurations from database"""
    try:
        conn = sqlite3.connect(db_path)
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
    except Exception as e:
        print(f"Error loading room config from database: {e}")
        # Fallback to default configuration
        return [
            {
                'room_name': 'Room-A',
                'capacity': 30,
                'allowed_years': [2, 3],
                'max_subjects': 15,
                'max_branches': 5,
                'max_departments': 2,
                'max_years': 2,
                'layout_columns': 6,
                'layout_rows': 5
            },
            {
                'room_name': 'Room-B',
                'capacity': 40,
                'allowed_years': [2, 3],
                'max_subjects': 15,
                'max_branches': 5,
                'max_departments': 2,
                'max_years': 2,
                'layout_columns': 8,
                'layout_rows': 5
            }
        ]

def init_database_if_needed():
    """Initialize database with default room configurations if needed."""
    db_path = 'data/system.db'
    os.makedirs('data', exist_ok=True)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS room_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_name TEXT NOT NULL UNIQUE,
                capacity INTEGER NOT NULL,
                max_subjects INTEGER,
                max_branches INTEGER,
                allowed_years TEXT,
                allowed_branches TEXT,
                layout_columns INTEGER DEFAULT 6,
                layout_rows INTEGER DEFAULT 5
            )
        ''')

        cursor.execute('SELECT COUNT(*) FROM room_configs')
        count = cursor.fetchone()[0]

        if count == 0:
            default_rooms = [
                ('Room-A', 30, 15, 5, '2,3', 'CS,EC,ME', 6, 5),
                ('Room-B', 40, 15, 5, '2,3', 'CS,EC,ME', 8, 5),
                ('Room-C', 25, 10, 3, '2,3,4', 'CS,EC', 5, 5)
            ]
            for room_data in default_rooms:
                cursor.execute('''
                    INSERT INTO room_configs
                    (room_name, capacity, max_subjects, max_branches, allowed_years, allowed_branches, layout_columns, layout_rows)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', room_data)

        conn.commit()
        conn.close()
    except Exception:
        pass  # Use fallback configuration

# For backward compatibility, set ROOMS_CONFIG to load from database
def load_rooms_config():
    """Load room configurations with database initialization"""
    init_database_if_needed()
    return get_rooms_config_from_db()

ROOMS_CONFIG = load_rooms_config()

def create_index_page(room_names, final_layout, metadata, output_path="visualizations/index.html"):
    """Create a searchable dashboard of all students"""
    # Create a searchable database of all students
    student_database = []
    for room, seats in final_layout.items():
        for seat in seats:
            student_id = seat['student_id']
            info = metadata.get(student_id, {})
            student_database.append({
                'id': student_id,
                'name': info.get('Name', 'Unknown'),
                'branch': info.get('Branch', 'Unknown'),
                'subject': info.get('Subject', 'Unknown'),
                'room': room,
                'seat_no': seat['seat_no'],
                'year': info.get('Year', 'Unknown'),
                'department': info.get('Department', 'Unknown')
            })
    
    with open(output_path, "w") as f:
        f.write(f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Seating Dashboard</title>
  <style>
    body {{
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      margin: 0;
      background: #f9fafb;
      color: #111827;
    }}
    header {{
      background-color: #1f2937;
      color: white;
      padding: 2rem;
      text-align: center;
      font-size: 2rem;
    }}
    .container {{
      max-width: 1000px;
      margin: 2rem auto;
      padding: 1rem;
    }}
    .search-box {{
      margin-bottom: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }}
    .search-filters {{
      display: flex;
      gap: 1rem;
      flex-wrap: wrap;
    }}
    input[type="text"], select {{
      padding: 0.75rem;
      font-size: 1rem;
      border-radius: 0.5rem;
      border: 1px solid #d1d5db;
      flex: 1;
      min-width: 200px;
    }}
    .results {{
      margin-top: 1rem;
    }}
    .result-item {{
      padding: 1rem;
      background: white;
      border: 1px solid #e5e7eb;
      border-radius: 0.5rem;
      margin-bottom: 0.5rem;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .student-info {{
      flex: 1;
    }}
    .student-info h3 {{
      margin: 0 0 0.5rem 0;
      color: #1f2937;
    }}
    .student-details {{
      color: #6b7280;
      font-size: 0.9rem;
    }}
    .room-actions {{
        display: flex;
        gap: 0.5rem;
    }}
    .room-link {{
      text-decoration: none;
      color: #2563eb;
      font-weight: 600;
      padding: 0.5rem 1rem;
      background: #eff6ff;
      border-radius: 0.25rem;
      border: 1px solid #2563eb;
      transition: all 0.2s;
      white-space: nowrap;
    }}
    .room-link:hover {{
      background: #2563eb;
      color: white;
    }}
    .no-results {{
      text-align: center;
      color: #6b7280;
      font-style: italic;
      padding: 2rem;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }}
    .stat-card {{
      background: white;
      padding: 1rem;
      border-radius: 0.5rem;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
      text-align: center;
    }}
    .stat-number {{
      font-size: 2rem;
      font-weight: bold;
      color: #2563eb;
    }}
    .stat-label {{
      color: #6b7280;
      font-size: 0.9rem;
    }}
  </style>
</head>
<body>
  <header>
    Exam Seating Dashboard
  </header>
  <div class="container">
    <div class="stats">
      <div class="stat-card">
        <div class="stat-number">{len(student_database)}</div>
        <div class="stat-label">Total Students</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">{len(room_names)}</div>
        <div class="stat-label">Active Rooms</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">{len(set([s['subject'] for s in student_database]))}</div>
        <div class="stat-label">Subjects</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">{len(set([s['branch'] for s in student_database]))}</div>
        <div class="stat-label">Branches</div>
      </div>
    </div>
    
    <div class="search-box">
      <input type="text" id="searchInput" placeholder="Search by student name, ID, branch, subject, or room...">
      <div class="search-filters">
        <select id="roomSelect">
          <option value="">All Rooms</option>""")
        
        for room in room_names:
            f.write(f'          <option value="{room}">{room}</option>\n')
        
        f.write("""        </select>
        <select id="branchSelect">
          <option value="">All Branches</option>""")
        
        branches = sorted(set([s['branch'] for s in student_database if s['branch'] != 'Unknown']))
        for branch in branches:
            f.write(f'          <option value="{branch}">{branch}</option>\n')
        
        f.write("""        </select>
        <select id="subjectSelect">
          <option value="">All Subjects</option>""")
        
        subjects = sorted(set([s['subject'] for s in student_database if s['subject'] != 'Unknown']))
        for subject in subjects:
            f.write(f'          <option value="{subject}">{subject}</option>\n')
        
        f.write(f"""        </select>
      </div>
    </div>
    <div class="results" id="results"></div>
  </div>
  <script>
    const students = {str(student_database).replace("'", '"')};
    const rooms = [""")
        
        for room in room_names:
            f.write(f"      {{ name: '{room}', html_url: '{room}.html?teacher=1' }},\n")
        
        f.write("""
    ];

    document.getElementById("searchInput").addEventListener("input", updateResults);
    document.getElementById("roomSelect").addEventListener("change", updateResults);
    document.getElementById("branchSelect").addEventListener("change", updateResults);
    document.getElementById("subjectSelect").addEventListener("change", updateResults);

    function updateResults() {
      const query = document.getElementById("searchInput").value.toLowerCase();
      const selectedRoom = document.getElementById("roomSelect").value;
      const selectedBranch = document.getElementById("branchSelect").value;
      const selectedSubject = document.getElementById("subjectSelect").value;
      
      let filtered = students;
      
      // Apply room filter first if a room is selected
      if (selectedRoom) {
        filtered = filtered.filter(student => student.room === selectedRoom);
      } else {
        // If no room is selected, clear results unless a search query is present
        // This makes sure the list is empty by default
        if (!query && !selectedBranch && !selectedSubject) {
            document.getElementById("results").innerHTML = '<div class="no-results">Please select a room or enter a search query to find students.</div>';
            return;
        }
      }

      if (query) {
        filtered = filtered.filter(student => 
          student.name.toLowerCase().includes(query) ||
          student.id.toLowerCase().includes(query) ||
          student.branch.toLowerCase().includes(query) ||
          student.subject.toLowerCase().includes(query) ||
          student.room.toLowerCase().includes(query) ||
          student.department.toLowerCase().includes(query)
        );
      }
      
      // Apply other filters only if a room is selected or if there's a search query
      if (selectedBranch) {
        filtered = filtered.filter(student => student.branch === selectedBranch);
      }
      
      if (selectedSubject) {
        filtered = filtered.filter(student => student.subject === selectedSubject);
      }

      const resultsDiv = document.getElementById("results");
      resultsDiv.innerHTML = '';

      if (filtered.length === 0) {
        resultsDiv.innerHTML = '<div class="no-results">No students found matching your search criteria.</div>';
        return;
      }

      filtered.forEach(student => {
        const div = document.createElement("div");
        div.className = "result-item";
        div.innerHTML = `
          <div class="student-info">
            <h3>${{student.name}} (${{student.id}})</h3>
            <div class="student-details">
              ${{student.branch}} • ${{student.subject}} • Seat #${{student.seat_no}} • Year ${{student.year}}
            </div>
          </div>
          <div class="room-actions">
            <a href="${{student.room}}.html?teacher=1&highlight=${{student.id}}" class="room-link">View Room</a>
          </div>
        `;
        resultsDiv.appendChild(div);
      }});
    }}

    // Initial load: no results by default until a room is selected or search initiated
    document.addEventListener('DOMContentLoaded', () => {{
        document.getElementById("results").innerHTML = '<div class="no-results">Please select a room or enter a search query to find students.</div>';
    }});
  </script>
</body>
</html>
""")

def main(use_postgresql=True):
    """
    Main entry point for seating arrangement generation.

    Args:
        use_postgresql: If True, load data from PostgreSQL. If False, fall back to CSV.
    """
    print("Exam Seating Arrangement System")
    print("=" * 40)

    os.makedirs('visualizations', exist_ok=True)
    os.makedirs('exports', exist_ok=True)
    os.makedirs('data', exist_ok=True)

    df_students = None
    friends_map = {}  # Initialize friends map

    # Try PostgreSQL first if enabled
    if use_postgresql:
        print("Loading data from PostgreSQL...")
        df_students = load_exam_data_from_postgresql()
        if df_students is not None and len(df_students) > 0:
            print(f"[PostgreSQL] Successfully loaded {len(df_students)} exam entries")
            # Load friend relationships for seating constraints
            friends_map = load_friend_relationships()
            if friends_map:
                print(f"[PostgreSQL] Loaded {len(friends_map)} students with friend relationships")
        else:
            print("[PostgreSQL] No data found, falling back to CSV...")
            df_students = None

    # Fall back to CSV if PostgreSQL failed or was disabled
    if df_students is None:
        INPUT_FILE = 'data/students.csv'
        if not os.path.exists(INPUT_FILE):
            print(f"Error: No data source available. Import data via admin panel or add {INPUT_FILE}")
            return

        print(f"Loading student data from {INPUT_FILE}...")
        try:
            df_students = pd.read_csv(INPUT_FILE)
            required_columns = ['StudentID', 'Name', 'Department', 'Year', 'Subject', 'ExamDate', 'ExamTime']
            missing = [c for c in required_columns if c not in df_students.columns]

            if missing:
                print(f"Error: Missing columns: {missing}")
                return

            if 'Branch' not in df_students.columns and 'Batch' in df_students.columns:
                df_students['Branch'] = df_students['Batch']

            for col, default in [('PhotoPath', '/static/uploads/default.jpg'), ('Gender', 'U')]:
                if col not in df_students.columns:
                    df_students[col] = default
            if 'Semester' not in df_students.columns:
                df_students['Semester'] = df_students['Year'] * 2

            print(f"Loaded {len(df_students)} exam entries from CSV")

        except Exception as e:
            print(f"Error loading data: {e}")
            return

    if df_students is None or len(df_students) == 0:
        print("No exam data to process.")
        return

    # Group by exam session (date + time)
    exam_sessions = df_students.groupby(['ExamDate', 'ExamTime'])
    print(f"Found {len(exam_sessions)} exam sessions")

    current_rooms_config = get_rooms_config_from_db()
    room_config_dict = {r['room_name']: r for r in current_rooms_config}
    total_capacity = sum(r['capacity'] for r in current_rooms_config)
    print(f"Available rooms: {len(current_rooms_config)}, total capacity: {total_capacity}")

    all_session_layouts = {}
    all_room_names = []

    # Process each exam session separately
    for (exam_date, exam_time), session_df in exam_sessions:
        session_key = f"{exam_date}_{exam_time}"
        print(f"\n--- Processing: {exam_date} {exam_time} ---")

        # Get unique students for this session (by subject)
        subjects_in_session = session_df['Subject'].unique()
        print(f"  Subjects: {', '.join(subjects_in_session)}")
        print(f"  Students: {len(session_df)}")

        # Extract metadata for this session
        metadata = extract_student_metadata(session_df)

        # Simple assignment: distribute students across rooms
        student_ids = session_df['StudentID'].tolist()
        room_assignment = {}
        student_idx = 0

        for room_config in current_rooms_config:
            room_name = room_config['room_name']
            capacity = room_config['capacity']
            room_students = student_ids[student_idx:student_idx + capacity]
            if room_students:
                room_assignment[room_name] = room_students
                student_idx += len(room_students)
                print(f"    {room_name}: {len(room_students)} students")

        if student_idx < len(student_ids):
            print(f"  Warning: {len(student_ids) - student_idx} students could not be assigned!")

        # Generate seat layout with spread pattern (pass friends_map for separation)
        final_layout = assign_seats_in_room(room_assignment, metadata, room_config_dict, friends_map)
        all_session_layouts[session_key] = {
            'layout': final_layout,
            'metadata': metadata,
            'date': exam_date,
            'time': exam_time,
            'subjects': list(subjects_in_session)
        }

        # Export CSV for this session
        for room, seats in final_layout.items():
            if not seats:
                continue
            room_data = [{
                'SeatNo': s['seat_no'], 'StudentID': s['student_id'],
                'Name': metadata.get(s['student_id'], {}).get('Name', 'Unknown'),
                'Department': metadata.get(s['student_id'], {}).get('Department', 'Unknown'),
                'Branch': metadata.get(s['student_id'], {}).get('Branch', 'Unknown'),
                'Year': metadata.get(s['student_id'], {}).get('Year', 'Unknown'),
                'Subject': metadata.get(s['student_id'], {}).get('Subject', 'Unknown'),
                'Room': room, 'Position_X': s['x'], 'Position_Y': s['y'],
                'ExamDate': exam_date, 'ExamTime': exam_time
            } for s in seats]
            safe_session = session_key.replace('-', '')
            pd.DataFrame(room_data).to_csv(f"exports/{room}_{safe_session}_seating.csv", index=False)

        # Generate visualization for each room in this session
        for room, seats in final_layout.items():
            if not seats:
                continue
            try:
                room_config = room_config_dict[room]
                # Add session info to visualization title
                viz_title = f"{room} - {exam_date} {exam_time}"
                html_content = create_simple_html_visualization(viz_title, seats, metadata, room_config)
                safe_session = session_key.replace('-', '')
                filename = f"{room}_{safe_session}"
                with open(f"visualizations/{filename}.html", "w") as f:
                    f.write(html_content)
                all_room_names.append({
                    'filename': filename,
                    'room': room,
                    'date': exam_date,
                    'time': exam_time,
                    'subjects': subjects_in_session,
                    'student_count': len(seats)
                })
            except Exception as e:
                print(f"  Error creating visualization for {room}: {e}")

    # Create master index page with all sessions
    if all_room_names:
        create_session_index_page(all_room_names, all_session_layouts)

    print("\n" + "=" * 40)
    print("Complete!")
    print(f"  Exports: exports/")
    print(f"  Visualizations: visualizations/")
    print(f"  Total sessions: {len(exam_sessions)}")


def create_session_index_page(room_info_list, all_layouts, output_path="visualizations/index.html"):
    """Create an index page organized by exam session."""

    # Group by date and time
    sessions = {}
    for info in room_info_list:
        key = (info['date'], info['time'])
        if key not in sessions:
            sessions[key] = []
        sessions[key].append(info)

    # Sort sessions by date then time
    time_order = {'Morning': 0, 'Afternoon': 1, 'Evening': 2}
    sorted_sessions = sorted(sessions.keys(), key=lambda x: (x[0], time_order.get(x[1], 3)))

    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Exam Seating Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f5f5f5; color: #333; }
        header { background: #2c3e50; color: white; padding: 2rem; text-align: center; }
        header h1 { font-size: 2rem; margin-bottom: 0.5rem; }
        header p { opacity: 0.8; }
        .container { max-width: 1200px; margin: 2rem auto; padding: 0 1rem; }
        .session { background: white; border-radius: 8px; margin-bottom: 1.5rem; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden; }
        .session-header { background: #34495e; color: white; padding: 1rem 1.5rem; display: flex; justify-content: space-between; align-items: center; }
        .session-header h2 { font-size: 1.25rem; }
        .session-header .badge { background: #3498db; padding: 0.25rem 0.75rem; border-radius: 4px; font-size: 0.875rem; }
        .session-body { padding: 1.5rem; }
        .subjects { margin-bottom: 1rem; color: #666; }
        .subjects span { background: #ecf0f1; padding: 0.25rem 0.5rem; border-radius: 4px; margin-right: 0.5rem; font-size: 0.875rem; }
        .rooms-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 1rem; }
        .room-card { border: 1px solid #ddd; border-radius: 6px; padding: 1rem; transition: all 0.2s; }
        .room-card:hover { border-color: #3498db; box-shadow: 0 2px 8px rgba(52,152,219,0.2); }
        .room-card h3 { color: #2c3e50; margin-bottom: 0.5rem; }
        .room-card .count { color: #666; font-size: 0.875rem; margin-bottom: 1rem; }
        .room-card a { display: inline-block; background: #3498db; color: white; padding: 0.5rem 1rem; border-radius: 4px; text-decoration: none; font-size: 0.875rem; }
        .room-card a:hover { background: #2980b9; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
        .stat { background: white; padding: 1.5rem; border-radius: 8px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .stat-value { font-size: 2rem; font-weight: bold; color: #3498db; }
        .stat-label { color: #666; font-size: 0.875rem; }
    </style>
</head>
<body>
    <header>
        <h1>Exam Seating Dashboard</h1>
        <p>Seating arrangements organized by exam session</p>
    </header>
    <div class="container">
        <div class="stats">
            <div class="stat">
                <div class="stat-value">""" + str(len(sorted_sessions)) + """</div>
                <div class="stat-label">Exam Sessions</div>
            </div>
            <div class="stat">
                <div class="stat-value">""" + str(len(room_info_list)) + """</div>
                <div class="stat-label">Room Assignments</div>
            </div>
            <div class="stat">
                <div class="stat-value">""" + str(sum(r['student_count'] for r in room_info_list)) + """</div>
                <div class="stat-label">Total Seats Assigned</div>
            </div>
        </div>
"""

    for date, time in sorted_sessions:
        rooms = sessions[(date, time)]
        subjects = set()
        for r in rooms:
            subjects.update(r['subjects'])

        total_students = sum(r['student_count'] for r in rooms)

        html += f"""
        <div class="session">
            <div class="session-header">
                <h2>{date} - {time}</h2>
                <span class="badge">{total_students} students</span>
            </div>
            <div class="session-body">
                <div class="subjects">
                    Subjects: {' '.join(f'<span>{s}</span>' for s in sorted(subjects))}
                </div>
                <div class="rooms-grid">
"""
        for room in sorted(rooms, key=lambda x: x['room']):
            html += f"""
                    <div class="room-card">
                        <h3>{room['room']}</h3>
                        <div class="count">{room['student_count']} students assigned</div>
                        <a href="{room['filename']}.html" target="_blank">View Seating</a>
                    </div>
"""
        html += """
                </div>
            </div>
        </div>
"""

    html += """
    </div>
</body>
</html>
"""

    with open(output_path, 'w') as f:
        f.write(html)

def reload_rooms_config():
    """Reload room configurations from database (for use by Flask app)"""
    global ROOMS_CONFIG
    ROOMS_CONFIG = get_rooms_config_from_db()
    return ROOMS_CONFIG

if __name__ == '__main__':
    main()