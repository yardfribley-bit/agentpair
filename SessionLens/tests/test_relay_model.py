import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from sessionlens.relay_model import call,validate
class Response:
 def __init__(self,value):self.value=value
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self):return json.dumps(self.value).encode()
class RelayTests(unittest.TestCase):
 def test_deepseek_short_query_disables_extra_thinking_and_retries_truncation(self):
  with tempfile.TemporaryDirectory() as tmp:
   secret=Path(tmp)/'key';secret.write_text('{"apiKey":"test"}')
   config={'url':'https://example.com/v1/chat/completions','name':'deepseek-v4-flash','credentialFile':str(secret)}
   values=[Response({'choices':[{'finish_reason':'length','message':{'content':''}}]}),Response({'choices':[{'finish_reason':'stop','message':{'content':'{"terms":["SSH"]}'}}]})]
   with patch('sessionlens.relay_model.urllib.request.build_opener') as opener:
    opener.return_value.open.side_effect=values;r=call(config,'检索',{},max_tokens=500)
    self.assertEqual(r['terms'],['SSH']);payloads=[json.loads(x.args[0].data) for x in opener.return_value.open.call_args_list]
    self.assertEqual(payloads[0]['thinking'],{'type':'disabled'});self.assertGreater(payloads[1]['max_tokens'],500)
 def test_unknown_evidence_and_string_refs_rejected(self):
  for refs in (['E999'],'E001',[]):
   with self.assertRaises(ValueError):validate({'overview':{'text':'结论','basis':'recorded','evidenceRefs':refs},'steps':[]},{'E001'})
 def test_valid_citations_accepted(self):
  validate({'overview':{'text':'结论','basis':'recorded','evidenceRefs':['E001']},'steps':[]},{'E001'})
 def test_claim_review_corrects_unproved_global_absence_before_caching(self):
  from sessionlens.relay_model import answer
  with tempfile.TemporaryDirectory() as tmp:
   config={'url':'https://example.com/v1/chat/completions','name':'deepseek-v4-flash'}
   packet={'taskId':'test','fragments':[{'evidenceId':'E001','text':'写入脚本成功'}]}
   original={'overview':{'text':'脚本从未运行，发布没有成功','basis':'recorded','evidenceRefs':['E001']},'steps':[],'gaps':[],'toolExplanations':[]}
   corrected={'overview':{'text':'本段记录证明脚本已写入，但没有发布执行或成功返回的记录。','basis':'recorded','evidenceRefs':['E001']},'steps':[],'gaps':[],'toolExplanations':[]}
   with patch('sessionlens.relay_model.call',side_effect=[original,{'issues':['日志缺失不能证明从未执行'],'corrected':corrected}]) as call_model:
    result=answer(tmp,config,'真的发布了吗？',packet,[])
    self.assertEqual(result['overview']['text'],corrected['overview']['text']);self.assertTrue(result['qualityAudit']['reviewed']);self.assertEqual(call_model.call_count,2)
    self.assertEqual(answer(tmp,config,'真的发布了吗？',packet,[]),result);self.assertEqual(call_model.call_count,2)
 def test_review_cannot_replace_answer_with_unknown_citations(self):
  from sessionlens.relay_model import answer
  with tempfile.TemporaryDirectory() as tmp:
   result={'overview':{'text':'已写入','basis':'recorded','evidenceRefs':['E001']},'steps':[]}
   invalid={'overview':{'text':'已完成','basis':'recorded','evidenceRefs':['E999']},'steps':[]}
   with patch('sessionlens.relay_model.call',side_effect=[result,{'issues':['过度断言'],'corrected':invalid},invalid]):
    with self.assertRaises(ValueError):answer(tmp,{'url':'https://example.com','name':'test'},'结果？',{'fragments':[{'evidenceId':'E001'}]},[])
 def test_review_with_malformed_structure_gets_one_bounded_repair(self):
  from sessionlens.relay_model import answer
  with tempfile.TemporaryDirectory() as tmp:
   valid={'overview':{'text':'本段未显示执行','basis':'recorded','evidenceRefs':['E001']},'steps':[]}
   with patch('sessionlens.relay_model.call',side_effect=[valid,{'issues':['限定证据范围'],'corrected':{'overview':'本段未显示执行','steps':[]}},valid]) as call_model:
    result=answer(tmp,{'url':'https://example.com','name':'test'},'运行了吗？',{'fragments':[{'evidenceId':'E001'}]},[])
    self.assertEqual(result['overview']['text'],valid['overview']['text']);self.assertEqual(call_model.call_count,3)
