from collections import defaultdict, deque


def get_adjacent_positions(x, y, cols, rows):
    """Get all valid adjacent positions (including diagonals)."""
    adjacents = []
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < cols and 0 <= ny < rows:
                adjacents.append((nx, ny))
    return adjacents


def is_adjacent_to_friend(x, y, student_id, assigned_positions, friend_graph, cols, rows):
    """
    Check if placing student at (x, y) would be adjacent to a friend.

    Args:
        x, y: Position to check
        student_id: Student being placed
        assigned_positions: Dict mapping (x, y) -> student_id
        friend_graph: Dict mapping student_id -> set of friend student_ids
        cols, rows: Room dimensions

    Returns:
        True if position is adjacent to a friend
    """
    if student_id not in friend_graph:
        return False

    friends = friend_graph[student_id]
    for adj_x, adj_y in get_adjacent_positions(x, y, cols, rows):
        if (adj_x, adj_y) in assigned_positions:
            adjacent_student = assigned_positions[(adj_x, adj_y)]
            if adjacent_student in friends:
                return True
    return False


def find_non_adjacent_position(student_id, assigned_positions, friend_graph,
                                cols, rows, start_idx=0):
    """
    Find a position that is not adjacent to any friends.

    Args:
        student_id: Student to place
        assigned_positions: Currently assigned positions
        friend_graph: Friend adjacency dict
        cols, rows: Room dimensions
        start_idx: Starting index for search

    Returns:
        Tuple of (x, y, seat_no) or None if no valid position found
    """
    total_seats = cols * rows

    # First pass: find position not adjacent to friends
    for idx in range(total_seats):
        x, y = idx % cols, idx // cols
        if (x, y) in assigned_positions:
            continue
        if not is_adjacent_to_friend(x, y, student_id, assigned_positions,
                                     friend_graph, cols, rows):
            return (x, y, idx + 1)

    # Fallback: return any empty position (friends will be flagged by trigger)
    for idx in range(total_seats):
        x, y = idx % cols, idx // cols
        if (x, y) not in assigned_positions:
            return (x, y, idx + 1)

    return None


def assign_seats_with_separation(room_assignment, metadata, room_config, friend_graph=None):
    """
    Assign seats ensuring friends are not placed in adjacent positions.

    Args:
        room_assignment: Dict mapping room -> list of student_ids
        metadata: Dict mapping student_id -> student info
        room_config: Room configuration dict
        friend_graph: Dict mapping student_id -> set of friend student_ids.
                      If None, fetches from database.

    Returns:
        Dict mapping room -> list of seat assignments
    """
    # Fetch friend graph from database if not provided
    if friend_graph is None:
        try:
            from conflict_graph import get_friend_graph_from_db
            friend_graph = get_friend_graph_from_db()
        except ImportError:
            friend_graph = {}

    seating = {}
    adjacency_violations = []

    for room, students in room_assignment.items():
        if not students:
            continue

        # Group students by year for interleaving
        year_groups = defaultdict(list)
        for sid in students:
            info = metadata.get(sid, {})
            year = info.get('Year', 'Unknown')
            year_groups[year].append(sid)

        queue = interleave_groups(year_groups.values())

        # Get room layout configuration
        if isinstance(room_config, dict) and room in room_config:
            layout_info = room_config[room]
            cols = layout_info.get('layout_columns', layout_info.get('columns', 6))
            rows = layout_info.get('layout_rows', layout_info.get('rows', 5))
        else:
            cols, rows = 6, 5

        # Assign seats avoiding friend adjacencies
        seats = []
        assigned_positions = {}

        for student_id in queue:
            position = find_non_adjacent_position(
                student_id, assigned_positions, friend_graph, cols, rows
            )

            if position is None:
                continue

            x, y, seat_no = position
            assigned_positions[(x, y)] = student_id

            # Check for unavoidable adjacency violations
            if is_adjacent_to_friend(x, y, student_id, assigned_positions,
                                     friend_graph, cols, rows):
                adjacency_violations.append({
                    'room': room,
                    'student_id': student_id,
                    'position': (x, y)
                })

            student_info = metadata.get(student_id, {})
            seats.append({
                'x': x,
                'y': y,
                'student_id': student_id,
                'seat_no': seat_no,
                'Name': student_info.get('Name', 'Unknown'),
                'Department': student_info.get('Department', 'Unknown'),
                'Branch': student_info.get('Branch', 'Unknown'),
                'Year': student_info.get('Year', 'Unknown'),
                'Subject': student_info.get('Subject', 'Unknown'),
                'ExamTime': student_info.get('ExamTime', 'Unknown')
            })

        seating[room] = seats

    return seating


