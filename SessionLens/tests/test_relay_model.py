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
