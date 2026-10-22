"""Legacy summary pages stay narrow and keep source and owner boundaries."""
from contextlib import contextmanager
import sqlite3
import unittest
from unittest.mock import patch

from . import test_data_center as fixtures


class LegacyModelDataTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.DataCenterTests();self.f.setUp();self.addCleanup(self.f.tearDown)

    def test_pages_match_search_order_and_labels_without_building_portraits(self):
        f=self.f
        f.upload([f.event('legacy-'+str(index),timestamp=1700000000+index,kind='tool_call' if index%3==0 else 'user_message',name='Bash',
                          payload={'arguments':{'cmd':'echo synthetic'},'content':'synthetic input'}) for index in range(45)])
        f.context('legacy-app','{"messages":[{"role":"user","content":"synthetic"}]}',timestamp=1700000005)
        device=f.identities['alice']['id']
        expected={}
        for collector in ('all','sessionlens','applens'):
            for page in (1,2):
                data=f.center.search({'collector':collector,'device':device,'page':page,'pageSize':20},owner='alice')
                expected[collector,page]=(data['total'],[(row['recordId'],row['kindLabel'],row['sessionId']) for row in data['items']])
        f.center._worker={'running':False,'error':None}
        with patch.object(f.center,'search',side_effect=AssertionError('Summary must not run full search')), \
             patch.object(f.center,'_item',side_effect=AssertionError('Summary must not build portraits')), \
             patch.object(f.devices,'list',side_effect=AssertionError('Summary must not load all device snapshots')):
            for collector in ('all','sessionlens','applens'):
                for page in (1,2):
                    with self.subTest(collector=collector,page=page):
                        data=f.center.legacy_page('alice',device,collector,page)
                        total,rows=expected[collector,page]
                        self.assertEqual(data['total'],total)
                        self.assertEqual([(row['id'],row['modelEvidence'],row['sessionName']) for row in data['calls']],rows)
                        self.assertEqual(data['hasMore'],page*20<total)
                        self.assertEqual(data['items'],[])
                        self.assertTrue(all('body' not in row for row in data['calls']))

    def test_summary_sql_cannot_read_large_metadata_or_text_and_uses_covering_index(self):
        f=self.f;f.upload([f.event('narrow-'+str(index),text='long synthetic context '*3000,timestamp=1700000000+index) for index in range(42)])
        f.find();f.center._worker={'running':False,'error':None}
        connect=f.center._db;queries=[]
        @contextmanager
        def narrow():
            with connect() as db:
                def authorize(action,table,column,database,trigger):
                    if action==sqlite3.SQLITE_READ and (table in ('dc_texts','dc_fields','dc_field_refs') or table=='dc_records' and column=='metadata'):
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize);db.set_trace_callback(queries.append)
                yield db
        with patch.object(f.center,'_db',narrow):
            data=f.center.legacy_page('alice',f.identities['alice']['id'],page=2)
        self.assertEqual(data['total'],42);self.assertEqual(len(data['calls']),20)
        statement=next(sql for sql in queries if sql.startswith('WITH candidates AS MATERIALIZED'))
        self.assertNotIn('NOT EXISTS(SELECT 1 FROM dc_inventory d',statement)
        self.assertFalse(any(sql.startswith('SELECT count(*) FROM dc_inventory') for sql in queries),queries)
        with connect() as db:
            f.center._scope(db,'alice')
            plan=[row['detail'] for row in db.execute('EXPLAIN QUERY PLAN '+statement)]
        self.assertTrue(any('COVERING INDEX dc_inventory_legacy_covering' in detail and 'owner=? AND device=? AND collector=?' in detail for detail in plan),plan)

    def test_cached_device_counts_equal_exact_fallback_for_each_owner_and_collector(self):
        f=self.f
        f.upload([f.event('count-alice-'+str(index),timestamp=1700000000+index) for index in range(24)])
        f.upload([f.event('count-bob-'+str(index)) for index in range(3)],owner='bob')
        f.context('count-app','synthetic app');f.find()
        f.center._worker={'running':False,'error':None}
        coverage=f.center._coverage_summary
        for owner in ('alice','bob'):
            for collector in ('all','sessionlens','applens'):
                for page in (1,2):
                    with self.subTest(owner=owner,collector=collector,page=page):
                        device=f.identities[owner]['id']
                        with patch.object(f.center,'_coverage_summary',side_effect=lambda db,scope_owner:dict(coverage(db,scope_owner),updatedAt=None)):
                            baseline=f.center.legacy_page(owner,device,collector,page)
                        self.assertEqual(f.center.legacy_page(owner,device,collector,page),baseline)

    def test_old_summary_format_revision_and_watermark_fall_back_to_exact_counts(self):
        f=self.f;f.upload([f.event('before')]);f.context('app-before','synthetic before');f.find()
        f.center._worker={'running':False,'error':None}
        device=f.identities['alice']['id'];coverage=f.center._coverage_summary
        with f.center._db() as db:
            _,old_snapshot=f.center._snapshot(db,{},'alice')
        def legacy_summary(db,owner):
            result=coverage(db,owner);result.pop('deviceSources',None);return result
        with patch.object(f.center,'_coverage_summary',side_effect=legacy_summary):
            self.assertEqual(f.center.legacy_page('alice',device)['total'],2)
        f.upload([f.event('after',timestamp=1700001000)])
        f.context('app-after','synthetic after',received=1700002000,timestamp=1700001001)
        with f.center._db() as db:f.center._sync_batch(db)
        # Source and direct SQL changes invalidate the old per-device counts.
        self.assertEqual(f.center.legacy_page('alice',device)['total'],4)
        with f.center._db() as db:f.center._refresh_summaries(db)
        with patch.object(f.center,'_snapshot',return_value=('synthetic-old-snapshot',old_snapshot)):
            frozen=f.center.legacy_page('alice',device)
        self.assertEqual(frozen['total'],2)
        self.assertEqual(f.center.legacy_page('alice',device)['total'],4)
        with f.center._db() as db:db.execute("DELETE FROM dc_records WHERE collector='applens'")
        self.assertEqual(f.center.legacy_page('alice',device)['total'],2)

    def test_merged_alias_duplicate_counts_and_pages_remain_exact(self):
        f=self.f
        alias=f.identities['alice']
        enrolled=f.devices.enroll(f.devices.pairing('alice')['code'],'Canonical synthetic')
        canonical=f.devices.identity(enrolled['token'])
        shared=f.event('merged-shared',timestamp=1700000001)
        f.upload([shared,f.event('alias-only',timestamp=1700000002)])
        f.upload([shared,f.event('canonical-only',timestamp=1700000003)],identity=canonical)
        f.find();f.devices.merge_registrations('alice',canonical['id'],[alias['id']]);f.find()
        f.center._worker={'running':False,'error':None};coverage=f.center._coverage_summary
        with f.center._db() as db:self.assertEqual(coverage(db,'alice')['duplicates'],1)
        for device in (alias['id'],canonical['id']):
            for page in (1,2):
                with patch.object(f.center,'_coverage_summary',side_effect=lambda db,owner:dict(coverage(db,owner),updatedAt=None)):
                    baseline=f.center.legacy_page('alice',device,page=page,size=2)
                actual=f.center.legacy_page('alice',device,page=page,size=2)
                self.assertEqual(actual,baseline);self.assertEqual(actual['total'],3)

    def test_device_alias_deduplication_and_owner_revocation_are_preserved(self):
        f=self.f;event=f.event('alias-original');f.upload([event]);f.upload([event],owner='bob');f.find()
        alias=f.identities['alice']['id']
        canonical=f.devices.enroll(f.devices.pairing('alice')['code'],'Canonical synthetic')['deviceId']
        f.devices.merge_registrations('alice',canonical,[alias]);f.find()
        for device in (alias,canonical):
            data=f.center.legacy_page('alice',device)
            self.assertEqual(data['total'],1);self.assertEqual(data['device']['id'],canonical)
        with self.assertRaises(PermissionError):f.center.legacy_page('bob',canonical)
        f.devices.revoke(canonical,'alice')
        with self.assertRaises(PermissionError):f.center.legacy_page('alice',alias)


