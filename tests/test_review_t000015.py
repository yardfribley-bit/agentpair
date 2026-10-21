"""Synthetic acceptance cases for the Grok review; no external providers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
import sqlite3
import threading
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from agentpair.data_center import QueryBusy,QueryTimeout
from agentpair.request_limits import RequestLimits
from . import test_data_center as data_fixture
from . import test_collection_questions_api as api_fixture


class ReviewIndexTests(unittest.TestCase):
    def setUp(self):
        self.f=data_fixture.DataCenterTests();self.f.setUp();self.addCleanup(self.f.tearDown)

    def test_shared_growing_context_is_under_twenty_percent_and_original_is_retained(self):
        f=self.f;original=0
        for i in range(50):
            messages=[{'role':'system','content':'固定背景内容 '*3000}]+[{'role':'user','content':'需求 '+str(j)+' 内容 '*100} for j in range(i+1)]
            body=json.dumps({'messages':messages},ensure_ascii=False);original+=len(body.encode())
            f.context('growing-'+str(i),body)
        result=f.find('固定背景内容')
        self.assertEqual(result['total'],50)
        with f.center._db() as db:
            shared=db.execute('SELECT coalesce(sum(length(cast(value AS BLOB))),0) FROM dc_texts').fetchone()[0]
        self.assertLess(shared,original*.20)
        raw=f.center.raw(result['items'][0]['id'])
        self.assertTrue(raw)

    def test_shared_context_indexes_provider_extensions_and_function_names(self):
        f=self.f
        body=json.dumps({'messages':[
            {'role':'assistant','content':'response','id':'provider-needle-7351',
             'providerExtension':{'trace':'extension-needle-9273'},
             'tool_calls':[{'function':{'name':'function-needle-4186','arguments':'{}'}}]},
            'primitive-needle-5297']})
        f.context('provider-fields',body)
        for needle in ('provider-needle-7351','extension-needle-9273',
                       'function-needle-4186','primitive-needle-5297'):
            with self.subTest(needle=needle):self.assertEqual(f.find(needle)['total'],1)

    def test_replacement_builder_restores_query_indexes_after_bulk_load(self):
        f=self.f;f.upload([f.event('bulk-'+str(i),text='bulk password '+str(i)) for i in range(130)])
        target=f.root/'replacement'
        script=Path(__file__).resolve().parents[1]/'ops/rebuild_data_center.py'
        result=subprocess.run([sys.executable,str(script),'--runtime',str(f.root),'--target',str(target)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('READY',result.stdout)
        self.assertIn("'total': 130",result.stdout)
        with closing(sqlite3.connect(target/'data-center.db')) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0],'wal')
            self.assertEqual(db.execute("SELECT value FROM dc_state WHERE name='bulk_fts_deferred'").fetchone()[0],'0')
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN ('dc_texts_insert','dc_texts_delete')").fetchone()[0],2)
        from ops.rebuild_data_center import IndexSource
        replacement=type(f.center)(type(f.center.collections)(f.devices,IndexSource(f.sessions,target)))
        f.upload([f.event('after-bulk',text='bulk password appended')])
        self.assertEqual(replacement.search({'q':'password'})['total'],131)
        # Resume a projection-complete build whose final FTS transaction never
        # completed. It must restore both search and subsequent maintenance.
        with replacement._db() as db:
            replacement._set_state(db,'bulk_fts_deferred','1')
            db.execute('DROP TRIGGER dc_texts_insert');db.execute('DROP TRIGGER dc_texts_delete')
            db.execute("INSERT INTO dc_text_fts(dc_text_fts) VALUES('delete-all')")
        resumed=subprocess.run([sys.executable,str(script),'--runtime',str(f.root),'--target',str(target),'--skip-benchmarks'],capture_output=True,text=True,timeout=30)
        self.assertEqual(resumed.returncode,0,resumed.stderr)
        replacement=type(f.center)(type(f.center.collections)(f.devices,IndexSource(f.sessions,target)))
        self.assertEqual(replacement.search({'q':'password'})['total'],131)
        f.upload([f.event('after-resume',text='password after resumed build')])
        self.assertEqual(replacement.search({'q':'password'})['total'],132)

    def test_offline_builder_reserves_disk_and_refuses_the_live_target(self):
        from ops.rebuild_data_center import IndexSource,OfflineDataCenter
        from types import SimpleNamespace
        f=self.f;target=f.root/'low-space';target.mkdir()
        with patch('ops.rebuild_data_center.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(RuntimeError,'Insufficient free disk'):
                OfflineDataCenter(type(f.center.collections)(f.devices,IndexSource(f.sessions,target)))
        script=Path(__file__).resolve().parents[1]/'ops/rebuild_data_center.py'
        result=subprocess.run([sys.executable,str(script),'--runtime',str(f.root),'--target',str(f.root)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,2)
        self.assertIn('Target must be separate',result.stderr)

    def test_offline_target_rejects_database_and_sidecar_links(self):
        from ops.rebuild_data_center import _validate_target
        f=self.f;live=f.root/'data-center.db'
        for hard in (False,True):
            for suffix in ('','-wal'):
                with self.subTest(hard=hard,suffix=suffix):
                    target=f.root/('alias-'+str(hard)+str(len(suffix)));target.mkdir()
                    link=target/('data-center.db'+suffix)
                    if hard:os.link(live,link)
                    else:
                        try:link.symlink_to(live)
                        except OSError as error:
                            if getattr(error,'winerror',None)==1314:continue
                            raise
                    try:
                        with self.assertRaises(ValueError):_validate_target(f.root,target)
                    finally:link.unlink()

    def test_offline_sources_cannot_write_or_create_source_databases(self):
        from ops.rebuild_data_center import _ReadOnlySessionStore,_ReadOnlyDeviceStore
        f=self.f
        for kind,path in ((_ReadOnlySessionStore,f.sessions.path),(_ReadOnlyDeviceStore,f.devices.path)):
            source=kind.__new__(kind);source.path=path
            with source.connect() as db:
                self.assertEqual(db.execute('SELECT 1').fetchone()[0],1)
                with self.assertRaisesRegex(sqlite3.OperationalError,'readonly'):
                    db.execute('CREATE TABLE must_not_exist(x)')
        missing=f.root/'absent-source.db';source.path=missing
        with self.assertRaises(sqlite3.OperationalError):
            with source.connect():pass
        self.assertFalse(missing.exists())

    def test_unfinished_bulk_index_cannot_be_served_as_complete(self):
        f=self.f;f.upload([f.event('bulk-incomplete',text='password')]);f.find()
        with f.center._db() as db:
            f.center._set_state(db,'bulk_fts_deferred','1')
            db.execute('DROP INDEX dc_records_recent')
        with self.assertRaisesRegex(RuntimeError,'Isolated index build is incomplete'):
            type(f.center)(f.center.collections)
        with f.center._db() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='dc_records_recent'").fetchone())
            f.center._set_state(db,'bulk_fts_deferred','0')
        self.assertEqual(f.find('password')['total'],1)

    def test_offline_disk_guard_interrupts_a_running_sql_statement(self):
        from ops.rebuild_data_center import IndexSource,OfflineDataCenter
        from types import SimpleNamespace
        f=self.f;target=f.root/'disk-guard';target.mkdir()
        center=OfflineDataCenter(type(f.center.collections)(f.devices,IndexSource(f.sessions,target)))
        low=threading.Event()
        with patch('ops.rebuild_data_center.shutil.disk_usage',side_effect=lambda _:SimpleNamespace(free=0 if low.is_set() else 10*1024**3)):
            with self.assertRaisesRegex(sqlite3.OperationalError,'interrupted'):
                with center._db() as db:
                    low.set()
                    db.execute('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n').fetchone()

    def test_offline_disk_alarm_covers_idle_connections_and_immediate_commit(self):
        from ops.rebuild_data_center import IndexSource,OfflineDataCenter
        from types import SimpleNamespace
        f=self.f
        for phase in ('commit','idle'):
            with self.subTest(phase=phase):
                target=f.root/('disk-'+phase);target.mkdir()
                center=OfflineDataCenter(type(f.center.collections)(f.devices,IndexSource(f.sessions,target)))
                low=threading.Event()
                with patch('ops.rebuild_data_center.shutil.disk_usage',side_effect=lambda _:SimpleNamespace(free=0 if low.is_set() else 10*1024**3)):
                    with self.assertRaisesRegex(RuntimeError,'Insufficient free disk'):
                        with center._db() as db:
                            db.execute("INSERT INTO dc_state VALUES('must-rollback','1')")
                            low.set()
                            if phase=='idle':
                                threading.Event().wait(.8)
                                db.execute('SELECT 42')
                            else:db.commit()
                with closing(sqlite3.connect(target/'data-center.db')) as db:
                    self.assertIsNone(db.execute("SELECT value FROM dc_state WHERE name='must-rollback'").fetchone())

    def test_same_request_across_collectors_groups_once_without_cross_session_guessing(self):
        f=self.f;f.upload([f.event('linked',text='设计同一个页面',source='workbuddy')])
        for i in range(3):f.context('linked-app-'+str(i),json.dumps({'messages':[{'role':'user','content':'设计同一个页面'}]}))
        result=f.find('设计同一个页面',group='request')
        self.assertEqual(result['total'],1)
        self.assertEqual(result['groups'][0]['appLensRequestCount'],3)
        self.assertEqual(result['groups'][0]['request']['association'],'candidate')

    def test_context_delta_keeps_repeated_background_collapsed_and_new_occurrence_visible(self):
        f=self.f
        old=[{'role':'system','content':'fixed background'},{'role':'user','content':'same input'}]
        f.context('delta-old',json.dumps({'messages':old}))
        f.context('delta-new',json.dumps({'messages':old+[{'role':'user','content':'same input'}]}),received=1700000101)
        result=f.find(collector='applens')
        item=next(r for r in result['items'] if r['receivedAt']==1700000101)
        detail=f.center.record(item['id'])['item']
        self.assertEqual(detail['contextDelta']['newMessageIndices'],[2])
        self.assertEqual(detail['contextDelta']['sharedMessageCount'],2)
        self.assertIn('fixed background',detail['content'])

    def test_interrupted_fulltext_rebuild_recovers_before_reading(self):
        f=self.f;f.upload([f.event('recover-fts',text='recover password')]);f.find()
        with f.center._db() as db:
            db.execute("INSERT INTO dc_text_fts(dc_text_fts) VALUES('delete-all')")
            f.center._set_state(db,'positional_fts_rebuilding','1')
        f.center=type(f.center)(f.center.collections)
        self.assertEqual(f.find('password')['total'],1)

    def test_cached_totals_require_identical_watermarks_and_visible_devices(self):
        f=self.f;f.upload([f.event('alice-cached')]);f.upload([f.event('bob-cached')],owner='bob');f.find()
        with f.center._db() as db:
            f.center._scope(db)
            _,snapshot=f.center._snapshot(db,{},None)
            self.assertEqual(f.center._cached_scope(db,{}, {},snapshot),(2,2))
            self.assertIsNone(f.center._cached_scope(db,{}, {},dict(snapshot,sessionMax=0)))
            db.execute('DELETE FROM dc_scope WHERE owner=?',('bob',))
            self.assertIsNone(f.center._cached_scope(db,{}, {},snapshot))

    def test_sparse_rankings_keep_broad_matches_and_unknown_calls(self):
        f=self.f
        f.upload([f.event('plain-one'),f.event('plain-two'),
                  f.event('unknown-call',kind='tool_call'),
                  f.event('known-call',kind='tool_call',name='Read')])
        result=f.center.capabilities()
        self.assertEqual(result['coverage']['matchedRecords'],4)
        self.assertEqual(result['coverage']['activityRecords'],1)
        self.assertEqual(result['coverage']['unknownToolCount'],1)
        self.assertEqual(result['totals']['tools'],1)
        users=f.center.capabilities({'kind':'user'})
        self.assertEqual(users['coverage']['matchedRecords'],2)
        self.assertEqual(users['coverage']['activityRecords'],0)
        self.assertEqual(users['coverage']['unknownToolCount'],0)
        self.assertEqual(users['tools'],[])

    def test_summary_reuses_unchanged_state_but_invalidates_source_and_sql_changes(self):
        f=self.f;f.upload([f.event('summary-stable')]);f.find()
        with f.center._db() as db:
            first=db.execute("SELECT updated FROM dc_summaries WHERE owner=''").fetchone()[0]
            trace=[];db.set_trace_callback(trace.append)
            with patch('agentpair.data_center.time.time',return_value=first+100):f.center._refresh_summaries(db)
            self.assertEqual(db.execute("SELECT updated FROM dc_summaries WHERE owner=''").fetchone()[0],first)
            self.assertFalse(any('dc_inventory' in sql.lower() for sql in trace))
            db.set_trace_callback(None)
            f.center._set_state(db,'session_raw_count',4)
            f.center._refresh_summaries(db)
            self.assertEqual(f.center._coverage_summary(db,None)['unindexed'],3)
            db.execute("UPDATE dc_records SET metadata=json_set(metadata,'$.tool','changed')")
            self.assertIsNone(f.center._coverage_summary(db,None)['updatedAt'])
            before=f.center._state(db,'summaries_signature')
            f.center._refresh_summaries(db)
            self.assertNotEqual(f.center._state(db,'summaries_signature'),before)
            db.execute('DELETE FROM dc_records')
            f.center._refresh_summaries(db)
            self.assertEqual(f.center._coverage_summary(db,None)['retained'],0)

    def test_owner_summary_cache_tracks_names_and_revocation_without_scope_leaks(self):
        f=self.f;f.upload([f.event('summary-alice')]);f.upload([f.event('summary-bob'),f.event('summary-bob2')],owner='bob');f.find()
        for owner,count in [('alice',1),('bob',2)]:
            with f.center._db() as db:
                f.center._scope(db,owner)
                _,snapshot=f.center._snapshot(db,{},owner)
                self.assertEqual(f.center._cached_scope(db,{}, {},snapshot,owner),(count,1))
        with f.devices.connect() as db:db.execute('UPDATE devices SET name=? WHERE owner=?',('Renamed Device','alice'))
        self.assertEqual(f.center.summary('alice')['facets']['devices'],[])
        with f.center._db() as db:f.center._refresh_summaries(db)
        self.assertEqual(f.center.summary('alice')['facets']['devices'][0]['name'],'Renamed Device')
        with f.devices.connect() as db:db.execute('UPDATE devices SET revoked=1 WHERE owner=?',('alice',))
        self.assertEqual(f.center.summary('alice')['retainedRecords'],0)
        with f.center._db() as db:
            f.center._refresh_summaries(db)
            self.assertIsNone(db.execute('SELECT owner FROM dc_summaries WHERE owner=?',('alice',)).fetchone())
        self.assertEqual(f.center.summary('bob')['retainedRecords'],2)

    def test_pending_summary_does_not_claim_complete_text_coverage(self):
        f=self.f;f.upload([f.event('coverage-pending')]);f.find()
        with f.center._db() as db:f.center._set_state(db,'session_raw_count',4)
        f.center._worker={'running':False,'error':None}
        result=f.find()
        self.assertEqual(result['total'],1)
        self.assertFalse(result['coverage']['fullRetainedText'])
        self.assertFalse(f.center.capabilities()['coverage']['fullRetainedText'])

    def test_five_background_readers_do_not_serialize(self):
        f=self.f;f.upload([f.event('seed')]);f.find();f.center._worker={'running':False,'error':None}
        barrier=threading.Barrier(5)
        def read(_):
            with f.center._read_access():barrier.wait(timeout=3)
            return f.find()['total']
        with ThreadPoolExecutor(max_workers=5) as pool:self.assertEqual(list(pool.map(read,range(5))),[1]*5)

    def test_fulltext_query_uses_fts_and_checks_literal_boundaries(self):
        f=self.f;f.upload([f.event('fts',text='PASSWORD and 中文子串检索 Straße https://example.invalid/path'),f.event('false-grams',text='pas ass ssw swo wor ord')])
        self.assertEqual(f.find('password')['total'],1)
        self.assertEqual(f.find('中文子串')['total'],1)
        self.assertEqual(f.find('strasse')['total'],1)
        self.assertEqual(f.find('domain="example.invalid."')['total'],1)
        with f.center._db() as db:
            self.assertGreater(db.execute("SELECT count(*) FROM dc_text_fts WHERE dc_text_fts MATCH '\"pas\"'").fetchone()[0],0)


class ReviewSecurityTests(unittest.TestCase):
    def test_limits_are_per_ip_and_per_account(self):
        limits=RequestLimits()
        for _ in range(10):self.assertTrue(limits.allow('auth','192.0.2.1','alice'))
        self.assertFalse(limits.allow('auth','192.0.2.1','alice'))
        self.assertTrue(limits.allow('auth','192.0.2.2','admin'))
        self.assertFalse(limits.allow('auth','192.0.2.2','alice'))

    def test_failed_admin_login_flood_cannot_block_valid_admin_from_another_ip(self):
        f=api_fixture.CollectionQuestionsAPITests();f.setUp();self.addCleanup(f.tearDown)
        for _ in range(10):f.rejected((401,429),'/api/login',{'username':'admin','password':'wrong-password'},headers={'X-Real-IP':'192.0.2.10'})
        status,result=f.call('/api/login',{'username':'admin','password':f.PASSWORD},headers={'X-Real-IP':'192.0.2.11'})
        self.assertEqual(status,200)
        self.assertEqual(result['role'],'admin')

    def test_timeout_and_capacity_have_distinct_http_codes_and_retry_headers(self):
        f=api_fixture.CollectionQuestionsAPITests();f.setUp();self.addCleanup(f.tearDown)
        import urllib.error
        for error,status in [(QueryBusy('capacity'),429),(QueryTimeout('timeout'),503)]:
            with patch('agentpair.data_center.DataCenter.search',side_effect=error):
                with self.assertRaises(urllib.error.HTTPError) as raised:f.call('/api/data-center/search')
                with raised.exception as response:
                    self.assertEqual(response.code,status)
                    self.assertTrue(response.headers.get('Retry-After'))
        self.assertEqual(f.model.calls,[])

    def test_global_budget_applies_even_when_engine_has_no_budget(self):
        f=api_fixture.CollectionQuestionsAPITests();f.setUp();self.addCleanup(f.tearDown)
        with f.engine.connection() as db:db.execute('UPDATE ledger SET reserved=20 WHERE id=1')
        before=f.engine.accounts.balance(f.owners['alice'])['remainingCNY']
        identity=f.submit('alice',f.payload())
        self.assertEqual(f.finished(identity,'alice')['status'],'failed')
        self.assertEqual(f.model.calls,[])
        self.assertEqual(f.engine.accounts.balance(f.owners['alice'])['remainingCNY'],before)

    def test_signup_needs_invite_and_does_not_grant_credit(self):
        f=api_fixture.CollectionQuestionsAPITests();f.setUp();self.addCleanup(f.tearDown)
        f.call('/api/register',{'username':'newuser','password':f.PASSWORD},actor='alice')
        owner=f.engine.accounts.login('newuser',f.PASSWORD)['id']
        self.assertEqual(f.engine.accounts.balance(owner)['remainingCNY'],0)
        f.rejected(403,'/api/register',{'username':'blocked','password':f.PASSWORD,'inviteCode':'invalid'})
