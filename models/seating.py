"""
Exam, Enrollment, and Seating Assignment models.
"""

import enum
from . import db
from .base import TimestampMixin, ActiveMixin


class ExamTimeSlot(enum.Enum):
    MORNING = 'Morning'
    AFTERNOON = 'Afternoon'
    EVENING = 'Evening'


class Exam(db.Model, TimestampMixin, ActiveMixin):
    __tablename__ = 'exams'

    id = db.Column(db.Integer, primary_key=True)
    exam_code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'))
    exam_date = db.Column(db.Date, nullable=False)
    # Use native PostgreSQL enum with matching values
    exam_time = db.Column(
        db.Enum(ExamTimeSlot, values_callable=lambda x: [e.value for e in x], name='exam_time_slot'),
        nullable=False
    )
    duration_minutes = db.Column(db.Integer, default=180)
    total_students = db.Column(db.Integer, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))

    # Relationships
    enrollments = db.relationship('ExamEnrollment', backref='exam', lazy='dynamic',
                                  cascade='all, delete-orphan')
    seating_assignments = db.relationship('SeatingAssignment', backref='exam', lazy='dynamic',
                                          cascade='all, delete-orphan')
    history = db.relationship('SeatingHistory', backref='exam', lazy='dynamic',
                              cascade='all, delete-orphan')
    cheat_flags = db.relationship('CheatDetectionFlag', backref='exam', lazy='dynamic',
                                  cascade='all, delete-orphan')

    def get_enrolled_students(self):
        """Get all enrolled students."""
        from .student import Student
        return Student.query.join(ExamEnrollment).filter(
            ExamEnrollment.exam_id == self.id
        ).all()

    def get_seating_stats(self):
        """Get seating statistics for this exam."""
        from .relationships import CheatDetectionFlag

        enrolled = self.total_students
        seated = self.seating_assignments.count()
        rooms_used = db.session.query(
            db.func.count(db.distinct(SeatingAssignment.room_id))
        ).filter(SeatingAssignment.exam_id == self.id).scalar()

        friend_flags = CheatDetectionFlag.query.filter_by(
            exam_id=self.id,
            flag_type='friend_adjacent'
        ).count()

        return {
            'enrolled': enrolled,
            'seated': seated,
            'rooms_used': rooms_used or 0,
            'friend_adjacencies': friend_flags,
            'seating_complete': enrolled == seated
        }

    def __repr__(self):
        return f'<Exam {self.exam_code}: {self.subject} on {self.exam_date}>'


class ExamEnrollment(db.Model):
    __tablename__ = 'exam_enrollments'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey('exams.id', ondelete='CASCADE'), nullable=False)
    enrolled_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())

    __table_args__ = (
        db.UniqueConstraint('student_id', 'exam_id', name='uq_enrollment_student_exam'),
    )

    def __repr__(self):
        return f'<ExamEnrollment student={self.student_id} exam={self.exam_id}>'


class SeatingAssignment(db.Model):
    __tablename__ = 'seating_assignments'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('exams.id', ondelete='CASCADE'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False)
    seat_number = db.Column(db.Integer, nullable=False)
    seat_x = db.Column(db.Integer, nullable=False)
    seat_y = db.Column(db.Integer, nullable=False)
    color_group = db.Column(db.Integer)
    assigned_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    assigned_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    is_manual_override = db.Column(db.Boolean, default=False)
    override_reason = db.Column(db.Text)

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'student_id', name='uq_seating_exam_student'),
        db.UniqueConstraint('exam_id', 'room_id', 'seat_number', name='uq_seating_exam_room_seat'),
    )

    def get_adjacent_students(self):
        """Get students in adjacent seats (for cheat detection)."""
        adjacents = SeatingAssignment.query.filter(
            SeatingAssignment.exam_id == self.exam_id,
            SeatingAssignment.room_id == self.room_id,
            SeatingAssignment.id != self.id,
            db.func.abs(SeatingAssignment.seat_x - self.seat_x) <= 1,
            db.func.abs(SeatingAssignment.seat_y - self.seat_y) <= 1
        ).all()
        return adjacents

    def __repr__(self):
        return f'<SeatingAssignment student={self.student_id} room={self.room_id} seat={self.seat_number}>'


class SeatingHistory(db.Model):
    __tablename__ = 'seating_history'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('exams.id', ondelete='CASCADE'), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    generated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    generated_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    total_students = db.Column(db.Integer)
    rooms_used = db.Column(db.Integer)
    algorithm_used = db.Column(db.String(50))
    generation_time_ms = db.Column(db.Integer)
    snapshot = db.Column(db.JSON, nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'version', name='uq_history_exam_version'),
    )

    def __repr__(self):
        return f'<SeatingHistory exam={self.exam_id} v{self.version}>'


class SectionExamAssignment(db.Model):
    """Assigns entire sections to exams for bulk enrollment"""
    __tablename__ = 'section_exam_assignments'

    id = db.Column(db.Integer, primary_key=True)
    department_code = db.Column(db.String(10), nullable=False)
    branch = db.Column(db.String(50), nullable=False)
    section = db.Column(db.String(10), nullable=False)
    year = db.Column(db.Integer)
    semester = db.Column(db.Integer)
    exam_id = db.Column(db.Integer, db.ForeignKey('exams.id', ondelete='CASCADE'), nullable=False)
    assigned_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    assigned_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())

    # Relationships
    exam = db.relationship('Exam', backref=db.backref('section_assignments', lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('department_code', 'branch', 'section', 'exam_id',
                           name='uq_section_exam_assignment'),
    )

    def __repr__(self):
        return f'<SectionExamAssignment {self.department_code}-{self.branch}-{self.section} -> Exam {self.exam_id}>'
