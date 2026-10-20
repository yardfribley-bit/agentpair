"""Canonical fast-path must retain evidence and avoid unnecessary JSON parsing."""
import json
import sqlite3
import unittest
from contextlib import closing

from agentpair.data_center import DUPLICATES, DataCenter, parse_query
from tests import test_data_center as fixtures


class DataCenterDedupReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DataCenterTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def alias(self, label):
        f = self.fixture
        enrolled = f.devices.enroll(f.devices.pairing('alice')['code'], label)
        return f.devices.identity(enrolled['token'])

    def test_alias_only_identical_evidence_uses_one_alias_then_prefers_canonical(self):
        f = self.fixture
        aliases = [self.alias('Previous installation A'), self.alias('Previous installation B')]
        event = f.event('shared-alias-only', text='alias-only-review')
        for alias in aliases:
            f.upload([event], identity=alias)
        f.devices.merge_registrations('alice', f.identities['alice']['id'], [a['id'] for a in aliases])
        found = f.find('alias-only-review')
        self.assertEqual(found['total'], 1)
        self.assertEqual(found['items'][0]['sourceDeviceId'], min(a['id'] for a in aliases))
        f.upload([event])
        found = f.find('alias-only-review')
        self.assertEqual(found['total'], 1)
        self.assertEqual(found['items'][0]['sourceDeviceId'], f.identities['alice']['id'])

    def test_alias_conflicting_digest_is_not_lost_to_canonical_or_other_alias(self):
        f = self.fixture
        aliases = [self.alias('Conflicting installation A'), self.alias('Conflicting installation B')]
        canonical = f.event('shared-conflicting-id', text='dedup-original-canonical')
        f.upload([canonical])
        for index, alias in enumerate(aliases):
            f.upload([{**canonical, 'payload': {'content': 'dedup-conflicting-alias-' + str(index)}}], identity=alias)
        f.devices.merge_registrations('alice', f.identities['alice']['id'], [a['id'] for a in aliases])
        found = f.find('dedup-')
        self.assertEqual(found['total'], 3)
        self.assertEqual({f.center.raw(item['id'])['payload']['content'] for item in found['items']},
                         {'dedup-original-canonical', 'dedup-conflicting-alias-0', 'dedup-conflicting-alias-1'})

    def test_other_canonical_machine_or_owner_cannot_remove_alias_evidence(self):
        f = self.fixture
        alias = self.alias('Old installation')
        other = self.alias('Different physical machine')
        event = f.event('same-id-separate-scope', text='separate-device-owner-review')
        f.upload([event], identity=alias)
        f.upload([event], identity=other)
        f.upload([event], owner='bob')
        f.devices.merge_registrations('alice', f.identities['alice']['id'], [alias['id']])
        found = f.find('separate-device-owner-review')
        self.assertEqual(found['total'], 3)
        self.assertEqual({item['deviceId'] for item in found['items']},
                         {f.identities['alice']['id'], other['id'], f.identities['bob']['id']})
        self.assertEqual(f.center.search({'q': 'separate-device-owner-review'}, owner='alice')['total'], 2)
        selected = f.find('separate-device-owner-review', device=alias['id'])
        self.assertEqual(selected['total'], 1)
        self.assertEqual(selected['items'][0]['sourceDeviceId'], alias['id'])

    def test_canonical_rows_and_alias_self_candidates_do_not_parse_digest(self):
        # Count evaluations instead of imposing a machine-dependent timing threshold.
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE dc_records(collector TEXT,owner TEXT,ident TEXT,source TEXT,device TEXT,metadata TEXT)')
            db.execute('CREATE TABLE dc_scope(device TEXT,owner TEXT,canonical TEXT)')
            db.executemany('INSERT INTO dc_scope VALUES(?,?,?)', [('main','alice','main'), ('old','alice','main')])
            metadata = json.dumps({'recordDigest': 'same-digest', 'largePayload': 'x' * 20000})
            db.executemany('INSERT INTO dc_records VALUES(?,?,?,?,?,?)',
                           [('sessionlens','alice','canonical-only','codex','main',metadata),
                            ('sessionlens','alice','alias-only','codex','old',metadata)])
            evaluations = []

            def extract(value, path):
                evaluations.append(path)
                return json.loads(value)['recordDigest']

            db.create_function('json_extract', 2, extract)
            retained = db.execute('SELECT r.ident FROM dc_records r JOIN dc_scope s '
                                  'ON s.device=r.device AND s.owner=r.owner WHERE ' + DUPLICATES).fetchall()
            self.assertEqual({row[0] for row in retained}, {'canonical-only','alias-only'})
            self.assertEqual(evaluations, [], 'Neither a canonical row nor an alias itself can be its suppressing duplicate')

    def test_tool_lookup_is_additive_idempotent_and_used_for_structured_search(self):
        f = self.fixture
        f.upload([f.event('indexed-exec', 'tool_call', name='exec', payload={'arguments':'text("fixture");'}),
                  f.event('indexed-bash', 'tool_call', name='Bash', payload={'arguments':{'cmd':'pwd'}})])
        before = f.find('tool=exec')
        reopened = DataCenter(f.center.collections)
        self.assertEqual(reopened.search({'q':'tool=exec'})['items'][0]['id'], before['items'][0]['id'])
        with reopened._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dc_records').fetchone()[0], 2)
            plan = [row[3] for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT key FROM dc_records WHERE lower(json_extract(metadata,'$.tool'))=lower(?) "
                'AND collector=? AND seq<=?', ('exec','sessionlens',100))]
        self.assertTrue(any('dc_records_tool_lookup' in step and '<expr>=?' in step for step in plan), plan)

    def capability_events(self):
        f = self.fixture
        request = f.event('cap-request', payload={'id':'origin','content':'设计首页并检查天气'})
        calls = [
            f.event('cap-taste','tool_call',name='Skill',timestamp=1700000001,
                    payload={'parentId':'origin','arguments':{'skill':'taste-skill'}}),
            f.event('cap-other','tool_call',name='Skill',timestamp=1700000002,
                    payload={'parentId':'origin','arguments':{'skill':'other-skill'}}),
            f.event('cap-mcp','tool_call',name='mcp__browser__open',timestamp=1700000003,
                    payload={'parentId':'origin','arguments':{'url':'https://example.test'}}),
            f.event('cap-empty','tool_call',name='exec',timestamp=1700000004,
                    payload={'parentId':'origin','arguments':'text("empty capabilities");'})]
        f.upload([request]+calls)
        return request,calls

    def test_sparse_candidates_keep_mixed_or_negatives_missing_and_empty_capabilities(self):
        f = self.fixture
        request,calls = self.capability_events()
        f.find()  # Index the fixture, then exercise pre-existing missing metadata too.
        with f.center._db() as db:
            row = db.execute('SELECT key,metadata FROM dc_records WHERE ident=?',(request['id'],)).fetchone()
            metadata = json.loads(row['metadata']);metadata.pop('capabilities')
            db.execute('UPDATE dc_records SET metadata=? WHERE key=?',(json.dumps(metadata),row['key']))
        expected = lambda events: {'sessionlens:'+event['id'] for event in events}
        cases = [
            ('skill=taste-skill', [calls[0]]),
            ('skill=taste-skill || tool=exec', [calls[0],calls[3]]),
            ('skill!=taste-skill', [request,*calls[1:]]),
            ('mcp!=browser', [request,calls[0],calls[1],calls[3]]),
            ('skill=taste-skill || mcp=browser && mcp_method=open', [calls[0],calls[2]]),
            ('skill=taste-skill && tool=exec || skill=other-skill', [calls[1]])]
        for query,events in cases:
            with self.subTest(query=query):
                records = f.find(query)
                self.assertEqual({item['recordId'] for item in records['items']},expected(events))
                grouped = f.find(query,group='request')
                self.assertEqual(grouped['recordTotal'],len(events))
                self.assertEqual({item['recordId'] for group in grouped['groups'] for item in group['items']},expected(events))
        self.assertEqual(f.find('skill!=taste-skill',object='skill')['total'],1)
        self.assertEqual(f.find('mcp!=unknown',object='mcp')['total'],1)
        self.assertEqual(f.center.capabilities({'q':'skill=taste-skill || mcp=browser'})['coverage']['activityRecords'],2)

    def test_sparse_hint_requires_capability_in_every_or_branch(self):
        center = self.fixture.center
        for query,required in [
            ('skill=taste-skill',True), ('skill=taste-skill || mcp=browser',True),
            ('mcp_method=open && tool=exec',True), ('skill!=taste-skill',False),
            ('skill=taste-skill || tool=exec',False), ('skill=taste-skill || keyword',False),
            ('tool=exec',False), ('',False)]:
            with self.subTest(query=query):
                self.assertEqual(center._requires_capabilities({},parse_query(query)),required)
        self.assertTrue(center._requires_capabilities({'object':'skill'},parse_query('skill!=taste-skill')))

    def test_inventory_sparse_and_materialized_group_plans(self):
        f = self.fixture
        self.capability_events()
        with f.center._db() as db:
            f.center._sync(db);f.center._scope(db)
            values,page,size,dates = f.center._validate({'q':'skill=taste-skill','group':'request'})
            groups = parse_query(values['q'])
            token,snapshot = f.center._snapshot(db,values,None)
            statements = [];db.set_trace_callback(statements.append)
            join,predicate,args,count = f.center._query_scope(db,values,dates,groups,snapshot)
            db.set_trace_callback(None)
            self.assertEqual(count,5)
            self.assertNotIn('INDEXED BY',join,'The common join must remain composable for hit and group member lookups')
            scope_sql = next(sql for sql in statements if sql.startswith('SELECT count(*)'))
            scope_plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN '+scope_sql)]
            self.assertTrue(any('dc_inventory' in step for step in scope_plan),scope_plan)
            facet_plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN SELECT DISTINCT r.source'+f.center._inventory_join(join))]
            self.assertTrue(any('dc_inventory' in step for step in facet_plan),facet_plan)
            candidate_sql = 'SELECT r.key'+f.center._candidate_join(join,values,groups)+predicate
            candidate_plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN '+candidate_sql,args)]
            self.assertTrue(any('dc_records_capability_candidates' in step for step in candidate_plan),candidate_plan)
            self.assertFalse(any('dc_records_physical' in step and ' r ' in step for step in candidate_plan),candidate_plan)
            db.execute('CREATE TEMP TABLE dc_matches(key TEXT PRIMARY KEY)')
            db.execute('INSERT INTO dc_matches '+candidate_sql,args)
            matched_join = f.center._matched_join(join)
            match_plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN SELECT r.key'+matched_join)]
            self.assertTrue(match_plan[0].startswith('SCAN m'),match_plan)
            self.assertTrue(match_plan[1].startswith('SEARCH r USING INDEX sqlite_autoindex_dc_records_1'),match_plan)
            f.center._request_groups(db,matched_join,snapshot)
            group_id = db.execute('SELECT group_id FROM dc_request_groups').fetchone()[0]
            member_plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN SELECT r.key'+f.center._members_join(join),(group_id,))]
            self.assertTrue(member_plan[0].startswith('SEARCH m USING INDEX dc_request_members_group'),member_plan)
            self.assertTrue(member_plan[1].startswith('SEARCH r USING INDEX sqlite_autoindex_dc_records_1'),member_plan)


if __name__ == '__main__':
    unittest.main()
