"""Synthetic acceptance cases for the Grok review; no external providers."""
from concurrent.futures import ThreadPoolExecutor
import json
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

    def test_replacement_builder_restores_query_indexes_after_bulk_load(self):
        f=self.f;f.upload([f.event('bulk-'+str(i),text='bulk password '+str(i)) for i in range(130)])
        target=f.root/'replacement'
        script=Path(__file__).resolve().parents[1]/'ops/rebuild_data_center.py'
        result=subprocess.run([sys.executable,str(script),'--runtime',str(f.root),'--target',str(target)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('READY',result.stdout)
        self.assertIn("'total': 130",result.stdout)

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
