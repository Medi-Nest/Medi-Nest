"""Synthetic demonstration data, loaded only when explicitly requested."""
from datetime import date, timedelta
from .access import hash_password
from .domain import new_id, utc_now

DEMO_ACCOUNTS = [
    {'name':'Aarav Sharma','email':'patient@medinest.local','role':'PATIENT'},
    {'name':'Meera Rao','email':'meera@medinest.local','role':'PATIENT'},
    {'name':'Dr. Kavya Patel','email':'pharmacist@medinest.local','role':'PHARMACIST'},
    {'name':'Rohan Mehta','email':'manager@medinest.local','role':'MANAGER'},
]
DEMO_PASSWORD = 'MediNest@2026'


def seed_demo(database, service):
    with database.transaction() as db:
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return
        users=[]
        for account in DEMO_ACCOUNTS:
            user={**account,'id':new_id()}
            users.append(user)
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                       (user['id'],user['name'],user['email'],user['role'],hash_password(DEMO_PASSWORD),utc_now()))
        catalog=[('MED-001','Amoxicillin','500 mg','STANDARD'),('MED-002','Metformin','500 mg','STANDARD'),
                 ('MED-003','Atorvastatin','10 mg','STANDARD'),('MED-004','Salbutamol','100 mcg / dose','STANDARD'),
                 ('MED-005','Clonazepam','0.5 mg','RESTRICTED'),('MED-006','Levothyroxine','50 mcg','STANDARD')]
        db.executemany('INSERT INTO medicines VALUES (?,?,?,?)',catalog)
    authored=(date.today()-timedelta(days=3)).isoformat()
    expiry=(date.today()+timedelta(days=90)).isoformat()
    pharmacist=users[2]
    for code,quantity in [('MED-001',48),('MED-002',30),('MED-003',0),('MED-004',8),('MED-005',10),('MED-006',60)]:
        if quantity:
            service.restock(pharmacist,{'event_id':new_id(),'medicine_code':code,'quantity':quantity,'batch':'DEMO-'+code,
                                       'expires_on':(date.today()+timedelta(days=365)).isoformat()})
    for patient,code,quantity,approved,urgency,continuity in [
        (users[0],'MED-002',20,True,'NORMAL','CONTINUITY'),
        (users[0],'MED-003',10,True,'NORMAL','UNKNOWN'),
        (users[1],'MED-003',10,True,'HIGH','CONTINUITY'),
        (users[0],'MED-004',1,False,'NORMAL','UNKNOWN'),
        (users[1],'MED-001',12,True,'NORMAL','NO_CONTINUITY')]:
        prescription_id=service.submit_prescription(patient,{'authored_on':authored,'expires_on':expiry,
            'prescriber':'DEMO-PRESCRIBER-01','source_identifier':'DEMO-'+new_id(),
            'medicines':[{'medicine_code':code,'quantity':quantity}]})
        if approved:
            service.review_prescription(pharmacist,prescription_id,{'status':'VALID','reason':'Prescription checked against the supplied record.',
                'urgency':urgency,'continuity':continuity})
            with database.transaction() as db:
                request_id=db.execute('SELECT id FROM medication_requests WHERE prescription_id=?',(prescription_id,)).fetchone()[0]
            service.create_booking(patient,{'request_id':request_id,'quantity':quantity})
