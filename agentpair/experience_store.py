"""Tenant-scoped reusable methods, separate from private execution evidence.

Callers must validate execution evidence before recording success. The first
integration is endpoint modules; other executors can use the same schema.
"""
import hashlib
import json
import time


class ExperienceStore:
    def __init__(self, connect):
        self.connect = connect
        with connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS executable_experiences(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, method TEXT NOT NULL,
                environment TEXT NOT NULL, successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
                last_task TEXT, updated REAL NOT NULL)''')

    @staticmethod
    def descriptor(module, snapshot):
        # No tokens, PID, paths, captured data or generated conclusion in memory.
        method = {k: module[k] for k in ('id','version','runtime','sha256','permissions')}
        environment = {k: snapshot.get(k, 'unknown') for k in ('os','osVersion','architecture','hostRuntimeVersion')}
        return method, environment

    @staticmethod
    def key(owner, method, environment):
        value = json.dumps([owner, method, environment], sort_keys=True)
        return hashlib.sha256(value.encode()).hexdigest()

    def match(self, owner, module, snapshot):
        method, environment = self.descriptor(module, snapshot)
        # Unknown environment must not be treated as a compatibility proof.
        if any(v == 'unknown' for v in environment.values()): return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM executable_experiences WHERE id=? AND owner=?',
                             (self.key(owner, method, environment), owner)).fetchone()
        if not row or row['status'] == 'needs_revalidation': return None
        return {'id': row['id'], 'status': row['status'], 'successfulRuns': row['successes'],
                'reason': 'Exact module digest, runtime and reported OS/architecture match; fresh evidence required.'}

    def record(self, db, owner, module, snapshot, task_id, succeeded):
        method, environment = self.descriptor(module, snapshot)
        key = self.key(owner, method, environment)
        row = db.execute('SELECT * FROM executable_experiences WHERE id=?', (key,)).fetchone()
        successes = (row['successes'] if row else 0) + int(succeeded)
        failures = (row['failures'] if row else 0) + int(not succeeded)
        status = ('repeated_execution' if successes >= 2 else 'single_execution') if succeeded else 'needs_revalidation'
        db.execute('INSERT OR REPLACE INTO executable_experiences VALUES(?,?,?,?,?,?,?,?,?)',
                   (key,owner,json.dumps(method),json.dumps(environment),successes,failures,status,task_id,time.time()))

    def list(self, owner):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM executable_experiences WHERE owner=? ORDER BY updated DESC LIMIT 100', (owner,)).fetchall()
        return [{'id':r['id'],'method':json.loads(r['method']),'environment':json.loads(r['environment']),
                 'successfulRuns':r['successes'],'failedRuns':r['failures'],'status':r['status'],
                 'lastTaskId':r['last_task'],'updatedAt':r['updated'],
                 'validationLevel':'endpoint_execution_not_application_analysis'} for r in rows]
