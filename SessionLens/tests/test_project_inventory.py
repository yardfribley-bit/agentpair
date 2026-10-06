import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from sessionlens.project_inventory import ProjectInventory,candidate_root,write_path,inventory_question

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=sqlite3.connect(':memory:');self.store=ProjectInventory(self.root/'inventory.db',filesystem=False)
        self.source.executescript('''CREATE TABLE task_cursor(name TEXT,value INTEGER);CREATE TABLE tasks(id TEXT,source TEXT);
        CREATE TABLE task_steps(seq INTEGER,event TEXT,task TEXT,kind TEXT,excerpt TEXT);
        CREATE TABLE task_links(turn_task TEXT,root TEXT);CREATE TABLE task_groups(id TEXT,prompt TEXT,updated TEXT,state TEXT);
        INSERT INTO task_cursor VALUES('rowid',100),('boundary',100);''')
    def tearDown(self):self.store.close();self.source.close();self.tmp.cleanup()
    def add(self,seq,path,task='t',agent='workbuddy',tool='Write'):
        if not self.source.execute('SELECT 1 FROM tasks WHERE id=?',(task,)).fetchone():
            self.source.execute('INSERT INTO tasks VALUES(?,?)',(task,agent));self.source.execute('INSERT INTO task_groups VALUES(?,?,?,?)',(task,'开发任务 '+task,'2026-10-06','未知'))
        self.source.execute('INSERT INTO task_steps VALUES(?,?,?,?,?)',(seq,'e'+str(seq),task,'工具调用',tool+' · file_path:\n'+path+'\n\ncontent:\nbody'));self.source.commit()
    def test_not_reads_docs_installs_or_memory_and_source_separation(self):
        for seq,path,tool in [(1,'/Users/a/WorkBuddy/code/alpha/main.py','Write'),(2,'/Users/a/WorkBuddy/code/doc/README.md','Write'),(3,'/Users/a/.workbuddy/memory/rules.py','Edit'),(4,'/Users/a/WorkBuddy/code/beta/main.py','Read')]:self.add(seq,path,tool=tool)
        self.add(5,'/Users/a/WorkBuddy/code/alpha/main.py','other','codex');self.store.sync(self.source)
        snap=self.store.snapshot(self.source,'workbuddy');self.assertEqual([p['name'] for p in snap['projects']],['alpha']);self.assertEqual(snap['counts']['candidate'],1)
        wb=snap['projects'][0];self.assertEqual(len(self.store.details(self.source,wb['id'])['evidence']),1)
    def test_persistent_cursor_idempotence_and_no_skipped_history(self):
        self.source.execute("UPDATE task_cursor SET value=1 WHERE name='rowid'")
        self.add(1,'/work/alpha/main.py');self.add(110,'/work/beta/main.py')
        self.store.sync(self.source,live=True);self.store.sync(self.source);self.assertEqual(self.store._state('history'),1)
        self.add(2,'/work/gamma/main.py');self.source.execute("UPDATE task_cursor SET value=100 WHERE name='rowid'");self.store.sync(self.source)
        self.assertEqual({p['name'] for p in self.store.snapshot(self.source)['projects']},{'alpha','beta','gamma'})
        self.store.sync(self.source);self.assertEqual(self.store.db.execute('SELECT count(*) FROM inventory_writes').fetchone()[0],3)
    def test_merge_rename_exclude_and_task_lineage(self):
        self.add(1,'/work/alpha/main.py');self.add(2,'/work/alpha-copy/main.py','follow');self.add(3,'/work/test/main.py','test')
        self.source.execute("INSERT INTO task_links VALUES('follow','t')");self.store.sync(self.source)
        projects={p['name']:p['id'] for p in self.store.snapshot(self.source)['projects']}
        self.store.review(projects['alpha'],'confirmed','客户平台');self.store.review(projects['alpha-copy'],'candidate',target=projects['alpha']);self.store.review(projects['test'],'excluded')
        snap=self.store.snapshot(self.source);self.assertEqual(snap['counts'],{'identified':0,'confirmed':1,'candidate':0,'excluded':1})
        merged=next(p for p in snap['projects'] if p['state']=='confirmed');self.assertEqual(merged['name'],'客户平台');self.assertEqual(merged['taskCount'],1);self.assertEqual(merged['fileCount'],2)
        with self.assertRaises(ValueError):self.store.review(projects['alpha'],'confirmed',target=projects['alpha-copy'])
    def test_windows_and_content_cannot_forge_a_path(self):
        self.assertEqual(candidate_root('c:/projects/alpha/src/main.ts'),'c:/projects/alpha')
        self.assertIsNone(write_path('Write · content:\nfile_path:\n/work/fake/main.py'))
        self.assertEqual(write_path('Edit · file_path:\n/work/real/main.py\n\nnew_string:\nfile_path:\n/work/fake/main.py'),'/work/real/main.py')
        self.assertTrue(inventory_question('WorkBuddy 一共开发了多少项目，项目名称'));self.assertFalse(inventory_question('这个项目为什么用这个工具参数'))
        self.assertFalse(inventory_question('这个项目调用多少工具'));self.assertEqual(candidate_root('c:/users/a/workbuddy/code/alpha/main.py'),'c:/users/a/workbuddy/code/alpha')
    def test_component_manifest_and_same_name_copies_need_review(self):
        self.source.execute("UPDATE task_cursor SET value=2 WHERE name='rowid'")
        self.add(1,'/tmp/alpha/src/main.py');self.add(2,'/tmp/alpha/frontend/package.json');self.store.sync(self.source)
        snap=self.store.snapshot(self.source);self.assertEqual(snap['counts']['identified'],1);self.assertEqual(snap['projects'][0]['name'],'alpha')
        self.add(3,'/work/alpha/pyproject.toml');self.add(4,'/work/alpha/main.py');self.source.execute("UPDATE task_cursor SET value=4 WHERE name='rowid'");self.store.sync(self.source)
        snap=self.store.snapshot(self.source);self.assertEqual(snap['counts']['identified'],0);self.assertEqual(snap['counts']['candidate'],2)

@unittest.skipUnless(__import__('importlib').util.find_spec('PySide6'),'Qt unavailable')
class InventoryUiTests(unittest.TestCase):
    def test_native_overview_lists_projects_and_original_evidence(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        from sessionlens.project_window import ProjectWindow
        app=QApplication.instance() or QApplication([])
        fixture=InventoryTests();fixture.setUp()
        try:
            fixture.add(1,'/work/alpha/main.py');fixture.store.sync(fixture.source)
            disk=sqlite3.connect(fixture.root/'collector.db');fixture.source.backup(disk);disk.execute('CREATE TABLE events(id TEXT,event TEXT)');disk.execute('INSERT INTO events VALUES(?,?)',('e1',json.dumps({'kind':'tool_call','payload':{'arguments':{'file_path':'/work/alpha/main.py','content':'print(1)'}}})));disk.commit();disk.close()
            fixture.store.close();os.rename(fixture.root/'inventory.db',fixture.root/'project_inventory.db');fixture.store=ProjectInventory(fixture.root/'project_inventory.db')
            window=ProjectWindow(fixture.root);app.processEvents();self.assertEqual(window.projects.count(),1);self.assertIn('alpha',window.title.text());self.assertEqual(window.tasks.count(),1)
            window.proof(0);self.assertIn('print(1)',window.preview.toPlainText());window.close()
        finally:fixture.tearDown()
