"""
SQLAlchemy ORM Models for Exam Seating System

Usage:
    from models import db, User, Student, Room, Exam, SeatingAssignment

    # Initialize with Flask app
    db.init_app(app)
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Import all models to make them available from the package
from .user import User, UserRole
from .student import Student, Department
from .room import Room, Invigilator
from .seating import Exam, ExamEnrollment, SeatingAssignment, SeatingHistory, ExamTimeSlot, SectionExamAssignment
from .relationships import StudentRelationship, CheatDetectionFlag, RelationshipType
from .audit import AuditLog, AuditAction, SystemConfig

__all__ = [
    'db',
    # User
    'User', 'UserRole',
    # Student
    'Student', 'Department',
    # Room
    'Room', 'Invigilator',
    # Seating
    'Exam', 'ExamEnrollment', 'SeatingAssignment', 'SeatingHistory', 'ExamTimeSlot', 'SectionExamAssignment',
    # Relationships
    'StudentRelationship', 'CheatDetectionFlag', 'RelationshipType',
    # Audit
    'AuditLog', 'AuditAction', 'SystemConfig',
]
