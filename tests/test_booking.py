"""Integration tests for the persistent SRS booking workflows."""
import base64
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from medicine_booking.access import AuthService, hash_password
from medicine_booking.booking import BookingService
from medicine_booking.domain import AppError, new_id, utc_now
from medicine_booking.notifications import NotificationService
from medicine_booking.priority import priority_key
from medicine_booking.storage import Database

PASSWORD_HASH=hash_password('TestPassword123')


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.database=Database(Path(self.directory.name)/'test.db')
        self.service=BookingService(self.database)
        self.patient={'id':'patient-a','name':'Patient A','role':'PATIENT','email':'a@example.test'}
        self.other={'id':'patient-b','name':'Patient B','role':'PATIENT','email':'b@example.test'}
        self.pharmacist={'id':'pharmacist','name':'Pharmacist','role':'PHARMACIST','email':'rx@example.test'}
        self.manager={'id':'manager','name':'Manager','role':'MANAGER','email':'manager@example.test'}
        with self.database.transaction() as db:
            for actor in [self.patient,self.other,self.pharmacist,self.manager]:
                db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',(actor['id'],actor['name'],actor['email'],actor['role'],PASSWORD_HASH,utc_now()))
            db.executemany('INSERT INTO medicines VALUES (?,?,?,?)',[
                ('MED-A','Medicine A','10 mg','STANDARD'),('MED-R','Medicine R','5 mg','RESTRICTED'),
                ('MED-X','Medicine X','1 mg','EXCLUDED')])

    def tearDown(self):
        self.directory.cleanup()

    def query(self,sql,args=()):
        with self.database.transaction() as db:
            return [dict(row) for row in db.execute(sql,args)]

    def prescription(self,patient=None,quantity=10,code='MED-A',approved=True,urgency='NORMAL',continuity='UNKNOWN',restricted=False,**extras):
        patient=patient or self.patient
        payload={'authored_on':date.today().isoformat(),'expires_on':(date.today()+timedelta(days=30)).isoformat(),
                 'prescriber':'PRESCRIBER-001','medicines':[{'medicine_code':code,'quantity':quantity}],**extras}
        prescription_id=self.service.submit_prescription(patient,payload)
        if approved:
            self.service.review_prescription(self.pharmacist,prescription_id,{'status':'VALID','reason':'Verified against the source prescription.',
                'urgency':urgency,'continuity':continuity,'restricted_approved':restricted})
        request_id=self.query('SELECT id FROM medication_requests WHERE prescription_id=?',(prescription_id,))[0]['id']
        return prescription_id,request_id

    def stock(self,quantity,code='MED-A',event_id=None,batch=None):
        return self.service.restock(self.pharmacist,{'event_id':event_id or new_id(),'medicine_code':code,
            'quantity':quantity,'batch':batch or new_id(),'expires_on':(date.today()+timedelta(days=365)).isoformat()})

    def book(self,request_id,quantity=1,patient=None):
        return self.service.create_booking(patient or self.patient,{'request_id':request_id,'quantity':quantity})

    def booking(self,booking_id):
        return self.query('SELECT * FROM bookings WHERE id=?',(booking_id,))[0]

    def test_prescription_intake_preserves_source_consent_links_and_validation_history(self):
        prescription_id,request_id=self.prescription(approved=False,source_identifier='SOURCE-42',consent_reference='CONSENT-42')
        row=self.query('SELECT * FROM prescriptions WHERE id=?',(prescription_id,))[0]
        self.assertEqual(row['status'],'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(row['source_identifier'],'SOURCE-42')
        self.assertEqual(row['consent_reference'],'CONSENT-42')
        self.assertTrue(row['created_at'].endswith('+00:00'))
        self.assertEqual(len(self.query('SELECT * FROM audit WHERE object_id=?',(prescription_id,))),3)
        self.assertEqual(self.query('SELECT * FROM medication_requests WHERE id=?',(request_id,))[0]['prescription_id'],prescription_id)

    def test_structured_schema_quantity_and_duplicate_source_are_validated(self):
        self.prescription(source_identifier='SOURCE-42')
        with self.assertRaises(AppError):self.prescription(source_identifier='SOURCE-42')
        for quantity in (0,-1,True,1.5,'1'):
            with self.subTest(quantity=quantity),self.assertRaises(AppError):self.prescription(quantity=quantity)
        with self.assertRaises(AppError):self.prescription(urgency_from_patient='HIGH')
        with self.assertRaises(AppError):self.prescription(code='UNKNOWN')

    def test_upload_constraints_and_document_access(self):
        document={'filename':'prescription.pdf','content':base64.b64encode(b'%PDF-1.4\nsynthetic fixture').decode()}
        prescription_id,_=self.prescription(approved=False,document=document)
        filename,body=self.service.get_document(self.patient,prescription_id)
        self.assertEqual(filename,'prescription.pdf')
        self.assertTrue(body.startswith(b'%PDF-'))
        with self.assertRaises(AppError):self.service.get_document(self.other,prescription_id)
        for upload in [{'filename':'script.exe','content':document['content']},
                       {'filename':'fake.pdf','content':base64.b64encode(b'not a pdf').decode()},
                       {'filename':'../p.pdf','content':document['content']},
                       {'filename':'p.pdf','content':'%%%'}]:
            with self.subTest(upload=upload),self.assertRaises(AppError):self.prescription(document=upload)
        with self.assertRaises(AppError):
            self.prescription(document={'filename':'large.pdf','content':base64.b64encode(b'%PDF-'+b'x'*(5*1024*1024)).decode()})

    def test_only_pharmacists_can_review_or_confirm_urgency(self):
        prescription_id,_=self.prescription(approved=False)
        payload={'status':'VALID','reason':'Review','urgency':'HIGH','continuity':'UNKNOWN'}
        for actor in (self.patient,self.other,self.manager):
            with self.subTest(actor=actor['role']),self.assertRaises(AppError):self.service.review_prescription(actor,prescription_id,payload)
        self.service.review_prescription(self.pharmacist,prescription_id,payload)
        review=self.query('SELECT * FROM audit WHERE event=\'URGENCY_VERIFIED\'')[0]
        self.assertEqual(review['actor_id'],self.pharmacist['id'])

    def test_invalid_pending_and_expired_prescriptions_cannot_allocate(self):
        self.stock(10)
        for status in ('INVALID','MANUAL_REVIEW_REQUIRED','EXPIRED'):
            prescription_id,request_id=self.prescription(approved=False)
            self.service.review_prescription(self.pharmacist,prescription_id,{'status':status,'reason':'Review result','urgency':'NORMAL','continuity':'UNKNOWN'})
            with self.subTest(status=status),self.assertRaises(AppError):self.book(request_id)
        self.assertEqual(self.query('SELECT SUM(reserved) AS quantity FROM inventory')[0]['quantity'],0)
        self.assertEqual(len(self.query("SELECT * FROM audit WHERE event='BOOKING_REQUEST_REJECTED'")),3)

    def test_full_allocation_records_reservation_batch_location_and_expiry(self):
        inventory_id=self.stock(5)
        _,request_id=self.prescription()
        booking_id=self.book(request_id,3)
        booking=self.booking(booking_id)
        self.assertEqual((booking['status'],booking['reserved_quantity']),('RESERVED',3))
        self.assertIsNotNone(booking['expires_at'])
        self.assertEqual(self.query('SELECT * FROM reservation_items')[0]['inventory_id'],inventory_id)
        self.assertEqual(self.query('SELECT reserved FROM inventory')[0]['reserved'],3)
        self.assertEqual(booking['location_id'],'MAIN')

    def test_duplicate_and_excess_quantity_are_rejected(self):
        _,request_id=self.prescription(quantity=3)
        self.book(request_id,2)
        with self.assertRaises(AppError):self.book(request_id,1)
        _,another=self.prescription(quantity=2)
        with self.assertRaises(AppError):self.book(another,3)
        with self.assertRaises(AppError):self.service.create_booking(self.patient,{'request_id':another,'quantity':1,'urgency':'HIGH'})

    def test_waitlist_uses_verified_urgency_continuity_and_current_policy(self):
        _,normal=self.prescription()
        _,high=self.prescription(patient=self.other,urgency='HIGH',continuity='CONTINUITY')
        first=self.book(normal,2)
        second=self.book(high,2,self.other)
        self.stock(2)
        self.assertEqual(self.booking(second)['status'],'RESERVED')
        self.assertEqual(self.booking(first)['status'],'WAITLISTED')
        self.assertEqual(self.query('SELECT state FROM waitlist WHERE booking_id=?',(second,))[0]['state'],'PROMOTED')
        self.assertIn('VERIFIED_URGENCY:HIGH',json.loads(self.booking(second)['priority_reasons']))

    def test_priority_ties_use_continuity_waiting_time_and_identifier_only(self):
        base={'id':'a','override_rank':None,'urgency':'NORMAL','continuity':'UNKNOWN','created_at':'2026-01-01T00:00:00+00:00'}
        self.assertLess(priority_key({**base,'continuity':'CONTINUITY'}),priority_key(base))
        self.assertLess(priority_key(base),priority_key({**base,'id':'b'}))
        self.assertLess(priority_key(base),priority_key({**base,'created_at':'2026-01-02T00:00:00+00:00'}))
        self.assertEqual(priority_key(base),priority_key({**base,'purchase_value':99999,'subscription':'premium','age':99}))

    def test_review_change_re_evaluates_existing_waitlist(self):
        first_rx,first_request=self.prescription()
        second_rx,second_request=self.prescription(patient=self.other)
        first=self.book(first_request)
        second=self.book(second_request,patient=self.other)
        self.service.review_prescription(self.pharmacist,second_rx,{'status':'VALID','reason':'Authorized urgency confirmation',
            'urgency':'HIGH','continuity':'UNKNOWN'})
        self.assertEqual(self.booking(second)['priority_rank'],1)
        self.assertEqual(self.booking(first)['priority_rank'],2)
        history=self.query("SELECT * FROM audit WHERE object_id=? AND event='PRIORITY_EVALUATED'",(second,))
        self.assertGreaterEqual(len(history),2)
        self.assertIn('VERIFIED_URGENCY:NORMAL',history[-1]['previous_state'])
        self.assertIn('VERIFIED_URGENCY:HIGH',history[-1]['new_state'])
        self.stock(1)
        self.assertEqual(self.booking(second)['status'],'RESERVED')

    def test_partial_reservation_expiry_releases_quantity_and_closes_waitlist(self):
        self.service.update_settings(self.manager,{'reservation_minutes':60,'partial_allocation':True})
        self.stock(2)
        _,request_id=self.prescription(quantity=5)
        booking_id=self.book(request_id,5)
        with self.database.transaction() as db:
            db.execute("UPDATE bookings SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(booking_id,))
        self.service.reconcile()
        self.assertEqual(self.booking(booking_id)['status'],'EXPIRED')
        self.assertEqual(self.query('SELECT reserved FROM inventory')[0]['reserved'],0)
        self.assertEqual(self.query('SELECT state FROM waitlist')[0]['state'],'EXPIRED')

    def test_additional_partial_reservation_creates_an_update(self):
        self.service.update_settings(self.manager,{'reservation_minutes':60,'partial_allocation':True})
        self.stock(1)
        _,request_id=self.prescription(quantity=5)
        booking_id=self.book(request_id,5)
        self.stock(1)
        events=self.query("SELECT * FROM notifications WHERE booking_id=? AND template='PARTIALLY_RESERVED'",(booking_id,))
        self.assertEqual(len(events),2)
        self.assertEqual(self.booking(booking_id)['reserved_quantity'],2)

    def test_concurrent_last_unit_is_reserved_once(self):
        self.stock(1)
        _,first=self.prescription()
        _,second=self.prescription(patient=self.other)
        barrier=threading.Barrier(2)
        def submit(request,patient):
            barrier.wait()
            return self.book(request,1,patient)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(submit,first,self.patient),pool.submit(submit,second,self.other)]
            ids=[future.result() for future in futures]
        self.assertCountEqual([self.booking(i)['status'] for i in ids],['RESERVED','WAITLISTED'])
        self.assertEqual(self.query('SELECT reserved FROM inventory')[0]['reserved'],1)

    def test_restock_replay_is_idempotent_and_conflicting_replay_is_rejected(self):
        _,request_id=self.prescription()
        self.book(request_id)
        payload={'event_id':'RESTOCK-42','medicine_code':'MED-A','quantity':2,'batch':'BATCH-42','expires_on':(date.today()+timedelta(days=30)).isoformat()}
        self.service.restock(self.pharmacist,payload)
        self.service.restock(self.pharmacist,payload)
        self.assertEqual(self.query('SELECT on_hand FROM inventory')[0]['on_hand'],2)
        self.assertEqual(len(self.query('SELECT * FROM reservation_items')),1)
        with self.assertRaises(AppError):self.service.restock(self.pharmacist,{**payload,'quantity':3})

    def test_cancellation_releases_stock_and_promotes_next_request(self):
        self.stock(1)
        _,first=self.prescription()
        _,second=self.prescription(patient=self.other)
        reserved=self.book(first)
        waiting=self.book(second,patient=self.other)
        self.service.update_booking(self.patient,reserved,'cancel',{})
        self.assertEqual(self.booking(reserved)['status'],'CANCELLED')
        self.assertEqual(self.booking(waiting)['status'],'RESERVED')
        self.assertEqual(self.query('SELECT reserved FROM inventory')[0]['reserved'],1)
        with self.assertRaises(AppError):self.service.update_booking(self.patient,reserved,'cancel',{})

    def test_expiry_releases_reserved_stock_and_promotes_waitlist(self):
        self.stock(1)
        _,first=self.prescription()
        _,second=self.prescription(patient=self.other)
        expired=self.book(first)
        waiting=self.book(second,patient=self.other)
        with self.database.transaction() as db:
            db.execute("UPDATE bookings SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(expired,))
        self.service.reconcile()
        self.assertEqual(self.booking(expired)['status'],'EXPIRED')
        self.assertEqual(self.booking(waiting)['status'],'RESERVED')

    def test_partial_allocation_tracks_remaining_quantity_until_full_reservation(self):
        self.service.update_settings(self.manager,{'reservation_minutes':60,'partial_allocation':True})
        self.stock(2)
        _,request_id=self.prescription(quantity=5)
        booking_id=self.book(request_id,5)
        self.assertEqual((self.booking(booking_id)['status'],self.booking(booking_id)['reserved_quantity']),('PARTIALLY_RESERVED',2))
        self.assertEqual(self.service.snapshot(self.patient)['bookings'][0]['remaining_quantity'],3)
        self.assertEqual(self.query('SELECT state FROM waitlist')[0]['state'],'ACTIVE')
        self.stock(3)
        self.assertEqual(self.booking(booking_id)['status'],'RESERVED')
        self.assertEqual(self.booking(booking_id)['reserved_quantity'],5)
        self.assertEqual(self.query('SELECT state FROM waitlist')[0]['state'],'PROMOTED')

    def test_partial_disabled_never_reserves_insufficient_stock(self):
        self.stock(2)
        _,request_id=self.prescription(quantity=5)
        booking_id=self.book(request_id,5)
        self.assertEqual(self.booking(booking_id)['reserved_quantity'],0)
        self.assertEqual(self.booking(booking_id)['status'],'WAITLISTED')

    def test_recalled_quarantined_expired_and_blocked_batches_are_excluded(self):
        for state in ('RECALLED','QUARANTINED','EXPIRED','BLOCKED'):
            inventory_id=self.stock(2)
            self.service.update_inventory(self.pharmacist,inventory_id,{'state':state,'reason':'Batch safety review'})
        expired=self.stock(2)
        with self.database.transaction() as db:
            db.execute("UPDATE inventory SET expires_on='2000-01-01' WHERE id=?",(expired,))
        _,request_id=self.prescription()
        self.assertEqual(self.booking(self.book(request_id))['status'],'WAITLISTED')

    def test_recall_releases_existing_reservation_and_requeues_booking(self):
        inventory_id=self.stock(2)
        _,request_id=self.prescription()
        booking_id=self.book(request_id,2)
        self.service.update_inventory(self.pharmacist,inventory_id,{'state':'RECALLED','reason':'Recall notice'})
        self.assertEqual(self.booking(booking_id)['status'],'WAITLISTED')
        self.assertEqual(self.query('SELECT reserved FROM inventory WHERE id=?',(inventory_id,))[0]['reserved'],0)
        self.stock(2)
        self.assertEqual(self.booking(booking_id)['status'],'RESERVED')

    def test_restricted_and_excluded_medicines_require_safety_review(self):
        self.stock(2,'MED-R')
        _,request_id=self.prescription(code='MED-R')
        self.assertEqual(self.booking(self.book(request_id))['status'],'REJECTED')
        _,approved=self.prescription(code='MED-R',restricted=True)
        self.assertEqual(self.booking(self.book(approved))['status'],'RESERVED')
        self.stock(2,'MED-X')
        _,excluded=self.prescription(code='MED-X',restricted=True)
        self.assertEqual(self.booking(self.book(excluded))['status'],'REJECTED')

    def test_invalidated_prescription_cannot_be_promoted_or_keep_reservation(self):
        self.stock(1)
        prescription_id,request_id=self.prescription()
        booking_id=self.book(request_id)
        self.service.review_prescription(self.pharmacist,prescription_id,{'status':'INVALID','urgency':'NORMAL','continuity':'UNKNOWN','reason':'Prescription invalidated'})
        self.assertEqual(self.booking(booking_id)['status'],'REJECTED')
        self.assertEqual(self.query('SELECT reserved FROM inventory')[0]['reserved'],0)

    def test_override_requires_pharmacist_reason_and_preserves_audit(self):
        _,first=self.prescription()
        _,second=self.prescription(patient=self.other)
        older=self.book(first)
        override=self.book(second,patient=self.other)
        for actor in (self.patient,self.manager):
            with self.assertRaises(AppError):self.service.update_booking(actor,override,'override',{'rank':1,'reason':'Reviewed priority'})
        with self.assertRaises(AppError):self.service.update_booking(self.pharmacist,override,'override',{'rank':1,'reason':''})
        self.service.update_booking(self.pharmacist,override,'override',{'rank':1,'reason':'Documented pharmacist decision'})
        self.stock(1)
        self.assertEqual(self.booking(override)['status'],'RESERVED')
        self.assertEqual(self.booking(older)['status'],'WAITLISTED')
        audit=self.query("SELECT * FROM audit WHERE event='PRIORITY_OVERRIDE'")[0]
        self.assertEqual(audit['actor_id'],self.pharmacist['id'])
        self.assertIsNotNone(audit['previous_state'])
        self.assertIsNotNone(audit['new_state'])
        self.assertTrue(audit['reason'])

    def test_fulfilment_consumes_inventory_and_prevents_overprescription(self):
        self.stock(3)
        _,request_id=self.prescription(quantity=2)
        booking_id=self.book(request_id,2)
        with self.assertRaises(AppError):self.service.update_booking(self.pharmacist,booking_id,'fulfil',{})
        self.service.update_booking(self.pharmacist,booking_id,'ready',{})
        self.service.update_booking(self.pharmacist,booking_id,'fulfil',{})
        self.assertEqual(self.booking(booking_id)['status'],'FULFILLED')
        self.assertEqual(self.query('SELECT on_hand,reserved FROM inventory')[0],{'on_hand':1,'reserved':0})
        with self.assertRaises(AppError):self.book(request_id)

    def test_patient_ownership_and_role_boundaries(self):
        _,request_id=self.prescription()
        booking_id=self.book(request_id)
        with self.assertRaises(AppError):self.book(request_id,patient=self.other)
        with self.assertRaises(AppError):self.service.update_booking(self.other,booking_id,'cancel',{})
        for actor in (self.patient,self.manager):
            with self.assertRaises(AppError):self.service.restock(actor,{'event_id':'a','medicine_code':'MED-A','quantity':1,'batch':'A','expires_on':'2099-01-01'})
        with self.assertRaises(AppError):self.service.update_settings(self.pharmacist,{'reservation_minutes':1,'partial_allocation':True})
        self.assertEqual(self.service.snapshot(self.other)['bookings'],[])
        self.assertEqual(self.service.snapshot(self.other)['notifications'],[])
        snapshot=self.service.snapshot(self.patient)
        self.assertNotIn('audit',snapshot)
        self.assertNotIn('inventory',snapshot)
        for key in ('priority_reasons','priority_rank','urgency','continuity','override_rank','patient_name'):
            self.assertNotIn(key,snapshot['bookings'][0])
        self.assertNotIn('urgency',snapshot['prescriptions'][0])

    def test_notification_failure_does_not_undo_reservation_and_can_retry(self):
        class FailingProvider:
            def deliver(self,event):raise TimeoutError('provider unavailable')
        self.stock(1)
        _,request_id=self.prescription()
        booking_id=self.book(request_id)
        notifications=NotificationService(self.database,FailingProvider())
        notifications.dispatch()
        self.assertEqual(self.booking(booking_id)['status'],'RESERVED')
        failed=self.query("SELECT * FROM notifications WHERE status='FAILED'")
        self.assertTrue(failed)
        for event in failed:
            self.assertNotIn('Medicine A',event['message'])
            self.assertTrue(event['attempted_at'])
        notifications.retry(self.manager,failed[0]['id'])
        NotificationService(self.database).dispatch()
        self.assertEqual(self.query('SELECT status FROM notifications WHERE id=?',(failed[0]['id'],))[0]['status'],'SENT')

    def test_failed_transaction_leaves_no_orphan_reservation(self):
        self.stock(1)
        _,request_id=self.prescription()
        original=self.service.notify
        def fail_reserved(db,booking,template):
            if template=='RESERVED':raise RuntimeError('simulated transaction failure')
            return original(db,booking,template)
        self.service.notify=fail_reserved
        with self.assertRaises(RuntimeError):self.book(request_id)
        self.assertEqual(self.query('SELECT reserved FROM inventory')[0]['reserved'],0)
        self.assertEqual(self.query('SELECT * FROM bookings'),[])
        self.assertEqual(self.query('SELECT * FROM reservation_items'),[])
        self.service.notify=original
        retried=self.book(request_id)
        self.assertEqual(self.booking(retried)['status'],'RESERVED')

    def test_lifecycle_notifications_are_scoped_and_timestamped(self):
        _,request_id=self.prescription(quantity=2)
        booking_id=self.book(request_id,2)
        self.stock(2)
        self.service.update_booking(self.pharmacist,booking_id,'ready',{})
        self.service.update_booking(self.pharmacist,booking_id,'fulfil',{})
        events=self.query('SELECT * FROM notifications WHERE booking_id=? ORDER BY created_at',(booking_id,))
        self.assertEqual([event['template'] for event in events],['SUBMITTED','WAITLISTED','RESERVED','READY','FULFILLED'])
        for event in events:
            self.assertEqual(event['patient_id'],self.patient['id'])
            self.assertEqual(event['channel'],'IN_APP')
            self.assertTrue(event['created_at'])
            self.assertNotIn('Medicine A',event['message'])

    def test_notification_templates_are_configurable(self):
        self.service=BookingService(self.database,{'WAITLISTED':'Your request is queued. Sign in for updates.'})
        _,request_id=self.prescription()
        self.book(request_id)
        event=self.query("SELECT message FROM notifications WHERE template='WAITLISTED'")[0]
        self.assertEqual(event['message'],'Your request is queued. Sign in for updates.')
        with self.assertRaises(ValueError):BookingService(self.database,{'UNSUPPORTED':'Message'})

    def test_state_survives_reopening_database(self):
        self.stock(2)
        _,request_id=self.prescription()
        booking_id=self.book(request_id)
        reopened=BookingService(Database(self.database.path))
        self.assertEqual(reopened.snapshot(self.patient)['bookings'][0]['id'],booking_id)
        reopened.reconcile()
        self.assertEqual(len(self.query('SELECT * FROM reservation_items')),1)

    def test_password_hashes_sessions_and_expiry(self):
        auth=AuthService(self.database)
        with self.assertRaises(AppError):auth.login(self.patient['email'],'wrong-password')
        token,csrf=auth.login(self.patient['email'],'TestPassword123')
        self.assertEqual(auth.get_session(token)['id'],self.patient['id'])
        self.assertEqual(auth.get_session(token)['csrf'],csrf)
        self.assertNotIn(token,str(self.query('SELECT * FROM sessions')))
        with self.database.transaction() as db:
            db.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
        with self.assertRaises(AppError):auth.get_session(token)
        auth.register('New Patient','new@example.test','NewPassword123')
        row=self.query("SELECT role,password FROM users WHERE email='new@example.test'")[0]
        self.assertEqual(row['role'],'PATIENT')
        self.assertNotEqual(row['password'],'NewPassword123')


if __name__=='__main__':unittest.main()
