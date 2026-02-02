"""
Student Relationship and Cheat Detection models.
"""

import enum
from . import db
from .base import TimestampMixin, ActiveMixin


class RelationshipType(enum.Enum):
    FRIEND = 'friend'
    RELATIVE = 'relative'
    SAME_HOSTEL = 'same_hostel'
    SAME_ROOM = 'same_room'


class StudentRelationship(db.Model, TimestampMixin, ActiveMixin):
    __tablename__ = 'student_relationships'

    id = db.Column(db.Integer, primary_key=True)
    student1_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    student2_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    relationship_type = db.Column(
        db.Enum(RelationshipType, values_callable=lambda x: [e.value for e in x], name='relationship_type'),
        default=RelationshipType.FRIEND
    )
    reported_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    notes = db.Column(db.Text)

    # Relationships
    student1 = db.relationship('Student', foreign_keys=[student1_id], backref='relationships_as_first')
    student2 = db.relationship('Student', foreign_keys=[student2_id], backref='relationships_as_second')
    reporter = db.relationship('User', foreign_keys=[reported_by])

    __table_args__ = (
        db.CheckConstraint('student1_id < student2_id', name='ck_relationship_ordering'),
        db.UniqueConstraint('student1_id', 'student2_id', name='uq_relationship_pair'),
    )

    @classmethod
    def add_relationship(cls, student_a_id, student_b_id, rel_type=RelationshipType.FRIEND,
                         reported_by=None, notes=None):
        """
        Add a relationship between two students.
        Automatically orders IDs for consistent storage.
        """
        # Ensure consistent ordering
        s1, s2 = min(student_a_id, student_b_id), max(student_a_id, student_b_id)

        existing = cls.query.filter_by(student1_id=s1, student2_id=s2).first()
        if existing:
            existing.relationship_type = rel_type
            existing.is_active = True
            if notes:
                existing.notes = notes
            return existing

        rel = cls(
            student1_id=s1,
            student2_id=s2,
            relationship_type=rel_type,
            reported_by=reported_by,
            notes=notes
        )
        db.session.add(rel)
        return rel

    @classmethod
    def get_all_pairs(cls):
        """Get all active relationship pairs as set of tuples."""
        relationships = cls.query.filter_by(is_active=True).all()
        return {(r.student1_id, r.student2_id) for r in relationships}

    @classmethod
    def get_friend_graph(cls):
        """Get friend relationships as adjacency dict for algorithms."""
        from collections import defaultdict

        relationships = cls.query.filter_by(is_active=True).all()
        graph = defaultdict(set)

        for rel in relationships:
            graph[rel.student1_id].add(rel.student2_id)
            graph[rel.student2_id].add(rel.student1_id)

        return dict(graph)

    def __repr__(self):
        return f'<StudentRelationship {self.student1_id}<->{self.student2_id} ({self.relationship_type.value})>'


class CheatDetectionFlag(db.Model):
    __tablename__ = 'cheat_detection_flags'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('exams.id', ondelete='CASCADE'))
    student1_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'))
    student2_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'))
    flag_type = db.Column(db.String(50), nullable=False)
    severity = db.Column(db.String(20), default='low')
    details = db.Column(db.JSON)
    reviewed = db.Column(db.Boolean, default=False)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    reviewed_at = db.Column(db.DateTime)
    resolution_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())

    # Relationships
    student1 = db.relationship('Student', foreign_keys=[student1_id])
    student2 = db.relationship('Student', foreign_keys=[student2_id])
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

    FLAG_TYPES = {
        'friend_adjacent': 'Friends seated adjacent',
        'same_section_adjacent': 'Same section students adjacent',
        'similar_answers': 'Similar answer patterns (future)',
        'suspicious_timing': 'Suspicious submission timing (future)'
    }

    SEVERITY_LEVELS = ['low', 'medium', 'high', 'critical']

    @classmethod
    def create_flag(cls, exam_id, student1_id, student2_id, flag_type,
                    severity='low', details=None):
        """Create a new cheat detection flag with consistent student ordering."""
        s1, s2 = min(student1_id, student2_id), max(student1_id, student2_id)

        # Check if similar flag already exists
        existing = cls.query.filter_by(
            exam_id=exam_id,
            student1_id=s1,
            student2_id=s2,
            flag_type=flag_type
        ).first()

        if existing:
            return existing

        flag = cls(
            exam_id=exam_id,
            student1_id=s1,
            student2_id=s2,
            flag_type=flag_type,
            severity=severity,
            details=details
        )
        db.session.add(flag)
        return flag

    def mark_reviewed(self, user_id, notes=None):
        """Mark this flag as reviewed."""
        from datetime import datetime
        self.reviewed = True
        self.reviewed_by = user_id
        self.reviewed_at = datetime.utcnow()
        if notes:
            self.resolution_notes = notes

    def __repr__(self):
        return f'<CheatFlag {self.flag_type} students={self.student1_id},{self.student2_id}>'
