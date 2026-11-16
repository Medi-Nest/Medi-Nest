"""Shared validation and ISO 8601 timestamps."""
from datetime import date, datetime, timezone
from uuid import uuid4


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def new_id():
    return str(uuid4())


def validate_text(value, label, maximum=200, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise AppError(f'Enter a valid {label} (up to {maximum} characters).')
    return value.strip()


def validate_integer(value, label, minimum=1, maximum=100000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise AppError(f'{label} must be a whole number from {minimum} to {maximum}.')
    return value


def validate_date(value, label):
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise AppError(f'Enter {label} as YYYY-MM-DD.') from None


def validate_fields(payload, allowed, required=()):
    if not isinstance(payload, dict) or set(payload) - set(allowed) or set(required) - set(payload):
        raise AppError('The request contains missing or unsupported fields.')


TERMINAL_STATES = {'CANCELLED', 'REJECTED', 'FULFILLED', 'EXPIRED'}
PRESCRIPTION_STATES = {'RECEIVED', 'VALIDATION_PENDING', 'VALID', 'INVALID', 'EXPIRED', 'MANUAL_REVIEW_REQUIRED'}
INVENTORY_STATES = {'IN_STOCK', 'QUARANTINED', 'RECALLED', 'BLOCKED', 'EXPIRED'}
