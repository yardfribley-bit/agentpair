"""Incremental task projection. Raw evidence stays in collector.db."""
import json
import re
import sqlite3
from .desktop import category, plain


def text_content(value,budget=128000):
    if isinstance(value,str):return value[:budget]
    if isinstance(value,list):return '\n'.join(text_content(x,budget) for x in value[:64])[:budget]
    if isinstance(value,dict):
        for key in ('text','content','message','summary','output','input','arguments','command','stdout'):
            if key in value:return text_content(value[key],budget)
    return plain(value)

def readable(value,user=False):
    text=text_content(value)
    if user:
        # Context wrappers are data, not fresh user requests. Explicit queries win.
        if re.match(r'\s*<(cb_summary|conversation_history_summary)\b',text):return ''
        queries=re.findall(r'<user_query\b[^>]*>(.*?)</user_query>',text,re.S)
        if queries:text=queries[-1]
        else:
            text=re.sub(r'<(system-reminder|in-app-browser-context)\b[^>]*>.*?</\1>','',text,flags=re.S)
            if re.match(r'\s*<(environment_context|permissions|instructions)\b',text):return ''
    return text.strip()[:16000]

def search_terms(query):
    query=re.sub(r'^(请|帮我|帮忙|查一下|查询一下|查询|搜索一下|搜索|查)+','',query.strip())
    # Chinese queries are matched as short words so 上海的天气 / 上海 天气 both match.
    return re.findall(r'[a-zA-Z0-9_./-]+|[\u4e00-\u9fff]{1,2}',query.lower())

def describe(kind,excerpt):
    names={'WebSearch':'搜索网页','WebFetch':'读取网页','Read':'读取文件','ReadFile':'读取文件','Bash':'运行命令','ExecuteCommand':'运行命令','Edit':'修改文件','Write':'写入文件','VideoGen':'生成视频'}
    if kind=='工具调用':
        name,_,args=excerpt.partition(' · ')
        return names.get(name,'调用 '+name),args
    return {'用户提问':'你的要求','Agent 回复':'Agent 的回应','解题思路':'记录中的处理思路','工具返回':'工具给出的结果','文件修改':'文件发生的变化'}.get(kind,kind),excerpt

