"""
Base classes and mixins for SQLAlchemy models.
"""

from datetime import datetime
from . import db


class TimestampMixin:
    """Mixin for created_at and updated_at timestamps."""
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ActiveMixin:
    """Mixin for soft delete via is_active flag."""
    is_active = db.Column(db.Boolean, default=True)

    @classmethod
    def active(cls):
        """Query only active records."""
        return cls.query.filter_by(is_active=True)

    def deactivate(self):
        """Soft delete by setting is_active to False."""
        self.is_active = False


def set_audit_context(user_id=None, username=None, session_id=None):
    """
    Set audit context for database triggers.
    Call this at the start of each request with user info.
    """
    if user_id is not None:
        db.session.execute(
            db.text("SELECT set_config('app.current_user_id', :uid, true)"),
            {'uid': str(user_id)}
        )
    if username is not None:
        db.session.execute(
            db.text("SELECT set_config('app.current_username', :uname, true)"),
            {'uname': username}
        )
    if session_id is not None:
        db.session.execute(
            db.text("SELECT set_config('app.session_id', :sid, true)"),
            {'sid': session_id}
        )


def clear_audit_context():
    """Clear audit context at end of request."""
    db.session.execute(db.text("SELECT clear_audit_context()"))
