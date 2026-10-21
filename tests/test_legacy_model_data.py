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

    def test_summary_sql_cannot_read_large_metadata_or_text_and_uses_scope_index(self):
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
        with connect() as db:
            f.center._scope(db,'alice')
            plan=[row['detail'] for row in db.execute('EXPLAIN QUERY PLAN '+statement)]
        self.assertTrue(any('dc_inventory_scope' in detail and 'owner=? AND device=? AND collector=?' in detail for detail in plan),plan)

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
