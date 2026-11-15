"""MBA-19: booking, waitlist, reservation, and cancellation notifications.

``NotificationService`` is a drop-in ``notify`` callback for
``WaitlistService``: ``WaitlistService(..., notify=notifications)``. It picks
the recipient from the booking's own patient, renders a template that keeps
medicine and health details off lock screens, records every attempt, and
retries failures by policy. It never raises into the booking flow, so a
delivery problem cannot undo a booking or reservation (REQ-NT-13).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from string import Formatter
from threading import RLock
from typing import Callable, Iterable, Mapping

from .contracts import (
    ExternalServiceError, NotificationRequest, NotificationSender, call_external,
)
from .domain import (
    ActorRole, AuditEvent, Booking, DeliveryStatus, NotificationChannel,
    NotificationEvent, new_record_id, utc_now,
)

# Events the booking and waitlist services emit.
SUBMITTED = "SUBMITTED"
WAITLISTED = "WAITLISTED"
RESERVED = "RESERVED"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"
EVENT_TYPES = frozenset({SUBMITTED, WAITLISTED, RESERVED, EXPIRED, CANCELLED, REJECTED})

# Only these values may appear in a message. None of them reveal a medicine,
# prescription, or diagnosis (REQ-NT-12).
ALLOWED_PARAMETERS = frozenset({"booking_ref", "expires_at", "pharmacy"})


@dataclass(frozen=True, slots=True)
class NotificationTemplate:
    template_id: str
    event_type: str
    channel: NotificationChannel
    body: str

    def __post_init__(self) -> None:
        if not self.template_id.strip() or not self.body.strip():
            raise ValueError("template id and body are required")
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown notification event: {self.event_type}")
        used = {name for _, name, _, _ in Formatter().parse(self.body) if name}
        if used - ALLOWED_PARAMETERS:
            raise ValueError(f"template uses parameters that are not allowed: {sorted(used - ALLOWED_PARAMETERS)}")

    @property
    def parameters(self) -> frozenset[str]:
        return frozenset(name for _, name, _, _ in Formatter().parse(self.body) if name)


def _defaults() -> dict[tuple[str, NotificationChannel], NotificationTemplate]:
    text = {
        SUBMITTED: "We received your pharmacy booking {booking_ref}. Open the app for details.",
        WAITLISTED: "Your pharmacy booking {booking_ref} is on the waitlist. We will tell you when it is reserved.",
        RESERVED: "Your pharmacy booking {booking_ref} is reserved at {pharmacy}. Please collect it by {expires_at}.",
        EXPIRED: "Your reservation for booking {booking_ref} has ended. Open the app to book again.",
        CANCELLED: "Your pharmacy booking {booking_ref} has been cancelled.",
        REJECTED: "Your pharmacy booking {booking_ref} could not be accepted. Open the app for details.",
    }
    templates = {}
    for channel in NotificationChannel:
        for event, body in text.items():
            template_id = f"{event.lower()}-{channel.value.lower()}-v1"
            templates[(event, channel)] = NotificationTemplate(template_id, event, channel, body)
    return templates


DEFAULT_TEMPLATES: Mapping[tuple[str, NotificationChannel], NotificationTemplate] = _defaults()


@dataclass(frozen=True, slots=True)
class Recipient:
    """A patient's verified contact. Comes from the patient's own account."""

    patient_id: str
    contact_reference: str
    channel: NotificationChannel

    def __post_init__(self) -> None:
        if not self.patient_id.strip() or not self.contact_reference.strip():
            raise ValueError("recipient patient and contact are required")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    # Wait before attempt 2, attempt 3, ...; the last value repeats.
    backoff: tuple[timedelta, ...] = (timedelta(minutes=1), timedelta(minutes=5))
    timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.timeout_seconds <= 0:
            raise ValueError("attempts and timeout must be positive")
        if not self.backoff or any(step <= timedelta() for step in self.backoff):
            raise ValueError("backoff steps must be positive")

    def delay_before(self, attempt_number: int) -> timedelta:
        return self.backoff[min(attempt_number - 2, len(self.backoff) - 1)]


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    """One send attempt (REQ-NT-08, REQ-NT-10)."""

    notification_id: str
    attempt_number: int
    channel: NotificationChannel
    attempted_at: datetime
    status: DeliveryStatus
    error_code: str | None = None


@dataclass(slots=True)
class _Pending:
    request: NotificationRequest
    attempts: int = 0
    next_attempt_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    """A notification event with its delivery history, for review screens."""

    event: NotificationEvent
    event_type: str
    attempts: tuple[DeliveryAttempt, ...] = field(default_factory=tuple)


class NotificationService:
    def __init__(
        self, sender: NotificationSender, *,
        bookings: Mapping[str, Booking],
        recipients: Mapping[str, Recipient],
        templates: Mapping[tuple[str, NotificationChannel], NotificationTemplate] | None = None,
        retry: RetryPolicy | None = None,
        reservation_expiry: Callable[[str], datetime | None] | None = None,
        pharmacy_name: str = "your pharmacy",
        clock: Callable[[], datetime] = utc_now,
        audit_events: list[AuditEvent] | None = None,
    ) -> None:
        self.sender = sender
        # A live view of the booking store, so new bookings are found.
        self.bookings = bookings
        self.recipients = dict(recipients)
        self.templates = dict(templates if templates is not None else DEFAULT_TEMPLATES)
        self.retry = retry if retry is not None else RetryPolicy()
        self.reservation_expiry = reservation_expiry
        self.pharmacy_name = pharmacy_name
        self.clock = clock
        self.audit_events = audit_events if audit_events is not None else []
        self.events: dict[str, NotificationEvent] = {}
        self.event_types: dict[str, str] = {}
        self.attempts: list[DeliveryAttempt] = []
        self._pending: dict[str, _Pending] = {}
        self._lock = RLock()
        for recipient_id, recipient in self.recipients.items():
            if recipient_id != recipient.patient_id:
                raise ValueError("recipient table key must match the recipient's patient")

    def __call__(self, booking_id: str, event_type: str) -> None:
        """Callback used by the booking and waitlist services."""
        self.notify(booking_id, event_type, at=self.clock())

    def notify(self, booking_id: str, event_type: str, *,
               at: datetime | None = None) -> NotificationEvent | None:
        """Create and send one notification. Returns None when nothing applies."""
        when = at if at is not None else self.clock()
        if event_type not in EVENT_TYPES:
            return None
        booking = self.bookings.get(booking_id)
        if booking is None:
            return None
        # The recipient is always the booking's own patient (REQ-NT-15).
        recipient = self.recipients.get(booking.patient_id)
        if recipient is None:
            self._audit("NOTIFICATION_SKIPPED", booking_id, when, "NO_VERIFIED_RECIPIENT")
            return None
        template = self.templates.get((event_type, recipient.channel))
        if template is None:
            self._audit("NOTIFICATION_SKIPPED", booking_id, when, "NO_TEMPLATE")
            return None
        params = self._parameters(booking_id, event_type, template, when)
        if params is None:
            self._audit("NOTIFICATION_SKIPPED", booking_id, when, "MISSING_PARAMETER")
            return None
        request = NotificationRequest(
            new_record_id(), booking_id, recipient.contact_reference, recipient.channel,
            template.template_id, template.body.format(**params), params, created_at=when,
        )
        event = NotificationEvent(request.notification_id, booking_id,
                                  recipient.contact_reference, recipient.channel,
                                  template.template_id, created_at=when)
        with self._lock:
            self.events[event.notification_id] = event
            self.event_types[event.notification_id] = event_type
            self._pending[event.notification_id] = _Pending(request)
            self._attempt(event.notification_id, when)
            return self.events[event.notification_id]

    def retry_due(self, *, at: datetime | None = None) -> tuple[str, ...]:
        """Retry failed notifications whose backoff has passed (REQ-NT-14)."""
        when = at if at is not None else self.clock()
        with self._lock:
            due = sorted(nid for nid, pending in self._pending.items()
                         if pending.next_attempt_at is not None
                         and pending.next_attempt_at <= when)
            for nid in due:
                self._attempt(nid, when)
            return tuple(due)

    def history(self, booking_id: str | None = None) -> tuple[NotificationRecord, ...]:
        with self._lock:
            return tuple(
                NotificationRecord(event, self.event_types[nid],
                                   tuple(a for a in self.attempts if a.notification_id == nid))
                for nid, event in self.events.items()
                if booking_id is None or event.booking_id == booking_id
            )

    def _parameters(self, booking_id: str, event_type: str,
                    template: NotificationTemplate, when: datetime) -> dict[str, str] | None:
        values = {"booking_ref": booking_id[-6:].upper(), "pharmacy": self.pharmacy_name}
        if "expires_at" in template.parameters:
            expiry = self.reservation_expiry(booking_id) if self.reservation_expiry else None
            if expiry is None:
                if event_type == RESERVED:
                    values["expires_at"] = "the time shown in the app"
                else:
                    return None
            else:
                values["expires_at"] = expiry.strftime("%d %b %Y, %H:%M %Z").strip()
        return {k: v for k, v in values.items() if k in template.parameters}

    def _attempt(self, nid: str, when: datetime) -> None:
        pending = self._pending[nid]
        pending.attempts += 1
        request = pending.request
        try:
            receipt = call_external("notification", lambda: self.sender.send(request),
                                    timeout=self.retry.timeout_seconds)
        except ExternalServiceError as exc:
            status, error = DeliveryStatus.FAILED, exc.code
        else:
            status = receipt.status
            error = receipt.error_code if status is not DeliveryStatus.SENT else None
        self.attempts.append(DeliveryAttempt(nid, pending.attempts, request.channel,
                                             when, status, error))
        event = self.events[nid]
        if status is DeliveryStatus.SENT:
            self.events[nid] = replace(event, delivery_status=DeliveryStatus.SENT,
                                       delivered_at=max(when, event.created_at))
            del self._pending[nid]
            return
        self.events[nid] = replace(event, delivery_status=DeliveryStatus.FAILED)
        self._audit("NOTIFICATION_FAILED", request.booking_id, when,
                    f"attempt={pending.attempts};error={error}")
        if pending.attempts >= self.retry.max_attempts:
            pending.next_attempt_at = None
            del self._pending[nid]
            self._audit("NOTIFICATION_ABANDONED", request.booking_id, when,
                        f"attempts={pending.attempts}")
        else:
            pending.next_attempt_at = when + self.retry.delay_before(pending.attempts + 1)

    def _audit(self, event_type: str, booking_id: str, when: datetime, reason: str) -> None:
        self.audit_events.append(AuditEvent(
            new_record_id(), "notification-service", ActorRole.SYSTEM, event_type,
            "Booking", booking_id, timestamp=when, reason=reason,
        ))


def notification_events(service: NotificationService) -> Iterable[NotificationEvent]:
    """Events for ``manager_overview(..., notifications=...)``."""
    with service._lock:
        return tuple(service.events.values())


__all__ = [
    "ALLOWED_PARAMETERS", "CANCELLED", "DEFAULT_TEMPLATES", "DeliveryAttempt", "EVENT_TYPES",
    "EXPIRED", "NotificationRecord", "NotificationService", "NotificationTemplate",
    "REJECTED", "RESERVED", "Recipient", "RetryPolicy", "SUBMITTED", "WAITLISTED",
    "notification_events",
]
