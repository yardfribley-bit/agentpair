from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from sessionlens.core import Collector
from sessionlens.desktop import Runtime

class CollectionPerformanceTests(unittest.TestCase):
    def test_cursor_cache_keeps_history_and_appends_without_repeated_full_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);logs=root/'logs';logs.mkdir()
            log=logs/'history.jsonl'
            def row(text):
                return json.dumps({'type':'message','sessionId':'s','role':'user','content':text})+'\n'
            log.write_text(''.join(row(str(i)) for i in range(60)))
            runtime=Runtime(root/'state',{'sources':{'workbuddy':{'enabled':True,'roots':[str(logs)]}},'endpoint':''})
            queries=[]
            class TracedCollector(Collector):
                def __init__(self,*args,**kwargs):
                    super().__init__(*args,**kwargs);self.db.set_trace_callback(queries.append)
            def wait_for(count):
                end=time.monotonic()+5
                while time.monotonic()<end:
                    with closing(sqlite3.connect(runtime.path)) as db:
                        if db.execute('SELECT count(*) FROM events').fetchone()[0]>=count:return
                    time.sleep(.05)
                self.fail('History or appended event did not progress')
            with patch('sessionlens.desktop.Collector',TracedCollector):
                thread=threading.Thread(target=runtime.collect);thread.start()
                try:
                    wait_for(60)
                    with log.open('a') as f:f.write(row('appended'))
                    wait_for(61)
                    time.sleep(.6)
                    self.assertEqual(queries.count('SELECT path,identity,offset FROM cursors'),1)
                    self.assertEqual(queries.count('SELECT path,identity,offset FROM live_cursors'),1)
                    self.assertFalse(runtime.status.get('errors'))
                finally:runtime.stop.set();thread.join(2)
                self.assertFalse(thread.is_alive())
