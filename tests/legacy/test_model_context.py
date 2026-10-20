import unittest,json,tempfile
from pathlib import Path
from agentpair.model_context import context_items
from agentpair.devices import DeviceStore
class ContextTests(unittest.TestCase):
    def test_raw_content_preserved_and_not_network_claim(self):
        request={'id':'a'*64,'source':'workbuddy_generation_context','body':json.dumps([{'role':'system','content':'SOUL.md\nUSER.md\nunchanged=abc'}])}
        items=context_items(request)
        self.assertIn('unchanged=abc',items[0]['rawContent']);self.assertFalse(items[0]['sourceVerified'])
        with tempfile.TemporaryDirectory() as t:
            store=DeviceStore(Path(t)/'d.db');d=store.enroll(store.pairing()['code'],'test')
            store.ingest_model_context(d['token'],{'requests':[request]})
            self.assertEqual(len(store.llm_data('admin',d['deviceId'])['items']),1)
            with self.assertRaises(PermissionError):store.llm_data('other',d['deviceId'])

    def test_named_heading_does_not_consume_unrelated_section(self):
        text='prefix\n## USER.md\nprofile\n## Other\nunknown\n## SOUL.md\nrules'
        r={'id':'a'*64,'body':json.dumps([{'role':'user','content':[{'type':'text','text':text}]}])}
        items=context_items(r)
        self.assertEqual(''.join(i['rawContent'] for i in items),text)
        self.assertEqual(next(i['rawContent'] for i in items if i['name']=='USER.md'),'## USER.md\nprofile\n')
        self.assertTrue(any('unknown' in i['rawContent'] and i['category']=='未分类内容' for i in items))

    def test_receipt_dedup_and_bad_digest(self):
        with tempfile.TemporaryDirectory() as t:
            store=DeviceStore(Path(t)/'d.db');d=store.enroll(store.pairing()['code'],'test')
            r={'id':'b'*64,'source':'workbuddy_generation_context','body':'original'}
            receipt=store.ingest_model_context(d['token'],{'requests':[r]})
            self.assertEqual(receipt['receipts'][0]['bodyBytes'],8)
            store.ingest_model_context(d['token'],{'requests':[r]})
            self.assertEqual(len(store.llm_data('admin',d['deviceId'])['calls']),1)
            with self.assertRaises(ValueError):store.ingest_model_context(d['token'],{'requests':[{**r,'bodySHA256':'bad'}]})

class LazyContextTests(unittest.TestCase):
    def test_summary_contains_no_body_and_detail_is_exact(self):
        with tempfile.TemporaryDirectory() as t:
            store=DeviceStore(Path(t)/'d.db');d=store.enroll(store.pairing()['code'],'test')
            records=[{'id':c*64,'source':'workbuddy_network_context','body':json.dumps({'messages':[{'role':'user','content':'content '+c}]}),'sessionId':c,'sessionName':'session '+c} for c in ('a','b')]
            store.ingest_model_context(d['token'],{'requests':records})
            summary=store.llm_data('admin',d['deviceId'],summary=True)
            self.assertEqual(len(summary['calls']),2);self.assertFalse(summary['items']);self.assertTrue(all('body' not in c for c in summary['calls']))
            detail=store.llm_data('admin',d['deviceId'],request_id='b'*64)
            self.assertEqual([c['id'] for c in detail['calls']],['b'*64]);self.assertTrue(all(i['requestId']=='b'*64 for i in detail['items']))
            self.assertEqual(detail['calls'][0]['body'],records[1]['body'])
            with self.assertRaises(ValueError):store.llm_data('admin',d['deviceId'],request_id='f'*64)
            with self.assertRaises(PermissionError):store.llm_data('other',d['deviceId'],summary=True)
