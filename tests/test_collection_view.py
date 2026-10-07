import json
import tempfile
import unittest
from pathlib import Path
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore
from agentpair.collection_view import CollectionView

class CollectionViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.devices=DeviceStore(root/'devices.db');self.sessions=SessionStore(root/'sessions.db');self.view=CollectionView(self.devices,self.sessions)
        self.device=self.devices.enroll(self.devices.pairing('alice')['code'],'Test Mac')
        self.identity=self.devices.identity(self.device['token'])
        self.event={'schemaVersion':1,'id':'a'*64,'source':'codex','sessionId':'session-1','kind':'tool_call','timestamp':1700000000,'payload':{'name':'curl','url':'https://example.com'},'evidence':{'path':'local.jsonl','line':1}}
        self.sessions.ingest(self.identity,{'schemaVersion':1,'events':[self.event]})
    def tearDown(self):self.tmp.cleanup()
    def test_session_only_device_and_readable_record(self):
        items=self.view.inventory(self.devices.list('alice'),'alice')
        self.assertEqual(len(items),1);self.assertEqual(items[0]['collectors'][0]['id'],'sessionlens')
        data=self.view.model_data('alice',self.identity['id'])
        self.assertEqual(data['calls'][0]['application'],'Codex');self.assertEqual(data['items'][0]['category'],'工具调用')
        self.assertIn('https://example.com',data['items'][0]['rawContent']);self.assertFalse(data['calls'][0]['complete'])
    def test_summary_detail_and_collector_filter(self):
        data=self.view.model_data('alice',self.identity['id'],summary=True)
        self.assertNotIn('body',data['calls'][0]);self.assertEqual(data['items'],[])
        detail=self.view.model_data('alice',self.identity['id'],request=data['calls'][0]['id'])
        self.assertEqual(detail['calls'][0]['evidence'],self.event['evidence'])
        self.assertEqual(self.view.model_data('alice',self.identity['id'],collector='applens')['calls'],[])
    def test_owner_isolation(self):
        with self.assertRaises(PermissionError):self.view.model_data('bob',self.identity['id'])
    def test_both_collectors_without_duplicate_asset(self):
        record={'id':'request-1','source':'workbuddy_generation','timestamp':1700000001,'body':'{"messages":[{"role":"user","content":"hello"}]}'}
        with self.devices.connect() as db:db.execute('INSERT INTO applens_model_context VALUES(?,?,?,?)',(self.identity['id'],'request-1',json.dumps(record),1700000001))
        items=self.view.inventory(self.devices.list('alice'),'alice');self.assertEqual(len(items),1)
        self.assertEqual({c['id'] for c in items[0]['collectors']},{'applens','sessionlens'})
        data=self.view.model_data('alice',self.identity['id']);self.assertEqual({c['collector'] for c in data['calls']},{'applens','sessionlens'})
