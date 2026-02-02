#!/bin/bash
# Database Setup Script for Exam Seating System
# Run with: sudo ./setup_database.sh

set -e

DB_NAME="exam_seating"
DB_USER="exam_user"
DB_PASS="exam_pass_2025"

echo "=== Exam Seating System - Database Setup ==="

# Start PostgreSQL if not running
echo "Starting PostgreSQL..."
systemctl start postgresql || service postgresql start

# Wait for PostgreSQL to be ready
sleep 2

# Create database and user
echo "Creating database and user..."
sudo -u postgres psql << EOF
-- Create user if not exists
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$DB_USER') THEN
        CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';
    END IF;
END
\$\$;

-- Create database if not exists
SELECT 'CREATE DATABASE $DB_NAME OWNER $DB_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME')\gexec

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
EOF

# Run schema files
echo "Running schema.sql..."
PGPASSWORD=$DB_PASS psql -h localhost -U $DB_USER -d $DB_NAME -f database/schema.sql

echo "Running triggers.sql..."
PGPASSWORD=$DB_PASS psql -h localhost -U $DB_USER -d $DB_NAME -f database/triggers.sql

echo "Running procedures.sql..."
PGPASSWORD=$DB_PASS psql -h localhost -U $DB_USER -d $DB_NAME -f database/procedures.sql

echo "Running views.sql..."
PGPASSWORD=$DB_PASS psql -h localhost -U $DB_USER -d $DB_NAME -f database/views.sql

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Database URL: postgresql://$DB_USER:$DB_PASS@localhost/$DB_NAME"
echo ""
echo "Add this to your environment:"
echo "  export DATABASE_URL=\"postgresql://$DB_USER:$DB_PASS@localhost/$DB_NAME\""
echo ""
echo "Then run the migration:"
echo "  python migrations/migrate_sqlite_to_postgres.py"
