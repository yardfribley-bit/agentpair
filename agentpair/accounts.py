"""Local self-service accounts. Password hashes only; expiring per-login sessions."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import closing


SESSION_TTL = 7 * 86400


class Accounts:
    def __init__(self, path, admin):
        self.path, self.admin = str(path), admin.casefold()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, data TEXT NOT NULL, expires REAL NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires)')
            db.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT UNIQUE, salt BLOB, hash BLOB)')
            db.execute('CREATE TABLE IF NOT EXISTS wallets (owner TEXT PRIMARY KEY, remaining INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS charges (id TEXT PRIMARY KEY, owner TEXT, reserved INTEGER)')
            db.execute('INSERT OR IGNORE INTO wallets SELECT id,2000000 FROM users')

    def register(self, name, password):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,40}', name):
            raise ValueError('账号使用 3–40 位英文字母、数字、下划线或短横线')
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise ValueError('密码须为 12–128 个字符')
        name = name.casefold()
        if name == self.admin:
            raise ValueError('账号不可用')
        salt, uid = secrets.token_bytes(16), secrets.token_hex(16)
        hashed = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('INSERT INTO users VALUES (?,?,?,?)', (uid, name, salt, hashed))
                db.execute('INSERT INTO wallets VALUES (?,2000000)', (uid,))
        except sqlite3.IntegrityError:
            raise ValueError('账号不可用') from None
        return {'id': uid, 'name': name, 'role': 'user'}

    def login(self, name, password):
        if not isinstance(name, str) or not isinstance(password, str) or len(password) > 128:
            return None
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT id,name,salt,hash FROM users WHERE name=?', (name.casefold(),)).fetchone()
        hashed = hashlib.scrypt(password.encode(), salt=row[2] if row else b'0'*16, n=16384, r=8, p=1)
        if row and hmac.compare_digest(hashed, row[3]):
            return {'id': row[0], 'name': row[1], 'role': 'user'}

    @staticmethod
    def session_key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, user):
        import json
        token = secrets.token_urlsafe(32)
        record = {**user, 'csrf': secrets.token_urlsafe(32), 'expires': time.time()+SESSION_TTL}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)',
                       (self.session_key(token), json.dumps(record), record['expires']))
        return token, record

    def get(self, token):
        import json
        if not isinstance(token,str) or not 32<=len(token)<=128:return None
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT data FROM sessions WHERE token_hash=? AND expires>?',
                           (self.session_key(token),time.time())).fetchone()
        return json.loads(row[0]) if row else None

    def logout(self, token):
        if not isinstance(token,str) or not token:return
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM sessions WHERE token_hash=?',(self.session_key(token),))

    def balance(self, owner):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT remaining FROM wallets WHERE owner=?',(owner,)).fetchone()
        return {'grantedCNY':2,'remainingCNY':row[0]/1000000 if row else 0,
                'accounting':'estimated_reservation', 'note':'按调用预估预扣，非供应商实际账单'}

    def reserve(self, owner, amount):
        import math
        if not isinstance(amount,(int,float)) or not math.isfinite(amount) or not 0<amount<=.30:
            raise ValueError('Invalid model reservation')
        units=math.ceil(amount*1000000)
        with closing(sqlite3.connect(self.path)) as db, db:
            changed=db.execute('UPDATE wallets SET remaining=remaining-? WHERE owner=? AND remaining>=?',(units,owner,units))
            if not changed.rowcount:raise ValueError('模型额度不足，无法继续调用；注册赠送额度为 ¥2')
            db.execute('INSERT INTO charges VALUES (?,?,?)',(secrets.token_hex(16),owner,units))
