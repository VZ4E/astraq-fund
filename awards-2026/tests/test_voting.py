import concurrent.futures
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from server import App, Store, Rejected, ThreadingHTTPServer

class VotingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'votes.db')
        self.token = self.store.issue('member-1','Approved name')
        self.config = {'open':True,'opens_at':time.time()-10,'closes_at':time.time()+100,
          'categories':[{'id':'moment','nominees':[{'id':'a','name':'A'}]}]}
        self.body = {'token':self.token,'choices':{'moment':'a'},'consent':True}
    def tearDown(self):
        self.tmp.cleanup()
    def test_accept_and_registry_privacy(self):
        receipt = self.store.cast(self.body,self.config)
        self.assertTrue(receipt)
        self.assertEqual(self.store.registry(),[{'display_name':'Approved name'}])
        with self.store.db() as db:
            ballot = dict(db.execute('SELECT * FROM ballots').fetchone())
            self.assertEqual(set(ballot),{'receipt','choices'})
            self.assertNotIn(self.token,json.dumps([dict(r) for r in db.execute('SELECT * FROM voters')]))
    def test_duplicate_and_concurrency(self):
        def attempt(_):
            try: self.store.cast(self.body,self.config); return 'accepted'
            except Rejected as e: return e.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt,range(8)))
        self.assertEqual(results.count('accepted'),1)
        self.assertEqual(results.count(409),7)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ballots').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0],1)
    def test_invalid_ballots_do_not_consume_code(self):
        for changes in [{'token':'x'*32},{'consent':False},{'choices':{}},{'choices':{'moment':'invented'}},{'choices':{'moment':['a']}},{'extra':'bad'}]:
            with self.subTest(changes=changes), self.assertRaises(Rejected):
                self.store.cast({**self.body,**changes},self.config)
        self.assertEqual(self.store.registry(),[])
        self.store.cast(self.body,self.config)
    def test_closed_and_expired(self):
        for changes in [{'open':False},{'opens_at':time.time()+100},{'closes_at':time.time()-1}]:
            with self.subTest(changes=changes),self.assertRaises(Rejected):
                self.store.cast(self.body,{**self.config,**changes})
    def test_abstain(self):
        self.store.cast({**self.body,'choices':{'moment':'abstain'}},self.config)
    def test_unique_member(self):
        with self.assertRaises(Exception): self.store.issue('member-1','Other name')
    def test_rate_limit(self):
        for _ in range(20): self.store.rate('address')
        with self.assertRaises(Rejected) as result: self.store.rate('address')
        self.assertEqual(result.exception.status,429)
    def test_transaction_rollback(self):
        with self.store.db() as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(Exception): self.store.cast(self.body,self.config)
        self.assertEqual(self.store.registry(),[])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ballots').fetchone()[0],0)
    def test_http_security_and_signed_forms(self):
        server = ThreadingHTTPServer(('127.0.0.1',0),App)
        server.store,server.election,server.demo = self.store,self.config,False
        server.origin = 'https://awards.example'
        server.forms_secret = 'test-only-secret'
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,body=None,headers=None):
            connection = http.client.HTTPConnection(*server.server_address)
            connection.request('POST' if body else 'GET',path,body,headers or {})
            response = connection.getresponse();result=(response.status,response.read(),dict(response.getheaders()));connection.close();return result
        try:
            raw=json.dumps(self.body)
            self.assertEqual(request('/api/ballots',raw,{'Content-Type':'application/json','Origin':'https://evil.example'})[0],403)
            self.assertEqual(request('/server.py')[0],404)
            self.assertEqual(request('/.runtime/votes.sqlite3')[0],404)
            self.assertEqual(request('/api/forms',raw,{'Content-Type':'application/json','X-Awards-Timestamp':str(int(time.time())),'X-Awards-Signature':'bad'})[0],401)
            stamp=str(int(time.time()))
            signature=hmac.new(server.forms_secret.encode(),(stamp+'.'+raw).encode(),hashlib.sha256).hexdigest()
            headers={'Content-Type':'application/json','X-Awards-Timestamp':stamp,'X-Awards-Signature':signature}
            self.assertEqual(request('/api/forms',raw,headers)[0],201)
            self.assertEqual(request('/api/ballots',raw,{'Content-Type':'application/json','Origin':server.origin})[0],409)
            status,payload,headers=request('/api/registry')
            self.assertEqual(json.loads(payload)['voters'],[{'display_name':'Approved name'}])
            self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
            self.assertEqual(headers['Cache-Control'],'no-store')
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
