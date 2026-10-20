"""Build a replacement index beside the live database, without model calls."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class IndexSource:
    def __init__(self, source, target):
        self.source,self.path=source,str(target/'source.db')
    def __getattr__(self,name):return getattr(self.source,name)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--target',type=Path,required=True)
    args=parser.parse_args();args.target.mkdir(mode=0o700,parents=True,exist_ok=True)
    session_path=args.runtime/'sessionlens.db'
    if not session_path.exists():session_path=args.runtime/'session-lens.db'
    # Read existing sources without running unrelated startup backfills.
    sessions=SessionStore.__new__(SessionStore);sessions.path=session_path
    devices=DeviceStore.__new__(DeviceStore);devices.path=str(args.runtime/'devices.db')
    center=DataCenter(CollectionView(devices,IndexSource(sessions,args.target)),bulk=True)
    # This is an isolated replacement, so bulk load before building secondary
    # record indexes. Keep physical identity and shared-text lookups indexed.
    with center._db() as db:
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
    # Query against the committed replacement; no synchronous source writes.
    center._worker={'running':False,'error':None}
    for label,params in [('default',{}),('password',{'q':'password'})]:
        started=time.monotonic();result=center.search(params)
        print({'check':label,'seconds':round(time.monotonic()-started,3),'total':result['total']},flush=True)
    started=time.monotonic();result=center.capabilities()
    print({'check':'rankings','seconds':round(time.monotonic()-started,3),'totals':result['totals']},flush=True)
    print('READY',flush=True)

if __name__=='__main__':main()