def get_spread_positions(num_students, cols, rows):
    """
    Generate seat positions that spread students across the room with gaps.
    Uses checkerboard pattern and maximizes distance between students.
    """
    total_seats = cols * rows

    if num_students >= total_seats:
        # No room for gaps, use all seats
        return [(i % cols, i // cols, i + 1) for i in range(total_seats)]

    # Calculate optimal spacing
    spacing = max(1, total_seats // num_students)

    positions = []

    if spacing >= 2:
        # Use checkerboard pattern - every other seat
        # First pass: even positions (0, 2, 4...)
        for row in range(rows):
            start = row % 2  # Alternate starting position per row
            for col in range(start, cols, 2):
                idx = row * cols + col
                positions.append((col, row, idx + 1))
                if len(positions) >= num_students:
                    return positions

        # Second pass: fill odd positions if needed
        for row in range(rows):
            start = 1 - (row % 2)  # Opposite of first pass
            for col in range(start, cols, 2):
                idx = row * cols + col
                pos = (col, row, idx + 1)
                if pos not in positions:
                    positions.append(pos)
                    if len(positions) >= num_students:
                        return positions
    else:
        # Not enough room for checkerboard, use sequential
        for i in range(min(num_students, total_seats)):
            positions.append((i % cols, i // cols, i + 1))

    return positions[:num_students]


def assign_seats_in_room(room_assignment, metadata, room_config, friend_graph=None):
    """
    Assign seats with year grouping and branch/subject distribution.
    If friend_graph is provided, attempts to prevent friend adjacencies.
    Spreads students across room to avoid adjacent seating when possible.
    """
    if friend_graph:
        return assign_seats_with_separation(room_assignment, metadata, room_config, friend_graph)

    seating = {}

    for room, students in room_assignment.items():
        if not students:
            continue

        # Group students by year
        year_groups = defaultdict(list)
        for sid in students:
            info = metadata.get(sid, {})
            year = info.get('Year', 'Unknown')
            year_groups[year].append(sid)

        queue = interleave_groups(year_groups.values())

        # Get room layout configuration
        if isinstance(room_config, dict) and room in room_config:
            layout_info = room_config[room]
            cols = layout_info.get('layout_columns', layout_info.get('columns', 6))
            rows = layout_info.get('layout_rows', layout_info.get('rows', 5))
        else:
            cols, rows = 6, 5

        # Get spread positions for students
        positions = get_spread_positions(len(queue), cols, rows)

        # Generate seating coordinates with spread layout
        seats = []
        for idx, student_id in enumerate(queue):
            if idx >= len(positions):
                break

            x, y, seat_no = positions[idx]
            student_info = metadata.get(student_id, {})
            seats.append({
                'x': x,
                'y': y,
                'student_id': student_id,
                'seat_no': seat_no,
                'Name': student_info.get('Name', 'Unknown'),
                'Department': student_info.get('Department', 'Unknown'),
                'Branch': student_info.get('Branch', 'Unknown'),
                'Year': student_info.get('Year', 'Unknown'),
                'Subject': student_info.get('Subject', 'Unknown'),
                'ExamTime': student_info.get('ExamTime', 'Unknown')
            })

        seating[room] = seats

    return seating

def interleave_groups(groups):
    """Interleave students from different groups using round-robin."""
    non_empty_groups = [group for group in groups if group]
    if not non_empty_groups:
        return []

    queues = [deque(group) for group in non_empty_groups]
    result = []

    while any(queues):
        for q in queues:
            if q:
                result.append(q.popleft())

    return result