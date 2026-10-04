import tempfile,unittest
from pathlib import Path
from sessionlens.analyze import render
class ReportTests(unittest.TestCase):
 def test_untrusted_html_escaped_and_evidence_retained(self):
  packet={'sessionId':'test-session','recordCount':1,'limitations':[],'fragments':[{'evidenceId':'E001','kind':'message','eventId':'abc','tool':None,'source':{'byteStart':10,'byteEnd':20},'truncated':True,'excerpt':'<img src=x onerror=alert(1)>'}]}
  report={'title':'<script>unsafe</script>','findings':[{'title':'test','severity':'high','status':'observed','fact':'fact','impact':'impact','remediation':'fix','evidenceRefs':['E001']}],'story':[]}
  task={'status':'completed','messages':[{'stage':'review','answer':{'report':report}}]}
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'report.html';render(packet,task,p);s=p.read_text()
   self.assertNotIn('<img src=x',s);self.assertIn('&lt;script&gt;unsafe',s);self.assertIn('href="#E001"',s);self.assertIn('10–20',s);self.assertIn('片段已截取',s)
