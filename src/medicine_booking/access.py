"""MBA-9: server-side role checks, object-level access, and safe errors.

The host application authenticates users and builds a ``Principal`` from its
verified session. Permissions come only from the role table below; a caller
cannot grant itself extra permissions through request data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from .domain import ActorRole, utc_now
from .safety import PharmacistPrincipal


# Permission names used across the services.
BOOKING_CREATE_OWN = "booking:create"
BOOKING_CREATE_ANY = "booking:create_any"
BOOKING_READ_OWN = "booking:read_own"
BOOKING_READ_ANY = "booking:read_any"
BOOKING_CANCEL_OWN = "booking:cancel_own"
BOOKING_CANCEL_ANY = "booking:cancel_any"
PRESCRIPTION_REVIEW = "prescription:review"
URGENCY_CONFIRM = "urgency:confirm"
ALLOCATION_RUN = "allocation:run"
ALLOCATION_OVERRIDE = "allocation:override"
RESERVATION_EXPIRE = "reservation:expire"
WAITLIST_READ = "waitlist:read"
AUDIT_READ = "audit:read"
POLICY_CONFIGURE = "policy:configure"

ROLE_PERMISSIONS: Mapping[ActorRole, frozenset[str]] = MappingProxyType({
    ActorRole.PATIENT: frozenset({BOOKING_CREATE_OWN, BOOKING_READ_OWN, BOOKING_CANCEL_OWN}),
    ActorRole.CAREGIVER: frozenset({BOOKING_CREATE_OWN, BOOKING_READ_OWN, BOOKING_CANCEL_OWN}),
    ActorRole.PHARMACIST: frozenset({
        BOOKING_CREATE_ANY, BOOKING_READ_ANY, BOOKING_CANCEL_ANY,
        PRESCRIPTION_REVIEW, URGENCY_CONFIRM, ALLOCATION_RUN,
        ALLOCATION_OVERRIDE, RESERVATION_EXPIRE, WAITLIST_READ,
    }),
    ActorRole.PHARMACY_MANAGER: frozenset({BOOKING_READ_ANY, WAITLIST_READ, AUDIT_READ}),
    ActorRole.ADMINISTRATOR: frozenset({AUDIT_READ, POLICY_CONFIGURE}),
    ActorRole.SYSTEM: frozenset({ALLOCATION_RUN, RESERVATION_EXPIRE}),
})

_PATIENT_SIDE_ROLES = frozenset({ActorRole.PATIENT, ActorRole.CAREGIVER})


class AccessDenied(PermissionError):
    """Raised for every authorization failure.

    The message never says whether a record exists, so a known or guessed
    identifier cannot be used to probe other patients' records (NFR-SEC-13).
    """

    def __init__(self) -> None:
        super().__init__("not authorized")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Principal:
    """A verified caller identity for any role.

    ``patient_ids`` lists the patients a patient or caregiver may act for. It
    must come from the host's verified account links, never from the request.
    """

    actor_id: str
    actor_role: ActorRole
    authenticated: bool
    patient_ids: frozenset[str] = frozenset()
    session_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("actor identifier is required")
        object.__setattr__(self, "patient_ids", frozenset(self.patient_ids))
        if any(not patient_id.strip() for patient_id in self.patient_ids):
            raise ValueError("linked patient identifiers cannot be blank")
        if self.patient_ids and self.actor_role not in _PATIENT_SIDE_ROLES:
            raise ValueError("only patients and caregivers have linked patients")
        if self.session_expires_at is not None:
            _aware(self.session_expires_at, "session_expires_at")

    @property
    def permissions(self) -> frozenset[str]:
        return ROLE_PERMISSIONS.get(self.actor_role, frozenset())

    def is_active(self, at: datetime | None = None) -> bool:
        when = at if at is not None else utc_now()
        _aware(when, "at")
        if not self.authenticated:
            return False
        return self.session_expires_at is None or when < self.session_expires_at

    def has(self, permission: str, *, at: datetime | None = None) -> bool:
        return self.is_active(at) and permission in self.permissions

    def require(self, permission: str, *, at: datetime | None = None) -> None:
        if not self.has(permission, at=at):
            raise AccessDenied()

    def as_pharmacist(self, *, at: datetime | None = None) -> PharmacistPrincipal:
        """Bridge to the pharmacist identity used by review and override code."""
        if self.actor_role is not ActorRole.PHARMACIST or not self.is_active(at):
            raise AccessDenied()
        return PharmacistPrincipal(self.actor_id, self.actor_role, True, self.permissions)


def authorize_patient_record(
    principal: Principal,
    patient_id: str,
    *,
    own_permission: str,
    any_permission: str,
    at: datetime | None = None,
) -> None:
    """Allow staff with ``any_permission`` or a linked patient/caregiver.

    Every patient-owned read or write must go through this check before data
    is returned, even when the caller already knows a valid record ID.
    """
    if principal.has(any_permission, at=at):
        return
    if principal.has(own_permission, at=at) and patient_id in principal.patient_ids:
        return
    raise AccessDenied()


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    """Configured prescription upload limits (NFR-SEC-08, REQ-EP-03)."""

    allowed_content_types: frozenset[str] = frozenset({
        "application/pdf", "image/jpeg", "image/png",
    })
    max_bytes: int = 5 * 1024 * 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_content_types", frozenset(self.allowed_content_types))
        if not self.allowed_content_types:
            raise ValueError("at least one upload content type must be allowed")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) or self.max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")


# Leading bytes for each accepted type, so a renamed file is caught.
_SIGNATURES: Mapping[str, tuple[bytes, ...]] = MappingProxyType({
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
})


def validate_upload(content_type: str, data: bytes, *,
                    policy: UploadPolicy | None = None) -> tuple[str, ...]:
    """Return rejection reason codes; an empty tuple means the upload is acceptable."""
    rules = policy if policy is not None else UploadPolicy()
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    reasons: list[str] = []
    if not data:
        reasons.append("UPLOAD_EMPTY")
    elif len(data) > rules.max_bytes:
        reasons.append("UPLOAD_TOO_LARGE")
    if declared not in rules.allowed_content_types:
        reasons.append("UPLOAD_TYPE_NOT_ALLOWED")
    elif data:
        signatures = _SIGNATURES.get(declared)
        if signatures is not None and not any(data.startswith(sig) for sig in signatures):
            reasons.append("UPLOAD_CONTENT_MISMATCH")
    return tuple(reasons)


def public_error_message(error: BaseException) -> str:
    """Map an internal exception to text that is safe to show an end user.

    Stack traces, identifiers, and internal reasons stay in server logs
    (NFR-SEC-15).
    """
    if isinstance(error, PermissionError):
        return "You are not authorized to perform this action."
    if isinstance(error, (ValueError, KeyError)):
        return "The request could not be processed. Check the details and try again."
    return "Something went wrong. Please try again later or contact the pharmacy."
