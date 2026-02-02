"""
Audit Log and System Configuration models.
"""

import enum
from . import db


class AuditAction(enum.Enum):
    CREATE = 'CREATE'
    UPDATE = 'UPDATE'
    DELETE = 'DELETE'
    LOGIN = 'LOGIN'
    LOGOUT = 'LOGOUT'
    SEATING_GENERATED = 'SEATING_GENERATED'
    EXPORT = 'EXPORT'


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    username = db.Column(db.String(100))
    action = db.Column(db.Enum(AuditAction), nullable=False)
    table_name = db.Column(db.String(100))
    record_id = db.Column(db.Integer)
    old_values = db.Column(db.JSON)
    new_values = db.Column(db.JSON)
    ip_address = db.Column(db.String(45))  # Supports IPv6
    user_agent = db.Column(db.Text)
    session_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())

    # Relationship
    user = db.relationship('User', foreign_keys=[user_id])

    @classmethod
    def log_action(cls, action, table_name=None, record_id=None,
                   old_values=None, new_values=None, user_id=None,
                   username=None, ip_address=None, user_agent=None):
        """Manually log an action (for actions not covered by triggers)."""
        log = cls(
            user_id=user_id,
            username=username or 'system',
            action=action,
            table_name=table_name,
            record_id=record_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent
        )
        db.session.add(log)
        return log

    @classmethod
    def get_recent(cls, limit=100, action_filter=None, table_filter=None):
        """Get recent audit logs with optional filters."""
        query = cls.query.order_by(cls.created_at.desc())

        if action_filter:
            query = query.filter(cls.action == action_filter)
        if table_filter:
            query = query.filter(cls.table_name == table_filter)

        return query.limit(limit).all()

    @classmethod
    def get_user_activity(cls, user_id, days=30):
        """Get activity summary for a specific user."""
        from datetime import datetime, timedelta

        cutoff = datetime.utcnow() - timedelta(days=days)
        return cls.query.filter(
            cls.user_id == user_id,
            cls.created_at >= cutoff
        ).order_by(cls.created_at.desc()).all()

    def __repr__(self):
        return f'<AuditLog {self.action.value} on {self.table_name} by {self.username}>'


class SystemConfig(db.Model):
    __tablename__ = 'system_config'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(),
                           onupdate=db.func.current_timestamp())
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))

    # Relationship
    updater = db.relationship('User', foreign_keys=[updated_by])

    @classmethod
    def get(cls, key, default=None):
        """Get a configuration value."""
        config = cls.query.get(key)
        return config.value if config else default

    @classmethod
    def get_bool(cls, key, default=False):
        """Get a boolean configuration value."""
        value = cls.get(key)
        if value is None:
            return default
        return value.lower() in ('true', '1', 'yes', 'on')

    @classmethod
    def get_int(cls, key, default=0):
        """Get an integer configuration value."""
        value = cls.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    @classmethod
    def set(cls, key, value, description=None, user_id=None):
        """Set a configuration value."""
        config = cls.query.get(key)
        if config:
            config.value = str(value)
            if description:
                config.description = description
            config.updated_by = user_id
        else:
            config = cls(
                key=key,
                value=str(value),
                description=description,
                updated_by=user_id
            )
            db.session.add(config)
        return config

    # Common configuration keys
    SEATING_ALGORITHM = 'seating_algorithm'
    FRIEND_SEPARATION_ENABLED = 'friend_separation_enabled'
    SECTION_SEPARATION_ENABLED = 'section_separation_enabled'
    AUDIT_RETENTION_DAYS = 'audit_retention_days'
    MAX_LOGIN_ATTEMPTS = 'max_login_attempts'
    LOCKOUT_DURATION_MINUTES = 'lockout_duration_minutes'

    def __repr__(self):
        return f'<SystemConfig {self.key}={self.value}>'
