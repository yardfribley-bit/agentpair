"""Build a replacement index beside the live database, without model calls."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


def _validate_target(runtime,target):
    """Accept only a dedicated directory containing ordinary SQLite files."""
    if target.resolve()==runtime.resolve():
        raise ValueError('Target must be separate from the live runtime')
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError('Target must be an ordinary isolated directory')
    if not target.exists():return
    allowed={'data-center.db'+suffix for suffix in ('','-wal','-shm','-journal')}
    entries=list(target.iterdir())
    for entry in entries:
        if entry.name not in allowed:
            raise ValueError('Unexpected file in isolated index target: '+entry.name)
        if entry.is_symlink():
            raise ValueError('Isolated index files must not be symbolic links: '+entry.name)
        info=entry.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('Isolated index files must be ordinary files without hard links: '+entry.name)
    if entries and not (target/'data-center.db').is_file():
        raise ValueError('Isolated index target has sidecars without a database')


class _ReadOnlyConnections:
    @contextmanager
    def connect(self):
        # mode=ro also fails on a missing source instead of creating a database.
        # Do not use immutable=1: ingestion may still append to the live WAL.
        db=sqlite3.connect(Path(self.path).resolve().as_uri()+'?mode=ro',uri=True,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            with db:yield db
        finally:db.close()


class _ReadOnlySessionStore(_ReadOnlyConnections,SessionStore):pass


class _ReadOnlyDeviceStore(_ReadOnlyConnections,DeviceStore):pass


class IndexSource:
    def __init__(self, source, target):
        self.source,self.path=source,str(target/'source.db')
    def __getattr__(self,name):return getattr(self.source,name)


class _DiskCheckedConnection:
    """Keep an idle-time disk alarm effective for later SQL and commits."""
    def __init__(self, connection, check):
        self.connection,self.check=connection,check

    def __getattr__(self,name):return getattr(self.connection,name)

    def execute(self,*args,**kwargs):
        self.check()
        return self.connection.execute(*args,**kwargs)

    def executemany(self,*args,**kwargs):
        self.check()
        return self.connection.executemany(*args,**kwargs)

    def executescript(self,*args,**kwargs):
        # executescript can commit an existing transaction before running SQL.
        self.check(refresh=True)
        return self.connection.executescript(*args,**kwargs)

    def commit(self):
        self.check(refresh=True)
        return self.connection.commit()


class OfflineDataCenter(DataCenter):
    def __init__(self, collections, min_free_gib=4):
        source=getattr(collections.sessions,'source',collections.sessions)
        self.offline_runtime=Path(source.path).parent
        _validate_target(self.offline_runtime,Path(collections.sessions.path).parent)
        self.offline_reserve=int(min_free_gib*1024**3)
        super().__init__(collections,bulk=True)

    def _schema(self,db,sql):
        super()._schema(db,sql.replace('PRAGMA journal_mode=WAL;',
                                      'PRAGMA journal_mode=DELETE;'))

    @contextmanager
    def _db(self):
        _validate_target(self.offline_runtime,self.path.parent)
        with super()._db() as db:
            folder=self.path.parent
            free=shutil.disk_usage(folder).free
            if free<self.offline_reserve+64*1024**2:
                raise RuntimeError('Insufficient free disk for isolated index build')
            db.execute('PRAGMA cache_size=-262144')
            page=db.execute('PRAGMA page_size').fetchone()[0]
            count=db.execute('PRAGMA page_count').fetchone()[0]
            db.execute('PRAGMA max_page_count='+str(count+(free-self.offline_reserve)//page))
            stop=threading.Event()
            low_disk=threading.Event()
            def check(refresh=False):
                if refresh and shutil.disk_usage(folder).free<self.offline_reserve+64*1024**2:
                    low_disk.set()
                if low_disk.is_set():
                    raise RuntimeError('Insufficient free disk for isolated index build')
            # interrupt() alone is a no-op while Python is projecting records or
            # reading a source connection. Latch the alarm so later statements
            # and commits cannot resume writes after that idle-time interrupt.
            db.set_progress_handler(lambda:int(low_disk.is_set()),10000)
            def guard():
                while not stop.wait(.5):
                    if shutil.disk_usage(folder).free<self.offline_reserve+64*1024**2:
                        low_disk.set()
                    if low_disk.is_set():
                        db.interrupt()
            thread=threading.Thread(target=guard,daemon=True);thread.start()
            checked=_DiskCheckedConnection(db,check)
            try:
                yield checked
                checked.commit()
            finally:
                stop.set();thread.join(timeout=2)
                db.set_progress_handler(None,0)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--min-free-gib',type=float,default=4)
    parser.add_argument('--skip-benchmarks',action='store_true')
    args=parser.parse_args()
    try:_validate_target(args.runtime,args.target)
    except ValueError as error:parser.error(str(error))
    if args.min_free_gib<1:parser.error('At least 1GiB free disk reserve is required')
    args.target.mkdir(mode=0o700,parents=True,exist_ok=True)
    session_path=args.runtime/'sessionlens.db'
    if not session_path.exists():session_path=args.runtime/'session-lens.db'
    # Read existing sources without running unrelated startup backfills.
    sessions=_ReadOnlySessionStore.__new__(_ReadOnlySessionStore);sessions.path=session_path
    devices=_ReadOnlyDeviceStore.__new__(_ReadOnlyDeviceStore);devices.path=str(args.runtime/'devices.db')
    center=OfflineDataCenter(CollectionView(devices,IndexSource(sessions,args.target)),args.min_free_gib)
    # This is an isolated replacement, so bulk load before building secondary
    # record indexes. Keep physical identity and shared-text lookups indexed.
    with center._db() as db:
        text_triggers=[r['sql'] for r in db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name IN ('dc_texts_insert','dc_texts_delete')")]
        center._set_state(db,'bulk_fts_deferred','1')
        db.execute('DROP TRIGGER IF EXISTS dc_texts_insert')
        db.execute('DROP TRIGGER IF EXISTS dc_texts_delete')
        indexes=[(r['name'],r['sql']) for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND tbl_name IN ('dc_records','dc_inventory') AND name!='dc_records_physical'")]
        for name,sql in indexes:db.execute('DROP INDEX '+name)
    center._refresh_capabilities=lambda *a,**k:0
    center._refresh_request_evidence=lambda *a,**k:0
    start=time.monotonic()
    with sessions.connect() as db:maximum=db.execute('SELECT coalesce(max(rowid),0) FROM session_events').fetchone()[0]
    # Retain the pager/text cache across batches; commit cursors atomically.
    with center._db() as db:
        for batch in range(1000000):
            before=(center._state(db,'session_seq'),center._state(db,'app_seq'),center._state(db,'app_receipt'))
            center._sync_batch(db,session_limit=128,app_limit=4)
            after=(center._state(db,'session_seq'),center._state(db,'app_seq'),center._state(db,'app_receipt'))
            if batch%100==0:
                print({'sessionIndexed':int(after[0]),'sessionTargetAtStart':maximum,'batches':batch+1,'seconds':round(time.monotonic()-start,2)},flush=True)
            done=before==after and int(after[0])>=maximum
            if done:
                for sql in center._deferred_indexes:db.execute(sql)
                for name,sql in indexes:
                    if 'IF NOT EXISTS' not in sql:sql=sql.replace('CREATE INDEX ','CREATE INDEX IF NOT EXISTS ',1)
                    db.execute(sql)
                center._refresh_summaries(db)
            db.commit()
            if done:break
        else:raise RuntimeError('Index did not catch up')
    # Build positional postings once, without a second full-sized WAL copy.
    print('PROJECTIONS_READY; building positional FTS',flush=True)
    with center._db() as db:
        db.execute("INSERT INTO dc_text_fts(dc_text_fts) VALUES('rebuild')")
        for sql in text_triggers:db.execute(sql)
        center._set_state(db,'bulk_fts_deferred','0')
    with center._db() as db:
        db.execute('PRAGMA max_page_count=2147483646')
        db.execute('PRAGMA journal_mode=WAL')
    print('INDEX_READY',flush=True)
    if args.skip_benchmarks:
        print('READY',flush=True)
        return
    # Query against the committed replacement; no synchronous source writes.
    center._worker={'running':False,'error':None}
    for label,params in [('default',{}),('password',{'q':'password'})]:
        started=time.monotonic();result=center.search(params)
        print({'check':label,'seconds':round(time.monotonic()-started,3),'total':result['total']},flush=True)
    started=time.monotonic();result=center.capabilities()
    print({'check':'rankings','seconds':round(time.monotonic()-started,3),'totals':result['totals']},flush=True)
    print('READY',flush=True)

if __name__=='__main__':main()
