from collections import defaultdict, Counter
from typing import List, Dict

class Student:
    def __init__(self, student_id: str, metadata: dict):
        self.id = student_id
        self.year = int(metadata.get('Year'))
        self.subject = metadata.get('Subject')
        self.department = metadata.get('Department')
        self.branch = metadata.get('Branch', metadata.get('Batch', 'Unknown'))
        self.batch = metadata.get('Batch', 'Unknown')

class RoomConfig:
    def __init__(self, config: dict):
        self.room_id = config['room_name']
        self.capacity = config['capacity']
        self.max_subjects = config.get('max_subjects', 0)
        self.max_branches = config.get('max_branches', 0)
        # New constraints: max 2 departments and max 2 years per room
        self.max_departments = config.get('max_departments', 2)
        self.max_years = config.get('max_years', 2)

        if isinstance(config.get('allowed_years', ''), str):
            if config.get('allowed_years'):
                self.allowed_years = set(map(int, config['allowed_years'].split(',')))
            else:
                self.allowed_years = {1, 2, 3, 4, 5, 6}  # Allow all years if not specified
        elif isinstance(config.get('allowed_years'), list):
            # Handle both string and int lists
            self.allowed_years = set(int(year) if isinstance(year, str) else year for year in config['allowed_years'])
        else:
            self.allowed_years = set(config.get('allowed_years', {1, 2, 3, 4, 5, 6}))

def assign_rooms_to_groups(
    groups: Dict[int, List[str]],
    student_metadata: Dict[str, dict],
    rooms_config: List[dict]
) -> Dict[str, List[str]]:
    """
    Main entry point for room assignment
    Args:
        groups: Dictionary of colored groups {color: [student_ids]}
        student_metadata: Dictionary of student metadata {student_id: info}
        rooms_config: List of room configuration dictionaries
    Returns:
        Dictionary of {room_id: [student_ids]}
    """
    # Convert rooms config to RoomConfig objects
    room_objects = [RoomConfig(rc) for rc in rooms_config]
    
    # Convert student groups to Student objects
    student_groups = defaultdict(list)
    for color, student_ids in groups.items():
        student_groups[color] = [
            Student(sid, student_metadata[sid]) for sid in student_ids
        ]
    
    total_students = sum(len(students) for students in student_groups.values())
    total_capacity = sum(room.capacity for room in room_objects)

    if total_students > total_capacity:
        raise ValueError(f"Not enough room capacity. Need {total_students} seats, have {total_capacity}")

    # Try First-Fit Decreasing first
    ffd_result = first_fit_decreasing(student_groups, room_objects)
    if ffd_result is not None:
        return ffd_result

    # Fallback to backtracking
    return backtracking_assign(student_groups, room_objects)

def first_fit_decreasing(
    groups: Dict[int, List[Student]],
    rooms: List[RoomConfig]
) -> Dict[str, List[str]]:
    """Modified First-Fit Decreasing algorithm with flexible constraints.

    Enforces:
    - Max 2 departments per room
    - Max 2 years per room
    - Capacity, subject, and branch constraints
    """
    sorted_groups = sorted(groups.values(), key=lambda x: len(x), reverse=True)
    assignments = defaultdict(list)
    room_status = {
        room.room_id: {
            'remaining_capacity': room.capacity,
            'subjects': set(),
            'branches': set(),
            'years': set(),
            'departments': set(),  # Track departments in each room
            'students': []
        } for room in rooms
    }

    all_groups_placed = True

    for group in sorted_groups:
        placed = False
        group_years = {s.year for s in group}
        group_subjects = {s.subject for s in group}
        group_branches = {s.branch for s in group}
        group_departments = {s.department for s in group}

        # Try each room, sorted by remaining capacity (prefer less full rooms)
        sorted_rooms = sorted(rooms, key=lambda x: room_status[x.room_id]['remaining_capacity'], reverse=True)

        for room in sorted_rooms:
            status = room_status[room.room_id]

            # Check capacity
            if len(group) > status['remaining_capacity']:
                continue

            # Check year constraints (allowed years from config)
            if not group_years.issubset(room.allowed_years):
                continue

            # CONSTRAINT: Max years per room (default 2)
            if room.max_years > 0 and len(status['years'].union(group_years)) > room.max_years:
                continue

            # CONSTRAINT: Max departments per room (default 2)
            if room.max_departments > 0 and len(status['departments'].union(group_departments)) > room.max_departments:
                continue

            # Check subject constraints
            if room.max_subjects > 0 and len(status['subjects'].union(group_subjects)) > room.max_subjects:
                continue

            # Check branch constraints
            if room.max_branches > 0 and len(status['branches'].union(group_branches)) > room.max_branches:
                continue

            # All constraints met - assign students to this room
            status['remaining_capacity'] -= len(group)
            status['subjects'].update(group_subjects)
            status['branches'].update(group_branches)
            status['years'].update(group_years)
            status['departments'].update(group_departments)
            assignments[room.room_id].extend([s.id for s in group])
            status['students'].extend([s.id for s in group])
            placed = True
            break

        if not placed:
            all_groups_placed = False
            break

    if not all_groups_placed:
        return None

    # If all groups were placed successfully, return the accumulated assignments
    final_assignments = {rid: s_ids for rid, s_ids in assignments.items() if s_ids}
    return final_assignments

