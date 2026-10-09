"""Execution projections keep request actions separate from returned content."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter
from agentpair.devices import DeviceStore
from agentpair.execution_evidence import project
from agentpair.session_lens import SessionStore


def wrapped(output,exit_code=0):
    return [{'type':'text','text':'Script completed\nWall time 0.2 seconds\nOutput:\n'},
            {'type':'text','text':json.dumps({'chunk_id':'fixture','wall_time_seconds':.1,'exit_code':exit_code,'output':output})}]


class ExecutionEvidenceTests(unittest.TestCase):
    def test_read_code_is_returned_data_not_executed_database_action(self):
        code='text(await tools.exec_command({cmd:"sed -n \'660,735p\' agentpair/data_center.py",workdir:"/repo"}));'
        value=project('exec',{'code':code},wrapped("def inspect():\n    db.execute('DROP TABLE fixtures')"),detail=True)
        op=value['operations'][0]
        self.assertEqual(op['action'],'读取文件')
        self.assertEqual(op['targets'][0]['value'],'agentpair/data_center.py')
        self.assertEqual(op['return']['contentType'],'code')
        self.assertEqual(op['return']['association'],'outer_single_operation')
        self.assertIn('2 行已采集代码',op['return']['summary'])
        self.assertNotIn('DROP',op['action'])
        self.assertIn('db.execute',op['return']['content'])

    def test_parallel_outputs_never_inherit_success_by_order_or_index(self):
        code='await Promise.allSettled([tools.exec_command({cmd:"pwd"}),tools.exec_command({cmd:"false"})]);'
        result=wrapped('/repo')+[{'type':'text','text':json.dumps({'i':1,'chunk_id':'other','exit_code':1,'output':'failed'})}]
        value=project('exec',{'code':code},result)
        self.assertEqual(len(value['operations']),2)
        self.assertTrue(all(op['return']['association']=='unconfirmed' for op in value['operations']))
        self.assertTrue(all(r['association']=='unassigned' for r in value['returns']))
        self.assertEqual([r['exitCode'] for r in value['returns']],[0,1])

    def test_one_visible_call_plus_hidden_dynamic_call_cannot_be_paired(self):
        code='text(await tools.exec_command({cmd:"pwd"})); await tools[dynamicName](args);'
        value=project('exec',{'code':code},wrapped('/repo'))
        self.assertEqual(len(value['operations']),1)
        self.assertEqual(value['operations'][0]['return']['association'],'unconfirmed')

    def test_failed_read_does_not_claim_file_contents(self):
        value=project('exec',{'code':'text(await tools.exec_command({cmd:"cat absent.py"}));'},wrapped('cat: absent.py: No such file',1))
        returned=value['operations'][0]['return']
        self.assertEqual(returned['status'],'exit_nonzero')
        self.assertEqual(returned['contentType'],'text')
        self.assertIn('执行失败',returned['summary'])

    def test_empty_output_and_dynamic_arguments_remain_explicit(self):
        value=project('exec',{'code':'text(await tools.exec_command(args));'},wrapped(''))
        self.assertIsNone(value['operations'][0]['arguments'])
        self.assertIn('动态表达式',value['operations'][0]['action'])
        self.assertEqual(value['returns'][0]['contentType'],'empty')
        self.assertEqual(value['operations'][0]['return']['association'],'unconfirmed')

    def test_json_output_is_business_data_without_technical_envelope(self):
        value=project('exec',{'code':'const r=await tools.exec_command({cmd:"curl https://weather.example.test/a"});text(r);'},wrapped('{"city":"Shanghai","temperature":23}'),detail=True)
        self.assertEqual(value['outer']['status'],'completed')
        self.assertEqual(value['outer']['durationSeconds'],.2)
        self.assertEqual(value['operations'][0]['targets'][0]['value'],'https://weather.example.test/a')
        self.assertEqual(value['returns'][0]['contentType'],'json')
        self.assertEqual(value['returns'][0]['content'],{'city':'Shanghai','temperature':23})
        self.assertIn('Shanghai',value['returns'][0]['summary'])

    def test_truncated_script_and_return_are_marked_without_false_pair(self):
        value=project('exec',{'code':'text(await tools.exec_command({cmd:"pwd"}));'+(' '*64000)},wrapped('x'*130000))
        self.assertTrue(value['coverage']['sourceTruncated'])
        self.assertTrue(value['coverage']['returnTruncated'])
        self.assertEqual(value['operations'][0]['return']['association'],'unconfirmed')

    def test_printed_source_strings_do_not_add_operations(self):
        code='text("await tools.exec_command({cmd:\"rm /file\"});");'
        value=project('exec',{'code':code},wrapped('db.execute("DELETE FROM table")'))
        self.assertEqual(value['operations'],[])
        self.assertIn('db.execute',value['returns'][0]['preview'])

    def test_echo_url_is_not_a_network_target(self):
        value=project('exec',{'code':'text(await tools.exec_command({cmd:"echo https://example.test"}));'},wrapped('https://example.test'))
        self.assertEqual(value['operations'][0]['targets'],[])

    def test_explicit_actions_have_readable_names_without_claiming_success(self):
        code='await tools.apply_patch("*** Begin Patch\\n*** Update File: app.py\\n*** End Patch");await tools.write_stdin({session_id:7,chars:""});await tools.exec_command({cmd:"node --check tests/view.js"});'
        value=project('exec',{'code':code},wrapped('done'))
        self.assertEqual([op['action'] for op in value['operations']],['变更文件','读取运行输出','检查 JavaScript 语法'])
        self.assertEqual(value['operations'][0]['targets'][0]['value'],'app.py')
        self.assertTrue(all(op['return']['association']=='unconfirmed' for op in value['operations']))

    def test_input_text_blocks_and_pending_command_return(self):
        result=wrapped('')
        for block in result:block['type']='input_text'
        result[1]['text']=json.dumps({'chunk_id':'fixture','session_id':8,'output':''})
        value=project('exec',{'code':'text(await tools.exec_command({cmd:"sleep 1"}));'},result)
        self.assertEqual(value['returns'][0]['status'],'running')
        self.assertNotIn('成功',value['returns'][0]['summary'])

    def test_running_script_header_is_state_not_business_output(self):
        value=project('exec',{'code':'text(await tools.exec_command({cmd:"sleep 1"}));'},[{'type':'input_text','text':'Script running with cell ID 8\nWall time 10.0 seconds'}])
        self.assertEqual(value['outer']['status'],'running')
        self.assertEqual(value['returns'],[])

    def test_nontext_blocks_are_not_silently_removed(self):
        result=wrapped('ok')+[{'type':'image','data':'fixture'}]
        value=project('exec',{'code':'text(await tools.exec_command({cmd:"pwd"}));'},result)
        self.assertEqual(value['outer']['nonTextBlocks'],1)
        self.assertEqual(value['returns'][-1]['contentType'],'non_text')
        self.assertTrue(all(op['return']['association']=='unconfirmed' for op in value['operations']))

    def test_running_script_keeps_collected_partial_output(self):
        result=[{'type':'input_text','text':'Script running with cell ID 8\nWall time 10.0 seconds\nOutput:\n'},
                {'type':'input_text','text':json.dumps({'session_id':9,'chunk_id':'fixture','output':'building module one'})}]
        value=project('exec',{'code':'text(await tools.exec_command({cmd:"npm run build"}));'},result)
        self.assertEqual(value['outer']['status'],'running')
        self.assertEqual(value['returns'][0]['preview'],'building module one')
        self.assertTrue(value['returns'][0]['partial'])

    def test_curl_payload_url_is_not_a_request_target(self):
        value=project('exec',{'code':'text(await tools.exec_command({cmd:"curl --data-binary https://payload.example.test https://target.example.test"}));'},wrapped('ok'))
        self.assertEqual([t['value'] for t in value['operations'][0]['targets']],['https://target.example.test'])


class DataCenterExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        devices=DeviceStore(root/'devices.db');self.sessions=SessionStore(root/'sessions.db')
        self.identity=devices.identity(devices.enroll(devices.pairing('alice')['code'],'Office Mac')['token'])
        self.center=DataCenter(CollectionView(devices,self.sessions))

    def tearDown(self):self.temp.cleanup()

    def event(self,label,kind,payload,**extra):
        return {'id':hashlib.sha256(label.encode()).hexdigest(),'schemaVersion':1,'source':'codex','sessionId':'s',
                'kind':kind,'timestamp':1700000000,'payload':payload,'evidence':{'path':'/fixture/log','byteStart':0,'byteEnd':1},**extra}

    def test_existing_source_is_projected_on_page_and_detail_without_reindex(self):
        code='text(await tools.exec_command({cmd:"cat test.py"}));'
        call=self.event('call','tool_call',{'arguments':{'code':code}},name='exec',callId='one')
        result=self.event('return','tool_result',{'output':wrapped('db.execute("SELECT 1")')},callId='one')
        self.sessions.ingest(self.identity,{'schemaVersion':1,'events':[call,result]})
        listed=self.center.search({'q':'tool=exec'})['items'][0]
        self.assertEqual(listed['execution']['operations'][0]['return']['contentType'],'code')
        self.assertNotIn('content',listed['execution']['returns'][0])
        detail=self.center.record(listed['id'])['item']
        self.assertEqual(detail['arguments']['code'],code)
        self.assertEqual(detail['result'],wrapped('db.execute("SELECT 1")'))
        self.assertIn('db.execute',detail['execution']['returns'][0]['content'])
        returned=self.center.search({'q':'kind=tool_result'})['items'][0]
        self.assertEqual(returned['execution']['operations'][0]['function'],'exec_command')

    def test_budget_exhaustion_marks_projection_and_keeps_raw_access(self):
        records=[self.event(str(i),'tool_call',{'arguments':{'code':'text(await tools.exec_command({cmd:"pwd"}));'}},name='exec') for i in range(45)]
        for start in range(0,len(records),30):self.sessions.ingest(self.identity,{'schemaVersion':1,'events':records[start:start+30]})
        items=self.center.search({'q':'tool=exec','pageSize':50})['items']
        skipped=[item for item in items if item['execution']['coverage']['incomplete']]
        self.assertGreaterEqual(len(skipped),5)
        self.assertTrue(self.center.record(skipped[0]['id'])['item']['execution']['operations'])

    def test_projection_budget_excludes_waits_between_records(self):
        records=[self.event(str(i),'tool_call',{'arguments':{'code':'text(await tools.exec_command({cmd:"pwd"}));'}},
                            name='exec',callId=str(i)) for i in range(5)]
        self.sessions.ingest(self.identity,{'schemaVersion':1,'events':records})
        self.center.search({'q':'tool=exec'})
        clock=[0.];original=self.center._execution_source
        def read(row):
            value=original(row);clock[0]+=.01
            return value
        with self.center._db() as db,self.center._execution_page(),\
                patch('agentpair.data_center.time.monotonic',side_effect=lambda:clock[0]),\
                patch.object(self.center,'_execution_source',side_effect=read):
            rows=db.execute("SELECT * FROM dc_records WHERE kind='tool_call'").fetchall()
            for row in rows:
                clock[0]+=1. # unrelated matching/association query time
                meta=json.loads(row['metadata'])
                value=self.center._execution_projection(db,row,meta,meta.get('summary') or {})
                self.assertTrue(value['operations'])
            self.assertAlmostEqual(self.center._execution_budget['spentSeconds'],.05)
            self.assertEqual(self.center._execution_budget['records'],35)

    def test_slow_source_prevents_starting_paired_and_following_reads(self):
        records=[]
        for i in range(2):
            records.extend([self.event('call'+str(i),'tool_call',{'arguments':{'code':'text(await tools.exec_command({cmd:"pwd"}));'}},name='exec',callId=str(i)),
                            self.event('return'+str(i),'tool_result',{'output':wrapped('/fixture')},callId=str(i))])
        self.sessions.ingest(self.identity,{'schemaVersion':1,'events':records})
        self.center.search({'q':'tool=exec'})
        clock=[0.];original=self.center._execution_source;read_keys=[]
        def read(row):
            value=original(row)
            if value is not None:read_keys.append(row['key']);clock[0]+=.3
            return value
        with self.center._db() as db,self.center._execution_page(),\
                patch('agentpair.data_center.time.monotonic',side_effect=lambda:clock[0]),\
                patch.object(self.center,'_execution_source',side_effect=read):
            rows=db.execute("SELECT * FROM dc_records WHERE kind='tool_call'").fetchall()
            values=[]
            for row in rows:
                meta=json.loads(row['metadata']);summary=dict(meta.get('summary') or {})
                summary['resultRecordId']=db.execute("SELECT key FROM dc_records WHERE kind='tool_result' AND json_extract(metadata,'$.callId')=?",(meta['callId'],)).fetchone()[0]
                values.append(self.center._execution_projection(db,row,meta,summary))
            self.assertEqual(len(read_keys),1)
            self.assertEqual(self.center._execution_budget['records'],39)
            self.assertEqual(values[0]['returns'],[])
            self.assertTrue(values[0]['coverage']['incomplete'])
            self.assertTrue(values[0]['coverage']['returnProjectionUnavailable'])
            self.assertIn('已有返回',values[0]['outer']['statusLabel'])
            self.assertTrue(values[1]['coverage']['projectionUnavailable'])
        self.assertTrue(self.center.record(rows[1]['key'])['item']['execution']['returns'])


if __name__=='__main__':unittest.main()
