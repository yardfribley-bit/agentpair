import json,tempfile,unittest
from pathlib import Path
from agentpair.interaction_audit import build_audit,redact
from agentpair.model_security import analyze_request
from agentpair.devices import DeviceStore

class AuditTests(unittest.TestCase):
    def req(self,messages,**extra):
        return {'id':'a'*64,'source':'workbuddy_network_context','body':json.dumps(messages,ensure_ascii=False),'timestamp':1,**extra}
    def audit(self,r):return build_audit({'id':'dev','os':'Windows'},r)
    def test_user_query_separated_from_agent_context_and_request_metadata(self):
        content='<system-reminder><project_context>附加项目内容</project_context></system-reminder>\n<user_query>只读排查，不修改文件。\n给出原因。</user_query>'
        body={'model':'test-model','stream':True,'tools':[{'type':'function','function':{'name':'read_file'}}],'messages':[{'role':'user','content':content}]}
        a=self.audit(self.req(body))
        self.assertEqual(a['task']['text'],'只读排查，不修改文件。\n给出原因。')
        self.assertEqual(a['model'],'test-model')
        self.assertEqual(a['requestOverview']['toolNames'],['read_file'])
        self.assertIn('project_context',a['events'][0]['attachedContextTags'])
        offset=a['events'][0]['userQueryOffsets'][0]
        self.assertEqual(content[offset['start']:offset['end']],'只读排查，不修改文件。\n给出原因。')
        plain=self.audit(self.req([{'role':'user','content':'普通文本'}]))
        self.assertIsNone(plain['events'][0]['userQuery'])

    def test_history_not_current_response_or_execution(self):
        a=self.audit(self.req([{'role':'user','content':'只读排查'}, {'role':'assistant','content':'检查配置','tool_calls':[{'function':{'name':'write_file','arguments':'{}'}}]}, {'role':'tool','content':'success'}]))
        self.assertEqual([e['role'] for e in a['events']],['user','assistant','tool'])
        self.assertTrue(all(not e['executionVerified'] and not e['currentResponse'] for e in a['events']))
        self.assertFalse(a['coverage']['currentResponse'])
        self.assertFalse(next(r for r in a['rules'] if r['id']=='A-01')['enabled'])
    def test_mask_secret_and_correct_decoded_location(self):
        secret='sk-abcdefghijklmnopqrstuvwx'
        r=self.req({'messages':[{'role':'user','content':'hello '+secret+' 中文'}]})
        a=self.audit(r);self.assertNotIn(secret,json.dumps(a));f=a['events'][0]['findings'][0]
        self.assertEqual(f['jsonPointer'],'/messages/0/content')
        self.assertEqual(('hello '+secret+' 中文')[f['charStart']:f['charEnd']],secret)
        self.assertIn('[已隐藏]',a['events'][0]['content']['text'])
    def test_json_secret_fields_and_placeholder(self):
        r=self.req({'password':'actual_value_29384','token':'example_token'})
        a=analyze_request({'id':'dev','os':'macOS'},r)
        self.assertEqual(a['ruleCounts']['credential'],1)
        self.assertNotIn('actual_value_29384',json.dumps(self.audit(r)))
        self.assertNotIn('abcdEFGH',redact('-----BEGIN PRIVATE KEY-----\nabcdEFGH\n-----END PRIVATE KEY-----'))
    def test_truncated_unknown_shapes_and_non_text(self):
        a=self.audit(self.req([],body='[{"role":"user","content":"broken',truncated=True))
        self.assertFalse(a['request']['jsonParseable']);self.assertFalse(a['events']);self.assertIsNotNone(a['unparsed'])
        a=self.audit(self.req([{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,abcdef'}}]}]))
        self.assertNotIn('base64',a['events'][0]['content']['text'])
    def test_default_uses_parseable_snapshot_but_explicit_choice_is_preserved(self):
        with tempfile.TemporaryDirectory() as t:
            store=DeviceStore(Path(t)/'d.db');d=store.enroll(store.pairing()['code'],'test')
            store.ingest_model_context(d['token'],{'requests':[self.req([{'role':'assistant','content':'history'}]),self.req([],id='b'*64,timestamp=2,body='[{broken',truncated=True)]})
            data=store.interaction_audit('admin',d['deviceId'])
            self.assertTrue(data['selectionNotice']);self.assertEqual(data['audit']['request']['id'],'a'*64)
            explicit=store.interaction_audit('admin',d['deviceId'],'b'*64)
            self.assertFalse(explicit['selectionNotice']);self.assertFalse(explicit['audit']['events'])

    def test_owner_scope_and_snapshot_isolation(self):
        with tempfile.TemporaryDirectory() as t:
            store=DeviceStore(Path(t)/'d.db');d=store.enroll(store.pairing('alice')['code'],'test')
            store.ingest_model_context(d['token'],{'requests':[self.req([{'role':'user','content':'first'}]),self.req([{'role':'user','content':'second'}],id='b'*64,timestamp=2)]})
            a=store.interaction_audit('alice',d['deviceId'])
            self.assertEqual(a['audit']['task']['text'],'second');self.assertEqual(len(a['audit']['events']),1)
            self.assertEqual(store.interaction_audit('alice',d['deviceId'],'a'*64)['audit']['task']['text'],'first')
            with self.assertRaises(PermissionError):store.interaction_audit('bob',d['deviceId'])
            with self.assertRaises(ValueError):store.interaction_audit('alice',d['deviceId'],'c'*64)
            store.revoke(d['deviceId'],'alice')
            with self.assertRaises(PermissionError):store.interaction_audit('alice',d['deviceId'])

if __name__=='__main__':unittest.main()
