"""
Room and Invigilator models.
"""

from . import db
from .base import TimestampMixin, ActiveMixin


class Room(db.Model, TimestampMixin, ActiveMixin):
    __tablename__ = 'rooms'

    id = db.Column(db.Integer, primary_key=True)
    room_name = db.Column(db.String(50), unique=True, nullable=False)
    building = db.Column(db.String(100))
    floor = db.Column(db.Integer)
    capacity = db.Column(db.Integer, nullable=False)
    max_subjects = db.Column(db.Integer, default=15)
    max_branches = db.Column(db.Integer, default=5)
    allowed_years = db.Column(db.ARRAY(db.Integer), default=[1, 2, 3, 4])
    allowed_branches = db.Column(db.ARRAY(db.Text))
    layout_columns = db.Column(db.Integer, default=6)
    layout_rows = db.Column(db.Integer, default=5)
    has_ac = db.Column(db.Boolean, default=False)
    has_projector = db.Column(db.Boolean, default=False)
    has_cctv = db.Column(db.Boolean, default=False)

    # Relationships
    seating_assignments = db.relationship('SeatingAssignment', backref='room', lazy='dynamic')
    invigilators = db.relationship('Invigilator', backref='room', lazy='dynamic')

    def get_available_seats(self, exam_id):
        """Get number of available seats for a specific exam."""
        from .seating import SeatingAssignment
        assigned = SeatingAssignment.query.filter_by(
            room_id=self.id,
            exam_id=exam_id
        ).count()
        return self.capacity - assigned

    def get_seat_grid(self, exam_id):
        """Get 2D grid of seat assignments for visualization."""
        from .seating import SeatingAssignment

        assignments = SeatingAssignment.query.filter_by(
            room_id=self.id,
            exam_id=exam_id
        ).all()

        grid = [[None for _ in range(self.layout_columns)]
                for _ in range(self.layout_rows)]

        for assignment in assignments:
            if 0 <= assignment.seat_y < self.layout_rows and \
               0 <= assignment.seat_x < self.layout_columns:
                grid[assignment.seat_y][assignment.seat_x] = assignment

        return grid

    def to_config_dict(self):
        """Convert to dictionary format for room assignment algorithm."""
        return {
            'room_name': self.room_name,
            'capacity': self.capacity,
            'max_subjects': self.max_subjects,
            'max_branches': self.max_branches,
            'allowed_years': self.allowed_years or [1, 2, 3, 4],
            'layout_columns': self.layout_columns,
            'layout_rows': self.layout_rows
        }

    def __repr__(self):
        return f'<Room {self.room_name} (cap: {self.capacity})>'


class Invigilator(db.Model):
    __tablename__ = 'invigilators'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id', ondelete='SET NULL'))
    is_primary = db.Column(db.Boolean, default=False)
    assigned_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())

    __table_args__ = (
        db.UniqueConstraint('user_id', 'room_id', name='uq_invigilator_user_room'),
    )

    def __repr__(self):
        return f'<Invigilator user={self.user_id} room={self.room_id}>'
