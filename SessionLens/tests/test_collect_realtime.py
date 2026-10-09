import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sessionlens.core import Collector
from sessionlens.desktop import Runtime


class Clock:
    def __init__(self):self.now=100
    def monotonic(self):return self.now


class StopAfter:
    def __init__(self,clock,rounds):self.clock=clock;self.rounds=rounds;self.waits=0
    def is_set(self):return self.waits>=self.rounds
    def wait(self,seconds):self.clock.now+=seconds;self.waits+=1;return self.is_set()


class RealtimeCollectorTests(unittest.TestCase):
    def config(self,*roots):
        return {'sources':{name:{'enabled':True,'roots':[str(root)]} for name,root in roots},'endpoint':''}

    def write(self,p,source='workbuddy',count=1,padding=0):
        p.parent.mkdir(parents=True,exist_ok=True)
        rows=[{'type':'message','sessionId':p.stem,'role':'user','content':f'row {n} '+'x'*padding} if source=='workbuddy'
              else {'type':'event_msg','payload':{'type':'user_message','message':f'row {n} '+'x'*padding}} for n in range(count)]
        p.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')

    def run_rounds(self,runtime,rounds):
        clock=Clock();runtime.stop=StopAfter(clock,rounds)
        with patch('sessionlens.desktop.time.monotonic',clock.monotonic):runtime.collect()

    def test_each_source_tails_before_history_and_index_is_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);codex=root/'codex';wb=root/'workbuddy'
            self.write(codex/'rollout-a.jsonl','codex',300);self.write(wb/'a.jsonl',count=300)
            runtime=Runtime(root/'state',self.config(('codex',codex),('workbuddy',wb)))
            calls=[];scan=Collector.scan;tail=Collector.scan_recent
            def history(c,p,*args,**kwargs):calls.append(('history',args[1],p));return scan(c,p,*args,**kwargs)
            def recent(c,p,*args,**kwargs):calls.append(('live',args[1],p));return tail(c,p,*args,**kwargs)
            with patch.object(Collector,'scan',history),patch.object(Collector,'scan_recent',recent),patch.object(runtime,'index',side_effect=AssertionError('collection must not index')):
                self.run_rounds(runtime,1)
            self.assertEqual([(phase,source) for phase,source,_ in calls[:2]],[('live','codex'),('live','workbuddy')])
            self.assertEqual({source for phase,source,_ in calls if phase=='history'},{'codex','workbuddy'})
            self.assertGreater(runtime.status['heartbeat'],0)
            self.assertEqual(set(runtime.status['source_progress']),{'codex','workbuddy'})
            self.assertTrue(all(state['historicalBytes']<state['totalBytes'] for state in runtime.status['source_progress'].values()))

    def test_transient_read_failure_retries_unchanged_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'logs'/'task.jsonl';self.write(p,count=400)
            runtime=Runtime(root/'state',self.config(('workbuddy',p.parent)))
            calls=[];tail=Collector.scan_recent
            def recent(c,*args,**kwargs):
                calls.append(args[0])
                if len(calls)==1:raise OSError('temporary read failure')
                return tail(c,*args,**kwargs)
            with patch.object(Collector,'scan_recent',recent):self.run_rounds(runtime,3)
            self.assertEqual(len(calls),2)
            self.assertEqual(runtime.status['errors'],{})

    def test_history_rotation_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);logs=root/'logs'
            for n in range(3):self.write(logs/f'{n}.jsonl',count=500)
            config=self.config(('workbuddy',logs));scan=Collector.scan;calls=[]
            def history(c,p,*args,**kwargs):calls.append(p);return scan(c,p,*args,**kwargs)
            runtime=Runtime(root/'state',config)
            with patch.object(Collector,'scan',history):self.run_rounds(runtime,1)
            with sqlite3.connect(runtime.path) as db:
                previous=Path(db.execute('SELECT path FROM collection_file_schedule WHERE lane=? AND source=?',('history_priority','workbuddy')).fetchone()[0])
            calls.clear()
            restarted=Runtime(root/'state',config)
            with patch.object(Collector,'scan',history):self.run_rounds(restarted,1)
            self.assertNotEqual(calls[0],previous)
            with sqlite3.connect(runtime.path) as db:
                self.assertEqual(db.execute('SELECT path FROM history_schedule WHERE source=?',('workbuddy',)).fetchone()[0],str(calls[-1]))

    def test_unexpected_batch_failure_is_visible_and_collection_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'logs'/'task.jsonl';self.write(p,count=3)
            runtime=Runtime(root/'state',self.config(('workbuddy',p.parent)))
            updates=[];attempts=[];tail=Collector.scan_recent
            def recent(c,*args,**kwargs):
                attempts.append(1)
                if len(attempts)==1:raise RuntimeError('unexpected batch failure')
                return tail(c,*args,**kwargs)
            def update(**values):updates.append(values);Runtime.update(runtime,**values)
            with patch.object(Collector,'scan_recent',recent),patch.object(runtime,'update',update):self.run_rounds(runtime,3)
            self.assertTrue(any('采集线程' in values.get('errors',{}) for values in updates))
            self.assertEqual(runtime.status['errors'],{})
            with sqlite3.connect(runtime.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],3)

    def test_history_budget_yields_to_other_source_next_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);codex=root/'codex';wb=root/'workbuddy'
            self.write(codex/'rollout-a.jsonl','codex',500);self.write(wb/'a.jsonl',count=500)
            runtime=Runtime(root/'state',self.config(('codex',codex),('workbuddy',wb)))
            clock=Clock();runtime.stop=StopAfter(clock,2);calls=[];scan=Collector.scan
            def slow(c,p,*args,**kwargs):calls.append(args[1]);clock.now+=.2;return scan(c,p,*args,**kwargs)
            with patch.object(Collector,'scan',slow),patch('sessionlens.desktop.time.monotonic',clock.monotonic):runtime.collect()
            self.assertEqual(calls,['codex','workbuddy'])

    def test_slow_history_source_rotation_survives_each_round_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);codex=root/'codex';wb=root/'workbuddy'
            self.write(codex/'rollout-a.jsonl','codex',500);self.write(wb/'a.jsonl',count=500)
            config=self.config(('codex',codex),('workbuddy',wb));calls=[];scan=Collector.scan
            for _ in range(3):
                runtime=Runtime(root/'state',config);clock=Clock();runtime.stop=StopAfter(clock,1)
                def slow(c,p,*args,**kwargs):calls.append(args[1]);clock.now+=.2;return scan(c,p,*args,**kwargs)
                with patch.object(Collector,'scan',slow),patch('sessionlens.desktop.time.monotonic',clock.monotonic):runtime.collect()
            self.assertEqual(calls,['codex','workbuddy','codex'])
            with sqlite3.connect(runtime.path) as db:
                self.assertEqual(db.execute("SELECT next_source FROM collection_schedule WHERE name='history'").fetchone()[0],'workbuddy')

    def test_recent_live_rotation_reaches_six_files_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();logs=root/'logs';paths=[];now=time.time()
            for n in range(6):
                p=logs/f'{n}.jsonl';self.write(p);os.utime(p,(now-n,now-n));paths.append(p)
            config=self.config(('workbuddy',logs));calls=[];tail=Collector.scan_recent
            def recent(c,p,*args,**kwargs):
                calls.append(p)
                self.assertEqual(args[0],100)
                self.assertEqual(kwargs,{'max_bytes':4*1024*1024,'max_seconds':.1})
                return tail(c,p,*args,**kwargs)
            for _ in range(2):
                with patch.object(Collector,'scan_recent',recent):self.run_rounds(Runtime(root/'state',config),1)
            self.assertEqual(set(calls),set(paths))
            self.assertEqual(calls[0],paths[0]);self.assertEqual(calls[4],paths[0])
            with sqlite3.connect(root/'state'/'collector.db') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM live_cursors').fetchone()[0],6)

    def test_recent_gap_before_tail_is_recovered_while_old_history_advances(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();logs=root/'logs';now=time.time();old=[]
            for n in range(30):
                p=logs/f'old-{n:02}.jsonl';self.write(p,count=400);os.utime(p,(now-3*86400,now-3*86400));old.append(p)
            for n in range(5):
                p=logs/f'{n}.jsonl';self.write(p,count=400);os.utime(p,(now-n,now-n))
            target=logs/'target.jsonl';self.write(target,count=300,padding=12000);os.utime(target,(now-5,now-5))
            runtime=Runtime(root/'state',self.config(('workbuddy',logs)))
            c=Collector(runtime.path);c.scan(target,5,'workbuddy')
            initial=c.db.execute('SELECT offset FROM cursors WHERE path=?',(str(target),)).fetchone()[0];c.db.close()
            starts=[];scan=Collector.scan
            def history(c,p,*args,**kwargs):
                if p==target:starts.append(c.db.execute('SELECT offset FROM cursors WHERE path=?',(str(p),)).fetchone()[0])
                return scan(c,p,*args,**kwargs)
            with patch.object(Collector,'scan',history):self.run_rounds(runtime,8)
            with sqlite3.connect(runtime.path) as db:
                events=[json.loads(body) for (body,) in db.execute('SELECT event FROM events')]
                evidence=next(e for e in events if e['evidence']['path']==str(target) and e['payload'].get('content','').startswith('row 50 '))
                self.assertLess(evidence['evidence']['byteEnd'],target.stat().st_size-2*1024*1024)
                offsets=dict(db.execute('SELECT path,offset FROM cursors'))
                self.assertGreater(sum(offsets.get(str(p),0) for p in old),0)
                self.assertLess(sum(offsets.get(str(p),0) for p in old),sum(p.stat().st_size for p in old))
                positions=[(e['evidence']['path'],e['evidence']['fileIdentity'],e['evidence']['epoch'],e['evidence']['byteStart']) for e in events]
                self.assertEqual(len(positions),len(set(positions)))
            self.assertEqual(starts[0],initial);self.assertEqual(starts,sorted(starts))

    def test_history_three_recent_one_all_and_source_rotation_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();codex=root/'codex';wb=root/'workbuddy';now=time.time()
            for source,folder in (('codex',codex),('workbuddy',wb)):
                for n in range(2):self.write(folder/(f'rollout-{n}.jsonl' if source=='codex' else f'{n}.jsonl'),source,count=500)
                old=folder/('rollout-old.jsonl' if source=='codex' else 'old.jsonl');self.write(old,source,count=500);os.utime(old,(now-3*86400,now-3*86400))
            config=self.config(('codex',codex),('workbuddy',wb));calls=[];scan=Collector.scan
            for _ in range(8):
                runtime=Runtime(root/'state',config);clock=Clock();runtime.stop=StopAfter(clock,1)
                def slow(c,p,*args,**kwargs):
                    phase=c.db.execute("SELECT value FROM collection_state WHERE name='history_phase'").fetchone()[0]
                    calls.append((phase,args[1],p));clock.now+=.2;return scan(c,p,*args,**kwargs)
                with patch.object(Collector,'scan',slow),patch('sessionlens.desktop.time.monotonic',clock.monotonic):runtime.collect()
            self.assertEqual([phase for phase,_,_ in calls],[1,2,3,0,1,2,3,0])
            self.assertEqual({calls[n][1] for n in (3,7)},{'codex','workbuddy'})
            for source in ('codex','workbuddy'):
                priority=[p for phase,s,p in calls if s==source and phase!=0]
                self.assertGreaterEqual(len(set(priority)),2)

    def test_recent_priority_recovers_truncated_file_from_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();logs=root/'logs';target=logs/'target.jsonl';self.write(target,count=500)
            config=self.config(('workbuddy',logs));runtime=Runtime(root/'state',config)
            c=Collector(runtime.path);c.scan(target,500,'workbuddy');c.db.close();self.write(target,count=300)
            # Newer hot files keep the target out of the first realtime slots.
            now=time.time();os.utime(target,(now-10,now-10))
            for n in range(4):
                p=logs/f'{n}.jsonl';self.write(p,count=500);os.utime(p,(now-n,now-n))
            starts=[];scan=Collector.scan
            def history(c,p,*args,**kwargs):
                if p==target:starts.append(c.db.execute('SELECT offset FROM cursors WHERE path=?',(str(p),)).fetchone()[0])
                return scan(c,p,*args,**kwargs)
            with patch.object(Collector,'scan',history):self.run_rounds(runtime,2)
            self.assertTrue(starts)
            with sqlite3.connect(runtime.path) as db:
                self.assertEqual(db.execute('SELECT epoch FROM cursors WHERE path=?',(str(target),)).fetchone()[0],1)
                events=[json.loads(row[0]) for row in db.execute('SELECT event FROM events')]
                self.assertTrue(any(e['evidence']['path']==str(target) and e['evidence']['epoch']==1 and e['evidence']['byteStart']==0 for e in events))

    def test_blocked_display_does_not_delay_new_tail_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'logs'/'task.jsonl';self.write(p,count=900,padding=20000)
            runtime=Runtime(root/'state',self.config(('workbuddy',p.parent)))
            entered=threading.Event();release=threading.Event();index=runtime.index
            def blocked(c,*args):entered.set();release.wait(5);return index(c,*args)
            def idle():runtime.stop.wait(5)
            def event_id(value,start):
                st=p.stat();raw=(json.dumps({'type':'message','sessionId':p.stem,'role':'user','content':value})+'\n').encode()
                return hashlib.sha256(f'{st.st_dev}:{st.st_ino}:0:{start}:'.encode()+raw).hexdigest()
            def has_event(identity):
                with sqlite3.connect(runtime.path,timeout=.25) as db:
                    return db.execute('SELECT 1 FROM events WHERE id=?',(identity,)).fetchone() is not None
            def wait_for(predicate,timeout=3):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    if predicate():return True
                    time.sleep(.02)
                return False
            runtime.index=blocked;runtime.upload=idle;runtime.project=idle;runtime.knowledge=idle;runtime.inventory=idle
            runtime.start()
            try:
                self.assertTrue(entered.wait(1))
                newest='row 899 '+'x'*20000
                raw=(json.dumps({'type':'message','sessionId':p.stem,'role':'user','content':newest})+'\n').encode()
                latest_id=event_id(newest,p.stat().st_size-len(raw))
                self.assertTrue(wait_for(lambda:has_event(latest_id)))
                with sqlite3.connect(runtime.path) as db:
                    history=db.execute('SELECT offset FROM cursors WHERE path=?',(str(p),)).fetchone()
                    self.assertLess(history[0] if history else 0,p.stat().st_size)
                start=p.stat().st_size
                with p.open('a',encoding='utf-8') as f:f.write(json.dumps({'type':'message','sessionId':p.stem,'role':'user','content':'appended now'})+'\n')
                appended_id=event_id('appended now',start)
                self.assertTrue(wait_for(lambda:has_event(appended_id)))
                self.assertFalse(release.is_set())
            finally:
                release.set();runtime.close()


if __name__=='__main__':unittest.main()
