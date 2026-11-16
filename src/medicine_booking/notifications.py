"""Durable notification outbox; delivery failure never reverses allocation."""
from typing import Protocol
from .access import require_role
from .domain import AppError, utc_now

DEFAULT_TEMPLATES = {
    'SUBMITTED': 'Your booking has been submitted.',
    'WAITLISTED': 'Your request is on the waitlist.',
    'RESERVED': 'Your medicine reservation is confirmed.',
    'PARTIALLY_RESERVED': 'Part of your request is reserved. The remainder is waitlisted.',
    'READY': 'Your reservation is ready for pickup.',
    'FULFILLED': 'Your booking has been fulfilled.',
    'CANCELLED': 'Your booking has been cancelled.',
    'EXPIRED': 'Your reservation has expired.',
    'REJECTED': 'Your booking requires attention. Sign in for details.',
}


class NotificationProvider(Protocol):
    def deliver(self, event: dict) -> None: ...


class InAppNotificationProvider:
    def deliver(self, event):
        # The persisted inbox is the delivery destination.
        return None


class NotificationService:
    def __init__(self, database, provider=None):
        self.database=database
        self.provider=provider or InAppNotificationProvider()

    def dispatch(self):
        # External providers must enforce their own bounded network timeout.
        with self.database.transaction() as db:
            pending=[dict(r) for r in db.execute("SELECT * FROM notifications WHERE status='PENDING' ORDER BY created_at LIMIT 100")]
        for event in pending:
            timestamp=utc_now()
            try:
                self.provider.deliver(event)
                status='SENT'
            except Exception:
                status='FAILED'
            with self.database.transaction() as db:
                db.execute('''UPDATE notifications SET status=?,attempts=attempts+1,attempted_at=?,delivered_at=?
                    WHERE id=? AND status='PENDING' ''',(status,timestamp,timestamp if status=='SENT' else None,event['id']))

    def retry(self,actor,event_id):
        require_role(actor,'PHARMACIST','MANAGER')
        with self.database.transaction() as db:
            row=db.execute("SELECT * FROM notifications WHERE id=? AND status='FAILED'",(event_id,)).fetchone()
            if not row:
                raise AppError('Failed notification not found.',404)
            if row['attempts']>=5:
                raise AppError('The retry limit has been reached.',409)
            db.execute("UPDATE notifications SET status='PENDING' WHERE id=?",(event_id,))
