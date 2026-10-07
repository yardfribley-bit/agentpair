"""Tests the generated AgentPair Web view, not the SessionLens Qt client."""
import json
import tempfile
import unittest
from pathlib import Path
from html.parser import HTMLParser
from sessionlens.ui import render, publish

class Inspector(HTMLParser):
    def __init__(self):
        super().__init__();self.tags=[];self.details=0;self.bad_details=False
    def handle_starttag(self,tag,attrs):
        self.tags.append((tag,dict(attrs)))
        if tag=='details':self.details+=1
    def handle_endtag(self,tag):
        if tag=='details':
            self.details-=1
            if self.details<0:self.bad_details=True

class InsightsWebUITests(unittest.TestCase):
    def packet(self):
        return {'sessionId':'synthetic-task','recordCount':2,'recordKinds':{'tool_call':1,'tool_result':1},'limitations':[],
                'fragments':[{'evidenceId':'E001','kind':'tool_call','tool':'Bash','eventId':'synthetic-event','source':{'byteStart':1,'byteEnd':30},'truncated':True,'excerpt':'curl https://example.org/?x=<script>untrusted</script>'}]}
    def task(self):
        return {'status':'completed','messages':[{'stage':'review','answer':{'report':{'title':'查询天气','goal':'做天气页面','completion':'partial','outcome':'页面文件已生成，未核验运行结果',
                'story':[{'title':'写网页','action':'写入天气页面','result':'文件已保存','evidenceRefs':['E001']},{'title':'验证接口','action':'执行 curl','result':'返回错误','evidenceRefs':['E001']}],'findings':[],'limitations':[]}}}]}
    def test_delivery_before_steps_and_only_one_step_visible(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.html';render(self.packet(),self.task(),p);text=p.read_text(encoding='utf-8');doc=Inspector();doc.feed(text)
        self.assertLess(text.index('页面文件已生成'),text.index('任务过程'))
        self.assertIn('部分交付',text);self.assertNotIn('已完成独立验证',text)
        engineering=next(attrs for tag,attrs in doc.tags if attrs.get('id')=='engineering')
        findings=next(attrs for tag,attrs in doc.tags if attrs.get('id')=='findings')
        self.assertNotIn('hidden',engineering);self.assertIn('hidden',findings)
        cards=[attrs for tag,attrs in doc.tags if 'data-step' in attrs]
        self.assertEqual(len(cards),2);self.assertNotIn('hidden',cards[0]);self.assertIn('hidden',cards[1])
        self.assertFalse(doc.bad_details);self.assertEqual(doc.details,0)
        self.assertIn('href="#E001"',text);self.assertIn('&lt;script&gt;untrusted',text);self.assertNotIn('<script>untrusted',text)
    def test_published_shell_preserves_platform_route_and_rewrites_insights_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=root/'synthetic';folder.mkdir()
            (folder/'analysis.json').write_text(json.dumps(self.task()));(folder/'evidence.json').write_text(json.dumps(self.packet()))
            publish(root)
            text=(folder/'report.html').read_text(encoding='utf-8');index=(root/'index.html').read_text(encoding='utf-8')
        self.assertIn('<body class="agentpair-web"',text);self.assertIn('data-product-ui',text)
        self.assertIn('<a class="brand" href="/">',text)
        self.assertIn('data-insights href="/session-insights/"',text)
        self.assertIn('href="/session-insights/sessions.html"',text)
        self.assertIn('href="/session-insights/synthetic/report.html"',index)
        self.assertIn('href="/devices"',text)
    def test_absent_story_has_explicit_empty_state(self):
        task=self.task();task['messages'][0]['answer']['report']['story']=[]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.html';render(self.packet(),task,p);text=p.read_text(encoding='utf-8')
        self.assertIn('该分析没有记录可回放的过程',text);self.assertNotIn('data-story-step=',text)

if __name__=='__main__':unittest.main()
