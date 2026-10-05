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
  self.assertEqual(select_task(db,{'followup':'false'},'调用了什么',[{'retrievedTaskIds':['ssh']}],[]),(None,'not_found'));db.close()

 def test_direct_task_beats_quoted_conversation_with_incidental_tool_terms(self):
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE tasks(id,prompt,source,session,updated,search)')
  db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?)',[('quoted','用户: 上海天气\n助手: 请查看天气 App','workbuddy','a','2026','上海天气 工具调用'),('lookup','上海天气','workbuddy','b','2026','curl')])
  self.assertEqual(candidates(db,['上海天气','工具调用'],question='查上海天气的需求，调用哪些工具')[0][0],'lookup');db.close()
