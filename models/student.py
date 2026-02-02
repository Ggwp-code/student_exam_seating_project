"""
Student and Department models.
"""

from . import db
from .base import TimestampMixin, ActiveMixin


class Department(db.Model):
    __tablename__ = 'departments'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())

    # Relationships
    students = db.relationship('Student', backref='department', lazy='dynamic')
    exams = db.relationship('Exam', backref='department', lazy='dynamic')

    @classmethod
    def get_or_create(cls, code, name=None):
        """Get existing department or create new one."""
        dept = cls.query.filter_by(code=code).first()
        if dept is None:
            dept = cls(code=code, name=name or code)
            db.session.add(dept)
        return dept

    def __repr__(self):
        return f'<Department {self.code}: {self.name}>'


class Student(db.Model, TimestampMixin, ActiveMixin):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'))
    branch = db.Column(db.String(50))
    section = db.Column(db.String(10))
    year = db.Column(db.Integer)
    semester = db.Column(db.Integer)
    batch = db.Column(db.String(20))
    email = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    photo_path = db.Column(db.String(500))
    gender = db.Column(db.String(1))

    # Relationships
    enrollments = db.relationship('ExamEnrollment', backref='student', lazy='dynamic')
    seating_assignments = db.relationship('SeatingAssignment', backref='student', lazy='dynamic')

    def get_friends(self):
        """Get all students with active friend relationships."""
        from .relationships import StudentRelationship

        relationships = StudentRelationship.query.filter(
            db.or_(
                StudentRelationship.student1_id == self.id,
                StudentRelationship.student2_id == self.id
            ),
            StudentRelationship.is_active == True
        ).all()

        friends = []
        for rel in relationships:
            friend_id = rel.student2_id if rel.student1_id == self.id else rel.student1_id
            friend = Student.query.get(friend_id)
            if friend:
                friends.append({
                    'student': friend,
                    'relationship_type': rel.relationship_type,
                    'relationship_id': rel.id
                })
        return friends

    def get_friend_ids(self):
        """Get just the IDs of friends (for seating algorithm)."""
        return [f['student'].id for f in self.get_friends()]

    def get_upcoming_exams(self):
        """Get student's upcoming exams with seating info."""
        from .seating import Exam, ExamEnrollment, SeatingAssignment
        from datetime import date

        return db.session.query(
            Exam, SeatingAssignment
        ).join(
            ExamEnrollment, ExamEnrollment.exam_id == Exam.id
        ).outerjoin(
            SeatingAssignment,
            db.and_(
                SeatingAssignment.exam_id == Exam.id,
                SeatingAssignment.student_id == self.id
            )
        ).filter(
            ExamEnrollment.student_id == self.id,
            Exam.exam_date >= date.today(),
            Exam.is_active == True
        ).order_by(Exam.exam_date, Exam.exam_time).all()

    def __repr__(self):
        return f'<Student {self.student_id}: {self.name}>'