class LegacySummaryAPITests(unittest.TestCase):
    def test_missing_collector_matches_explicit_all_and_page_two_is_summary_only(self):
        fixture=fixtures.DataCenterAPITests();fixture.setUp();self.addCleanup(fixture.tearDown)
        sessions=fixtures.SessionStore(fixture.root/'session-lens.db')
        sessions.ingest(fixture.identity,{'schemaVersion':1,'events':[{'schemaVersion':1,'id':format(index+10,'064x'),'source':'codex','sessionId':'api-session','kind':'user_message','timestamp':1700000001+index,'payload':{'content':'synthetic'},'evidence':{}} for index in range(25)]})
        fixtures.wait_for_background_index(fixture.root)
        base='/api/audit/model-data/'+fixture.identity['id']+'?summary=1'
        with patch('agentpair.data_center.DataCenter.search',side_effect=AssertionError('Legacy summary cannot run search')):
            status,implicit=fixture.get(base)
            explicit_status,explicit=fixture.get(base+'&collector=all')
            page_status,second=fixture.get(base+'&page=2')
        self.assertEqual((status,explicit_status,page_status),(200,200,200))
        self.assertEqual(implicit['calls'],explicit['calls']);self.assertEqual(implicit['total'],26)
        self.assertEqual(len(second['calls']),6);self.assertFalse(second['hasMore'])
        self.assertFalse({row['id'] for row in implicit['calls']} & {row['id'] for row in second['calls']})
        self.assertEqual(second['items'],[])


if __name__=='__main__':unittest.main()
