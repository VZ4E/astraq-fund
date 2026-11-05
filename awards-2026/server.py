"""Isolated awards draft server. No production voting without explicit configuration."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
class Rejected(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS voters(member_id TEXT PRIMARY KEY, name TEXT NOT NULL,
              token_hash TEXT UNIQUE NOT NULL, used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ballots(receipt TEXT PRIMARY KEY, choices TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, event TEXT NOT NULL,
              previous TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS limits(key TEXT PRIMARY KEY, count INTEGER NOT NULL);
            ''')
        os.chmod(path, 0o600)
    def db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level='DEFERRED')
        db.row_factory = sqlite3.Row
        return db
    def issue(self, member_id, name):
        if not member_id.strip() or not 1 <= len(name.strip()) <= 80:
            raise ValueError('Member ID and a display name of 1–80 characters required')
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('INSERT INTO voters(member_id,name,token_hash) VALUES(?,?,?)',
                       (member_id, name.strip(), digest(token)))
        return token
    def rate(self, key, maximum=20):
        # Shared across processes, persisted across restarts; no raw IP stored.
        bucket = digest(key) + ':' + str(int(time.time()) // 900)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM limits WHERE CAST(substr(key,66) AS INTEGER) < ?', (int(time.time()) // 900 - 1,))
            db.execute('INSERT INTO limits VALUES(?,1) ON CONFLICT(key) DO UPDATE SET count=count+1', (bucket,))
            count = db.execute('SELECT count FROM limits WHERE key=?', (bucket,)).fetchone()[0]
        if count > maximum:
            raise Rejected('Too many attempts. Try again in 15 minutes.', 429)
    def cast(self, body, election, demo=False):
        if not election.get('open'):
            raise Rejected('Voting is closed.', 403)
        if not demo and not election.get('opens_at', float('inf')) <= time.time() < election.get('closes_at', 0):
            raise Rejected('Voting is outside the published window.', 403)
        if not isinstance(body, dict) or set(body) != {'token','choices','consent'}:
            raise Rejected('Invalid ballot fields.')
        if body['consent'] is not True:
            raise Rejected('Agree to publication of your approved display name.')
        token, choices = body['token'], body['choices']
        if not isinstance(token, str) or not 20 <= len(token) <= 200 or not isinstance(choices, dict):
            raise Rejected('Invalid ballot.')
        categories = election.get('categories', [])
        if not categories or set(choices) != {c['id'] for c in categories}:
            raise Rejected('Complete each category, or choose abstain.')
        for category in categories:
            value = choices[category['id']]
            if not isinstance(value, str) or value not in ['abstain'] + [n['id'] for n in category['nominees']]:
                raise Rejected('Unknown nominee.')
        receipt = secrets.token_urlsafe(24)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            voter = db.execute('SELECT member_id,used FROM voters WHERE token_hash=?', (digest(token),)).fetchone()
            if not voter:
                raise Rejected('Access code is invalid.', 401)
            if voter['used']:
                raise Rejected('A ballot has already been accepted for this member.', 409)
            # Identity table has participation only. No receipt or choices are linked to it.
            db.execute('UPDATE voters SET used=1 WHERE member_id=?', (voter['member_id'],))
            db.execute('INSERT INTO ballots VALUES(?,?)', (receipt, json.dumps(choices, sort_keys=True)))
            previous = db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
            previous = previous[0] if previous else '0' * 64
            event = json.dumps({'event':'ballot_accepted','ballot_digest':digest(receipt + json.dumps(choices, sort_keys=True))}, sort_keys=True)
            db.execute('INSERT INTO audit(event,previous,hash) VALUES(?,?,?)', (event, previous, digest(previous + event)))
        return receipt
    def registry(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT name AS display_name FROM voters WHERE used=1 ORDER BY name COLLATE NOCASE')]

class App(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log tokens, ballot contents, or public receipt queries.
    def send(self, status, payload, kind='application/json'):
        data = json.dumps(payload).encode() if kind == 'application/json' else payload
        self.send_response(status)
        for key, value in {'Content-Type':kind, 'Content-Length':str(len(data)),
            'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/status':
            config = dict(self.server.election)
            config['demo'] = self.server.demo
            if not self.server.demo:
                config['open'] = bool(config.get('open') and config.get('opens_at', float('inf')) <= time.time() < config.get('closes_at', 0))
            return self.send(200, config)
        if path == '/api/registry':
            return self.send(200, {'voters': self.server.store.registry(), 'demo':self.server.demo})
        files = {'/':'index.html','/index.html':'index.html','/voting.js':'voting.js','/voting.css':'voting.css'}
        if path not in files:
            return self.send(404, {'error':'Not found'})
        name = files[path]
        kinds = {'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}
        self.send(200, (ROOT/name).read_bytes(), kinds[name.split('.')[-1]])
    def do_POST(self):
        try:
            path = urlsplit(self.path).path
            if path not in ('/api/ballots','/api/forms'):
                raise Rejected('Not found',404)
            self.server.store.rate(self.client_address[0])
            if self.headers.get('Transfer-Encoding'):
                raise Rejected('Unsupported transfer encoding')
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 16384:
                raise Rejected('Request too large or empty',413)
            if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                raise Rejected('JSON required',415)
            raw = self.rfile.read(size)
            if path == '/api/forms':
                secret = self.server.forms_secret
                if not secret:
                    raise Rejected('Google Forms integration is not configured.',503)
                stamp = self.headers.get('X-Awards-Timestamp','')
                if abs(time.time() - int(stamp)) > 300:
                    raise Rejected('Expired request',401)
                signature = hmac.new(secret.encode(), stamp.encode()+b'.'+raw, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, self.headers.get('X-Awards-Signature','')):
                    raise Rejected('Invalid signature',401)
            else:
                expected = self.server.origin
                origin = self.headers.get('Origin','')
                if self.server.demo and urlsplit(origin).scheme in ('http','https') and urlsplit(origin).netloc == self.headers.get('Host'):
                    expected = origin
                if not expected or origin != expected:
                    raise Rejected('Unapproved origin.',403)
            body = json.loads(raw)
            receipt = self.server.store.cast(body, self.server.election, self.server.demo)
            self.send(201, {'receipt':receipt,'demo':self.server.demo})
        except Rejected as exc:
            self.send(exc.status, {'error':exc.message})
        except (ValueError, TypeError, UnicodeError):
            self.send(400, {'error':'Malformed request.'})
        except Exception:
            self.send(500, {'error':'Unable to record ballot. Please retry; duplicate protection remains active.'})

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--port',type=int,default=4173)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--strictPort',action='store_true')
    parser.add_argument('--issue',nargs=2,metavar=('MEMBER_ID','DISPLAY_NAME'))
    args = parser.parse_args()
    os.umask(0o077)
    db_path = os.environ.get('AWARDS_DB',str(ROOT/'.runtime'/'votes.sqlite3'))
    if args.demo:
        db_path = str(ROOT/'.runtime'/'demo.sqlite3')
    store = Store(db_path)
    if args.issue:
        if args.demo:
            parser.error('Do not issue real voter codes in demo mode')
        print(store.issue(*args.issue))
        return
    election = json.loads((ROOT/'election.json').read_text())
    if args.demo:
        election = {'open':True,'categories':[
            {'id':'demo-moment','name':'Demo: Best Moment','nominees':[{'id':'a','name':'Demo moment A'},{'id':'b','name':'Demo moment B'}]},
            {'id':'demo-duo','name':'Demo: Best Duo','nominees':[{'id':'a','name':'Demo duo A'},{'id':'b','name':'Demo duo B'}]}]}
        with store.db() as db:
            db.execute('INSERT OR IGNORE INTO voters(member_id,name,token_hash) VALUES(?,?,?)', ('demo-member','Demo voter',digest('DEMO-2026-PREVIEW-CODE')))
    server = ThreadingHTTPServer((args.host,args.port),App)
    server.store, server.election, server.demo = store,election,args.demo
    server.forms_secret = '' if args.demo else os.environ.get('AWARDS_FORMS_SECRET','')
    server.origin = os.environ.get('AWARDS_ORIGIN','')
    print(f'Awards server ready on port {args.port}; demo={args.demo}',flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
