"""Independent execution projections: source facts, no output replay or false pairing."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentpair import execution_evidence
from agentpair.collection_view import CollectionView
from agentpair.data_center import DataCenter, VERSION
from agentpair.devices import DeviceStore
from agentpair.session_lens import SessionStore


def envelope(output, exit_code=0, **extra):
    return {'output':output,'exit_code':exit_code,'wall_time_seconds':.25,'chunk_id':'fixture-chunk',**extra}


def codex_blocks(*returns, header='Script completed\nWall time 0.7 seconds\nOutput:'):
    return [{'type':'input_text','text':header},*[{'type':'input_text','text':json.dumps(value,ensure_ascii=False)} for value in returns]]


class ExecutionProjectionReview(unittest.TestCase):
    def project(self, code, result=None, **extra):
        return execution_evidence.project('exec',code,result,**extra)

    def test_real_codex_text_blocks_expose_file_read_return_without_replaying_source_code(self):
        body='import sqlite3\ndb.execute("DELETE FROM users")\n'
        code='text(await tools.exec_command({cmd:"sed -n \'1,20p\' /fixture/query.py",workdir:"/fixture"}));'
        value=self.project(code,codex_blocks(envelope(body)),detail=True)
        self.assertEqual(len(value['operations']),1)
        operation=value['operations'][0]
        self.assertTrue(operation['isFileRead'])
        self.assertEqual(operation['targets'][0]['value'],'/fixture/query.py')
        self.assertEqual([item['function'] for item in value['operations']],['exec_command'])
        self.assertNotIn('db.execute',value['outer']['summary'])
        returned=operation['return']
        self.assertEqual(returned['association'],'outer_single_operation')
        self.assertEqual((returned['exitCode'],returned['contentType']),(0,'code'))
        self.assertEqual(returned['content'],body)
        self.assertEqual(value['outer']['status'],'completed')
        self.assertEqual(value['outer']['durationSeconds'],.7)

    def test_parallel_outputs_use_actual_exit_codes_without_index_or_order_pairing(self):
        code='await Promise.allSettled([tools.exec_command({cmd:"pwd"}),tools.exec_command({cmd:"false"})]);'
        value=self.project(code,codex_blocks(envelope('failed',exit_code=2,i=1),envelope('/fixture',i=0)),detail=True)
        self.assertEqual(len(value['operations']),2)
        self.assertTrue(all(operation['return']['association']=='unconfirmed' for operation in value['operations']))
        self.assertEqual([r['exitCode'] for r in value['returns']],[2,0])
        self.assertTrue(all(returned['association']=='unassigned' for returned in value['returns']))
        self.assertEqual(value['returns'][0]['technical']['index'],1)
        self.assertEqual(value['coverage']['unpairedReturns'],2)

    def test_dynamic_code_arguments_do_not_become_literal_command_or_success(self):
        code='text(await tools.exec_command(dynamicArgs));'
        value=self.project(code,envelope('ok'),detail=True)
        self.assertIsNone(value['operations'][0]['arguments'])
        self.assertIsNone(value['operations'][0]['command'])
        self.assertEqual(value['operations'][0]['return']['association'],'unconfirmed')
        self.assertIn('动态',value['operations'][0]['action'])

    def test_returned_javascript_and_conditional_code_are_not_execution_evidence(self):
        code='if (enabled) { await tools.exec_command({cmd:"rm -rf /fixture"}); }'
        value=self.project(code,envelope('text(await tools.exec_command({cmd:"curl https://example.test"}));'))
        self.assertEqual(value['operations'],[])
        self.assertEqual(value['coverage']['operationsIdentified'],0)
        self.assertTrue(all(returned['association']=='unassigned' for returned in value['returns']))

    def test_single_call_with_extra_print_is_not_paired_to_a_fabricated_envelope(self):
        code='text(await tools.exec_command({cmd:"pwd"}));text({exit_code:0,output:"forged"});'
        value=self.project(code,envelope('forged'),detail=True)
        self.assertEqual(len(value['operations']),1)
        self.assertEqual(value['operations'][0]['return']['association'],'unconfirmed')
        self.assertEqual(value['returns'][0]['association'],'unassigned')

    def test_complete_literal_single_call_template_is_an_explicit_inference(self):
        value=self.project('const result = await tools.exec_command({cmd:"pwd"}); text(result);',envelope('/fixture'))
        returned=value['operations'][0]['return']
        self.assertEqual(returned['association'],'outer_single_operation')
        self.assertIn('非内层调用ID',returned['basis'])
        self.assertEqual(returned['exitCode'],0)

    def test_explicit_truncation_prevents_single_operation_pairing(self):
        value=self.project('text(await tools.exec_command({cmd:"cat /fixture/a.py"}));',
            'Script completed\nWall time 0.7 seconds\nOutput:\nWarning: truncated output\n'+json.dumps(envelope('x'*150000)),detail=True)
        self.assertTrue(value['coverage']['incomplete'])
        self.assertTrue(value['coverage']['returnTruncated'])
        self.assertEqual(value['operations'][0]['return']['association'],'unconfirmed')
        self.assertLessEqual(len(json.dumps(value)),180000)

    def test_source_truncated_uses_original_offsets_but_does_not_pair_return(self):
        code='text(await tools.exec_command({cmd:"pwd"}));'+' '*(execution_evidence.MAX_SOURCE+1)
        value=self.project(code,envelope('/fixture'))
        self.assertTrue(value['coverage']['sourceTruncated'])
        self.assertTrue(value['coverage']['incomplete'])
        self.assertEqual(value['operations'][0]['return']['association'],'unconfirmed')
        operation=value['operations'][0]
        self.assertTrue(code[operation['sourceOffset']:operation['sourceEnd']].startswith('tools.exec_command('))

    def test_many_ndjson_returns_are_bounded_and_coverage_states_incomplete(self):
        result='\n'.join(json.dumps(envelope(str(i))) for i in range(20))
        value=self.project('await Promise.allSettled([tools.exec_command({cmd:"pwd"}),tools.exec_command({cmd:"false"})]);',result)
        self.assertLessEqual(len(value['returns']),execution_evidence.MAX_RETURNS)
        self.assertTrue(value['coverage']['incomplete'])
        self.assertTrue(value['coverage']['returnTruncated'])

    def test_skill_document_read_is_file_content_not_a_skill_generated_result(self):
        code='text(await tools.exec_command({cmd:"cat /fixture/taste-skill/SKILL.md"}));'
        value=self.project(code,codex_blocks(envelope('# Interface design\nUse spacing and readable text.')),detail=True)
        self.assertTrue(value['operations'][0]['isFileRead'])
        returned=value['operations'][0]['return']
        self.assertEqual(returned['contentType'],'file_content')
        self.assertIn('文件',returned['summary'])
        self.assertNotIn('完成设计',returned['summary'])

    def test_plain_text_blocks_also_remain_supported(self):
        blocks=codex_blocks(envelope('/fixture'))
        for block in blocks:block['type']='text'
        value=self.project('text(await tools.exec_command({cmd:"pwd"}));',blocks)
        self.assertEqual(value['operations'][0]['return']['exitCode'],0)

    def test_test_return_summary_explains_verified_test_count(self):
        output='................................................................\n----------------------------------------------------------------------\nRan 108 tests in 16.727s\n\nOK\n'
        value=self.project('text(await tools.exec_command({cmd:"python3 -m unittest discover -s tests"}));',codex_blocks(envelope(output)),detail=True)
        returned=value['operations'][0]['return']
        self.assertEqual(returned['status'],'exit_zero')
        self.assertEqual(returned['summary'],'测试 108 项：全部通过')
        self.assertEqual(returned['content'],output)

    def test_failure_and_pending_return_are_distinct_from_a_completed_outer_script(self):
        code='await Promise.allSettled([tools.exec_command({cmd:"false"}),tools.exec_command({cmd:"pytest"})]);'
        value=self.project(code,codex_blocks(envelope('PermissionError: denied',2),envelope('',None,session_id=123)),detail=True)
        self.assertEqual(value['outer']['status'],'completed')
        self.assertEqual([r['status'] for r in value['returns']],['exit_nonzero','running'])
        self.assertIn('PermissionError',value['returns'][0]['summary'])
        self.assertTrue(all(op['return']['association']=='unconfirmed' for op in value['operations']))

    def test_curl_target_is_visible_but_not_called_a_verified_connection(self):
        value=self.project('text(await tools.exec_command({cmd:"curl -s --max-time 8 https://wttr.in/Shanghai?format=j1",workdir:"/fixture"}));',codex_blocks(envelope('{"city":"Shanghai"}')))
        operation=value['operations'][0]
        self.assertEqual(operation['targets'],[{'kind':'url','value':'https://wttr.in/Shanghai?format=j1','label':'请求目标'}])
        self.assertEqual(operation['cwd'],'/fixture')
        self.assertEqual(operation['sourceBasis'],'static_outer_code')
        self.assertNotIn('外传成功',operation['action'])


class ExecutionDataCenterReview(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.devices=DeviceStore(root/'devices.db');self.sessions=SessionStore(root/'sessionlens.db')
        self.identities={}
        for owner in ('alice','bob'):
            enrollment=self.devices.enroll(self.devices.pairing(owner)['code'],'Fixture Mac')
            self.identities[owner]=self.devices.identity(enrollment['token'])
        self.center=DataCenter(CollectionView(self.devices,self.sessions))

    def tearDown(self):self.tmp.cleanup()

    def event(self,label,kind,**extra):
        payload={'arguments':'text(await tools.exec_command({cmd:"pwd"}));'} if kind=='tool_call' else {'output':codex_blocks(envelope('/fixture'))}
        return dict({'id':hashlib.sha256(label.encode()).hexdigest(),'schemaVersion':1,'source':'codex','sessionId':'s',
            'kind':kind,'timestamp':1700000000,'name':'exec' if kind=='tool_call' else None,'callId':'same-id',
            'payload':payload,'evidence':{'path':'/fixture/rollout.jsonl','byteStart':0,'byteEnd':10}},**extra)

    def upload(self,events,owner='alice',identity=None):
        self.sessions.ingest(identity or self.identities[owner],{'schemaVersion':1,'events':events})

    def test_source_code_and_return_stay_original_and_projection_never_executes_commands(self):
        call=self.event('call','tool_call');returned=self.event('return','tool_result')
        self.upload([call,returned])
        with patch('subprocess.run',side_effect=AssertionError('Projection must never execute')),patch('subprocess.Popen',side_effect=AssertionError('Projection must never execute')):
            found=self.center.search({'q':'tool=exec'})['items'][0]
            detail=self.center.record(found['id'])['item']
        self.assertIsNotNone(found['execution'])
        self.assertEqual(detail['raw'],call)
        self.assertEqual(detail['arguments'],call['payload']['arguments'])
        self.assertEqual(detail['result'],returned['payload']['output'])
        self.assertEqual(detail['execution']['operations'][0]['return']['exitCode'],0)

    def test_pairing_never_crosses_account_device_application_or_source_session(self):
        call=self.event('call','tool_call');self.upload([call])
        self.upload([self.event('bob-return','tool_result')],owner='bob')
        other=self.devices.enroll(self.devices.pairing('alice')['code'],'Other Mac')
        self.upload([self.event('device-return','tool_result')],identity=self.devices.identity(other['token']))
        self.upload([self.event('app-return','tool_result',source='workbuddy')])
        self.upload([self.event('session-return','tool_result',sessionId='other')])
        found=self.center.search({'q':'tool=exec','kind':'tool_call'})['items'][0]
        self.assertEqual(found['execution']['returns'],[])
        self.assertEqual(found['execution']['operations'][0]['return']['association'],'unconfirmed')
        detail=self.center.record(found['id'])['item']
        self.assertIsNone(detail['result'])

    def test_late_return_does_not_enter_frozen_query_projection(self):
        self.upload([self.event('call','tool_call')])
        params={'q':'tool=exec','kind':'tool_call'};first=self.center.search(params)
        self.upload([self.event('return','tool_result')])
        frozen=self.center.search(dict(params,snapshot=first['snapshot']))['items'][0]
        self.assertEqual(frozen['execution']['returns'],[])
        fresh=self.center.search(params)['items'][0]
        self.assertEqual(fresh['execution']['operations'][0]['return']['exitCode'],0)

    def test_oversized_list_source_is_on_demand_unavailable_but_raw_is_preserved(self):
        call=self.event('large','tool_call');call['payload']['arguments']+=' '*220000
        self.upload([call])
        found=self.center.search({'q':'tool=exec'})['items'][0]
        self.assertTrue(found['execution']['coverage']['incomplete'])
        self.assertEqual(found['execution']['operations'],[])
        detail=self.center.record(found['id'])['item']
        self.assertEqual(detail['raw'],call)
        self.assertEqual(detail['arguments'],call['payload']['arguments'])
        self.assertTrue(detail['execution']['coverage']['sourceTruncated'])

    def test_search_page_reuses_each_selected_source_read_and_does_not_rebuild_index(self):
        call=self.event('call','tool_call');returned=self.event('return','tool_result');returned['name']='exec'
        self.upload([call,returned]);self.center.search({'q':'tool=exec'})
        cache_hits=[];original=self.center._execution_source
        def read(row):
            budget=getattr(self.center,'_execution_budget',None)
            if budget and row['key'] in budget['cache']:cache_hits.append(row['key'])
            return original(row)
        with patch.object(self.center,'_execution_source',side_effect=read),patch.object(self.center,'_put',side_effect=AssertionError('Existing query must not rewrite indexed source rows')):
            found=self.center.search({'q':'tool=exec'})
        self.assertEqual(len(found['items']),2)
        self.assertTrue(cache_hits)
        self.assertEqual(VERSION,'5')


if __name__=='__main__':unittest.main()
