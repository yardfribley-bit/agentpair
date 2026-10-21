"""Request-local literal hit reuse preserves the uncached query semantics."""
from contextlib import contextmanager
import json
import re
import unittest
from unittest.mock import patch

from . import test_data_center as fixtures


class LiteralHitTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.DataCenterTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.c=self.f.center
        self.f.upload([
            self.f.event('alice-password',text='PASSWORD 中文子串 Straße 192.0.2.1 example.invalid'),
            self.f.event('alice-other',text='alpha only and to; badexample.invalidx 1192.0.2.10'),
            self.f.event('alice-reply',kind='message',role='assistant',text='password reply unique'),
            self.f.event('alice-call',kind='tool_call',name='Read',payload={'arguments':{'path':'/password/project'}}),
            self.f.event('alice-quotes',text='quoted "needle" literal')])
        self.f.upload([self.f.event('bob-password',text='password bob-only Straße')],owner='bob')
        self.f.context('app-shared',json.dumps({'messages':[
            {'role':'system','content':'fixed background'},
            {'role':'user','content':'password 中文子串'},
            {'role':'assistant','content':'STRASSE reply'}]}))
        self.f.find()
        # Fixtures are committed; reads do not race or re-run ingestion.
        self.c._worker={'running':False,'error':None}

    def compare(self,params,owner=None):
        with patch.object(self.c,'_materialize_literal_hits',return_value=False):
            baseline=self.c.search(params,owner)
        actual=self.c.search(params,owner)
        self.assertEqual(actual,baseline)
        return actual

    def without_zero_duplicate_proof(self,params,owner=None):
        query_scope=self.c._query_scope;coverage=self.c._coverage_summary
        def original_duplicate_check(*args,**kwargs):
            def pending(db,scope_owner):
                return dict(coverage(db,scope_owner),updatedAt=None)
            # Disable the optimization only while constructing its predicate;
            # response coverage still uses the real committed summary.
            with patch.object(self.c,'_coverage_summary',side_effect=pending):
                return query_scope(*args,**kwargs)
        with patch.object(self.c,'_query_scope',side_effect=original_duplicate_check):
            return self.c.search(params,owner)

    def candidate(self,statements):
        candidates=[sql for sql in statements if sql.startswith('INSERT INTO dc_matches SELECT')]
        self.assertEqual(len(candidates),1)
        return candidates[0]

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

    def test_boolean_unicode_location_and_owner_match_unmaterialized_results(self):
        queries=['password','PASSWORD password || strasse || Straße','password && 中文子串',
            'password && kind!=user','kind=user || absent-literal',
            'password && ip!=192.0.2.1','password || domain=example.invalid.',
            'ip=192.0.2.1 || domain=example.invalid.','192.0.2.1 || password','to || 中文子串',
            "'quoted \"needle\"'"]
        for owner in (None,'alice','bob'):
            for location in ('all','user','arguments','reply'):
                for query in queries:
                    with self.subTest(owner=owner,location=location,query=query):
                        self.compare({'q':query,'location':location,'pageSize':2},owner)
        self.assertEqual(self.compare({'q':'bob-only'},'alice')['total'],0)
        self.assertEqual(self.compare({'q':'bob-only'},'bob')['total'],1)

    def test_distinct_casefolded_terms_each_open_global_fts_once(self):
        with self.trace() as statements:
            result=self.c.search({'q':'PASSWORD password || strasse || Straße'})
        self.assertGreater(len(result['items']),1)
        fts=[sql for sql in statements if re.search(r'\bdc_text_fts\s+MATCH\b',sql,re.I)]
        self.assertEqual(len(fts),2,fts)
        self.assertTrue(all(sql.startswith('INSERT INTO dc_literal_hits ') for sql in fts),fts)
        self.assertTrue(any('h.term=' in sql and 'dc_snippet' in sql for sql in statements))

    def test_group_previews_reuse_literals_without_page_fts_queries(self):
        params={'q':'password || strasse','group':'request','pageSize':2}
        expected=self.compare(params)
        with self.trace() as statements:actual=self.c.search(params)
        self.assertEqual(actual,expected)
        fts=[sql for sql in statements if re.search(r'\bdc_text_fts\s+MATCH\b',sql,re.I)]
        self.assertEqual(len(fts),2,fts)
        self.assertTrue(all(sql.startswith('INSERT INTO dc_literal_hits ') for sql in fts),fts)

    def test_snapshots_exclude_late_rows_but_current_search_and_paging_see_them(self):
        params={'q':'password || strasse','pageSize':2}
        first=self.compare(params)
        self.f.upload([self.f.event('late-password',text='password fresh-only',timestamp=1700001000)])
        self.f.context('late-app',json.dumps({'messages':[{'role':'user','content':'password fresh-only'}]}),received=1700002000)
        with self.c._db() as db:self.c._sync_batch(db);self.c._refresh_summaries(db)
        frozen=self.compare({**params,'snapshot':first['snapshot']})
        self.assertEqual(frozen['total'],first['total'])
        self.assertEqual([item['id'] for item in frozen['items']],[item['id'] for item in first['items']])
        second=self.compare({**params,'snapshot':first['snapshot'],'page':2})
        self.assertFalse({item['id'] for item in first['items']} & {item['id'] for item in second['items']})
        self.assertEqual(self.compare(params)['total'],first['total']+2)

    def test_hits_do_not_survive_a_request_failure_or_different_owner_query(self):
        with patch.object(self.c,'_item',side_effect=RuntimeError('fixture projection failure')):
            with self.assertRaisesRegex(RuntimeError,'fixture projection failure'):
                self.c.search({'q':'password'})
        self.assertEqual(self.compare({'q':'bob-only'},'alice')['total'],0)
        self.assertEqual(self.compare({'q':'alpha'},'alice')['total'],1)
        self.assertEqual(self.compare({'q':'no-match-literal'})['total'],0)
        with self.c._db() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_temp_master WHERE name='dc_literal_hits'").fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='dc_literal_hits'").fetchone())
        self.assertEqual(self.compare({'q':'bob-only'},'bob')['total'],1)

    def test_current_zero_duplicate_proof_skips_checks_without_cross_owner_visibility(self):
        for owner in (None,'alice','bob'):
            with self.subTest(owner=owner):
                params={'q':'password || bob-only'}
                baseline=self.without_zero_duplicate_proof(params,owner)
                with self.trace() as statements:actual=self.c.search(params,owner)
                self.assertEqual(actual,baseline)
                self.assertNotIn('NOT EXISTS(SELECT 1 FROM dc_inventory d',self.candidate(statements))
                if owner is not None:self.assertTrue(all(item['ownerAccount']==owner for item in actual['items']))
        self.assertEqual(self.c.search({'q':'bob-only'},'alice')['total'],0)
        self.assertEqual(self.c.search({'q':'bob-only'},'bob')['total'],1)
        with self.f.devices.connect() as db:db.execute("UPDATE devices SET revoked=1 WHERE owner='alice'")
        with self.trace() as statements:revoked=self.c.search({'q':'password'},'alice')
        self.assertEqual(revoked['total'],0)
        self.assertIn('NOT EXISTS(SELECT 1 FROM dc_inventory d',self.candidate(statements))
        self.assertEqual(self.c.search({'q':'bob-only'},'bob')['total'],1)

    def test_adding_an_alias_duplicate_restores_the_original_check(self):
        event=self.f.event('new-duplicate-proof',text='new-duplicate-proof canonical')
        self.f.upload([event])
        with self.c._db() as db:self.c._sync_batch(db);self.c._refresh_summaries(db)
        params={'q':'new-duplicate-proof'}
        with self.trace() as statements:self.assertEqual(self.c.search(params)['total'],1)
        self.assertNotIn('NOT EXISTS(SELECT 1 FROM dc_inventory d',self.candidate(statements))
        enrolled=self.f.devices.enroll(self.f.devices.pairing('alice')['code'],'Duplicate installation')
        alias=self.f.devices.identity(enrolled['token'])
        self.f.upload([event],identity=alias)
        self.f.devices.merge_registrations('alice',self.f.identities['alice']['id'],[alias['id']])
        with self.c._db() as db:self.c._sync_batch(db);self.c._refresh_summaries(db)
        baseline=self.without_zero_duplicate_proof(params)
        with self.trace() as statements:actual=self.c.search(params)
        self.assertEqual(actual,baseline)
        self.assertEqual(actual['total'],1)
        self.assertEqual(actual['items'][0]['sourceDeviceId'],self.f.identities['alice']['id'])
        self.assertGreater(actual['coverage']['duplicateAliasRecords'],0)
        self.assertIn('NOT EXISTS(SELECT 1 FROM dc_inventory d',self.candidate(statements))

    def test_stale_revision_and_old_snapshot_cannot_reuse_zero_duplicate_proof(self):
        params={'q':'password'};first=self.c.search(params)
        with self.c._db() as db:db.execute('UPDATE dc_records SET title=title WHERE key=(SELECT key FROM dc_records LIMIT 1)')
        baseline=self.without_zero_duplicate_proof(params)
        with self.trace() as statements:stale=self.c.search(params)
        self.assertEqual(stale,baseline)
        self.assertEqual(stale['total'],first['total'])
        self.assertIn('NOT EXISTS(SELECT 1 FROM dc_inventory d',self.candidate(statements))
        self.f.upload([self.f.event('late-proof',text='password late proof',timestamp=1700001000)])
        with self.c._db() as db:self.c._sync_batch(db);self.c._refresh_summaries(db)
        frozen_params={**params,'snapshot':first['snapshot']}
        baseline=self.without_zero_duplicate_proof(frozen_params)
        with self.trace() as statements:frozen=self.c.search(frozen_params)
        self.assertEqual(frozen,baseline)
        self.assertEqual(frozen['total'],first['total'])
        self.assertEqual([item['id'] for item in frozen['items']],[item['id'] for item in first['items']])
        self.assertIn('NOT EXISTS(SELECT 1 FROM dc_inventory d',self.candidate(statements))
        with self.trace() as statements:current=self.c.search(params)
        self.assertEqual(current['total'],first['total']+1)
        self.assertNotIn('NOT EXISTS(SELECT 1 FROM dc_inventory d',self.candidate(statements))

    def test_alias_inventory_digest_reuses_covering_row_without_changing_deduplication(self):
        aliases=[]
        for name in ('Previous installation A','Previous installation B'):
            enrolled=self.f.devices.enroll(self.f.devices.pairing('alice')['code'],name)
            aliases.append(self.f.devices.identity(enrolled['token']))
        canonical=self.f.event('inventory-canonical-dup',text='inventory-alias-needle canonical')
        alias_only=self.f.event('inventory-alias-only',text='inventory-alias-needle alias-only')
        self.f.upload([canonical])
        self.f.upload([canonical,alias_only],identity=aliases[0])
        self.f.upload([alias_only],identity=aliases[1])
        self.f.devices.merge_registrations('alice',self.f.identities['alice']['id'],[alias['id'] for alias in aliases])
        with self.c._db() as db:self.c._sync_batch(db);self.c._refresh_summaries(db)
        params={'q':'inventory-alias-needle'}
        with patch.object(self.c,'_inventory_predicate',side_effect=lambda value:value):
            baseline=self.c.search(params,owner='alice')
        original=self.c._db;candidate_sql=[];plans=[]
        @contextmanager
        def capture_candidate():
            with original() as db:
                statements=[];db.set_trace_callback(statements.append)
                try:yield db
                finally:
                    db.set_trace_callback(None)
                    candidate_sql.extend(sql for sql in statements if sql.startswith('INSERT INTO dc_matches SELECT'))
                    for sql in candidate_sql:plans.extend(row[3] for row in db.execute('EXPLAIN QUERY PLAN '+sql))
        with patch.object(self.c,'_db',capture_candidate):actual=self.c.search(params,owner='alice')
        self.assertEqual(actual,baseline)
        self.assertEqual(actual['total'],2)
        sources={item['recordId']:item['sourceDeviceId'] for item in actual['items']}
        self.assertEqual(sources['sessionlens:'+canonical['id']],self.f.identities['alice']['id'])
        self.assertEqual(sources['sessionlens:'+alias_only['id']],min(alias['id'] for alias in aliases))
        self.assertEqual(len(candidate_sql),1)
        self.assertIn('NOT EXISTS(SELECT 1 FROM dc_inventory d',candidate_sql[0])
        self.assertNotRegex(candidate_sql[0],r'SELECT\s+digest\s+FROM\s+dc_inventory\s+WHERE\s+key\s*=\s*r\.key')
        self.assertTrue(any('COVERING INDEX dc_inventory_key_covering' in step for step in plans),plans)
        self.assertFalse(any('SEARCH dc_inventory USING' in step for step in plans),plans)
