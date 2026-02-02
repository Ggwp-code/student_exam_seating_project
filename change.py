import pandas as pd

# Read old data
df = pd.read_csv('data/students_old.csv')

# Add missing columns with default values
df['Name'] = 'Pending Registration'
df['Password'] = ''
df['Photo'] = ''
df['Location'] = ''

# Reorder columns and save
new_df = df[['StudentID', 'Name', 'Department', 'Subject', 
            'ExamTime', 'Password', 'Photo', 'Location']]
new_df.to_csv('data/students.csv', index=False)