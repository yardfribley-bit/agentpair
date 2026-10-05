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
