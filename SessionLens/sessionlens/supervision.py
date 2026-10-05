"""Incremental task projection. Raw evidence stays in collector.db."""
import json
import re
import sqlite3
from .desktop import category, plain


def readable(value):
    text=value if isinstance(value,str) else plain(value)
    if isinstance(value,dict) and value.get('role')=='user':text=plain(value.get('content',value))
    text=re.sub(r'<in-app-browser-context\b[^>]*>.*?</in-app-browser-context>','',text,flags=re.S)
    return text.strip()[:16000]

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
        CREATE TABLE IF NOT EXISTS task_marks(event TEXT PRIMARY KEY);
        ''')
    def advance(self,limit=200):
        row=self.db.execute("SELECT value FROM task_cursor WHERE name='rowid'").fetchone();cursor=row[0] if row else 0
        records=self.db.execute('SELECT rowid,id,event FROM events WHERE rowid>? ORDER BY rowid LIMIT ?',(cursor,limit)).fetchall()
        with self.db:
            for seq,identity,raw in records:
                e=json.loads(raw);source=e.get('source','codex');session=e['sessionId'];stream=source+':'+session
                kind=category(e);p=e.get('payload',{});item=p.get('item',p);body=readable(item)
                head=self.db.execute('SELECT task,prompt FROM task_heads WHERE stream=?',(stream,)).fetchone()
                # Mirrored user events share a task, until another meaningful action.
                if kind=='用户提问' and body:
                    last=self.db.execute('SELECT kind FROM task_steps WHERE task=? ORDER BY seq DESC LIMIT 1',(head[0],)).fetchone() if head else None
                    duplicate=head and head[1]==body and last and last[0]=='用户提问'
                    if not duplicate:
                        head=(identity,body)
                        self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',(identity,source,session,body,e.get('timestamp') or '', '结束状态未记录',seq,body))
                        self.db.execute('INSERT OR REPLACE INTO task_heads VALUES(?,?,?)',(stream,identity,body))
                if head:
                    task=head[0];k=e['kind'];state='已记录结束' if k=='turn_completed' else '已中止' if k=='turn_aborted' else '进行中 · 日志记录' if k=='turn_started' else None
                    self.db.execute('UPDATE tasks SET updated=?,last_row=?,state=COALESCE(?,state) WHERE id=?',(e.get('timestamp') or '',seq,state,task))
                    if kind in ('用户提问','Agent 回复','解题思路','工具调用','工具返回') or k=='file_change':
                        name=e.get('name') or item.get('name','') if isinstance(item,dict) else e.get('name','')
                        excerpt=(str(name)+' · ' if name else '')+body
                        call=e.get('callId') or (item.get('call_id') if isinstance(item,dict) else None)
                        self.db.execute('INSERT OR IGNORE INTO task_steps VALUES(?,?,?,?,?,?)',(identity,task,seq,'文件修改' if k=='file_change' else kind,excerpt,call))
                        # Bound search text per task; all full evidence remains addressable.
                        self.db.execute('UPDATE tasks SET search=substr(search || char(10) || ?,1,100000) WHERE id=?',(excerpt,task))
                cursor=seq
            if records:self.db.execute("INSERT OR REPLACE INTO task_cursor VALUES('rowid',?)",(cursor,))
        return len(records)
    def tasks(self,query='',source='',live=False):
        clauses=[];args=[]
        if source:clauses.append('source=?');args.append(source)
        for word in query.split():clauses.append('instr(lower(search),lower(?))>0');args.append(word)
        # Monitor lists latest task per session; it does not claim these agents are alive.
        if live:clauses.append('id IN (SELECT task FROM task_heads)')
        sql='SELECT id,source,session,prompt,updated,state,last_row FROM tasks'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY last_row DESC LIMIT 100'
        return self.db.execute(sql,args).fetchall()
    def steps(self,task,limit=300):
        return self.db.execute('SELECT event,kind,excerpt,call_id FROM task_steps WHERE task=? ORDER BY seq LIMIT ?',(task,limit)).fetchall()
    def evidence(self,event):
        row=self.db.execute('SELECT event FROM events WHERE id=?',(event,)).fetchone()
        return json.loads(row[0]) if row else None
    def mark(self,event):
        with self.db:
            if self.marked(event):self.db.execute('DELETE FROM task_marks WHERE event=?',(event,))
            else:self.db.execute('INSERT INTO task_marks VALUES(?)',(event,))
    def marked(self,event):return bool(self.db.execute('SELECT 1 FROM task_marks WHERE event=?',(event,)).fetchone())
    def close(self):self.db.close()
