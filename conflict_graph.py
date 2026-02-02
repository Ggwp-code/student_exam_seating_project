import networkx as nx
from collections import defaultdict


# Edge weight for friend relationships (higher = stronger separation)
FRIEND_EDGE_WEIGHT = 5
SECTION_EDGE_WEIGHT = 2


def get_friend_pairs_from_db():
    """
    Fetch active friend relationships from database.
    Returns set of (student_id, student_id) tuples.
    """
    try:
        from models import db, StudentRelationship
        return StudentRelationship.get_all_pairs()
    except ImportError:
        return set()
    except Exception:
        return set()


def get_friend_graph_from_db():
    """
    Fetch friend relationships as adjacency dict.
    Returns dict mapping student_id -> set of friend student_ids.
    """
    try:
        from models import StudentRelationship
        return StudentRelationship.get_friend_graph()
    except ImportError:
        return {}
    except Exception:
        return {}


def build_enhanced_conflict_graph(df, friend_pairs=None, section_separation=True):
    """
    Build conflict graph with exam conflicts and friend relationships.

    Args:
        df: DataFrame with student exam data
        friend_pairs: Set of (student1_id, student2_id) tuples, or None to fetch from DB
        section_separation: If True, add soft edges between same-section students

    Returns:
        NetworkX graph with weighted edges
    """
    graph = nx.Graph()

    # Add all students as nodes
    for student_id in df['StudentID']:
        graph.add_node(student_id)

    # Add edges between students with same exam date/time (hard constraint)
    for i in range(len(df)):
        s1 = df.iloc[i]
        for j in range(i + 1, len(df)):
            s2 = df.iloc[j]
            if s1['ExamDate'] == s2['ExamDate'] and s1['ExamTime'] == s2['ExamTime']:
                graph.add_edge(s1['StudentID'], s2['StudentID'], weight=10)

    # Fetch friend pairs from database if not provided
    if friend_pairs is None:
        friend_pairs = get_friend_pairs_from_db()

    # Add friend edges (weighted for separation priority)
    student_ids = set(df['StudentID'])
    for s1_id, s2_id in friend_pairs:
        if s1_id in student_ids and s2_id in student_ids:
            if graph.has_edge(s1_id, s2_id):
                # Increase weight if edge exists
                graph[s1_id][s2_id]['weight'] += FRIEND_EDGE_WEIGHT
            else:
                graph.add_edge(s1_id, s2_id, weight=FRIEND_EDGE_WEIGHT)

    # Add soft edges between same-section students
    if section_separation:
        section_groups = defaultdict(list)
        for _, row in df.iterrows():
            section_key = (row.get('Batch', ''), row.get('Year', ''), row.get('Department', ''))
            section_groups[section_key].append(row['StudentID'])

        for students in section_groups.values():
            if len(students) > 1:
                for i in range(len(students)):
                    for j in range(i + 1, len(students)):
                        s1, s2 = students[i], students[j]
                        if not graph.has_edge(s1, s2):
                            graph.add_edge(s1, s2, weight=SECTION_EDGE_WEIGHT)

    return graph


def dsatur_coloring(graph):
    """
    DSatur (Degree of Saturation) graph coloring algorithm.
    Returns a mapping of nodes to colors (integers).
    """
    if len(graph.nodes) == 0:
        return {}

    colors = {}
    saturation = defaultdict(set)
    degrees = dict(graph.degree())

    # Start with highest degree node
    current = max(degrees, key=lambda x: degrees[x]) if degrees else list(graph.nodes)[0]
    colors[current] = 0

    while len(colors) < len(graph.nodes):
        # Update saturation for uncolored nodes
        for node in graph.nodes:
            if node not in colors:
                saturation[node] = {colors[nbr] for nbr in graph.neighbors(node) if nbr in colors}

        uncolored = [node for node in graph.nodes if node not in colors]
        if not uncolored:
            break

        # Select node with highest saturation (ties broken by degree)
        next_node = max(uncolored, key=lambda x: (len(saturation[x]), degrees.get(x, 0)))

        # Assign lowest available color
        used_colors = {colors[nbr] for nbr in graph.neighbors(next_node) if nbr in colors}
        color = 0
        while color in used_colors:
            color += 1

        colors[next_node] = color

    return colors


def get_colored_groups(df, friend_pairs=None, enable_friend_separation=True,
                        enable_section_separation=True):
    """
    Build conflict graph and partition students into non-conflicting groups.
    Students conflict if they have exams at the same date and time.

    Args:
        df: DataFrame with student exam data
        friend_pairs: Optional set of friend pairs. If None and friend_separation
                      is enabled, fetches from database.
        enable_friend_separation: Add friend constraints to prevent adjacent seating
        enable_section_separation: Add soft constraints for same-section students

    Returns:
        Dictionary mapping color (group ID) to list of student IDs
    """
    if enable_friend_separation or enable_section_separation:
        # Use enhanced graph with friend/section edges
        graph = build_enhanced_conflict_graph(
            df,
            friend_pairs=friend_pairs if enable_friend_separation else set(),
            section_separation=enable_section_separation
        )
    else:
        # Original basic conflict graph (exam time conflicts only)
        graph = nx.Graph()
        for student_id in df['StudentID']:
            graph.add_node(student_id)

        for i in range(len(df)):
            s1 = df.iloc[i]
            for j in range(i + 1, len(df)):
                s2 = df.iloc[j]
                if s1['ExamDate'] == s2['ExamDate'] and s1['ExamTime'] == s2['ExamTime']:
                    graph.add_edge(s1['StudentID'], s2['StudentID'])

    color_mapping = dsatur_coloring(graph)
    groups = defaultdict(list)
    for student, color in color_mapping.items():
        groups[color].append(student)

    return groups


def get_colored_groups_with_stats(df, friend_pairs=None, enable_friend_separation=True,
                                   enable_section_separation=True):
    """
    Build conflict graph and return groups with statistics.

    Returns:
        Tuple of (groups dict, stats dict)
    """
    if enable_friend_separation:
        if friend_pairs is None:
            friend_pairs = get_friend_pairs_from_db()

    graph = build_enhanced_conflict_graph(
        df,
        friend_pairs=friend_pairs if enable_friend_separation else set(),
        section_separation=enable_section_separation
    )

    color_mapping = dsatur_coloring(graph)
    groups = defaultdict(list)
    for student, color in color_mapping.items():
        groups[color].append(student)

    stats = {
        'total_students': len(df),
        'total_groups': len(groups),
        'graph_edges': graph.number_of_edges(),
        'friend_pairs_applied': len(friend_pairs) if friend_pairs else 0,
        'chromatic_number': len(set(color_mapping.values())) if color_mapping else 0
    }

    return groups, stats


def extract_student_metadata(df):
    """Extract metadata dictionary for each student from DataFrame."""
    metadata = {}
    for _, row in df.iterrows():
        metadata[row['StudentID']] = {
            'Name': row.get('Name', f"Student-{row['StudentID']}"),
            'Department': row['Department'],
            'Subject': row['Subject'],
            'ExamTime': row['ExamTime'],
            'ExamDate': row['ExamDate'],
            'Year': str(row['Year']),
            'Branch': row.get('Batch', 'Unknown'),
            'Semester': row.get('Semester', 'Unknown'),
            'Batch': row.get('Batch', 'Unknown'),
            'Photo': row.get('Photo', ''),
            'Location': row.get('Location', '')
        }
    return metadata