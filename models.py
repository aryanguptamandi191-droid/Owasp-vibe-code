from datetime import date, datetime, timedelta
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

STATUS_CHOICES = ['Applied', 'Test', 'Interview', 'Offer', 'Rejected']

# Warm Editorial color mapping as primary theme styling
STATUS_COLORS = {
    'Applied': {'bg': '#eef2ff', 'text': '#3730a3', 'border': '#c7d2fe', 'badge': 'badge-applied', 'color': '#4338ca'},
    'Test': {'bg': '#faf5ff', 'text': '#6b21a8', 'border': '#e9d5ff', 'badge': 'badge-test', 'color': '#7e22ce'},
    'Interview': {'bg': '#fff7ed', 'text': '#9a3412', 'border': '#fed7aa', 'badge': 'badge-interview', 'color': '#c2410c'},
    'Offer': {'bg': '#f7fee7', 'text': '#3f6212', 'border': '#d9f99d', 'badge': 'badge-offer', 'color': '#4d7c0f'},
    'Rejected': {'bg': '#fef2f2', 'text': '#991b1b', 'border': '#fecaca', 'badge': 'badge-rejected', 'color': '#b91c1c'}
}

class Application(db.Model):
    __tablename__ = 'applications'

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(150), nullable=False)
    job_role = db.Column(db.String(150), nullable=False)
    job_description = db.Column(db.Text, nullable=True)
    company_website = db.Column(db.String(255), nullable=True)
    application_date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(50), nullable=False, default='Applied')
    deadline_date = db.Column(db.Date, nullable=True)
    interview_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    # Feature: Dream Company tracking
    is_dream_company = db.Column(db.Boolean, default=False, nullable=False)

    # Feature: Auto-archive for inactive applications (>15 days without update)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    archived_at = db.Column(db.DateTime, nullable=True)

    # Feature: Auto-delete rejected applications after 2 days
    rejection_date = db.Column(db.Date, nullable=True)

    # Feature: Offer comparison details
    stipend = db.Column(db.String(100), nullable=True)          # e.g. "$55/hr", "₹1,20,000/mo"
    location = db.Column(db.String(150), nullable=True)         # e.g. "San Francisco, CA (Hybrid)"
    perks = db.Column(db.Text, nullable=True)                   # e.g. "Housing stipend, 401k match, return offer track"
    offer_deadline = db.Column(db.Date, nullable=True)          # Decision deadline for offer

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def is_overdue(self):
        """Returns True if deadline_date has passed."""
        if not self.deadline_date:
            return False
        return self.deadline_date < date.today()

    @property
    def is_urgent(self):
        """Returns True if deadline_date is within next 3 days (inclusive of today)."""
        if not self.deadline_date:
            return False
        today = date.today()
        days = (self.deadline_date - today).days
        return 0 <= days <= 3

    @property
    def is_deadline_within_3_days(self):
        """Specific red alert requirement: deadline is within 3 days or overdue."""
        if not self.deadline_date:
            return False
        days = (self.deadline_date - date.today()).days
        return days <= 3

    @property
    def days_remaining(self):
        """Days until deadline."""
        if not self.deadline_date:
            return None
        return (self.deadline_date - date.today()).days

    @property
    def days_since_update(self):
        """Number of days since last update."""
        if not self.updated_at:
            return 0
        return (date.today() - self.updated_at.date()).days

    @property
    def days_until_autodelete(self):
        """Days remaining before auto-delete for rejected applications (2 days limit)."""
        if self.status != 'Rejected' or not self.rejection_date:
            return None
        days_passed = (date.today() - self.rejection_date).days
        remaining = 2 - days_passed
        return max(0, remaining)

    @property
    def deadline_status_text(self):
        if not self.deadline_date:
            return None
        diff = (self.deadline_date - date.today()).days
        if diff < 0:
            abs_diff = abs(diff)
            return f"{abs_diff} day{'s' if abs_diff != 1 else ''} overdue"
        elif diff == 0:
            return "Due today"
        elif diff == 1:
            return "Due tomorrow"
        elif diff <= 3:
            return f"Due in {diff} days (Urgent)"
        else:
            return f"In {diff} days"

    @property
    def status_config(self):
        return STATUS_COLORS.get(self.status, STATUS_COLORS['Applied'])

    def to_dict(self):
        return {
            'id': self.id,
            'company_name': self.company_name,
            'job_role': self.job_role,
            'job_description': self.job_description or '',
            'company_website': self.company_website or '',
            'application_date': self.application_date.strftime('%Y-%m-%d') if self.application_date else None,
            'status': self.status,
            'deadline_date': self.deadline_date.strftime('%Y-%m-%d') if self.deadline_date else None,
            'interview_date': self.interview_date.strftime('%Y-%m-%d') if self.interview_date else None,
            'notes': self.notes or '',
            'is_dream_company': self.is_dream_company,
            'is_archived': self.is_archived,
            'archived_at': self.archived_at.strftime('%Y-%m-%d') if self.archived_at else None,
            'rejection_date': self.rejection_date.strftime('%Y-%m-%d') if self.rejection_date else None,
            'stipend': self.stipend or '',
            'location': self.location or '',
            'perks': self.perks or '',
            'offer_deadline': self.offer_deadline.strftime('%Y-%m-%d') if self.offer_deadline else None,
            'is_overdue': self.is_overdue,
            'is_urgent': self.is_urgent,
            'is_deadline_within_3_days': self.is_deadline_within_3_days,
            'days_remaining': self.days_remaining,
            'days_since_update': self.days_since_update,
            'days_until_autodelete': self.days_until_autodelete,
            'deadline_status_text': self.deadline_status_text
        }

    def __repr__(self):
        return f'<Application {self.company_name} - {self.job_role} ({self.status})>'
