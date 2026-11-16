"""SQLite persistence and atomic allocation transactions."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 role TEXT NOT NULL CHECK(role IN ('PATIENT','PHARMACIST','MANAGER')),
 password TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS medicines (
 code TEXT PRIMARY KEY, name TEXT NOT NULL, strength TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'STANDARD');
CREATE TABLE IF NOT EXISTS prescriptions (
 id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES users(id), source TEXT NOT NULL,
 source_identifier TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, authored_on TEXT NOT NULL,
 expires_on TEXT NOT NULL, prescriber TEXT NOT NULL, reason TEXT NOT NULL,
 urgency TEXT NOT NULL DEFAULT 'NORMAL', continuity TEXT NOT NULL DEFAULT 'UNKNOWN',
 verified_by TEXT REFERENCES users(id), verified_at TEXT, consent_reference TEXT NOT NULL DEFAULT '',
 restricted_approved INTEGER NOT NULL DEFAULT 0, filename TEXT, document BLOB,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS medication_requests (
 id TEXT PRIMARY KEY, prescription_id TEXT NOT NULL REFERENCES prescriptions(id),
 medicine_code TEXT NOT NULL REFERENCES medicines(code), quantity INTEGER NOT NULL CHECK(quantity>0),
 UNIQUE(prescription_id, medicine_code));
CREATE TABLE IF NOT EXISTS inventory (
 id TEXT PRIMARY KEY, medicine_code TEXT NOT NULL REFERENCES medicines(code), location_id TEXT NOT NULL,
 batch TEXT NOT NULL, expires_on TEXT NOT NULL, state TEXT NOT NULL,
 on_hand INTEGER NOT NULL CHECK(on_hand>=0), reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0 AND reserved<=on_hand),
 updated_at TEXT NOT NULL, UNIQUE(medicine_code, location_id, batch));
CREATE TABLE IF NOT EXISTS bookings (
 id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES users(id),
 request_id TEXT NOT NULL REFERENCES medication_requests(id), location_id TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(quantity>0), reserved_quantity INTEGER NOT NULL DEFAULT 0,
 fulfilled_quantity INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 expires_at TEXT, override_rank INTEGER, override_reason TEXT,
 priority_reasons TEXT NOT NULL DEFAULT '[]', priority_rank INTEGER, policy_version TEXT, evaluated_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK(reserved_quantity>=0 AND fulfilled_quantity>=0 AND reserved_quantity+fulfilled_quantity<=quantity));
CREATE UNIQUE INDEX IF NOT EXISTS active_booking_request ON bookings(request_id)
 WHERE status NOT IN ('CANCELLED','REJECTED','FULFILLED','EXPIRED');
CREATE TABLE IF NOT EXISTS reservation_items (
 id TEXT PRIMARY KEY, booking_id TEXT NOT NULL REFERENCES bookings(id),
 inventory_id TEXT NOT NULL REFERENCES inventory(id), quantity INTEGER NOT NULL CHECK(quantity>0),
 state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS waitlist (
 id TEXT PRIMARY KEY, booking_id TEXT NOT NULL UNIQUE REFERENCES bookings(id),
 state TEXT NOT NULL, entered_at TEXT NOT NULL, promoted_at TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (
 id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES users(id), booking_id TEXT NOT NULL REFERENCES bookings(id),
 template TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'IN_APP', message TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, attempted_at TEXT, delivered_at TEXT);
CREATE TABLE IF NOT EXISTS audit (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, actor_id TEXT NOT NULL,
 actor_role TEXT NOT NULL, event TEXT NOT NULL, object_id TEXT NOT NULL,
 previous_state TEXT, new_state TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS restock_events (
 event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, inventory_id TEXT NOT NULL REFERENCES inventory(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO settings VALUES ('reservation_minutes', '120');
INSERT OR IGNORE INTO settings VALUES ('partial_allocation', 'false');
CREATE INDEX IF NOT EXISTS booking_patient ON bookings(patient_id);
CREATE INDEX IF NOT EXISTS prescription_patient ON prescriptions(patient_id);
CREATE INDEX IF NOT EXISTS inventory_lookup ON inventory(medicine_code, location_id);
'''


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript(SCHEMA)
        finally:
            connection.close()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