class TaskStore:
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=10)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS task_cursor(name TEXT PRIMARY KEY,value INTEGER);
        CREATE TABLE IF NOT EXISTS task_heads(stream TEXT PRIMARY KEY,task TEXT,prompt TEXT);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,source TEXT,session TEXT,prompt TEXT,updated TEXT,state TEXT,last_row INTEGER,search TEXT);
        CREATE INDEX IF NOT EXISTS tasks_recent ON tasks(last_row DESC);
        CREATE TABLE IF NOT EXISTS task_steps(event TEXT PRIMARY KEY,task TEXT,seq INTEGER,kind TEXT,excerpt TEXT,call_id TEXT);
        CREATE INDEX IF NOT EXISTS task_step_order ON task_steps(task,seq);
        CREATE TABLE IF NOT EXISTS task_history_heads(stream TEXT PRIMARY KEY,task TEXT,prompt TEXT);
        CREATE TABLE IF NOT EXISTS task_marks(event TEXT PRIMARY KEY);
        ''')
        version=self.db.execute("SELECT value FROM task_cursor WHERE name='version'").fetchone()
        if not version or version[0]!=2:
            with self.db:
                # Rebuild derived data only, preserving raw evidence and marks.
                for table in ('tasks','task_steps','task_heads','task_history_heads','task_cursor'):self.db.execute('DELETE FROM '+table)
                boundary=self.db.execute('SELECT COALESCE(max(rowid),0) FROM events').fetchone()[0]
                self.db.executemany('INSERT INTO task_cursor VALUES(?,?)',[('version',2),('boundary',boundary),('live',max(0,boundary-2000)),('rowid',0)])
    def advance(self,limit=100,realtime=False,records=None):
        key='live' if realtime else 'rowid'
        row=self.db.execute('SELECT value FROM task_cursor WHERE name=?',(key,)).fetchone();cursor=row[0] if row else 0
        boundary=self.db.execute("SELECT value FROM task_cursor WHERE name='boundary'").fetchone()[0]
        cap=9223372036854775807 if realtime else boundary
        custom=records is not None
        if records is None:records=self.db.execute('SELECT rowid,id,event FROM events WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',(cursor,cap,limit)).fetchall()
        heads='task_heads' if realtime else 'task_history_heads'
        with self.db:
            for seq,identity,raw in records:
                e=json.loads(raw);source=e.get('source','codex');session=e['sessionId'];stream=source+':'+session
                kind=category(e);p=e.get('payload',{});item=p.get('item',p);body=readable(item,user=kind=='用户提问')
                head=self.db.execute('SELECT task,prompt FROM '+heads+' WHERE stream=?',(stream,)).fetchone()
                # Mirrored user events share a task, until another meaningful action.
                if kind=='用户提问' and body:
                    last=self.db.execute('SELECT kind FROM task_steps WHERE task=? ORDER BY seq DESC LIMIT 1',(head[0],)).fetchone() if head else None
                    duplicate=head and head[1]==body and last and last[0]=='用户提问'
                    if not duplicate:
                        head=(identity,body)
                        self.db.execute('INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,?)',(identity,source,session,body,e.get('timestamp') or '', '结束状态未记录',seq,body))
                        self.db.execute('INSERT OR REPLACE INTO '+heads+' VALUES(?,?,?)',(stream,identity,body))
                if head:
                    if kind=='用户提问' and not body:kind='会话背景'
                    task=head[0];k=e['kind'];state='已记录结束' if k=='turn_completed' else '已中止' if k=='turn_aborted' else '进行中 · 日志记录' if k=='turn_started' else None
                    self.db.execute('UPDATE tasks SET updated=?,last_row=?,state=COALESCE(?,state) WHERE id=? AND last_row<=?',(str(e.get('timestamp') or ''),seq,state,task,seq))
                    current=self.db.execute('SELECT t.last_row FROM task_heads h JOIN tasks t ON h.task=t.id WHERE h.stream=?',(stream,)).fetchone()
                    if not current or seq>=current[0]:self.db.execute('INSERT OR REPLACE INTO task_heads VALUES(?,?,?)',(stream,task,head[1]))
                    if kind in ('用户提问','Agent 回复','解题思路','工具调用','工具返回') or k=='file_change':
                        name=e.get('name') or item.get('name','') if isinstance(item,dict) else e.get('name','')
                        excerpt=(str(name)+' · ' if name else '')+body
                        call=e.get('callId') or (item.get('call_id') if isinstance(item,dict) else None)
                        self.db.execute('INSERT OR IGNORE INTO task_steps VALUES(?,?,?,?,?,?)',(identity,task,seq,'文件修改' if k=='file_change' else kind,excerpt,call))
                        # Bound search text per task; all full evidence remains addressable.
                        self.db.execute('UPDATE tasks SET search=substr(search || char(10) || ?,1,100000) WHERE id=?',(excerpt,task))
                cursor=seq
            if records and not custom:self.db.execute('INSERT OR REPLACE INTO task_cursor VALUES(?,?)',(key,cursor))
        return len(records)
    def recent_source(self,source,limit=500):
        # Priority warm-up for both agents, so Codex history cannot starve WorkBuddy.
        return self.db.execute('SELECT e.rowid,e.id,e.event FROM display_index i JOIN events e ON e.id=i.id WHERE i.source=? ORDER BY e.rowid DESC LIMIT ?',(source,limit)).fetchall()[::-1]
    def tasks(self,query='',source='',live=False):
        clauses=[];args=[]
        if source:clauses.append('source=?');args.append(source)
        for word in search_terms(query):clauses.append('instr(lower(search),lower(?))>0');args.append(word)
        # Monitor lists latest task per session; it does not claim these agents are alive.
        if live:clauses.append('id IN (SELECT task FROM task_heads)')
        sql='SELECT id,source,session,prompt,updated,state,last_row FROM tasks'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY last_row DESC LIMIT 100'
        return self.db.execute(sql,args).fetchall()
    def steps(self,task,limit=300,latest=False):
        rows=self.db.execute('SELECT event,kind,excerpt,call_id FROM task_steps WHERE task=? ORDER BY seq '+('DESC' if latest else 'ASC')+' LIMIT ?',(task,limit)).fetchall()
        return rows[::-1] if latest else rows
    def evidence(self,event):
        row=self.db.execute('SELECT event FROM events WHERE id=?',(event,)).fetchone()
        return json.loads(row[0]) if row else None
    def mark(self,event):
        with self.db:
            if self.marked(event):self.db.execute('DELETE FROM task_marks WHERE event=?',(event,))
            else:self.db.execute('INSERT INTO task_marks VALUES(?)',(event,))
    def marked(self,event):return bool(self.db.execute('SELECT 1 FROM task_marks WHERE event=?',(event,)).fetchone())
    def close(self):self.db.close()
