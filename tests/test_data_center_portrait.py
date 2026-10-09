"""Readable portraits derived from source evidence, with isolated synthetic stores."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter, _result_view, _targets
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


class PortraitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.devices = DeviceStore(root/'devices.db')
        self.sessions = SessionStore(root/'sessions.db')
        self.identities = {}
        for owner in ('alice','bob'):
            enrollment = self.devices.enroll(self.devices.pairing(owner)['code'],'Fixture Mac')
            self.identities[owner] = self.devices.identity(enrollment['token'])
        self.center = DataCenter(CollectionView(self.devices,self.sessions))

    def tearDown(self): self.tmp.cleanup()

    def event(self, label, kind, payload, stamp=1700000000, **extra):
        return dict(schemaVersion=1,id=hashlib.sha256(label.encode()).hexdigest(),source='workbuddy',
                    sessionId='fixture-session',kind=kind,timestamp=stamp,payload=payload,evidence={'path':'/synthetic/fixture.jsonl'},**extra)

    def upload(self, events, owner='alice'):
        for offset in range(0,len(events),30):
            self.sessions.ingest(self.identities[owner],{'schemaVersion':1,'events':events[offset:offset+30]})

    def test_actual_nested_tool_and_executor_and_exact_paired_summary(self):
        call=self.event('video','tool_call',{'arguments':json.dumps({'toolName':'VideoGen','params':{
            'prompt':'Make a protocol animation','enable_audio':False,'resolution':'1080P'}})},name='DeferExecuteTool',callId='c')
        result=self.event('return','tool_result',{'output':{'text':json.dumps({'type':'video_gen_tool_result',
            'status':'completed','videos':[{'localPath':'/tmp/fixture.mp4'}]})}},1700000001,callId='c')
        image=self.event('image','tool_call',{'arguments':{'toolName':'ImageGen','params':{'prompt':'Mention VideoGen'}}},
                         name='DeferExecuteTool')
        self.upload([call,result,image])
        self.assertEqual(self.center.search({'q':'tool=VideoGen'})['total'],1)
        hit=self.center.search({'q':'tool=VideoGen && executor=DeferExecuteTool'})['items'][0]
        self.assertEqual(hit['summary']['promptPreview'],'Make a protocol animation')
        self.assertEqual(hit['summary']['outcome']['status'],'completed')
        self.assertEqual(hit['summary']['outcome']['outputs'][0]['path'],'/tmp/fixture.mp4')
        self.assertEqual(hit['sourceDeviceId'],self.identities['alice']['id'])
        detail=self.center.record(hit['id'])['item']
        self.assertEqual(detail['presentation']['prompt'],'Make a protocol animation')
        self.assertEqual(detail['arguments']['toolName'],'VideoGen')
        self.assertEqual(detail['presentation']['parameters'][0]['value'],False)

    def test_result_summary_never_pairs_other_owner_source_or_duplicate(self):
        call=self.event('call','tool_call',{'arguments':{'command':'curl https://wttr.in'}},name='Bash',callId='shared')
        wrong=self.event('wrong','tool_result',{'output':{'status':'completed'}},callId='shared')
        self.upload([call]);self.upload([wrong],'bob')
        hit=self.center.search({'q':'tool=Bash'})['items'][0]
        self.assertEqual(hit['summary']['outcome']['status'],'unknown')
        duplicate=self.event('duplicate','tool_call',{'arguments':{'command':'pwd'}},name='Bash',callId='shared')
        self.upload([duplicate,wrong])
        hits=self.center.search({'q':'tool=Bash'})['items']
        self.assertTrue(all(h['summary']['outcome']['status']=='unknown' for h in hits))

    def test_targets_ignore_mentions_headers_body_and_keep_all_requested_hosts(self):
        command='curl -H "Referer: https://wttr.in" -e https://ref.invalid -x https://proxy.invalid --data https://data.invalid https://first.invalid https://192.0.2.9/weather'
        call=self.event('targets','tool_call',{'arguments':{'command':command,'prompt':'https://wttr.in'}},name='Bash')
        self.upload([call])
        self.assertEqual(self.center.search({'q':'domain=wttr.in'})['total'],1)
        self.assertEqual(self.center.search({'q':'target_domain=wttr.in'})['total'],0)
        self.assertEqual(self.center.search({'q':'target_domain=first.invalid && target_ip=192.0.2.9'})['total'],1)
        self.assertEqual(_targets({},'python - <<\'PY\'\ncurl https://not-executed.invalid\nPY\ncurl https://executed.invalid'),['https://executed.invalid'])
        self.assertEqual(_targets({'prompt':{'url':'https://mention.invalid'},'params':{'url':'https://requested.invalid'}},None),['https://requested.invalid'])

    def test_partial_weather_is_readable_and_explicitly_incomplete(self):
        stdout=json.dumps({'current_condition':[{'temp_C':'23','FeelsLikeC':'24','humidity':'82',
            'weatherDesc':[{'value':'Patchy rain nearby'}]}]})[:-1]+', "weather": ['
        result={'text':'Command: curl https://wttr.in | head -c 3000\nStdout: '+stdout+'\nStderr: (empty)\nExit Code: 0\nSignal: (none)'}
        view=_result_view(result,'curl https://wttr.in | head -c 3000')
        self.assertEqual(view['exitCode'],0)
        self.assertEqual(view['label'],'命令退出码 0')
        self.assertEqual(view['facts'],[{'label':'气温','value':'23℃'},{'label':'体感温度','value':'24℃'},
            {'label':'湿度','value':'82%'},{'label':'天气','value':'Patchy rain nearby'}])
        self.assertTrue(view['sourceTruncated'])
        complete=_result_view({'text':'Stdout: {"items":[1,2]}\nExit Code: 0'})
        self.assertEqual(complete['parsed'],{'items':[1,2]})

    def test_captured_bare_host_target_without_invented_scheme(self):
        source={'id':'captured-request','source':'workbuddy_network_context','timestamp':1700000000,
                'sessionId':'fixture-session','body':'{"messages":[]}',
                'destination':'copilot.tencent.com/v2/chat/completions'}
        with self.devices.connect() as db:
            db.execute('INSERT INTO applens_model_context VALUES(?,?,?,?)',
                       (self.identities['alice']['id'],source['id'],json.dumps(source),1700000001))
        hit=self.center.search({'q':'target_domain=copilot.tencent.com'})['items'][0]
        self.assertEqual(hit['addressBasis'],'captured_request_target')
        self.assertEqual(hit['destination'],'copilot.tencent.com/v2/chat/completions')
        self.assertEqual(hit['matchBasis'][0]['targets'],['copilot.tencent.com/v2/chat/completions'])

    def test_parent_linked_portrait_contains_full_readable_reasoning_and_reply(self):
        events=[self.event('user','user_message',{'id':'u','content':'上海天气怎么样'}),
                self.event('thought','reasoning',{'id':'reason','parentId':'u','text':'先获取实时天气，再解释降雨风险。'},1700000001),
                self.event('call','tool_call',{'id':'call','parentId':'reason','arguments':{'command':'curl https://wttr.in/Shanghai'}},1700000002,name='Bash',callId='weather'),
                self.event('result','tool_result',{'id':'result','parentId':'call','output':{'temp_C':'23'}},1700000003,callId='weather'),
                self.event('reply','assistant_message',{'id':'reply','parentId':'result','content':'上海目前23度。'},1700000004),
                self.event('new-user','user_message',{'id':'new-user','parentId':'reply','content':'另一个任务'},1700000005),
                self.event('new-reply','assistant_message',{'id':'new-reply','parentId':'new-user','content':'另一任务的答案'},1700000006)]
        self.upload(events)
        hit=self.center.search({'q':'tool=Bash'})['items'][0]
        item=self.center.record(hit['id'])['item']
        self.assertEqual(item['portrait']['userRequest']['userInput'],'上海天气怎么样')
        self.assertEqual([r['kind'] for r in item['portrait']['steps']],['user','reasoning','tool_call','tool_result','reply'])
        self.assertEqual(item['portrait']['steps'][1]['presentation']['bodyText'],'先获取实时天气，再解释降雨风险。')
        self.assertEqual(item['portrait']['steps'][-1]['presentation']['bodyText'],'上海目前23度。')
        self.assertFalse(item['portrait']['coverage']['sourceSessionsAreTasks'])
        self.assertTrue(all(r['association']=='same_session_neighbor' for r in item['portrait']['neighbors']))

    def test_related_content_cap_keeps_exact_original_available(self):
        user=self.event('long-user','user_message',{'id':'u','content':'需求'+('x'*65000)})
        call=self.event('call','tool_call',{'id':'call','parentId':'u','arguments':{'command':'pwd'}},1700000001,name='Bash')
        self.upload([user,call])
        hit=self.center.search({'q':'tool=Bash'})['items'][0]
        item=self.center.record(hit['id'])['item']
        linked=next(r for r in item['related'] if r['kind']=='user')
        self.assertTrue(linked['contentTruncated'])
        self.assertLessEqual(len(linked['content']['content']),60000)
        self.assertEqual(self.center.raw(linked['id'])['payload']['content'],user['payload']['content'])
        self.assertEqual(item['portrait']['coverage']['truncatedRecords'],1)

    def test_snapshot_summary_never_uses_later_ingested_return(self):
        call=self.event('snapshot-call','tool_call',{'arguments':{'command':'pwd'}},name='Bash',callId='snapshot')
        self.upload([call])
        first=self.center.search({'q':'tool=Bash'})
        self.upload([self.event('snapshot-result','tool_result',{'output':{'status':'completed'}},1700000001,callId='snapshot')])
        second=self.center.search({'q':'tool=Bash','snapshot':first['snapshot']})
        self.assertEqual(second['items'][0]['summary']['outcome']['status'],'unknown')
        self.assertEqual(self.center.search({'q':'tool=Bash'})['items'][0]['summary']['outcome']['status'],'completed')

    def test_user_preview_uses_extracted_goal_and_preserves_original_envelope(self):
        envelope='<system-reminder>craft_mode '+('environment '*1000)+'</system-reminder><user_query>上海天气怎么样</user_query>'
        user=self.event('wrapped-user','user_message',{'id':'u','content':envelope})
        call=self.event('wrapped-call','tool_call',{'id':'call','parentId':'u','arguments':{'command':'pwd'}},1700000001,name='Bash')
        self.upload([user,call])
        hit=self.center.search({'q':'kind=user'})['items'][0]
        self.assertEqual(hit['userInput'],'上海天气怎么样')
        self.assertFalse(hit['userInputTruncated'])
        detail=self.center.record(self.center.search({'q':'tool=Bash'})['items'][0]['id'])['item']
        step=next(r for r in detail['portrait']['steps'] if r['kind']=='user')
        self.assertEqual(step['userInput'],'上海天气怎么样')
        self.assertEqual(step['content']['content'],envelope)
        self.assertEqual(self.center.raw(hit['id'])['payload']['content'],envelope)
        long=self.event('long-preview','user_message',{'content':'真实需求 '+('x'*600)})
        self.upload([long])
        preview=self.center.search({'q':'真实需求'})['items'][0]
        self.assertEqual(len(preview['userInput']),300)
        self.assertTrue(preview['userInputTruncated'])
        self.assertEqual(self.center.record(preview['id'])['item']['userInput'],long['payload']['content'])


if __name__=='__main__':unittest.main()
