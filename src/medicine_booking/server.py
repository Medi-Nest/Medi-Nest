"""Loopback-only HTTP application, JSON API and static assets."""
import argparse
import hmac
import json
import logging
import mimetypes
import os
import re
import socket
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .access import AuthService
from .booking import BookingService
from .domain import AppError, validate_fields
from .notifications import NotificationService
from .seed import DEMO_ACCOUNTS, DEMO_PASSWORD, seed_demo
from .storage import Database

ROOT=Path(__file__).resolve().parents[2]
STATIC=Path(__file__).parent/'static'


class Application:
    def __init__(self,database_path,demo=False):
        self.database=Database(database_path)
        self.auth=AuthService(self.database)
        templates=json.loads(os.environ.get('MEDINEST_NOTIFICATION_TEMPLATES','{}'))
        self.bookings=BookingService(self.database,notification_templates=templates)
        self.notifications=NotificationService(self.database)
        self.demo=demo
        self.login_attempts={}
        self.login_lock=threading.Lock()
        if demo:
            seed_demo(self.database,self.bookings)
        self.bookings.reconcile()
        self.notifications.dispatch()

    def maintenance(self,stop):
        while not stop.wait(10):
            try:
                self.bookings.reconcile()
                self.notifications.dispatch()
            except Exception:
                logging.error('Background maintenance did not complete; it will retry.')