def backtracking_assign(
    groups: Dict[int, List[Student]],
    rooms: List[RoomConfig]
) -> Dict[str, List[str]]:
    """Backtracking algorithm for room assignment with flexible constraints.

    Enforces:
    - Max 2 departments per room
    - Max 2 years per room
    - Capacity, subject, and branch constraints
    """
    sorted_groups = sorted(groups.values(), key=lambda x: len(x), reverse=True)
    room_assignments = defaultdict(list)
    room_status = {
        room.room_id: {
            'remaining_capacity': room.capacity,
            'subjects': set(),
            'branches': set(),
            'years': set(),
            'departments': set()  # Track departments
        } for room in rooms
    }

    def can_place(room_id, group_students):
        room_config = next(r for r in rooms if r.room_id == room_id)
        status = room_status[room_id]

        group_years = {s.year for s in group_students}
        group_subjects = {s.subject for s in group_students}
        group_branches = {s.branch for s in group_students}
        group_departments = {s.department for s in group_students}

        if len(group_students) > status['remaining_capacity']:
            return False

        if not group_years.issubset(room_config.allowed_years):
            return False

        # CONSTRAINT: Max years per room (default 2)
        if room_config.max_years > 0 and len(status['years'].union(group_years)) > room_config.max_years:
            return False

        # CONSTRAINT: Max departments per room (default 2)
        if room_config.max_departments > 0 and len(status['departments'].union(group_departments)) > room_config.max_departments:
            return False

        if room_config.max_subjects > 0 and len(status['subjects'].union(group_subjects)) > room_config.max_subjects:
            return False

        if room_config.max_branches > 0 and len(status['branches'].union(group_branches)) > room_config.max_branches:
            return False

        return True

    def dfs(index):
        if index == len(sorted_groups):
            return True  # All groups assigned

        group = sorted_groups[index]
        group_years = {s.year for s in group}
        group_subjects = {s.subject for s in group}
        group_branches = {s.branch for s in group}
        group_departments = {s.department for s in group}

        for room in rooms:
            if can_place(room.room_id, group):
                original_status = {  # Store original status for backtracking
                    'remaining_capacity': room_status[room.room_id]['remaining_capacity'],
                    'subjects': room_status[room.room_id]['subjects'].copy(),
                    'branches': room_status[room.room_id]['branches'].copy(),
                    'years': room_status[room.room_id]['years'].copy(),
                    'departments': room_status[room.room_id]['departments'].copy()
                }

                # Make assignment
                room_status[room.room_id]['remaining_capacity'] -= len(group)
                room_status[room.room_id]['subjects'].update(group_subjects)
                room_status[room.room_id]['branches'].update(group_branches)
                room_status[room.room_id]['years'].update(group_years)
                room_status[room.room_id]['departments'].update(group_departments)
                room_assignments[room.room_id].extend([s.id for s in group])

                # Recurse
                if dfs(index + 1):
                    return True

                # Backtrack
                room_status[room.room_id]['remaining_capacity'] = original_status['remaining_capacity']
                room_status[room.room_id]['subjects'] = original_status['subjects']
                room_status[room.room_id]['branches'] = original_status['branches']
                room_status[room.room_id]['years'] = original_status['years']
                room_status[room.room_id]['departments'] = original_status['departments']
                room_assignments[room.room_id] = [
                    sid for sid in room_assignments[room.room_id]
                    if sid not in [s.id for s in group]
                ]

        return False

    if dfs(0):
        return {rid: students for rid, students in room_assignments.items() if students}

    raise ValueError("No valid room assignment possible with current constraints")