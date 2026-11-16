"""Prescription, booking, inventory and pharmacist application services."""
import base64
import binascii
import json
from datetime import date, datetime, timedelta, timezone
from .access import require_owner, require_role
from .domain import (AppError, INVENTORY_STATES, PRESCRIPTION_STATES, TERMINAL_STATES,
                     new_id, utc_now, validate_date, validate_fields, validate_integer, validate_text)
from .priority import CONTINUITY_RANK, POLICY_VERSION, URGENCY_RANK, priority_key, priority_reasons
from .notifications import DEFAULT_TEMPLATES

SYSTEM = {'id': 'system', 'role': 'SYSTEM'}
BOOKING_QUERY = '''SELECT b.*,r.prescription_id,r.medicine_code,m.name AS medicine_name,m.strength,
    m.category,p.urgency,p.continuity,p.status AS prescription_status,p.expires_on AS prescription_expiry,
    p.restricted_approved,p.verified_by,u.name AS patient_name
    FROM bookings b JOIN medication_requests r ON r.id=b.request_id
    JOIN medicines m ON m.code=r.medicine_code JOIN prescriptions p ON p.id=r.prescription_id
    JOIN users u ON u.id=b.patient_id'''


class BookingService:
    def __init__(self, database, notification_templates=None):
        self.database = database
        templates = notification_templates or {}
        if not isinstance(templates,dict) or set(templates)-set(DEFAULT_TEMPLATES):
            raise ValueError('Unsupported notification template configuration.')
        for template in templates.values():
            if not isinstance(template,str) or not 1 <= len(template.strip()) <= 500:
                raise ValueError('Notification templates must contain 1–500 characters.')
        self.notification_templates = {**DEFAULT_TEMPLATES,**templates}

    @staticmethod
    def audit(db, actor, event, object_id, previous=None, current=None, reason=''):
        db.execute('''INSERT INTO audit (id,actor_id,actor_role,event,object_id,previous_state,new_state,reason,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)''', (new_id(), actor['id'], actor['role'], event, object_id,
            json.dumps(previous) if previous is not None else None,
            json.dumps(current) if current is not None else None, reason, utc_now()))

    def notify(self, db, booking, template):
        message = self.notification_templates[template]
        if template in {'RESERVED', 'PARTIALLY_RESERVED'} and booking['expires_at']:
            message += ' Collect before ' + booking['expires_at'] + '.'
        db.execute('''INSERT INTO notifications (id,patient_id,booking_id,template,message,created_at)
            VALUES (?,?,?,?,?,?)''', (new_id(), booking['patient_id'], booking['id'], template, message, utc_now()))

    def transition(self, db, booking, status, actor, reason=''):
        if booking['status'] != status:
            db.execute('UPDATE bookings SET status=?,reason=?,updated_at=? WHERE id=?', (status, reason, utc_now(), booking['id']))
            self.audit(db, actor, 'BOOKING_' + status, booking['id'], booking['status'], status, reason)
            self.notify(db, dict(booking), status)
        elif booking['reason'] != reason:
            db.execute('UPDATE bookings SET reason=?,updated_at=? WHERE id=?', (reason, utc_now(), booking['id']))

    @staticmethod
    def find_booking(db, booking_id, actor):
        booking = db.execute(BOOKING_QUERY + ' WHERE b.id=?', (booking_id,)).fetchone()
        if not booking:
            raise AppError('Booking not found.', 404)
        require_owner(actor, booking['patient_id'])
        return dict(booking)

    @staticmethod
    def eligibility_reason(booking):
        if booking['prescription_status'] != 'VALID' or booking['prescription_expiry'] < date.today().isoformat():
            return 'Prescription is invalid, expired, or awaiting review.'
        if not booking['verified_by']:
            return 'Prescription requires pharmacist validation.'
        if booking['category'] == 'EXCLUDED':
            return 'This medicine is excluded from allocation.'
        if booking['category'] == 'RESTRICTED' and not booking['restricted_approved']:
            return 'Restricted medicine requires explicit pharmacist review.'
        return ''

    def submit_prescription(self, actor, payload):
        require_role(actor, 'PATIENT', 'PHARMACIST')
        allowed = {'patient_id','source_identifier','authored_on','expires_on','prescriber','medicines','document','consent_reference'}
        validate_fields(payload, allowed, {'authored_on','expires_on','prescriber','medicines'})
        patient_id = payload.get('patient_id', actor['id'])
        if not isinstance(patient_id, str):
            raise AppError('Select a valid patient.')
        require_owner(actor, patient_id)
        authored = validate_date(payload['authored_on'], 'prescription date')
        expiry = validate_date(payload['expires_on'], 'expiry date')
        if authored > date.today().isoformat() or expiry < authored:
            raise AppError('Check the prescription and expiry dates.')
        prescriber = validate_text(payload['prescriber'], 'prescriber reference')
        source_id = validate_text(payload.get('source_identifier',''), 'source identifier', required=False)
        consent = validate_text(payload.get('consent_reference',''), 'consent reference', required=False)
        medicines = payload['medicines']
        if not isinstance(medicines, list) or not 1 <= len(medicines) <= 20:
            raise AppError('Include 1–20 prescribed medicines.')
        normalized = []
        for item in medicines:
            validate_fields(item, {'medicine_code','quantity'}, {'medicine_code','quantity'})
            code = validate_text(item['medicine_code'], 'medicine code', 40)
            quantity = validate_integer(item['quantity'], 'Prescribed quantity')
            if code in [x[0] for x in normalized]:
                raise AppError('Include each medicine only once.')
            normalized.append((code, quantity))
        filename = None
        document = None
        if 'document' in payload:
            upload = payload['document']
            validate_fields(upload, {'filename','content'}, {'filename','content'})
            filename = validate_text(upload['filename'], 'filename', 120)
            if '/' in filename or '\\' in filename:
                raise AppError('Use a filename without a directory.')
            try:
                if not isinstance(upload['content'], str) or len(upload['content']) > 7_000_000:
                    raise ValueError()
                document = base64.b64decode(upload['content'], validate=True)
            except (ValueError, binascii.Error):
                raise AppError('The document could not be read.') from None
            extension = filename.rsplit('.', 1)[-1].lower()
            signatures = {'pdf': b'%PDF-', 'png': b'\x89PNG\r\n\x1a\n', 'jpg': b'\xff\xd8\xff', 'jpeg': b'\xff\xd8\xff'}
            if not document or len(document) > 5*1024*1024 or extension not in signatures or not document.startswith(signatures[extension]):
                raise AppError('Upload a PDF, PNG or JPEG document up to 5 MB.')
        prescription_id = new_id()
        timestamp = utc_now()
        status = 'EXPIRED' if expiry < date.today().isoformat() else 'MANUAL_REVIEW_REQUIRED'
        with self.database.transaction() as db:
            if not db.execute("SELECT 1 FROM users WHERE id=? AND role='PATIENT'", (patient_id,)).fetchone():
                raise AppError('Patient not found.', 404)
            if source_id and db.execute('SELECT 1 FROM prescriptions WHERE patient_id=? AND source_identifier=?', (patient_id, source_id)).fetchone():
                raise AppError('This prescription has already been imported.', 409)
            for code, _ in normalized:
                if not db.execute('SELECT 1 FROM medicines WHERE code=?', (code,)).fetchone():
                    raise AppError('A prescribed medicine is not in the catalog.')
            db.execute('''INSERT INTO prescriptions (id,patient_id,source,source_identifier,status,authored_on,expires_on,
                prescriber,reason,consent_reference,filename,document,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (prescription_id, patient_id, 'UPLOAD' if document else 'STRUCTURED_IMPORT', source_id, status, authored, expiry,
                 prescriber, 'Awaiting pharmacist review.' if status != 'EXPIRED' else 'Prescription has expired.',
                 consent, filename, document, timestamp, timestamp))
            for code, quantity in normalized:
                db.execute('INSERT INTO medication_requests VALUES (?,?,?,?)', (new_id(), prescription_id, code, quantity))
            self.audit(db, actor, 'PRESCRIPTION_RECEIVED', prescription_id, current='RECEIVED')
            self.audit(db, SYSTEM, 'PRESCRIPTION_VALIDATION', prescription_id, 'RECEIVED', 'VALIDATION_PENDING', 'Structural validation completed.')
            self.audit(db, SYSTEM, 'PRESCRIPTION_VALIDATION', prescription_id, 'VALIDATION_PENDING', status, 'Clinical validation requires a pharmacist.')
        return prescription_id

    def review_prescription(self, actor, prescription_id, payload):
        require_role(actor, 'PHARMACIST')
        validate_fields(payload, {'status','reason','urgency','continuity','restricted_approved'}, {'status','reason','urgency','continuity'})
        status = payload['status']
        if status not in PRESCRIPTION_STATES or payload['urgency'] not in URGENCY_RANK or payload['continuity'] not in CONTINUITY_RANK:
            raise AppError('Choose a valid review status, urgency and continuity value.')
        reason = validate_text(payload['reason'], 'review reason', 500)
        restricted = payload.get('restricted_approved', False)
        if type(restricted) is not bool:
            raise AppError('Restricted-medicine approval must be true or false.')
        with self.database.transaction() as db:
            prescription = db.execute('SELECT * FROM prescriptions WHERE id=?', (prescription_id,)).fetchone()
            if not prescription:
                raise AppError('Prescription not found.', 404)
            if status == 'VALID' and prescription['expires_on'] < date.today().isoformat():
                raise AppError('An expired prescription cannot be approved.')
            previous = {key: prescription[key] for key in ('status','urgency','continuity','restricted_approved','verified_by')}
            current = {'status': status, 'urgency': payload['urgency'], 'continuity': payload['continuity'], 'restricted_approved': restricted, 'verified_by': actor['id']}
            db.execute('''UPDATE prescriptions SET status=?,reason=?,urgency=?,continuity=?,restricted_approved=?,
                verified_by=?,verified_at=?,updated_at=? WHERE id=?''',
                (status, reason, payload['urgency'], payload['continuity'], int(restricted), actor['id'], utc_now(), utc_now(), prescription_id))
            self.audit(db, actor, 'PRESCRIPTION_REVIEWED', prescription_id, previous, current, reason)
            self.audit(db, actor, 'URGENCY_VERIFIED', prescription_id, previous['urgency'], current['urgency'], reason)
            self._reconcile(db)

    def create_booking(self, actor, payload):
        require_role(actor, 'PATIENT', 'PHARMACIST')
        validate_fields(payload, {'request_id','quantity','location_id'}, {'request_id','quantity'})
        request_id = validate_text(payload['request_id'], 'medication request', 40)
        quantity = validate_integer(payload['quantity'], 'Requested quantity')
        location = validate_text(payload.get('location_id', 'MAIN'), 'location', 40)
        if location != 'MAIN':
            raise AppError('Select the configured pharmacy location.')
        booking_id = new_id()
        try:
            with self.database.transaction() as db:
                self._reconcile(db)
                request = db.execute('''SELECT r.*,p.patient_id,p.status,p.expires_on,p.verified_by,p.restricted_approved,m.category
                    FROM medication_requests r JOIN prescriptions p ON p.id=r.prescription_id
                    JOIN medicines m ON m.code=r.medicine_code WHERE r.id=?''', (request_id,)).fetchone()
                if not request:
                    raise AppError('Medication request not found.', 404)
                require_owner(actor, request['patient_id'])
                if request['status'] != 'VALID' or request['expires_on'] < date.today().isoformat():
                    raise AppError('This prescription must be valid and reviewed before booking.')
                if db.execute("SELECT 1 FROM bookings WHERE request_id=? AND status NOT IN ('CANCELLED','REJECTED','FULFILLED','EXPIRED')", (request_id,)).fetchone():
                    raise AppError('An active booking already exists for this medicine request.', 409)
                fulfilled = db.execute('SELECT COALESCE(SUM(fulfilled_quantity),0) FROM bookings WHERE request_id=?', (request_id,)).fetchone()[0]
                if quantity + fulfilled > request['quantity']:
                    raise AppError('Requested quantity exceeds the remaining prescribed quantity.')
                timestamp = utc_now()
                db.execute('''INSERT INTO bookings (id,patient_id,request_id,location_id,quantity,status,created_at,updated_at)
                    VALUES (?,?,?,?,?,'SUBMITTED',?,?)''', (booking_id, request['patient_id'], request_id, location, quantity, timestamp, timestamp))
                booking = self.find_booking(db, booking_id, actor)
                self.audit(db, actor, 'BOOKING_SUBMITTED', booking_id, current='SUBMITTED')
                self.notify(db, booking, 'SUBMITTED')
                self._allocate(db)
        except AppError as error:
            with self.database.transaction() as db:
                self.audit(db, actor, 'BOOKING_REQUEST_REJECTED', booking_id, reason=str(error))
            raise
        return booking_id

    def _release(self, db, booking, actor, reason):
        items = db.execute("SELECT * FROM reservation_items WHERE booking_id=? AND state='RESERVED'", (booking['id'],)).fetchall()
        for item in items:
            db.execute('UPDATE inventory SET reserved=reserved-?,updated_at=? WHERE id=?', (item['quantity'], utc_now(), item['inventory_id']))
        db.execute("UPDATE reservation_items SET state='RELEASED',updated_at=? WHERE booking_id=? AND state='RESERVED'", (utc_now(), booking['id']))
        db.execute('UPDATE bookings SET reserved_quantity=0,expires_at=NULL,updated_at=? WHERE id=?', (utc_now(), booking['id']))
        if items:
            self.audit(db, actor, 'STOCK_RELEASED', booking['id'], booking['reserved_quantity'], 0, reason)

    def _close_waitlist(self, db, booking_id, state):
        entry = db.execute('SELECT * FROM waitlist WHERE booking_id=?', (booking_id,)).fetchone()
        if entry and entry['state'] != state:
            db.execute('UPDATE waitlist SET state=?,updated_at=? WHERE id=?', (state, utc_now(), entry['id']))
            self.audit(db, SYSTEM, 'WAITLIST_' + state, booking_id, entry['state'], state)

    def _reconcile(self, db):
        today, timestamp = date.today().isoformat(), utc_now()
        expired = db.execute("SELECT id,status FROM prescriptions WHERE expires_on<? AND status NOT IN ('EXPIRED','INVALID')", (today,)).fetchall()
        for prescription in expired:
            db.execute("UPDATE prescriptions SET status='EXPIRED',reason='Prescription has expired.',updated_at=? WHERE id=?", (timestamp,prescription['id']))
            self.audit(db,SYSTEM,'PRESCRIPTION_EXPIRED',prescription['id'],prescription['status'],'EXPIRED')
        for row in db.execute(BOOKING_QUERY + " WHERE b.status NOT IN ('CANCELLED','REJECTED','FULFILLED','EXPIRED')").fetchall():
            booking = dict(row)
            reason = self.eligibility_reason(booking)
            if reason or (booking['expires_at'] and booking['expires_at'] <= timestamp):
                state = 'REJECTED' if reason else 'EXPIRED'
                reason = reason or 'Reservation collection window has ended.'
                self._release(db, booking, SYSTEM, reason)
                self.transition(db, booking, state, SYSTEM, reason)
                self._close_waitlist(db, booking['id'], state)
                continue
            bad_stock = db.execute('''SELECT 1 FROM reservation_items r JOIN inventory i ON i.id=r.inventory_id
                WHERE r.booking_id=? AND r.state='RESERVED' AND (i.state!='IN_STOCK' OR i.expires_on<?)''', (booking['id'],today)).fetchone()
            if bad_stock:
                self._release(db, booking, SYSTEM, 'Reserved batch is no longer allocatable.')
                self.transition(db, booking, 'WAITLISTED', SYSTEM, 'Reserved batch is unavailable. Waiting for eligible stock.')
        self._allocate(db)

    def _allocate(self, db):
        settings = dict(db.execute('SELECT key,value FROM settings').fetchall())
        partial = settings['partial_allocation'] == 'true'
        rows = db.execute(BOOKING_QUERY + " WHERE b.status IN ('SUBMITTED','WAITLISTED','PARTIALLY_RESERVED')").fetchall()
        candidates = sorted([dict(row) for row in rows], key=priority_key)
        group_ranks = {}
        for booking in candidates:
            reason = self.eligibility_reason(booking)
            if reason:
                self._release(db, booking, SYSTEM, reason)
                self.transition(db, booking, 'REJECTED', SYSTEM, reason)
                self._close_waitlist(db, booking['id'], 'REJECTED')
                continue
            group = (booking['medicine_code'],booking['location_id'])
            group_ranks[group] = group_ranks.get(group,0) + 1
            reasons = json.dumps(priority_reasons(booking))
            rank = group_ranks[group]
            if booking['priority_reasons'] != reasons or booking['priority_rank'] != rank or booking['policy_version'] != POLICY_VERSION:
                self.audit(db,SYSTEM,'PRIORITY_EVALUATED',booking['id'],
                           {'rank':booking['priority_rank'],'reasons':json.loads(booking['priority_reasons'])},
                           {'rank':rank,'reasons':json.loads(reasons),'policy_version':POLICY_VERSION})
            db.execute('UPDATE bookings SET priority_reasons=?,priority_rank=?,policy_version=?,evaluated_at=? WHERE id=?',
                       (reasons,rank,POLICY_VERSION,utc_now(),booking['id']))
            remaining = booking['quantity'] - booking['reserved_quantity'] - booking['fulfilled_quantity']
            batches = db.execute('''SELECT * FROM inventory WHERE medicine_code=? AND location_id=? AND state='IN_STOCK'
                AND expires_on>=? AND on_hand>reserved ORDER BY expires_on,id''', (*group,date.today().isoformat())).fetchall()
            available = sum(batch['on_hand'] - batch['reserved'] for batch in batches)
            quantity = min(available, remaining) if partial else (remaining if available >= remaining else 0)
            if quantity:
                left = quantity
                for batch in batches:
                    take = min(left,batch['on_hand']-batch['reserved'])
                    if take:
                        updated = db.execute('''UPDATE inventory SET reserved=reserved+?,updated_at=?
                            WHERE id=? AND on_hand-reserved>=?''', (take,utc_now(),batch['id'],take)).rowcount
                        if updated != 1:
                            raise AppError('Stock changed. Please retry.',409)
                        db.execute('INSERT INTO reservation_items VALUES (?,?,?,?,?,?,?)',
                                   (new_id(),booking['id'],batch['id'],take,'RESERVED',utc_now(),utc_now()))
                        left -= take
                    if not left:
                        break
                expiry = booking['expires_at'] or (datetime.now(timezone.utc)+timedelta(minutes=int(settings['reservation_minutes']))).isoformat()
                db.execute('UPDATE bookings SET reserved_quantity=reserved_quantity+?,expires_at=?,updated_at=? WHERE id=?',
                           (quantity,expiry,utc_now(),booking['id']))
                booking['reserved_quantity'] += quantity
                booking['expires_at'] = expiry
                self.audit(db,SYSTEM,'STOCK_RESERVED',booking['id'],current={'quantity':quantity,'location':booking['location_id'],'expires_at':expiry})
            unfilled = booking['quantity'] - booking['reserved_quantity'] - booking['fulfilled_quantity']
            if not unfilled:
                self.transition(db,booking,'RESERVED',SYSTEM,'Stock reserved for collection.')
                if db.execute("SELECT 1 FROM waitlist WHERE booking_id=? AND state='ACTIVE'",(booking['id'],)).fetchone():
                    db.execute('UPDATE waitlist SET promoted_at=? WHERE booking_id=?',(utc_now(),booking['id']))
                    self._close_waitlist(db,booking['id'],'PROMOTED')
            else:
                state = 'PARTIALLY_RESERVED' if booking['reserved_quantity'] else 'WAITLISTED'
                self.transition(db,booking,state,SYSTEM,'Waiting for additional eligible stock.')
                if quantity and booking['status'] == 'PARTIALLY_RESERVED':
                    self.notify(db,booking,'PARTIALLY_RESERVED')
                entry = db.execute('SELECT * FROM waitlist WHERE booking_id=?',(booking['id'],)).fetchone()
                if not entry:
                    timestamp = utc_now()
                    db.execute('INSERT INTO waitlist VALUES (?,?,\'ACTIVE\',?,NULL,?)',(new_id(),booking['id'],timestamp,timestamp))
                    self.audit(db,SYSTEM,'WAITLIST_ENTERED',booking['id'],current='ACTIVE')
                elif entry['state'] != 'ACTIVE':
                    db.execute("UPDATE waitlist SET state='ACTIVE',updated_at=? WHERE id=?",(utc_now(),entry['id']))
                    self.audit(db,SYSTEM,'WAITLIST_ENTERED',booking['id'],entry['state'],'ACTIVE')

    def update_booking(self, actor, booking_id, action, payload):
        if action == 'cancel':
            require_role(actor,'PATIENT','PHARMACIST')
        else:
            require_role(actor,'PHARMACIST')
        with self.database.transaction() as db:
            self._reconcile(db)
            booking = self.find_booking(db,booking_id,actor)
            if booking['status'] in TERMINAL_STATES:
                raise AppError('This booking is already closed.',409)
            if action == 'cancel':
                validate_fields(payload,set())
                self._release(db,booking,actor,'Booking cancelled.')
                self.transition(db,booking,'CANCELLED',actor,'Cancelled by an authorized user.')
                self._close_waitlist(db,booking_id,'CANCELLED')
            elif action == 'override':
                validate_fields(payload,{'rank','reason'},{'rank','reason'})
                if booking['status'] not in {'WAITLISTED','PARTIALLY_RESERVED'}:
                    raise AppError('Only a waiting request can receive a priority override.',409)
                rank = validate_integer(payload['rank'],'Override rank',maximum=10000)
                reason = validate_text(payload['reason'],'override reason',500)
                db.execute('UPDATE bookings SET override_rank=?,override_reason=?,updated_at=? WHERE id=?',(rank,reason,utc_now(),booking_id))
                self.audit(db,actor,'PRIORITY_OVERRIDE',booking_id,{'rank':booking['override_rank']},{'rank':rank},reason)
            elif action == 'ready':
                validate_fields(payload,set())
                if booking['status'] != 'RESERVED':
                    raise AppError('Reserve the full remaining quantity before marking it ready.',409)
                self.transition(db,booking,'READY',actor,'Ready for collection.')
            elif action == 'fulfil':
                validate_fields(payload,set())
                if booking['status'] != 'READY':
                    raise AppError('Mark the reservation ready before fulfilment.',409)
                items = db.execute("SELECT * FROM reservation_items WHERE booking_id=? AND state='RESERVED'",(booking_id,)).fetchall()
                if not items or sum(x['quantity'] for x in items) != booking['reserved_quantity']:
                    raise AppError('The reservation requires review.',409)
                for item in items:
                    db.execute('UPDATE inventory SET on_hand=on_hand-?,reserved=reserved-?,updated_at=? WHERE id=?',
                               (item['quantity'],item['quantity'],utc_now(),item['inventory_id']))
                db.execute("UPDATE reservation_items SET state='FULFILLED',updated_at=? WHERE booking_id=? AND state='RESERVED'",(utc_now(),booking_id))
                db.execute('UPDATE bookings SET fulfilled_quantity=fulfilled_quantity+reserved_quantity,reserved_quantity=0,expires_at=NULL,updated_at=? WHERE id=?',(utc_now(),booking_id))
                self.transition(db,booking,'FULFILLED',actor,'Collected at the pharmacy.')
                self._close_waitlist(db,booking_id,'FULFILLED')
            else:
                raise AppError('Action not found.',404)
            self._allocate(db)

    def restock(self, actor, payload):
        require_role(actor,'PHARMACIST')
        validate_fields(payload,{'event_id','medicine_code','quantity','batch','expires_on','location_id'},
                        {'event_id','medicine_code','quantity','batch','expires_on'})
        event_id = validate_text(payload['event_id'],'event identifier',100)
        code = validate_text(payload['medicine_code'],'medicine code',40)
        quantity = validate_integer(payload['quantity'],'Restock quantity')
        batch = validate_text(payload['batch'],'batch identifier',80)
        expiry = validate_date(payload['expires_on'],'batch expiry')
        location = payload.get('location_id','MAIN')
        if expiry < date.today().isoformat() or location != 'MAIN':
            raise AppError('Choose an unexpired batch and a configured location.')
        normalized = json.dumps({'medicine_code':code,'quantity':quantity,'batch':batch,'expires_on':expiry,'location_id':location},sort_keys=True)
        with self.database.transaction() as db:
            previous = db.execute('SELECT * FROM restock_events WHERE event_id=?',(event_id,)).fetchone()
            if previous:
                if previous['payload'] != normalized:
                    raise AppError('This event identifier was used for a different restock.',409)
                return previous['inventory_id']
            if not db.execute('SELECT 1 FROM medicines WHERE code=?',(code,)).fetchone():
                raise AppError('Medicine not found.',404)
            inventory = db.execute('SELECT * FROM inventory WHERE medicine_code=? AND location_id=? AND batch=?',(code,location,batch)).fetchone()
            inventory_id = inventory['id'] if inventory else new_id()
            if inventory:
                if inventory['expires_on'] != expiry or inventory['state'] != 'IN_STOCK':
                    raise AppError('The existing batch has different expiry details or is blocked.',409)
                db.execute('UPDATE inventory SET on_hand=on_hand+?,updated_at=? WHERE id=?',(quantity,utc_now(),inventory_id))
            else:
                db.execute('INSERT INTO inventory VALUES (?,?,?,?,?,?,?,0,?)',(inventory_id,code,location,batch,expiry,'IN_STOCK',quantity,utc_now()))
            db.execute('INSERT INTO restock_events VALUES (?,?,?,?)',(event_id,normalized,inventory_id,utc_now()))
            self.audit(db,actor,'INVENTORY_RESTOCKED',inventory_id,current=json.loads(normalized))
            self._reconcile(db)
        return inventory_id

    def update_inventory(self, actor, inventory_id, payload):
        require_role(actor,'PHARMACIST')
        validate_fields(payload,{'state','reason'},{'state','reason'})
        if payload['state'] not in INVENTORY_STATES:
            raise AppError('Choose a valid inventory state.')
        reason = validate_text(payload['reason'],'stock-change reason',500)
        with self.database.transaction() as db:
            item = db.execute('SELECT * FROM inventory WHERE id=?',(inventory_id,)).fetchone()
            if not item:
                raise AppError('Inventory batch not found.',404)
            if payload['state']=='IN_STOCK' and item['expires_on']<date.today().isoformat():
                raise AppError('Expired batches cannot be returned to available stock.')
            db.execute('UPDATE inventory SET state=?,updated_at=? WHERE id=?',(payload['state'],utc_now(),inventory_id))
            self.audit(db,actor,'INVENTORY_STATE_CHANGED',inventory_id,item['state'],payload['state'],reason)
            self._reconcile(db)

    def update_settings(self, actor, payload):
        require_role(actor,'MANAGER')
        validate_fields(payload,{'reservation_minutes','partial_allocation'},{'reservation_minutes','partial_allocation'})
        minutes = validate_integer(payload['reservation_minutes'],'Reservation duration',maximum=10080)
        if type(payload['partial_allocation']) is not bool:
            raise AppError('Partial allocation must be true or false.')
        with self.database.transaction() as db:
            previous = dict(db.execute('SELECT key,value FROM settings').fetchall())
            db.execute("UPDATE settings SET value=? WHERE key='reservation_minutes'",(str(minutes),))
            db.execute("UPDATE settings SET value=? WHERE key='partial_allocation'",(str(payload['partial_allocation']).lower(),))
            self.audit(db,actor,'POLICY_UPDATED','settings',previous,payload,'Manager updated allocation settings.')
            self._reconcile(db)

    def reconcile(self):
        with self.database.transaction() as db:
            self._reconcile(db)

    def get_document(self, actor, prescription_id):
        with self.database.transaction() as db:
            prescription = db.execute('SELECT patient_id,filename,document FROM prescriptions WHERE id=?',(prescription_id,)).fetchone()
            if not prescription:
                raise AppError('Document not found.',404)
            require_owner(actor,prescription['patient_id'])
            if not prescription['document']:
                raise AppError('This prescription has no attached document.',404)
            self.audit(db,actor,'PRESCRIPTION_DOCUMENT_VIEWED',prescription_id)
            return prescription['filename'],prescription['document']

    def snapshot(self, actor):
        with self.database.transaction() as db:
            patient = actor['role']=='PATIENT'
            where = ' WHERE patient_id=?' if patient else ''
            args = (actor['id'],) if patient else ()
            prescriptions = [dict(r) for r in db.execute('''SELECT id,patient_id,source,source_identifier,status,authored_on,
                expires_on,prescriber,reason,urgency,continuity,verified_by,verified_at,consent_reference,
                restricted_approved,filename,created_at,updated_at FROM prescriptions''' + where + ' ORDER BY created_at DESC',args)]
            for prescription in prescriptions:
                prescription['medicines']=[dict(r) for r in db.execute('''SELECT r.*,m.name,m.strength FROM medication_requests r
                    JOIN medicines m ON m.code=r.medicine_code WHERE r.prescription_id=?''',(prescription['id'],))]
                if patient:
                    for key in ('urgency','continuity','verified_by','restricted_approved','consent_reference'):
                        prescription.pop(key,None)
            bookings = [dict(r) for r in db.execute(BOOKING_QUERY + (' WHERE b.patient_id=?' if patient else '')+' ORDER BY b.created_at DESC',args)]
            for booking in bookings:
                booking['remaining_quantity']=booking['quantity']-booking['reserved_quantity']-booking['fulfilled_quantity']
                if patient:
                    for key in ('urgency','continuity','priority_rank','priority_reasons','override_rank','override_reason','verified_by','restricted_approved','patient_name','category'):
                        booking.pop(key,None)
                else:
                    booking['priority_reasons']=json.loads(booking['priority_reasons'])
                    booking['reservations']=[dict(r) for r in db.execute('''SELECT r.quantity,r.state,i.batch,i.expires_on,i.location_id
                        FROM reservation_items r JOIN inventory i ON i.id=r.inventory_id WHERE r.booking_id=?''',(booking['id'],))]
            inventory=[dict(r) for r in db.execute('''SELECT i.*,m.name,m.strength,m.category,
                CASE WHEN i.state='IN_STOCK' AND i.expires_on>=? THEN i.on_hand-i.reserved ELSE 0 END AS available
                FROM inventory i JOIN medicines m ON m.code=i.medicine_code ORDER BY m.name,i.expires_on''',(date.today().isoformat(),))]
            notifications=[dict(r) for r in db.execute('SELECT * FROM notifications'+where+' ORDER BY created_at DESC LIMIT 200',args)]
            result={'user':{k:actor[k] for k in ('id','name','email','role')},'csrf':actor.get('csrf',''),
                    'medicines':[dict(r) for r in db.execute('SELECT * FROM medicines ORDER BY name')],
                    'prescriptions':prescriptions,'bookings':bookings,'notifications':notifications,
                    'settings':dict(db.execute('SELECT key,value FROM settings').fetchall()),'policy_version':POLICY_VERSION}
            if patient:
                result['availability']={medicine['code']:sum(item['available'] for item in inventory if item['medicine_code']==medicine['code']) for medicine in result['medicines']}
            else:
                result['inventory']=inventory
                result['waitlist']=[dict(r) for r in db.execute('''SELECT w.*,b.request_id,b.location_id,b.priority_rank,r.medicine_code,
                    b.quantity-b.reserved_quantity-b.fulfilled_quantity AS remaining_quantity
                    FROM waitlist w JOIN bookings b ON b.id=w.booking_id JOIN medication_requests r ON r.id=b.request_id
                    WHERE w.state='ACTIVE' ORDER BY r.medicine_code,b.priority_rank''')]
                result['patients']=[dict(r) for r in db.execute("SELECT id,name FROM users WHERE role='PATIENT' ORDER BY name")]
                result['audit']=[dict(r) for r in db.execute('SELECT * FROM audit ORDER BY sequence DESC LIMIT 200')]
            return result
