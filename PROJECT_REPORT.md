# Exam Seating Arrangement System
## Detailed Project Report

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Objectives](#3-objectives)
4. [Technology Stack](#4-technology-stack)
5. [System Architecture](#5-system-architecture)
6. [Database Design](#6-database-design)
7. [Core Algorithms](#7-core-algorithms)
8. [Features & Functionality](#8-features--functionality)
9. [User Interface Design](#9-user-interface-design)
10. [API Documentation](#10-api-documentation)
11. [Security Implementation](#11-security-implementation)
12. [Testing & Validation](#12-testing--validation)
13. [Deployment Guide](#13-deployment-guide)
14. [Future Enhancements](#14-future-enhancements)
15. [Conclusion](#15-conclusion)
16. [References](#16-references)

---

## 1. Executive Summary

The **Exam Seating Arrangement System** is a comprehensive web-based application designed to automate the complex process of assigning exam seats to students while ensuring academic integrity through intelligent separation of friends and related students.

### Key Highlights:
- **Automated seating generation** using graph coloring algorithms (DSatur)
- **Cheat prevention** through friend/relative separation constraints
- **Multi-role access control** (Admin, Teacher, Student)
- **Two-Factor Authentication (2FA)** for administrator accounts
- **Complete audit trail** for all system actions
- **Interactive visualizations** with drag-drop seat management
- **PostgreSQL database** with 15+ tables, 20+ views, stored procedures
- **RESTful API** with 50+ endpoints

### Project Statistics:
| Metric | Value |
|--------|-------|
| Lines of Code (Python) | ~5,000+ |
| Database Tables | 15 |
| Database Views | 20+ |
| API Endpoints | 50+ |
| HTML Templates | 30+ |
| CSS Lines | 1,150+ |

---

## 2. Problem Statement

### Current Challenges in Manual Seating:

1. **Time-Consuming Process**: Manual seat allocation for hundreds of students takes hours
2. **Error-Prone**: Human errors lead to conflicts and overlaps
3. **Cheat Prevention Difficulty**: Manually tracking friend groups and relatives is impractical
4. **Room Utilization**: Suboptimal room usage due to lack of constraint optimization
5. **No Audit Trail**: No record of who made seating decisions
6. **Version Management**: No ability to compare or rollback seating arrangements
7. **Visualization**: Difficult to see seating patterns and identify issues

### Target Users:
- **Educational Institutions**: Universities, colleges, schools
- **Examination Controllers**: Managing exam logistics
- **Invigilators/Teachers**: Viewing assigned rooms and students
- **Students**: Checking their exam seats

---

## 3. Objectives

### Primary Objectives:

1. **Automate Seating Generation**
   - Use graph coloring to partition students into non-conflicting groups
   - Assign rooms based on capacity and constraints
   - Position seats to maximize distance between related students

2. **Prevent Academic Dishonesty**
   - Separate friends and relatives
   - Spread students from same section
   - Flag unavoidable adjacencies for review

3. **Provide Complete Traceability**
   - Log all user actions
   - Maintain seating history with rollback
   - Track relationship reports

### Secondary Objectives:

4. **Improve Administrative Efficiency**
   - Bulk import/export capabilities
   - Dashboard with key metrics
   - Role-based access control

5. **Enhance User Experience**
   - Interactive visualizations
   - Mobile-responsive design
   - Print-ready seating charts

6. **Ensure Security**
   - Two-factor authentication
   - Password hashing
   - Session management

---

## 4. Technology Stack

### Backend

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| Web Framework | Flask | 2.0+ | HTTP routing, session management |
| ORM | Flask-SQLAlchemy | 3.0+ | Database abstraction |
| Migrations | Flask-Migrate | 4.0+ | Schema versioning |
| Security | Werkzeug | 2.0+ | Password hashing, utilities |
| 2FA | pyotp | 2.8+ | TOTP implementation |

### Database

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| Primary DB | PostgreSQL | 12+ | Main data storage |
| Auxiliary DB | SQLite | 3.x | System configuration |
| Driver | psycopg2-binary | 2.9+ | PostgreSQL connectivity |

### Data Processing

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| Graph Algorithms | NetworkX | 2.8+ | DSatur coloring |
| Data Analysis | Pandas | 1.5+ | CSV/Excel processing |
| Numerical | NumPy | 1.x | Array operations |
| Excel Support | openpyxl | 3.1+ | .xlsx file handling |

### Frontend

| Component | Technology | Purpose |
|-----------|------------|---------|
| Templates | Jinja2 | HTML rendering |
| Styling | CSS3 | Retro Windows XP theme |
| Interactivity | Vanilla JS | DOM manipulation |
| Fonts | Google Fonts | VT323, Inter |

### Utilities

| Component | Technology | Purpose |
|-----------|------------|---------|
| QR Codes | qrcode[pil] | Student ID codes |
| Environment | python-dotenv | Configuration |
| Charts | Matplotlib | Analytics graphs |

---

## 5. System Architecture

### 5.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   Admin     │  │   Teacher   │  │   Student   │              │
│  │  Dashboard  │  │  Dashboard  │  │  Dashboard  │              │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘              │
└─────────┼────────────────┼────────────────┼─────────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      PRESENTATION LAYER                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              Flask Routes (app.py - 4000+ lines)           │ │
│  │  • Authentication routes    • Admin routes                 │ │
│  │  • Teacher routes           • Student routes               │ │
│  │  • API endpoints            • Visualization routes         │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                       BUSINESS LOGIC LAYER                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  conflict_   │  │    room_     │  │    seat_     │          │
│  │  graph.py    │  │ assignment.py│  │  layout.py   │          │
│  │  (DSatur)    │  │   (FFD)      │  │ (Separation) │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│  ┌──────────────┐  ┌──────────────┐                             │
│  │visualization │  │    main.py   │                             │
│  │    .py       │  │(Orchestrator)│                             │
│  └──────────────┘  └──────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                         DATA ACCESS LAYER                        │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                SQLAlchemy ORM Models                       │ │
│  │  models/student.py    models/seating.py    models/user.py │ │
│  │  models/room.py       models/relationships.py              │ │
│  │  models/audit.py      models/base.py                       │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                        DATABASE LAYER                            │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐   │
│  │      PostgreSQL         │  │        SQLite               │   │
│  │  • 15 Tables            │  │  • System Config            │   │
│  │  • 20+ Views            │  │  • Room Configs             │   │
│  │  • Stored Procedures    │  │                             │   │
│  │  • Triggers             │  │                             │   │
│  └─────────────────────────┘  └─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 Project Directory Structure

```
exam_seating/
├── app.py                      # Main Flask application
├── main.py                     # Seating algorithm orchestration
├── requirements.txt            # Python dependencies
├── conflict_graph.py           # Graph coloring (DSatur)
├── room_assignment.py          # Room assignment (FFD)
├── seat_layout.py              # Seat positioning
├── visualization.py            # HTML chart generation
│
├── database/
│   ├── schema.sql              # PostgreSQL schema
│   ├── views.sql               # Analytics views
│   ├── procedures.sql          # Stored procedures
│   ├── triggers.sql            # Audit triggers
│   └── migration_add_section.sql
│
├── models/
│   ├── __init__.py             # SQLAlchemy init
│   ├── base.py                 # Mixins (Timestamp, Active)
│   ├── student.py              # Student, Department
│   ├── seating.py              # Exam, SeatingAssignment
│   ├── relationships.py        # StudentRelationship, CheatFlag
│   ├── user.py                 # User, UserRole
│   ├── room.py                 # Room, Invigilator
│   └── audit.py                # AuditLog, SystemConfig
│
├── templates/
│   ├── base_retro.html         # Base layout
│   ├── enhanced_login.html     # Login page
│   ├── admin_dashboard.html    # Admin home
│   ├── admin_exams.html        # Exam management
│   ├── admin_relationships.html# Friend management
│   ├── admin_analytics.html    # Analytics
│   ├── admin_audit_logs.html   # Audit viewer
│   ├── admin_cheat_flags.html  # Flag review
│   ├── teacher_dashboard.html  # Teacher home
│   ├── student_dashboard.html  # Student home
│   └── ... (20+ more templates)
│
├── static/
│   └── css/
│       └── retro.css           # UI styling (1150+ lines)
│
├── visualizations/             # Generated HTML charts
├── exports/                    # Generated CSV exports
└── data/
    └── system.db               # SQLite auxiliary DB
```

---

## 6. Database Design

### 6.1 Entity-Relationship Diagram

```
                                    ┌─────────────────┐
                                    │   departments   │
                                    ├─────────────────┤
                              ┌─────│ id (PK)         │─────┐
                              │     │ code (UNIQUE)   │     │
                              │     │ name            │     │
                              │     └─────────────────┘     │
                              │                             │
                              ▼                             ▼
┌─────────────────┐    ┌─────────────────┐         ┌─────────────────┐
│     users       │    │    students     │         │     exams       │
├─────────────────┤    ├─────────────────┤         ├─────────────────┤
│ id (PK)         │    │ id (PK)         │         │ id (PK)         │
│ username        │    │ student_id (UQ) │◄────────│ exam_code (UQ)  │
│ email           │    │ name            │         │ subject         │
│ password_hash   │    │ department_id   │─────┐   │ department_id   │
│ role (ENUM)     │    │ branch          │     │   │ exam_date       │
│ totp_secret     │    │ section         │     │   │ exam_time (ENUM)│
│ student_id (FK) │────│ year            │     │   │ is_active       │
│ is_active       │    │ semester        │     │   └────────┬────────┘
└────────┬────────┘    │ email           │     │            │
         │             │ gender          │     │            │
         │             └────────┬────────┘     │            │
         │                      │              │            │
         │                      ▼              │            ▼
         │            ┌─────────────────────┐  │  ┌─────────────────────┐
         │            │  exam_enrollments   │  │  │ seating_assignments │
         │            │     (M:M Join)      │  │  ├─────────────────────┤
         │            ├─────────────────────┤  │  │ id (PK)             │
         │            │ student_id (FK,PK)  │◄─┴──│ exam_id (FK)        │
         │            │ exam_id (FK,PK)     │     │ student_id (FK)     │
         │            └─────────────────────┘     │ room_id (FK)        │
         │                                        │ seat_number         │
         │            ┌─────────────────────┐     │ seat_x, seat_y      │
         │            │ student_relationships│     │ color_group         │
         │            ├─────────────────────┤     │ assigned_by (FK)    │
         │            │ id (PK)             │     └─────────┬───────────┘
         │            │ student1_id (FK)    │◄──────────────┘
         │            │ student2_id (FK)    │
         │            │ relationship_type   │     ┌─────────────────┐
         │            │ reported_by (FK)    │◄────│     rooms       │
         │            │ is_active           │     ├─────────────────┤
         │            └─────────────────────┘     │ id (PK)         │
         │                                        │ room_name (UQ)  │
         │            ┌─────────────────────┐     │ capacity        │
         └───────────►│    audit_logs       │     │ max_subjects    │
                      ├─────────────────────┤     │ max_branches    │
                      │ id (PK)             │     │ allowed_years[] │
                      │ user_id (FK)        │     │ layout_cols/rows│
                      │ action (ENUM)       │     │ is_active       │
                      │ table_name          │     └─────────────────┘
                      │ old_values (JSONB)  │
                      │ new_values (JSONB)  │
                      │ ip_address          │
                      │ created_at          │
                      └─────────────────────┘
```

### 6.2 Table Specifications

#### Core Tables

| Table | Primary Key | Foreign Keys | Unique Constraints | Indexes |
|-------|-------------|--------------|-------------------|---------|
| students | id | department_id | student_id | dept_id, year, branch, section |
| departments | id | - | code | - |
| exams | id | department_id, created_by | exam_code | exam_date, dept_id, subject |
| rooms | id | - | room_name | - |
| users | id | student_id | username, email | role, is_active |

#### Join Tables

| Table | Composite Key | Purpose |
|-------|---------------|---------|
| exam_enrollments | (student_id, exam_id) | Student-Exam M:M |
| seating_assignments | id | Exam seating records |
| invigilators | id | Teacher-Room assignment |
| student_relationships | id | Friend/Relative pairs |

#### Audit & History

| Table | Purpose | Retention |
|-------|---------|-----------|
| audit_logs | Action tracking | 90 days (configurable) |
| seating_history | Seating versions | Permanent |
| cheat_detection_flags | Violation flags | Until reviewed |

### 6.3 Key Constraints

```sql
-- Student relationships must have ordered IDs
CHECK (student1_id < student2_id)

-- Unique seating per exam-student
UNIQUE (exam_id, student_id)

-- Unique seat per exam-room-seat_number
UNIQUE (exam_id, room_id, seat_number)

-- Exam time slots
CHECK (exam_time IN ('Morning', 'Afternoon', 'Evening'))

-- Severity levels
CHECK (severity IN ('low', 'medium', 'high', 'critical'))
```

### 6.4 Database Views

| View Name | Purpose |
|-----------|---------|
| v_system_stats | Dashboard metrics |
| v_exam_seating_summary | Seating completion % |
| v_room_utilization | Room usage stats |
| v_student_exam_schedule | Individual schedules |
| v_cheat_flags_summary | Flag statistics |
| v_relationship_network | Friend graph |
| v_recent_audit_activity | Latest 100 actions |
| v_department_stats | Students by dept/year |

---

## 7. Core Algorithms

### 7.1 DSatur Graph Coloring Algorithm

**Purpose**: Partition students into non-conflicting groups (colors) such that students with same exam time, friends, or same-section students are in different colors.

**Algorithm**: Degree of Saturation (DSatur)

```
ALGORITHM DSatur(G):
    INPUT: Graph G = (V, E) where V = students, E = conflicts
    OUTPUT: Color assignment C: V → {1, 2, 3, ...}

    1. Initialize:
       - saturation[v] = {} for all v ∈ V  // colors of neighbors
       - color[v] = 0 for all v ∈ V        // uncolored
       - degree[v] = |neighbors(v)|

    2. Select first vertex:
       v₀ = vertex with maximum degree
       color[v₀] = 1
       Update saturation of neighbors

    3. While uncolored vertices exist:
       a. Select vertex v with:
          - Maximum |saturation[v]| (saturation degree)
          - Ties broken by maximum degree[v]

       b. Assign color:
          c = minimum color ∉ saturation[v]
          color[v] = c

       c. Update saturation of neighbors:
          For each neighbor u of v:
             saturation[u] = saturation[u] ∪ {c}

    4. Return color assignment
```

**Implementation** (`conflict_graph.py`):

```python
def dsatur_coloring(graph):
    """
    DSatur algorithm for graph coloring.
    Returns dict: {color_id: [student_ids]}
    """
    colors = {}
    saturation = defaultdict(set)
    uncolored = set(graph.nodes())

    # Start with highest-degree node
    first = max(uncolored, key=lambda n: graph.degree(n))
    colors[first] = 0
    uncolored.remove(first)

    # Update saturation of neighbors
    for neighbor in graph.neighbors(first):
        saturation[neighbor].add(0)

    while uncolored:
        # Select node with highest saturation, break ties by degree
        next_node = max(uncolored,
                       key=lambda n: (len(saturation[n]), graph.degree(n)))

        # Find minimum available color
        neighbor_colors = {colors[n] for n in graph.neighbors(next_node)
                          if n in colors}
        color = 0
        while color in neighbor_colors:
            color += 1

        colors[next_node] = color
        uncolored.remove(next_node)

        # Update saturation
        for neighbor in graph.neighbors(next_node):
            if neighbor in uncolored:
                saturation[neighbor].add(color)

    # Group by color
    groups = defaultdict(list)
    for node, color in colors.items():
        groups[color].append(node)

    return dict(groups)
```

**Edge Weight Strategy**:
| Constraint Type | Edge Weight | Priority |
|----------------|-------------|----------|
| Same exam time | 10 | Highest (hard) |
| Friend/Relative | 5 | High (soft) |
| Same section | 2 | Medium (soft) |

**Complexity**: O(V²) where V = number of students

---

### 7.2 Room Assignment Algorithm

**Purpose**: Assign student groups (colors) to rooms respecting capacity and constraints.

**Primary Algorithm**: First-Fit Decreasing (FFD)

```
ALGORITHM FFD_RoomAssignment(groups, rooms):
    INPUT:
      - groups: Dict[color_id → List[student_ids]]
      - rooms: List[Room] with capacity, constraints
    OUTPUT:
      - assignment: Dict[room_name → List[student_ids]]

    1. Sort groups by size (descending)
    2. Sort rooms by remaining capacity (descending)

    3. For each group g in sorted order:
       a. For each room r in sorted order:
          - If capacity(r) >= size(g) AND
            all student years ∈ allowed_years(r) AND
            distinct_subjects(g) <= max_subjects(r) AND
            distinct_branches(g) <= max_branches(r):
              Assign g to r
              Update remaining capacity
              Break

       b. If g not assigned:
          Return FAILURE (trigger backtracking)

    4. Return assignment
```

**Fallback Algorithm**: Backtracking

```
ALGORITHM Backtracking(groups, rooms, index, current_assignment):
    IF index == len(groups):
        RETURN current_assignment  // Success

    group = groups[index]

    FOR each room r in rooms:
        IF valid_assignment(group, r, current_assignment):
            current_assignment[r].append(group)
            result = Backtracking(groups, rooms, index+1, current_assignment)
            IF result != FAILURE:
                RETURN result
            current_assignment[r].remove(group)  // Backtrack

    RETURN FAILURE
```

**Constraint Validation**:
```python
def validate_room_constraints(group, room, metadata):
    # 1. Capacity check
    if len(group) > room['capacity']:
        return False

    # 2. Year constraint
    student_years = {metadata[s]['Year'] for s in group}
    if not student_years.issubset(set(room['allowed_years'])):
        return False

    # 3. Subject constraint
    subjects = {metadata[s]['Subject'] for s in group}
    if len(subjects) > room['max_subjects']:
        return False

    # 4. Branch constraint
    branches = {metadata[s]['Branch'] for s in group}
    if len(branches) > room['max_branches']:
        return False

    return True
```

---

### 7.3 Seat Layout Algorithm

**Purpose**: Position students within a room to maximize separation of friends.

**Spread Pattern** (Checkerboard):
```
ALGORITHM SpreadPositioning(students, cols, rows):
    total_seats = cols * rows
    spacing = max(1, total_seats / len(students))

    positions = []

    // First pass: even positions
    for i in range(0, total_seats, 2):
        if len(positions) < len(students):
            x = i % cols
            y = i // cols
            positions.append((x, y))

    // Second pass: odd positions if needed
    for i in range(1, total_seats, 2):
        if len(positions) < len(students):
            x = i % cols
            y = i // cols
            positions.append((x, y))

    return positions
```

**Friend-Aware Placement**:
```python
def assign_seats_with_separation(students, room_config, friend_graph):
    """
    Assign seats avoiding friend adjacency.
    """
    cols = room_config['layout_columns']
    rows = room_config['layout_rows']

    placed = {}  # position → student_id
    assignments = []

    for student in students:
        friends = friend_graph.get(student, set())

        # Find non-adjacent position
        for x in range(cols):
            for y in range(rows):
                if (x, y) in placed:
                    continue

                if not is_adjacent_to_friend(x, y, friends, placed):
                    placed[(x, y)] = student
                    assignments.append({
                        'student_id': student,
                        'x': x, 'y': y,
                        'seat_no': y * cols + x + 1
                    })
                    break
            else:
                continue
            break
        else:
            # Fallback: place anywhere available
            for x in range(cols):
                for y in range(rows):
                    if (x, y) not in placed:
                        placed[(x, y)] = student
                        assignments.append({...})
                        break

    return assignments

def is_adjacent_to_friend(x, y, friends, placed):
    """Check if position is adjacent to any friend."""
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            if dx == 0 and dy == 0:
                continue
            neighbor_pos = (x + dx, y + dy)
            if neighbor_pos in placed and placed[neighbor_pos] in friends:
                return True
    return False
```

---

## 8. Features & Functionality

### 8.1 Authentication & Authorization

| Feature | Description |
|---------|-------------|
| Role-Based Access | Admin, Teacher, Student roles |
| Two-Factor Auth | TOTP for admin accounts |
| Password Security | SHA256 hashing with salt |
| Account Lockout | 5 failed attempts → 30 min lock |
| Session Management | 30 min timeout, secure cookies |

### 8.2 Exam Management

| Feature | Description |
|---------|-------------|
| CRUD Operations | Create, edit, delete exams |
| Bulk Enrollment | Enroll entire sections |
| Schedule View | Calendar with Morning/Afternoon/Evening slots |
| Student Tracking | Enrolled vs. seated counts |
| Status Management | Active/inactive exam filtering |

### 8.3 Room Configuration

| Feature | Description |
|---------|-------------|
| Physical Attributes | Building, floor, layout grid |
| Amenities | AC, projector, CCTV flags |
| Constraints | Max subjects, branches, allowed years |
| Capacity Planning | Dynamic availability tracking |
| Invigilator Assignment | Link teachers to rooms |

### 8.4 Relationship Management

| Feature | Description |
|---------|-------------|
| Relationship Types | Friend, relative, same_hostel, same_room |
| Bulk Upload | CSV import for large datasets |
| Reporting | Track who reported relationships |
| Active/Inactive | Toggle relationship status |
| Validation | Prevent duplicate pairs |

### 8.5 Seating Generation

| Feature | Description |
|---------|-------------|
| Automated Algorithm | DSatur + FFD + spread layout |
| Friend Separation | Prevents adjacent seating |
| Section Spread | Distributes same-section students |
| Manual Override | Admin can adjust seats |
| Conflict Detection | Visual highlighting of violations |
| Version History | Rollback to previous versions |

### 8.6 Cheat Detection

| Feature | Description |
|---------|-------------|
| Automatic Flagging | System flags friend adjacencies |
| Severity Levels | Low, medium, high, critical |
| Review Workflow | Admin review and resolution |
| Flag Types | friend_adjacent, same_section_adjacent |
| Analytics | Summary by exam/severity |

### 8.7 Analytics & Reporting

| Feature | Description |
|---------|-------------|
| System Dashboard | Total students, rooms, exams |
| Exam Analytics | Completion %, rooms used |
| Room Utilization | Usage %, efficiency metrics |
| Audit Logs | Complete action history |
| Department Stats | Students by year/branch |

### 8.8 Data Import/Export

| Feature | Description |
|---------|-------------|
| CSV Import | Students, exams, relationships |
| Excel Support | .xlsx file handling |
| Seating Export | Per-exam/room CSV |
| HTML Export | Interactive visualizations |
| Backup | Full system export |

### 8.9 Visualization

| Feature | Description |
|---------|-------------|
| Interactive Charts | Room seating layouts |
| Color Coding | By department with legend |
| Filters | Year, branch, time filters |
| Drag-Drop | Manual seat adjustments (Swap Mode) |
| Friend Highlighting | Visual friend connections |
| Print Ready | Print-optimized layouts |
| Toast Notifications | Animated feedback |
| Responsive Design | Desktop and tablet |

---

## 9. User Interface Design

### 9.1 Design Philosophy

The UI follows a **retro Windows XP / Minecraft aesthetic** with:
- Pixel borders and box shadows
- Muted earthy color palette
- Monospace font accents (VT323)
- ASCII-style icons ([E], [R], [&])

### 9.2 Color Palette

| Variable | Hex | Usage |
|----------|-----|-------|
| --bg-primary | #f5f5f0 | Page background |
| --bg-secondary | #ebebdf | Card headers |
| --bg-card | #fafaf5 | Card bodies |
| --border-color | #8b8b7a | Borders |
| --accent-blue | #4a7c9b | Primary actions |
| --accent-green | #5c8a4d | Success states |
| --accent-red | #9b4a4a | Danger actions |
| --accent-yellow | #b8a44a | Warnings |
| --accent-purple | #7a5c8a | Friends/special |

### 9.3 Component Library

| Component | Classes | Description |
|-----------|---------|-------------|
| Cards | `.card`, `.card-header`, `.card-body` | Content containers |
| Buttons | `.btn`, `.btn-primary`, `.btn-danger` | Action buttons |
| Forms | `.form-control`, `.form-label` | Input elements |
| Tables | `.table`, `.table-container` | Data tables |
| Badges | `.badge-blue`, `.badge-green` | Status indicators |
| Alerts | `.alert-success`, `.alert-danger` | Notifications |
| Stats | `.stat-box`, `.stat-value` | Metric displays |

### 9.4 Animations

| Animation | Keyframes | Usage |
|-----------|-----------|-------|
| fadeIn | opacity 0→1, translateY -10→0 | Page load |
| fadeInUp | opacity 0→1, translateY 20→0 | Staggered items |
| slideInRight | translateX 100%→0 | Toast notifications |
| pulse | scale 1→1.05→1 | Hover effects |
| spin | rotate 0→360 | Loading spinner |
| glow | box-shadow pulse | Friend adjacency warning |

### 9.5 Responsive Breakpoints

| Breakpoint | Width | Changes |
|------------|-------|---------|
| Mobile | < 768px | Single column, collapsed sidebar |
| Tablet | 768-1024px | Reduced grid columns |
| Desktop | > 1024px | Full layout |

---

## 10. API Documentation

### 10.1 Authentication Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/login` | User login | Public |
| POST | `/register` | Student registration | Public |
| POST | `/logout` | Session logout | Required |
| POST | `/teacher_setup_2fa` | Teacher 2FA setup | Teacher |

### 10.2 Admin Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/admin_dashboard` | Admin home | Admin |
| GET | `/admin/exams` | List exams | Admin |
| POST | `/admin/exams/add` | Create exam | Admin |
| GET | `/admin/exams/<id>` | View exam | Admin |
| POST | `/admin/exams/<id>/edit` | Update exam | Admin |
| DELETE | `/admin/exams/<id>/delete` | Delete exam | Admin |
| POST | `/admin/generate_seating` | Generate seats | Admin |

### 10.3 Room Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/admin/rooms_config` | List rooms | Admin |
| POST | `/admin/add_room_config` | Add room | Admin |
| POST | `/admin/edit_room_config/<id>` | Edit room | Admin |
| DELETE | `/admin/delete_room_config/<id>` | Delete room | Admin |

### 10.4 Relationship Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/admin/relationships` | List relationships | Admin |
| POST | `/admin/relationships/add` | Add relationship | Admin |
| POST | `/admin/relationships/bulk` | Bulk upload | Admin |
| DELETE | `/admin/relationships/delete/<id>` | Remove | Admin |

### 10.5 JSON API Endpoints

| Method | Endpoint | Response |
|--------|----------|----------|
| GET | `/api/student_seating/<id>` | Student seat info |
| GET | `/api/room_students/<name>` | Students in room |
| GET | `/api/analytics/exam/<id>` | Exam statistics |
| GET | `/api/analytics/room_utilization` | Room usage |
| GET | `/api/sections` | Available sections |
| GET | `/api/branches` | Available branches |

---

## 11. Security Implementation

### 11.1 Authentication Security

```python
# Password hashing
from werkzeug.security import generate_password_hash, check_password_hash

password_hash = generate_password_hash(password, method='sha256')
is_valid = check_password_hash(password_hash, password)
```

### 11.2 Two-Factor Authentication

```python
import pyotp

# Generate secret for new admin
totp_secret = pyotp.random_base32()

# Verify TOTP code
totp = pyotp.TOTP(user.totp_secret)
if totp.verify(user_code):
    # Login successful
```

### 11.3 Session Security

```python
app.config.update(
    SESSION_COOKIE_SECURE=True,      # HTTPS only
    SESSION_COOKIE_HTTPONLY=True,    # No JS access
    SESSION_COOKIE_SAMESITE='Lax',   # CSRF protection
    PERMANENT_SESSION_LIFETIME=1800   # 30 min timeout
)
```

### 11.4 Account Lockout

```python
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 30

def check_login(user, password):
    if user.locked_until and user.locked_until > datetime.now():
        raise AccountLocked("Account locked")

    if not check_password(password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)
        raise InvalidCredentials()

    user.failed_login_attempts = 0
    user.last_login = datetime.now()
```

### 11.5 SQL Injection Prevention

All database queries use SQLAlchemy ORM with parameterized queries:

```python
# Safe - parameterized
student = Student.query.filter_by(student_id=student_id).first()

# Safe - parameterized
db.session.execute(
    text("SELECT * FROM students WHERE id = :id"),
    {"id": student_id}
)
```

### 11.6 XSS Prevention

Jinja2 auto-escaping enabled:
```python
{{ user_input }}  # Automatically escaped
{{ user_input|safe }}  # Only when explicitly trusted
```

---

## 12. Testing & Validation

### 12.1 Algorithm Testing

| Test Case | Input | Expected Output |
|-----------|-------|-----------------|
| Empty graph | 0 students | Empty groups |
| Single student | 1 student | 1 group with 1 student |
| Complete graph | All conflicts | N groups (chromatic number) |
| Friends only | 2 friends | 2 different groups |
| Large dataset | 1000 students | Valid coloring in < 30s |

### 12.2 Constraint Validation

| Constraint | Test | Expected |
|------------|------|----------|
| Room capacity | 50 students, 30 capacity | Split into 2 rooms |
| Year restriction | Year 4 student, room allows [2,3] | Rejected |
| Subject limit | 20 subjects, max 15 | Rejected |
| Friend adjacency | Friends in same room | Non-adjacent seats |

### 12.3 Security Testing

| Test | Method | Expected |
|------|--------|----------|
| SQL Injection | `' OR 1=1 --` | Query fails safely |
| XSS | `<script>alert(1)</script>` | Escaped output |
| CSRF | Missing token | Request rejected |
| Brute force | 10 failed logins | Account locked |

---

## 13. Deployment Guide

### 13.1 Prerequisites

- Python 3.8+
- PostgreSQL 12+
- pip package manager
- Virtual environment (recommended)

### 13.2 Installation Steps

```bash
# 1. Clone repository
git clone <repository-url>
cd exam_seating

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create PostgreSQL database
createdb exam_seating

# 5. Run database scripts
psql -d exam_seating -f database/schema.sql
psql -d exam_seating -f database/procedures.sql
psql -d exam_seating -f database/triggers.sql
psql -d exam_seating -f database/views.sql

# 6. Set environment variables
export DATABASE_URL="postgresql://user:pass@localhost/exam_seating"
export SECRET_KEY="your-secret-key-here"

# 7. Run application
python app.py
```

### 13.3 Production Considerations

| Aspect | Recommendation |
|--------|----------------|
| Web Server | Gunicorn + Nginx |
| Database | Dedicated PostgreSQL server |
| Sessions | Redis for session storage |
| SSL | Let's Encrypt certificates |
| Monitoring | Prometheus + Grafana |
| Backups | Daily pg_dump automation |
| Scaling | Load balancer for multiple instances |

---

## 14. Future Enhancements

### 14.1 Planned Features

| Feature | Priority | Status |
|---------|----------|--------|
| Email notifications | High | Planned |
| Mobile app | Medium | Planned |
| Biometric attendance | Medium | Research |
| AI answer similarity | Low | Research |
| Real-time updates | Medium | Planned |
| Multi-language support | Low | Planned |

### 14.2 Technical Improvements

| Improvement | Benefit |
|-------------|---------|
| Redis caching | Faster API responses |
| WebSocket | Real-time seat updates |
| GraphQL API | Flexible queries |
| Docker deployment | Easier scaling |
| Kubernetes | Auto-scaling |
| CI/CD pipeline | Automated testing |

### 14.3 Algorithm Enhancements

| Enhancement | Description |
|-------------|-------------|
| Genetic algorithm | Better optimization |
| Machine learning | Predict friend groups |
| Simulated annealing | Escape local minima |
| Parallel processing | Faster generation |

---

## 15. Conclusion

The **Exam Seating Arrangement System** successfully addresses the challenges of manual seating allocation through:

1. **Intelligent Automation**: DSatur graph coloring ensures conflict-free groupings
2. **Cheat Prevention**: Friend/relative separation maintains academic integrity
3. **Complete Traceability**: Comprehensive audit logging for accountability
4. **User-Friendly Interface**: Retro-styled UI with modern interactivity
5. **Robust Security**: 2FA, password hashing, session management
6. **Scalability**: Handles 1000+ students efficiently

The system demonstrates practical application of:
- **Graph theory** (coloring, adjacency)
- **Constraint satisfaction** (room assignments)
- **Database design** (normalization, views, triggers)
- **Web development** (Flask, REST APIs)
- **Security practices** (2FA, auditing)

This project serves as a comprehensive solution for educational institutions seeking to automate exam logistics while maintaining academic integrity.

---

## 16. References

1. **DSatur Algorithm**: Brélaz, D. (1979). "New methods to color the vertices of a graph"
2. **Flask Documentation**: https://flask.palletsprojects.com/
3. **PostgreSQL Documentation**: https://www.postgresql.org/docs/
4. **NetworkX Documentation**: https://networkx.org/documentation/
5. **SQLAlchemy Documentation**: https://docs.sqlalchemy.org/
6. **OWASP Security Guidelines**: https://owasp.org/

---

## Appendix A: Database Schema

See `database/schema.sql` for complete table definitions.

## Appendix B: API Response Formats

See `API_DOCUMENTATION.md` for detailed request/response examples.

## Appendix C: Configuration Options

See `config.py` for all configurable parameters.

---

**Document Version**: 1.0
**Last Updated**: January 2026
**Authors**: Exam Seating Development Team
