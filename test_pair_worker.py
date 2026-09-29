import io
import json
import unittest
from unittest.mock import patch
from agentpair.pair_worker import run


def response(answer, finish='stop'):
    return io.BytesIO(json.dumps({'choices':[{'finish_reason':finish,'message':{'content':json.dumps(answer)}}],
                                'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}}).encode())


class WorkerTests(unittest.TestCase):
    def envelope(self, mode='plan', outputs=None):
        return {'task':{'adapter':'discussion','round':1},'mode':mode,
                'history':[{'role':'user','text':'Read https://github.com/o/r','round':1}],
                'outputs':outputs or {}}

    def test_plan_collects_real_tool_result(self):
        answer={'summary':'Read source','tool':{'name':'github_repository','url':'https://github.com/o/r'}}
        with patch('agentpair.pair_worker.urllib.request.urlopen',return_value=response(answer)), patch('agentpair.pair_worker.repository',return_value={'commit':'a'*40,'files':[]}) as collect:
            result=run(self.envelope(),'fake-token')
        collect.assert_called_once()
        self.assertEqual(result['evidence']['commit'],'a'*40)

    def test_truncation_retry_counts_both_calls(self):
        with patch('agentpair.pair_worker.urllib.request.urlopen',side_effect=[response({},'length'),response({'summary':'ok'})]) as http:
            result=run(self.envelope(),'fake-token')
        self.assertEqual(http.call_count,2)
        self.assertEqual(result['usage']['total_tokens'],60)

    def test_driver_receives_snapshot_and_keeps_refs(self):
        evidence={'tool':'github_repository','commit':'a'*40,'files':[{'evidenceId':'G001'}],'content':'1: source'}
        outputs={'plan':{'answer':{'tool':{'name':'github_repository'}},'evidence':evidence}}
        with patch('agentpair.pair_worker.urllib.request.urlopen',return_value=response({'summary':'source review','findings':[{'evidenceRefs':['G001']}]})) as http:
            result=run(self.envelope('driver',outputs),'fake-token')
        context=json.loads(json.loads(http.call_args.args[0].data)['messages'][1]['content'])
        self.assertEqual(context['evidence'],evidence)
        self.assertEqual(result['answer']['findings'][0]['evidenceRefs'],['G001'])
        self.assertNotIn('evidence',context['currentRound']['plan'])
