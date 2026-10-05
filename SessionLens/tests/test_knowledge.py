import sqlite3,unittest
from sessionlens.knowledge import candidates
class RetrievalTests(unittest.TestCase):
 def test_rank_prompt_over_incidental_text_and_dedupe(self):
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('1','上海天气','workbuddy','s','2026','上海天气'),('2','上海天气','workbuddy','s','2025','上海天气'),('3','安装工具','codex','x','2027','日志中提及上海')])
  found=candidates(db,['上海','天气']);self.assertEqual([r[0] for r in found],['1','3']);self.assertEqual(candidates(db,[]),[]);db.close()

 def test_explicit_source_excludes_other_agent_and_injected_history(self):
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('1','上海天气','workbuddy','s','2026','上海天气'),('2','上海天气','codex','c','2027','上海天气'),('3','The following is the Codex agent history 上海天气','codex','h','2028','上海天气')])
  self.assertEqual([r[0] for r in candidates(db,['上海','天气'],source='workbuddy')],['1'])
  self.assertNotIn('3',[r[0] for r in candidates(db,['上海','天气'])]);db.close()
 def test_time_filter_keeps_only_recent_tasks(self):
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('old','上海天气','workbuddy','a','2026-08-01','天气'),('new','上海天气','workbuddy','b','2026-10-04','天气')])
  self.assertEqual([r[0] for r in candidates(db,['天气'],since='2026-09-28')],['new']);db.close()
 def test_specific_question_prefers_video_over_other_ssh_task(self):
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('ops','SSH 登录服务器检查日志','workbuddy','a','2026','SSH 动画生成工具完成状态'),('video','帮我生成五秒动画，描述 ssh 协议','workbuddy','b','2026','VideoGen')])
  self.assertEqual(candidates(db,['SSH','动画生成'],question='那次 SSH 动画是怎么生成的，最后做成了吗？')[0][0],'video');db.close()
 def test_named_weather_task_overrides_incorrect_followup_and_keeps_weather_followup(self):
  from sessionlens.knowledge import select_task
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('ssh','生成 SSH 动画','workbuddy','a','2026','ToolSearch'),('weather','查上海天气','workbuddy','b','2026','curl https://weather.test')])
  found=candidates(db,['上海','天气'],question='查上海天气的需求，调用哪些工具')
  self.assertEqual(select_task(db,{'followup':True},'查上海天气的需求，调用哪些工具',[{'retrievedTaskIds':['ssh']}],found),('weather','new_task'))
  self.assertEqual(select_task(db,{'followup':True},'它具体请求什么网址',[{'retrievedTaskIds':['weather']}],[]),('weather','same_task'))
  self.assertEqual(select_task(db,{'followup':False},'它具体请求了什么网址？',[{'retrievedTaskIds':['weather']}],found),('weather','same_task'))
  self.assertEqual(select_task(db,{'followup':'false'},'调用了什么',[{'retrievedTaskIds':['ssh']}],[]),(None,'not_found'));db.close()

 def test_followup_facets_do_not_create_cross_word_task_anchors(self):
  from sessionlens.knowledge import query_anchors
  self.assertEqual(query_anchors('它具体请求了什么网址？'),[])
  self.assertEqual(query_anchors('这个工具返回了什么内容？'),[])
  self.assertIn('上海',query_anchors('查上海天气的需求 ，调用 了哪些工具'))

 def test_direct_task_beats_quoted_conversation_with_incidental_tool_terms(self):
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('quoted','用户: 上海天气\n助手: 请查看天气 App','workbuddy','a','2026','上海天气 工具调用'),('lookup','上海天气','workbuddy','b','2026','curl')])
  self.assertEqual(candidates(db,['上海天气','工具调用'],question='查上海天气的需求，调用哪些工具')[0][0],'lookup');db.close()

 def test_raw_question_recovers_new_task_when_rewrite_keeps_old_topic(self):
  from sessionlens.knowledge import retrieve_candidates,select_task
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('video','生成 SSH 视频','workbuddy','a','2026','VideoGen'),('weather','上海天气','workbuddy','b','2026','curl'),('discussion','讨论上海天气任务的工具调用展示','codex','c','2026','上海天气')])
  q='查上海天气的需求 ，调用 了哪些工具';plan={'terms':['SSH','视频'],'followup':True}
  found=retrieve_candidates(db,plan,q)
  self.assertEqual(select_task(db,plan,q,[{'retrievedTaskIds':['video']}],found),('weather','new_task'))
  db.close()

 def test_audits_legacy_wrong_answer_without_changing_followup(self):
  from sessionlens.knowledge import answer_mismatch
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('video','生成 SSH 视频','workbuddy','a','2026','VideoGen'),('weather','上海天气','workbuddy','b','2026','curl')])
  self.assertIsNotNone(answer_mismatch(db,{'taskId':'video','question':'查上海天气调用哪些工具','selection':None}))
  self.assertIsNone(answer_mismatch(db,{'taskId':'weather','question':'它具体请求了什么网址？'}))
  self.assertIsNotNone(answer_mismatch(db,{'taskId':'weather','question':'上海天气','presentation':{'taskId':'video'}}));db.close()

 def test_default_scope_preserves_agent_but_new_chat_clarifies_same_name(self):
  from sessionlens.knowledge import retrieve_candidates,select_task
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('video','生成 SSH 视频','workbuddy','a','2026','VideoGen'),('weather','上海天气','workbuddy','b','2026','curl'),('codex-weather','上海天气','codex','c','2027','weather')])
  q='查上海天气';plan={'terms':['上海天气'],'followup':False};found=retrieve_candidates(db,plan,q)
  self.assertEqual(select_task(db,plan,q,[{'retrievedTaskIds':['video']}],found),('weather','new_task'))
  self.assertEqual(select_task(db,plan,q,[],found),(None,'choose_task'))
  self.assertEqual(select_task(db,plan,q,[{'retrievedTaskIds':['video']}],retrieve_candidates(db,plan,q,source='codex'),source='codex'),('codex-weather','new_task'));db.close()

 def test_user_choice_uses_selected_task_without_another_rewrite(self):
  import json,tempfile
  from pathlib import Path
  from unittest.mock import patch
  from sessionlens.knowledge import ask
  with tempfile.TemporaryDirectory() as tmp:
   db=sqlite3.connect(Path(tmp)/'collector.db');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)');db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('chosen','修复登录','workbuddy','s','2026','登录'));db.commit();db.close()
   packet={'taskId':'chosen','prompt':'修复登录','source':'workbuddy'}
   with patch('sessionlens.relay_model.call') as rewrite,patch('sessionlens.knowledge.packet_for_task',return_value=packet),patch('sessionlens.task_presentation.project',return_value={'taskId':'chosen'}),patch('sessionlens.relay_model.answer',return_value={}) as answer:
    result=ask(tmp,{'enabled':True,'credentialFile':'configured'},'当时为什么这样改？',[],lambda _:None,selected_task='chosen')
    self.assertEqual(result['taskId'],'chosen');rewrite.assert_not_called();self.assertEqual(answer.call_args.args[-1],[])

 def test_followup_answer_only_receives_history_for_current_task(self):
  import tempfile
  from pathlib import Path
  from unittest.mock import patch
  from sessionlens.knowledge import ask
  with tempfile.TemporaryDirectory() as tmp:
   db=sqlite3.connect(Path(tmp)/'collector.db');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)');db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('video','生成视频','workbuddy','a','2026','video'),('weather','上海天气','workbuddy','b','2026','weather')]);db.commit();db.close()
   video={'taskId':'video','retrievedTaskIds':['video'],'question':'生成视频','selection':{'version':2}}
   weather={'taskId':'weather','retrievedTaskIds':['weather'],'question':'上海天气','selection':{'version':2}}
   packet={'taskId':'weather','prompt':'上海天气','source':'workbuddy'}
   with patch('sessionlens.relay_model.call',return_value={'terms':['天气'],'followup':True}),patch('sessionlens.knowledge.packet_for_task',return_value=packet),patch('sessionlens.task_presentation.project',return_value={'taskId':'weather'}),patch('sessionlens.relay_model.answer',return_value={}) as answer:
    result=ask(tmp,{'enabled':True,'credentialFile':'configured'},'它具体请求了什么网址？',[video,weather],lambda _:None)
    self.assertEqual(result['taskId'],'weather');self.assertEqual(answer.call_args.args[-1],[weather])

 def test_named_artifact_does_not_match_similar_filename_or_context_wrapper(self):
  from sessionlens.knowledge import retrieve_candidates,select_task
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[
   ('write','请新建 ledger.py，只创建，不运行','workbuddy','a','2026','Write ledger.py'),
   ('read','请阅读 ledger.py，不要修改','workbuddy','b','2026','Read ledger.py'),
   ('similar','请新建 ledger_test.py','workbuddy','c','2026','Write ledger_test.py'),
   ('summary','# 对话历史摘要\n<conversation_history_summary>ledger.py 创建 运行 网络 修改 文件</conversation_history_summary>','workbuddy','d','2027','ledger.py')])
  for q,expected,action in [('WorkBuddy 创建ledger.py 后有没有运行？','write','create'),('WorkBuddy 读 ledger.py 后解释了什么？','read','read')]:
   found=retrieve_candidates(db,{'terms':['ledger.py'],'taskAction':action},q)
   self.assertEqual(select_task(db,{},q,[],found)[0],expected)
   self.assertNotIn('summary',[r[0] for r in found]);self.assertNotIn('similar',[r[0] for r in found])
  db.close()

 def test_pronoun_followup_cannot_be_stolen_by_memory_or_status_topic(self):
  from sessionlens.knowledge import retrieve_candidates,select_task
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('current','写发布脚本','workbuddy','s','2026','Edit memory.md'),('other','研究记忆文件修改方案','codex','x','2027','记忆文件')])
  q='它修改了哪些记忆文件？';plan={'terms':['记忆文件'],'followup':True};found=retrieve_candidates(db,plan,q)
  self.assertEqual(select_task(db,plan,q,[{'retrievedTaskIds':['current']}],found),('current','same_task'));db.close()

 def test_absent_subject_and_ambiguous_question_do_not_guess_shortest_task(self):
  from sessionlens.knowledge import retrieve_candidates,select_task
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('short','看一下','workbuddy','s','2026','工具'),('other','制作销售日报','workbuddy','x','2027','日报工具')])
  q='上次制作北极科考日报用了什么工具？';plan={'terms':['日报'],'subjects':['北极科考']}
  self.assertEqual(select_task(db,plan,q,[],retrieve_candidates(db,plan,q)),(None,'not_found'))
  self.assertEqual(select_task(db,{},'回顾那个任务',[],retrieve_candidates(db,{},'回顾那个任务')),(None,'not_found'));db.close()

 def test_close_matches_request_confirmation_instead_of_guessing(self):
  from sessionlens.knowledge import retrieve_candidates,select_task
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('a','atlas 动画下载下来','workbuddy','s','2026','校验时长'),('b','atlas 动画怎么做的','workbuddy','x','2027','校验时长')])
  q='atlas 动画有没有校验？';found=retrieve_candidates(db,{},q)
  self.assertEqual(select_task(db,{},q,[],found),(None,'choose_task'));db.close()

 def test_topic_switch_prefers_established_agent_before_global_ranking(self):
  import tempfile
  from pathlib import Path
  from unittest.mock import patch
  from sessionlens.knowledge import ask
  with tempfile.TemporaryDirectory() as tmp:
   db=sqlite3.connect(Path(tmp)/'collector.db');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
   db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('old','查北京天气','workbuddy','s','2026','weather'),('exec','生成 MQTT 动画','workbuddy','s','2026','VideoGen'),('dev','生成 MQTT 动画的界面原型','codex','c','2027','MQTT 动画生成完成')]);db.commit();db.close()
   history=[{'question':'北京天气','taskId':'old','retrievedTaskIds':['old'],'selection':{'version':3},'retrieved':[{'title':'查北京天气','source':'workbuddy'}]}]
   packet={'taskId':'exec','prompt':'生成 MQTT 动画','source':'workbuddy'}
   plan={'terms':['MQTT','动画'],'subjects':['MQTT 动画'],'taskAction':'create','followup':False}
   with patch('sessionlens.relay_model.call',return_value=plan),patch('sessionlens.knowledge.packet_for_task',return_value=packet),patch('sessionlens.task_presentation.project',return_value={'taskId':'exec'}),patch('sessionlens.relay_model.answer',return_value={}):
    result=ask(tmp,{'enabled':True,'credentialFile':'configured'},'那次 MQTT 动画怎么做的？',history,lambda _:None)
    self.assertEqual(result['taskId'],'exec')
   db=sqlite3.connect(Path(tmp)/'collector.db');db.execute("DELETE FROM tasks WHERE id='exec'");db.commit();db.close()
   packet={'taskId':'dev','prompt':'生成 MQTT 动画的界面原型','source':'codex'}
   with patch('sessionlens.relay_model.call',return_value=plan),patch('sessionlens.knowledge.packet_for_task',return_value=packet),patch('sessionlens.task_presentation.project',return_value={'taskId':'dev'}),patch('sessionlens.relay_model.answer',return_value={}):
    result=ask(tmp,{'enabled':True,'credentialFile':'configured'},'那次 MQTT 动画怎么做的？',history,lambda _:None)
    self.assertEqual(result['taskId'],'dev')
