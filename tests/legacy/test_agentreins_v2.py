import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from agentreins_v2.store import Store, Pipeline
from agentreins_v2.adapters import JSONLTail, network_events

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'db')
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def record(self):
        body = {'model':'test','messages':[{'role':'user','content':'<user_query>查天气</user_query>'},{'role':'assistant','tool_calls':[{'id':'call1','function':{'name':'search','arguments':'{"city":"溧阳"}'}}]},{'role':'tool','tool_call_id':'call1','content':'五条天气结果'}]}
        raw = json.dumps(body,ensure_ascii=False).encode()
        return body, {'host':'copilot.tencent.com','path':'/v2/chat/completions','flowID':'flow1','observedAt':'2026-10-03T13:10:00Z','requestBodyBase64':base64.b64encode(raw).decode(),'requestSHA256':hashlib.sha256(raw).hexdigest()}
    def test_content_reconstruction_and_native_relation(self):
        body, record = self.record()
        events = network_events(record,'test')
        self.store.commit('test',{'offset':1},events)
        rows = self.store.events()
        request = next(r for r in rows if r['kind']=='model.request')
        self.assertEqual(self.store.content(request['content_ref']['sha256']),body)
        self.assertEqual(next(r for r in rows if r['kind']=='tool.result')['relation'],'native_tool_call_id')
        size = self.store.status()['content_bytes']
        self.store.commit('test',{'offset':2},events)
        self.assertEqual(len(self.store.events()),len(rows))
        self.assertEqual(self.store.status()['content_bytes'],size)
    def test_partial_line_and_restart(self):
        _,record = self.record()
        path = Path(self.tmp.name)/'network.jsonl'
        raw = json.dumps(record).encode()
        path.write_bytes(raw)
        pipe = Pipeline(self.store)
        try:
            tail = JSONLTail(path,pipe)
            self.assertFalse(tail.poll())
            path.write_bytes(raw+b'\n')
            self.assertTrue(tail.poll());pipe.flush()
            restarted = JSONLTail(path,pipe)
            self.assertFalse(restarted.poll())
            self.assertEqual(restarted.bytes_read,0)
        finally: pipe.close()
    def test_socket_scope_and_endpoints(self):
        from agentreins_v2.processes import parse_lsof_fields
        output = 'p42\nf7\nP TCP\nn127.0.0.1:5000->1.2.3.4:443\nTST=ESTABLISHED\np99\nf8\nn9.9.9.9:80\n'
        rows = parse_lsof_fields(output,{42:{'process_instance_id':'42:start'}})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['remote_endpoint'],'1.2.3.4:443')
        self.assertEqual(rows[0]['direction'],'not_determined')
        self.assertEqual(rows[0]['state'],'ESTABLISHED')
    def test_audit_not_false_completeness(self):
        from agentreins_v2.audit import audit
        _,record = self.record()
        self.store.commit('test',1,network_events(record,'test'))
        report = audit(self.store)
        self.assertEqual(report['reconstructed_requests'],1)
        self.assertEqual(report['all_model_requests_captured'],'unknown')
        self.assertEqual(report['network_coverage'],'not_sampled')
    def test_graph_native_edges_and_repeated_context(self):
        from agentreins_v2.graph import graph
        _,r = self.record()
        self.store.commit('test',1,network_events(r,'test'))
        r['flowID']='flow2'
        self.store.commit('test',2,network_events(r,'test'))
        report=graph(self.store)
        self.assertEqual(sum(e['relation']=='tool_return' for e in report['edges']),1)
        self.assertEqual(sum(e['relation']=='result_in_model_input' for e in report['edges']),2)
        self.assertFalse(any(e['relation']=='tool_process' for e in report['edges']))
    def test_invalid_hash(self):
        _,r = self.record();r['requestSHA256']='0'*64
        with self.assertRaisesRegex(ValueError,'hash mismatch'):network_events(r,'test')
    def test_quota_does_not_advance_cursor(self):
        self.store.quota = 1
        with self.assertRaises(OSError):
            self.store.commit('test',{'offset':1},[{'event_id':'a','kind':'test','data':{'content':'sensitive'}}])
        self.assertIsNone(self.store.cursor('test'))
        self.assertEqual(self.store.status()['events'],0)
    def test_oversized_batch_fails_explicitly(self):
        pipe = Pipeline(self.store,max_bytes=10)
        try:
            self.assertFalse(pipe.submit('test',1,[{'large':'x'*100}]))
            with self.assertRaisesRegex(OSError,'exceeds queue'):pipe.flush()
            self.assertIsNone(self.store.cursor('test'))
        finally:pipe.close()

if __name__=='__main__':unittest.main()
