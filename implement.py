import pandas as pd
import networkx as nx
from collections import defaultdict
from conflict_graph import dsatur_coloring

# Load data with error handling
try:
    df = pd.read_csv('students.csv')
except FileNotFoundError:
    raise SystemExit("❌ Error: students.csv file not found")

# Check required columns
required_columns = ['StudentID', 'Subject', 'ExamTime']
if not all(col in df.columns for col in required_columns):
    raise SystemExit("❌ Error: CSV file is missing required columns")

# Create conflict graph
G = nx.Graph()

# Efficiently add edges using pandas merge
# Create self-merge on Subject and ExamTime
conflicts = pd.merge(df, df, on=['Subject', 'ExamTime'])
# Filter out same-student pairs and duplicate pairs
conflicts = conflicts[conflicts['StudentID_x'] < conflicts['StudentID_y']]

# Add edges from conflicts
for _, row in conflicts.iterrows():
    G.add_edge(row['StudentID_x'], row['StudentID_y'])

# Apply DSatur algorithm
try:
    color_mapping = dsatur_coloring(G)
except nx.NetworkXException as e:
    raise SystemExit(f"❌ Graph coloring failed: {e}")

# Group students by color with sorting
groups = defaultdict(list)
for student, color in color_mapping.items():
    groups[color].append(student)

# Sort groups by color and student IDs for consistent output
sorted_groups = sorted(groups.items(), key=lambda x: x[0])
for color, members in sorted_groups:
    sorted_members = sorted(members)
    print(f"Group {color + 1} ({len(sorted_members)} students):")
    print(", ".join(sorted_members))
    print("-" * 50)