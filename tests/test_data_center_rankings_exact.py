"""Rank filters use sparse evidence and cached pages retain exact scope counts."""
from contextlib import contextmanager
import sqlite3
import unittest
from unittest.mock import patch

from . import test_data_center as fixtures


class RankingExactTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.DataCenterTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        f=self.f
        f.upload([f.event('plain',text='ordinary user'),
                  f.event('known','tool_call',name='Read',timestamp=1700000001),
                  f.event('skill','tool_call',name='Skill',payload={'arguments':{'skill':'writer'}},timestamp=1700000002),
                  f.event('mcp','tool_call',name='mcp__docs__search',timestamp=1700000003),
                  f.event('unknown','tool_call',timestamp=1700000004),
                  f.event('other-app','tool_call',name='Bash',source='workbuddy',timestamp=1700000005)])
        f.upload([f.event('other-owner','tool_call',name='BobOnly',timestamp=1700000006)],owner='bob')
        f.context('context-only','{"messages":[{"role":"assistant","tool_calls":[{"function":{"name":"not-an-execution"}}]}]}')
        f.find();self.c=f.center;self.c._worker={'running':False,'error':None}

    def reference(self,params,owner=None):
        """Execute original metadata object predicates as an independent oracle."""
        values,page,size,dates=self.c._validate(dict({'pageSize':100},**params))
        with self.c._db() as db:
            self.c._scope(db,owner)
            token,snapshot=self.c._snapshot(db,values,owner,'capabilities')
            join,predicate,args,count=self.c._query_scope(db,values,dates,[],snapshot,owner)
            # A nonempty group selects the retained metadata matching path;
            # its predicate is already complete and contains no keyword term.
            return self.c._capabilities_query(db,values,page,size,[[]],owner,token,snapshot,join,predicate,args,count)

    @contextmanager
    def without_metadata_reads(self):
        original=self.c._db
        @contextmanager
        def checked():
            with original() as db:
                def authorize(action,table,column,*_):
                    if action==sqlite3.SQLITE_READ and table=='dc_records' and column=='metadata':return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                try:yield db
                finally:db.set_authorizer(None)
        with patch.object(self.c,'_db',checked):yield

    def test_uncached_object_and_collector_filters_do_not_read_large_metadata(self):
        for category in ('all','tool','skill','mcp'):
            for collector in ('all','sessionlens','applens'):
                params={'object':category,'collector':collector,'pageSize':2}
                with self.subTest(params=params):
                    expected=self.reference(params)
                    with self.without_metadata_reads():actual=self.c.capabilities(params)
                    self.assertEqual(actual,expected)

    def test_other_filters_and_owner_scope_remain_exact(self):
        cases=[({'object':'tool','collector':'sessionlens','application':'workbuddy'},None),
               ({'object':'tool','after':1700000002,'before':1700000004},None),
               ({'object':'tool','kind':'user'},None),
               ({'object':'skill','location':'arguments'},None),
               ({'object':'tool','device':self.f.identities['alice']['id']},None),
               ({'object':'tool'},'alice'),({'object':'mcp'},'bob')]
        for params,owner in cases:
            with self.subTest(params=params,owner=owner):
                expected=self.reference(params,owner)
                with self.without_metadata_reads():actual=self.c.capabilities(params,owner)
                self.assertEqual(actual,expected)

    def test_cached_objects_collectors_and_second_page_skip_scope_queries(self):
        cases=[{'object':category,'collector':collector,'pageSize':2,'page':page}
               for category in ('all','tool','skill','mcp')
               for collector in ('all','sessionlens','applens') for page in (1,2)]
        expected=[self.reference(params) for params in cases]
        self.assertTrue(self.c.refresh_capabilities_cache())
        with patch.object(self.c,'_query_scope',side_effect=AssertionError('cached ranking reread scope')):
            for params,value in zip(cases,expected):
                with self.subTest(params=params):self.assertEqual(self.c.capabilities(params),value)

    def test_second_full_page_retains_all_rows_and_snapshot_excludes_new_calls(self):
        self.f.upload([self.f.event('many-'+str(i),'tool_call',name='Tool'+str(i),timestamp=1700010000+i) for i in range(110)])
        with self.c._db() as db:self.c._sync(db);self.c._refresh_summaries(db)
        self.c.refresh_capabilities_cache()
        params={'object':'tool','collector':'sessionlens','pageSize':100}
        first=self.c.capabilities(params)
        frozen=dict(params,page=2,snapshot=first['snapshot'])
        expected=self.reference(frozen)
        with patch.object(self.c,'_query_scope',side_effect=AssertionError('second cached page recomputed')):
            second=self.c.capabilities(frozen)
        self.assertEqual(second,expected);self.assertGreater(len(second['tools']),0)
        self.assertFalse(second['hasMore']['tools'])
        self.assertTrue({row['name'] for row in first['tools']}.isdisjoint(row['name'] for row in second['tools']))
        self.f.upload([self.f.event('late','tool_call',name='LateOnly',timestamp=1800000000)])
        with self.c._db() as db:self.c._sync_batch(db);self.c._refresh_summaries(db)
        self.c.refresh_capabilities_cache()
        after=self.c.capabilities(frozen)
        self.assertEqual(after,self.reference(frozen))
        self.assertEqual(after['tools'],expected['tools'])
        self.assertEqual(after['totals'],expected['totals'])

    def test_changed_scope_and_unsupported_filters_cannot_use_global_cache(self):
        self.c.refresh_capabilities_cache()
        with self.f.devices.connect() as db:db.execute("UPDATE devices SET revoked=1 WHERE owner='bob'")
        actual=self.c.capabilities({'object':'tool','collector':'sessionlens'})
        self.assertNotIn('BobOnly',{row['name'] for row in actual['tools']})
        self.assertEqual(actual,self.reference({'object':'tool','collector':'sessionlens'}))
        self.c.refresh_capabilities_cache()
        for params in ({'object':'tool','application':'workbuddy'},{'object':'tool','after':1700000004}):
            with self.subTest(params=params):self.assertEqual(self.c.capabilities(params),self.reference(params))

