"""
User model for authentication and authorization.
"""

import enum
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from . import db
from .base import TimestampMixin, ActiveMixin


class UserRole(enum.Enum):
    ADMIN = 'admin'
    TEACHER = 'teacher'
    STUDENT = 'student'


class User(db.Model, TimestampMixin, ActiveMixin):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(200))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum(UserRole), nullable=False)
    totp_secret = db.Column(db.String(100))
    last_login = db.Column(db.DateTime)
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='SET NULL'))

    # Relationships
    student = db.relationship('Student', backref='user_account', lazy='joined')
    invigilator_assignments = db.relationship('Invigilator', backref='user', lazy='dynamic')

    def set_password(self, password):
        """Hash and set the user's password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify the password against the hash."""
        return check_password_hash(self.password_hash, password)

    def is_locked(self):
        """Check if account is currently locked."""
        if self.locked_until is None:
            return False
        return datetime.utcnow() < self.locked_until

    def record_failed_login(self, max_attempts=5, lockout_minutes=30):
        """Record a failed login attempt, potentially locking the account."""
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            from datetime import timedelta
            self.locked_until = datetime.utcnow() + timedelta(minutes=lockout_minutes)

    def record_successful_login(self):
        """Record successful login, resetting failed attempts."""
        self.failed_login_attempts = 0
        self.locked_until = None
        self.last_login = datetime.utcnow()

    def is_admin(self):
        return self.role == UserRole.ADMIN

    def is_teacher(self):
        return self.role == UserRole.TEACHER

    def is_student(self):
        return self.role == UserRole.STUDENT

    def __repr__(self):
        return f'<User {self.username} ({self.role.value})>'
