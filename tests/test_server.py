"""HTTP tests for authentication, CSRF, role boundaries and safe responses."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from medicine_booking.server import create_server
from medicine_booking.seed import DEMO_PASSWORD


class HttpTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()
        cls.server=create_server(Path(cls.directory.name)/'api.db',port=0,demo=True)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def call(self,path,method='GET',payload=None,cookie='',csrf='',extra=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        headers={'Cookie':cookie,'X-CSRF-Token':csrf}
        if payload is not None:
            headers.update({'Content-Type':'application/json','X-Requested-With':'MediNest'})
        headers.update(extra or {})
        connection.request(method,path,body=json.dumps(payload) if payload is not None else None,headers=headers)
        response=connection.getresponse()
        body=response.read()
        result=(response.status,dict(response.getheaders()),body)
        connection.close()
        return result

    def login(self,email='patient@medinest.local'):
        status,headers,body=self.call('/api/auth/login','POST',{'email':email,'password':DEMO_PASSWORD})
        self.assertEqual(status,200)
        return headers['Set-Cookie'].split(';')[0],json.loads(body)['csrf']


class ServerTests(HttpTestCase):
    def test_static_application_and_security_headers(self):
        for path in ('/','/app.js','/styles.css','/favicon.svg'):
            status,headers,body=self.call(path)
            self.assertEqual(status,200)
            self.assertTrue(body)
            self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
            self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])

    def test_authentication_csrf_and_logout(self):
        self.assertEqual(self.call('/api/dashboard')[0],401)
        cookie,csrf=self.login()
        self.assertEqual(self.call('/api/dashboard',cookie=cookie)[0],200)
        self.assertEqual(self.call('/api/auth/logout','POST',{},cookie=cookie)[0],403)
        self.assertEqual(self.call('/api/auth/logout','POST',{},cookie=cookie,csrf=csrf)[0],200)
        self.assertEqual(self.call('/api/dashboard',cookie=cookie)[0],401)

    def test_cross_origin_and_invalid_host_requests_are_blocked(self):
        self.assertEqual(self.call('/api/config',extra={'Host':'attacker.example'})[0],403)
        self.assertEqual(self.call('/api/auth/login','POST',{'email':'patient@medinest.local','password':DEMO_PASSWORD},extra={'Origin':'https://attacker.example'})[0],403)

    def test_patient_cannot_call_staff_endpoints_or_access_other_records(self):
        cookie,csrf=self.login()
        snapshot=json.loads(self.call('/api/dashboard',cookie=cookie)[2])
        self.assertNotIn('audit',snapshot)
        self.assertEqual(self.call('/api/settings','POST',{'reservation_minutes':1,'partial_allocation':True},cookie,csrf)[0],403)
        staff_cookie,_=self.login('pharmacist@medinest.local')
        staff=json.loads(self.call('/api/dashboard',cookie=staff_cookie)[2])
        other=next(p for p in staff['prescriptions'] if p['patient_id']!=snapshot['user']['id'])
        self.assertEqual(self.call(f"/api/prescriptions/{other['id']}/document",cookie=cookie)[0],404)

    def test_http_booking_workflow(self):
        cookie,csrf=self.login('meera@medinest.local')
        patient=json.loads(self.call('/api/dashboard',cookie=cookie)[2])
        status,_,body=self.call('/api/prescriptions','POST',{'authored_on':'2026-01-01','expires_on':'2099-01-01',
            'prescriber':'HTTP-TEST','medicines':[{'medicine_code':'MED-006','quantity':3}]},cookie,csrf)
        self.assertEqual(status,200)
        prescription_id=json.loads(body)['id']
        staff_cookie,staff_csrf=self.login('pharmacist@medinest.local')
        self.assertEqual(self.call(f'/api/prescriptions/{prescription_id}/review','POST',{'status':'VALID','urgency':'NORMAL',
            'continuity':'UNKNOWN','reason':'HTTP test pharmacist review'},staff_cookie,staff_csrf)[0],200)
        data=json.loads(self.call('/api/dashboard',cookie=cookie)[2])
        prescription=next(p for p in data['prescriptions'] if p['id']==prescription_id)
        status,_,body=self.call('/api/bookings','POST',{'request_id':prescription['medicines'][0]['id'],'quantity':3},cookie,csrf)
        self.assertEqual(status,200)
        booking_id=json.loads(body)['id']
        for action in ('ready','fulfil'):
            self.assertEqual(self.call(f'/api/bookings/{booking_id}/{action}','POST',{},staff_cookie,staff_csrf)[0],200)
        data=json.loads(self.call('/api/dashboard',cookie=cookie)[2])
        self.assertEqual(next(b for b in data['bookings'] if b['id']==booking_id)['status'],'FULFILLED')

    def test_malformed_requests_return_safe_errors(self):
        cookie,csrf=self.login()
        status,_,body=self.call('/api/bookings','POST',{'quantity':True,'request_id':[]},cookie,csrf)
        self.assertEqual(status,400)
        self.assertNotIn('Traceback',body.decode())
        self.assertEqual(self.call('/api/config',method='POST',payload={},extra={'X-Requested-With':''})[0],403)


if __name__=='__main__':unittest.main()
