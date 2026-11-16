"""Prototype timing checks against the SRS response-time targets."""
import json
import time
from test_server import HttpTestCase


class PerformanceTests(HttpTestCase):
    def test_response_time_targets(self):
        cookie,csrf=self.login()
        durations=[]
        for _ in range(30):
            started=time.perf_counter()
            status,_,body=self.call('/api/dashboard',cookie=cookie)
            durations.append(time.perf_counter()-started)
            self.assertEqual(status,200)
        read_p95=sorted(durations)[int(len(durations)*.95)-1]
        self.assertLess(read_p95,1.0)
        staff_cookie,staff_csrf=self.login('pharmacist@medinest.local')
        submission_times=[]
        for i in range(20):
            status,_,body=self.call('/api/prescriptions','POST',{'authored_on':'2026-01-01','expires_on':'2099-01-01',
                'prescriber':'PERFORMANCE-TEST','medicines':[{'medicine_code':'MED-003','quantity':1}]},cookie,csrf)
            self.assertEqual(status,200)
            prescription_id=json.loads(body)['id']
            status,_,_=self.call(f'/api/prescriptions/{prescription_id}/review','POST',
                {'status':'VALID','reason':'Performance fixture review','urgency':'NORMAL','continuity':'UNKNOWN'},staff_cookie,staff_csrf)
            self.assertEqual(status,200)
            data=json.loads(self.call('/api/dashboard',cookie=cookie)[2])
            request_id=next(p for p in data['prescriptions'] if p['id']==prescription_id)['medicines'][0]['id']
            started=time.perf_counter()
            status,_,_=self.call('/api/bookings','POST',{'request_id':request_id,'quantity':1},cookie,csrf)
            submission_times.append(time.perf_counter()-started)
            self.assertEqual(status,200)
        submit_p95=sorted(submission_times)[int(len(submission_times)*.95)-1]
        self.assertLess(submit_p95,2.0)
        started=time.perf_counter()
        status,_,_=self.call('/api/inventory/restock','POST',{'event_id':'PERF-RESTOCK','medicine_code':'MED-003',
            'quantity':50,'batch':'PERF-BATCH','expires_on':'2099-01-01'},staff_cookie,staff_csrf)
        restock_seconds=time.perf_counter()-started
        self.assertEqual(status,200)
        self.assertLess(restock_seconds,3.0)
        print(f'\nPrototype timings: status p95={read_p95*1000:.1f} ms; booking p95={submit_p95*1000:.1f} ms; restock={restock_seconds*1000:.1f} ms.')
