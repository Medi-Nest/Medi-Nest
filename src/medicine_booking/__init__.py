"""Domain primitives for the medicine booking prototype."""

from .domain import (
    ActorRole,
    AuditEvent,
    Booking,
    BookingStatus,
    ConsentStatus,
    ContinuityStatus,
    DeliveryStatus,
    Inventory,
    InventoryState,
    MedicationRequest,
    MedicationRequestStatus,
    NotificationChannel,
    NotificationEvent,
    Patient,
    Prescription,
    PrescriptionRecord,
    PrescriptionSource,
    PrescriptionStateChange,
    PrescriptionStatus,
    PriorityContext,
    WaitlistEntry,
    WaitlistState,
    new_record_id,
    transition_booking,
    transition_prescription,
    utc_now,
)
from .priority import (
    PriorityChange, PriorityEvaluation, PriorityPolicy, PriorityResult,
    order_bookings, re_evaluate_bookings,
)
from .safety import (
    AllocationDecision, EmergencyGuidance, ManualReview, PharmacistPrincipal,
    PriorityOverride, SafetyPolicy, check_allocation_safety, override_priority,
    patient_status_message,
)
from .access import (
    ROLE_PERMISSIONS, AccessDenied, Principal, UploadPolicy,
    authorize_patient_record, public_error_message, validate_upload,
)
from .waitlist import (
    InMemoryInventoryGateway, InventoryGateway, RestockEvent, RestockResult,
    WaitlistService,
)
from .booking import (
    Allocation, AllocationOutcome, BookingOutcome, BookingPolicy, BookingService,
    DuplicateAction, DuplicateMatch,
)
from .patient_views import (
    PatientBookingView, patient_booking_view, patient_bookings, reason_message,
)
from .manager_views import (
    AuditRow, FulfilmentMetrics, ManagerOverview, NotificationSummary,
    WaitlistSummary, audit_history, manager_overview,
)

__all__ = [
    "ActorRole", "AuditEvent", "Booking", "BookingStatus", "ConsentStatus",
    "ContinuityStatus", "DeliveryStatus", "Inventory", "InventoryState",
    "MedicationRequest", "MedicationRequestStatus", "NotificationChannel",
    "NotificationEvent", "Patient", "Prescription", "PrescriptionRecord",
    "PrescriptionSource", "PrescriptionStateChange", "PrescriptionStatus",
    "PriorityContext", "WaitlistEntry", "WaitlistState", "new_record_id",
    "transition_booking", "transition_prescription", "utc_now",
    "PriorityChange", "PriorityEvaluation", "PriorityPolicy", "PriorityResult",
    "order_bookings", "re_evaluate_bookings", "AllocationDecision",
    "EmergencyGuidance", "ManualReview", "PharmacistPrincipal",
    "PriorityOverride", "SafetyPolicy", "check_allocation_safety",
    "override_priority", "patient_status_message", "InMemoryInventoryGateway",
    "InventoryGateway", "RestockEvent", "RestockResult", "WaitlistService",
    "ROLE_PERMISSIONS", "AccessDenied", "Principal", "UploadPolicy",
    "authorize_patient_record", "public_error_message", "validate_upload",
    "BookingOutcome", "BookingPolicy", "BookingService", "DuplicateAction",
    "DuplicateMatch", "Allocation", "AllocationOutcome", "PatientBookingView",
    "patient_booking_view", "patient_bookings", "reason_message", "AuditRow",
    "FulfilmentMetrics", "ManagerOverview", "NotificationSummary",
    "WaitlistSummary", "audit_history", "manager_overview",
]
