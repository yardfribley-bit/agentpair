"""Narrow query projections and exact default capability preaggregation."""
from contextlib import contextmanager
import json
import unittest
from unittest.mock import patch

from agentpair.data_center import parse_query
from . import test_data_center as fixtures


class QueryCacheTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.DataCenterTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.upload([
            self.f.event('plain',text='password user'),
            self.f.event('skill-a','tool_call',name='Skill',payload={'arguments':{'skill':'alpha'}}),
            self.f.event('skill-b','tool_call',name='Skill',payload={'arguments':{'skill':'beta'}}),
            self.f.event('unknown','tool_call',payload={'arguments':{}})])
        self.f.upload([self.f.event('bob-skill','tool_call',name='Skill',payload={'arguments':{'skill':'bob-only'}})],owner='bob')
        self.f.find();self.c=self.f.center;self.c._worker={'running':False,'error':None}

    @contextmanager
    def trace(self):
        original=self.c._db;statements=[]
        @contextmanager
        def traced():
            with original() as db:
                db.set_trace_callback(statements.append)
                try:yield db
                finally:db.set_trace_callback(None)
        with patch.object(self.c,'_db',traced):yield statements

    def test_covering_candidates_are_used_and_hits_retain_page_fields(self):
        with self.c._db() as db:
            self.c._scope(db);values,_,_,dates=self.c._validate({'q':'password'})
            _,snapshot=self.c._snapshot(db,values,None)
            join,predicate,args,_=self.c._query_scope(db,values,dates,parse_query('password'),snapshot)
            sql='SELECT r.key,s.canonical,r.stamp'+self.c._candidate_join(join,values,parse_query('password'))+predicate
            plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql,args)]
            self.assertTrue(any('COVERING INDEX dc_inventory_key_covering' in item for item in plan),plan)
            calls=self.c._inventory_join(join).replace(' FROM dc_inventory r JOIN',
                ' FROM dc_inventory r INDEXED BY dc_inventory_call_covering JOIN')
            plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN SELECT r.key,r.owner,r.source,r.session,r.stamp,r.kind'+calls+" WHERE r.kind='tool_call'")]
            self.assertTrue(any('COVERING INDEX dc_inventory_call_covering' in item for item in plan),plan)
        with self.trace() as statements:result=self.c.search({'q':'password'})
        self.assertEqual(result['total'],1)
        self.assertEqual(result['summary']['matchedDevices'],1)
        self.assertTrue(any('CREATE TEMP TABLE dc_matches(key TEXT PRIMARY KEY,canonical TEXT,stamp REAL)' in sql for sql in statements))
        self.assertFalse(any('SELECT count(DISTINCT s.canonical) FROM dc_matches' in sql for sql in statements))

    def test_collector_page_uses_ordered_index_without_sorting_the_full_scope(self):
        self.f.upload([self.f.event('recent-'+str(i),text='recent '+str(i),timestamp=1700001000+i) for i in range(40)])
        alias_enrollment=self.f.devices.enroll(self.f.devices.pairing('alice')['code'],'Old installation')
        alias=self.f.devices.identity(alias_enrollment['token'])
        duplicate=self.f.event('recent-alias',text='same evidence',timestamp=1700002000)
        self.f.upload([duplicate],identity=alias);self.f.upload([duplicate])
        self.f.devices.merge_registrations('alice',self.f.identities['alice']['id'],[alias['id']])
        self.f.context('newer-app','{"messages":[{"role":"user","content":"newer AppLens"}]}',timestamp=1800000000)
        with self.c._db() as db:
            self.c._sync_batch(db);self.c._refresh_summaries(db)
            index_sql=db.execute("SELECT sql FROM sqlite_master WHERE name='dc_inventory_collector_recent'").fetchone()[0]
            db.execute('DROP INDEX dc_inventory_collector_recent')
        pages=[{'collector':'sessionlens','pageSize':7,'page':page} for page in (1,2)]
        baseline=[self.c.search(params) for params in pages]
        with self.c._db() as db:db.execute(index_sql)
        for params,expected in zip(pages,baseline):
            with self.subTest(page=params['page']),self.trace() as statements:
                actual=self.c.search(params)
                self.assertEqual(actual,expected)
                self.assertTrue(all(item['collector']=='sessionlens' for item in actual['items']))
                sql=next(sql for sql in statements if sql.startswith('WITH picked AS MATERIALIZED'))
                with self.c._db() as db:
                    self.c._scope(db)
                    plan=[tuple(row) for row in db.execute('EXPLAIN QUERY PLAN '+sql)]
                self.assertTrue(any('dc_inventory_collector_recent' in row[3] for row in plan),plan)
                picked=next(row[0] for row in plan if row[3]=='MATERIALIZE picked')
                parents={row[0]:row[1] for row in plan}
                def inside_picked(node):
                    while node in parents and node!=picked:node=parents[node]
                    return node==picked
                # The outer ORDER BY may still sort the seven selected rows;
                # only a sort inside the unbounded source selection is costly.
                self.assertFalse(any('USE TEMP B-TREE FOR ORDER BY' in row[3] and inside_picked(row[0]) for row in plan),plan)
        self.assertTrue({item['id'] for item in baseline[0]['items']}.isdisjoint(item['id'] for item in baseline[1]['items']))
        self.assertEqual(baseline[0]['coverage']['duplicateAliasRecords'],1)

    def test_default_cache_preserves_results_and_resizes_without_querying_hits(self):
        expected=self.c.capabilities()
        self.assertTrue(self.c.refresh_capabilities_cache())
        with self.trace() as statements:actual=self.c.capabilities()
        self.assertEqual(actual,expected)
        self.assertFalse(any('CREATE TEMP TABLE dc_matches' in sql for sql in statements))
        with self.trace() as statements:small=self.c.capabilities({'pageSize':1})
        self.assertEqual(len(small['skills']),1)
        self.assertEqual(small['totals']['skills'],3)
        self.assertTrue(small['hasMore']['skills'])
        self.assertFalse(any('CREATE TEMP TABLE dc_matches' in sql for sql in statements))
        self.assertFalse(self.c.refresh_capabilities_cache())

    def test_unfiltered_ui_tabs_have_identical_category_rows_and_exact_pagination(self):
        self.f.upload([self.f.event('mcp-ui','tool_call',name='mcp__fixture__search',arguments={'q':'example'})])
        with self.c._db() as db:self.c._sync_batch(db);self.c._refresh_summaries(db)
        self.c.refresh_capabilities_cache()
        for page,size in ((1,1),(1,50),(2,1)):
            with self.subTest(page=page,size=size):
                broad=self.c.capabilities({'object':'all','page':page,'pageSize':size})
                for category,plural in (('tool','tools'),('skill','skills'),('mcp','mcps')):
                    selected=self.c.capabilities({'object':category,'page':page,'pageSize':size})
                    self.assertEqual(broad[plural],selected[plural])
                    self.assertEqual(broad['totals'][plural],selected['totals'][plural])
                    self.assertEqual(broad['hasMore'][plural],selected['hasMore'][plural])
                    self.assertEqual(broad['hasMore'][plural],broad['totals'][plural]>page*size)
        self.assertNotEqual(broad['totals']['tools'],broad['totals']['skills'])

    def test_unsupported_filters_owners_and_older_snapshots_do_not_reuse_default(self):
        self.c.refresh_capabilities_cache();old=self.c.capabilities()['snapshot']
        for params,owner in [({'q':'skill=alpha'},None),({'kind':'user'},None),
                ({'after':1700000000},None),({'application':'codex'},None),
                ({'group':'request'},None),({},'alice')]:
            with self.subTest(params=params,owner=owner),self.trace() as statements:
                self.c.capabilities(params,owner)
                self.assertTrue(any('CREATE TEMP TABLE dc_matches' in sql for sql in statements))
        own=self.c.capabilities(owner='alice')
        self.assertNotIn('bob-only',{item['name'] for item in own['skills']})
        self.f.upload([self.f.event('new-skill','tool_call',name='Skill',payload={'arguments':{'skill':'gamma'}})])
        with self.c._db() as db:self.c._sync_batch(db);self.c._refresh_summaries(db)
        self.c.refresh_capabilities_cache()
        with self.trace() as statements:frozen=self.c.capabilities({'snapshot':old})
        self.assertEqual(frozen['totals']['skills'],3)
        self.assertTrue(any('CREATE TEMP TABLE dc_matches' in sql for sql in statements))

    def test_activity_sql_scope_and_source_changes_invalidate_before_background(self):
        self.c.refresh_capabilities_cache()
        with self.c._db() as db:
            db.execute("UPDATE dc_activity SET name='changed' WHERE category='skill' AND name='alpha'")
        with self.trace() as statements:result=self.c.capabilities()
        self.assertIn('changed',{item['name'] for item in result['skills']})
        self.assertTrue(any('CREATE TEMP TABLE dc_matches' in sql for sql in statements))
        self.c.refresh_capabilities_cache()
        with self.f.devices.connect() as db:db.execute("UPDATE devices SET revoked=1 WHERE owner='bob'")
        with self.trace() as statements:result=self.c.capabilities()
        self.assertNotIn('bob-only',{item['name'] for item in result['skills']})
        self.assertTrue(any('CREATE TEMP TABLE dc_matches' in sql for sql in statements))
        self.c.refresh_capabilities_cache()
        with self.c._db() as db:self.c._set_state(db,'session_raw_count',99)
        with self.trace() as statements:result=self.c.capabilities()
        self.assertFalse(result['coverage']['fullRetainedText'])
        self.assertTrue(any('CREATE TEMP TABLE dc_matches' in sql for sql in statements))

    def test_background_prefill_is_rate_limited_and_unchanged_cache_is_reused(self):
        self.c.refresh_capabilities_cache()
        with self.c._db() as db:
            before=json.loads(self.c._state(db,'capabilities_default_cache'))
            self.assertFalse(self.c._refresh_capabilities_cache(db))
            self.c._set_state(db,'capabilities_cache_attempted_at',0)
            statements=[];db.set_trace_callback(statements.append)
            self.assertFalse(self.c._refresh_capabilities_cache(db))
            db.set_trace_callback(None)
            self.assertFalse(any('CREATE TEMP TABLE dc_matches' in sql for sql in statements))
            self.assertEqual(json.loads(self.c._state(db,'capabilities_default_cache')),before)