class RequestHandler(BaseHTTPRequestHandler):
    server_version='MediNest'
    sys_version=''

    @property
    def app(self):
        return self.server.app

    def log_message(self,*args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def send_content(self,status,body,content_type='application/json; charset=utf-8',headers=None):
        if not isinstance(body,bytes):
            body=json.dumps(body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key,value in (headers or {}).items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(body)

    def token(self):
        cookie=SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie',''))
            return cookie['medinest_session'].value if 'medinest_session' in cookie else ''
        except Exception:
            return ''

    def validate_host(self):
        hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in hosts:
            raise AppError('Invalid application host.',403)
        origin=self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in hosts}:
            raise AppError('Cross-origin requests are not permitted.',403)

    def do_GET(self):
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')

    def handle_request(self,method):
        try:
            if method=='GET':
                self.validate_host()
            path=urlsplit(self.path).path
            if method=='GET' and path in {'/','/app.js','/styles.css','/favicon.svg'}:
                filename='index.html' if path=='/' else path[1:]
                content_type=mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                self.send_content(200,(STATIC/filename).read_bytes(),content_type+'; charset=utf-8')
                return
            if method=='GET' and path=='/api/config':
                guidance=os.environ.get('MEDINEST_EMERGENCY_GUIDANCE','')
                version=os.environ.get('MEDINEST_EMERGENCY_GUIDANCE_VERSION','')
                self.send_content(200,{'demo':self.app.demo,'accounts':DEMO_ACCOUNTS if self.app.demo else [],
                                      'emergency_guidance':guidance if version else '', 'guidance_version':version})
                return
            payload={}
            if method=='POST':
                try:
                    length=int(self.headers.get('Content-Length','0'))
                except ValueError:
                    raise AppError('Invalid request length.') from None
                if length<=0 or length>7_100_000:
                    raise AppError('The request is empty or exceeds the upload limit.',413)
                raw_body=self.rfile.read(length)
                self.validate_host()
                if self.headers.get('X-Requested-With')!='MediNest' or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise AppError('Send an application/json request from this application.',403)
                try:
                    payload=json.loads(raw_body)
                except (ValueError,UnicodeDecodeError):
                    raise AppError('The request contains invalid JSON.') from None
                if not isinstance(payload,dict):
                    raise AppError('Send a JSON object.')
            if method=='POST' and path in {'/api/auth/login','/api/auth/register'}:
                validate_fields(payload,{'email','password'} if path.endswith('login') else {'name','email','password'},
                                {'email','password'} if path.endswith('login') else {'name','email','password'})
                key=self.client_address[0]
                with self.app.login_lock:
                    attempts=[stamp for stamp in self.app.login_attempts.get(key,[]) if stamp>time.monotonic()-60]
                    if len(attempts)>=20:
                        raise AppError('Too many sign-in attempts. Try again in a minute.',429)
                    attempts.append(time.monotonic())
                    self.app.login_attempts[key]=attempts
                if path.endswith('register'):
                    self.app.auth.register(payload['name'],payload['email'],payload['password'])
                token,csrf=self.app.auth.login(payload['email'],payload['password'])
                self.send_content(200,{'csrf':csrf},headers={'Set-Cookie':f'medinest_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={self.app.auth.session_hours*3600}'})
                return
            actor=self.app.auth.get_session(self.token())
            if method=='POST' and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),actor['csrf']):
                raise AppError('Your session changed. Refresh and try again.',403)
            if method=='GET' and path=='/api/dashboard':
                self.send_content(200,self.app.bookings.snapshot(actor))
                return
            document=re.fullmatch(r'/api/prescriptions/([a-f0-9-]+)/document',path)
            if method=='GET' and document:
                filename,body=self.app.bookings.get_document(actor,document[1])
                content_type=mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                self.send_content(200,body,content_type,{'Content-Disposition':'attachment; filename="prescription.'+filename.rsplit('.',1)[-1].lower()+'"'})
                return
            if method!='POST':
                raise AppError('Page not found.',404)
            result={}
            if path=='/api/auth/logout':
                self.app.auth.logout(self.token())
                self.send_content(200,{},headers={'Set-Cookie':'medinest_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
                return
            if path=='/api/prescriptions':
                result={'id':self.app.bookings.submit_prescription(actor,payload)}
            elif path=='/api/bookings':
                result={'id':self.app.bookings.create_booking(actor,payload)}
            elif path=='/api/inventory/restock':
                result={'id':self.app.bookings.restock(actor,payload)}
            elif path=='/api/settings':
                self.app.bookings.update_settings(actor,payload)
            elif match:=re.fullmatch(r'/api/prescriptions/([a-f0-9-]+)/review',path):
                self.app.bookings.review_prescription(actor,match[1],payload)
            elif match:=re.fullmatch(r'/api/bookings/([a-f0-9-]+)/(cancel|ready|fulfil|override)',path):
                self.app.bookings.update_booking(actor,match[1],match[2],payload)
            elif match:=re.fullmatch(r'/api/inventory/([a-f0-9-]+)/state',path):
                self.app.bookings.update_inventory(actor,match[1],payload)
            elif match:=re.fullmatch(r'/api/notifications/([a-f0-9-]+)/retry',path):
                validate_fields(payload,set())
                self.app.notifications.retry(actor,match[1])
            else:
                raise AppError('Action not found.',404)
            self.send_content(200,result)
        except AppError as error:
            self.send_content(error.status,{'error':str(error)})
        except (TypeError,ValueError,KeyError):
            self.send_content(400,{'error':'Check the request fields and try again.'})
        except (BrokenPipeError,ConnectionResetError):
            pass
        except Exception:
            logging.error('A request could not be completed.')
            self.send_content(500,{'error':'The request could not be completed. Please try again.'})


class ApplicationServer(ThreadingHTTPServer):
    allow_reuse_address=False

    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def create_server(database_path,port=8080,demo=False):
    server=ApplicationServer(('127.0.0.1',port),RequestHandler)
    server.app=Application(database_path,demo)
    return server


def main():
    parser=argparse.ArgumentParser(description='Run MediNest locally.')
    parser.add_argument('--port',type=int,default=8080)
    parser.add_argument('--database',type=Path,default=ROOT/'data'/'medinest.db')
    parser.add_argument('--demo',action='store_true',help='Initialize synthetic accounts and pharmacy data on an empty database.')
    options=parser.parse_args()
    server=create_server(options.database,options.port,options.demo)
    stop=threading.Event()
    threading.Thread(target=server.app.maintenance,args=(stop,),daemon=True).start()
    print(f'MediNest is running at http://127.0.0.1:{server.server_port}',flush=True)
    if options.demo:
        print(f'Demo sign-in: patient@medinest.local / {DEMO_PASSWORD}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
