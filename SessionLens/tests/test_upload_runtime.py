import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

from sessionlens.core import Collector
from sessionlens.desktop import Runtime


class LimitedStop:
    def __init__(self,waits=3):self.waits=waits;self.count=0
    def is_set(self):return self.count>=self.waits
    def wait(self,seconds):self.count+=1;return self.is_set()


class UploadRuntimeTests(unittest.TestCase):
    def seed(self,root,count=80):
        c=Collector(Path(root)/'collector.db')
        with c.db:
            for n in range(count):
                event={'id':str(n),'source':'codex' if n%2 else 'workbuddy','sessionId':'s','kind':'tool_call',
                       'timestamp':1791400000+n,'payload':{'name':'tool','arguments':{'item':n}}}
                body=json.dumps(event,ensure_ascii=False)
                row=c.db.execute('INSERT INTO events VALUES(?,?,?)',(event['id'],'s',body))
                c.uploads.register(row.lastrowid,event['id'],event)
        c.db.close()

    def runtime(self,root):
        return Runtime(root,{'endpoint':'https://example.com/events','sources':{'codex':{'enabled':True},'workbuddy':{'enabled':True}}},'fixture-token')

    def test_failed_batch_is_retained_and_other_records_keep_uploading(self):
        with tempfile.TemporaryDirectory() as root:
            runtime=self.runtime(root);self.seed(root);runtime.stop=LimitedStop(3);sent=[]
            class Opener:
                def open(self,req,timeout):
                    events=json.loads(req.data)['events'];ids=[e['id'] for e in events];sent.append(ids)
                    if len(sent)==1:raise urllib.error.HTTPError(req.full_url,503,'fixture failure',{},None)
                    return io.BytesIO(json.dumps({'ids':ids}).encode())
            with patch('sessionlens.desktop.urllib.request.build_opener',return_value=Opener()):runtime.upload()
            c=Collector(Path(root)/'collector.db')
            ack={r[0] for r in c.db.execute('SELECT id FROM deliveries WHERE destination=?',(runtime.destination,))}
            self.assertEqual(len(sent),3);self.assertEqual(len(ack),50)
            self.assertFalse(set(sent[0])&ack);self.assertTrue(set(sent[1])<=ack)
            self.assertEqual(c.db.execute('SELECT count(*) FROM upload_failures').fetchone()[0],30);c.db.close()

    def test_incomplete_receipt_never_marks_unsent_records_received(self):
        with tempfile.TemporaryDirectory() as root:
            runtime=self.runtime(root);self.seed(root,5);runtime.stop=LimitedStop(2)
            class Opener:
                def open(self,req,timeout):
                    ids=[e['id'] for e in json.loads(req.data)['events']]
                    return io.BytesIO(json.dumps({'ids':ids[:-1]}).encode())
            with patch('sessionlens.desktop.urllib.request.build_opener',return_value=Opener()):runtime.upload()
            c=Collector(Path(root)/'collector.db')
            self.assertEqual(c.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
            self.assertEqual(c.db.execute('SELECT count(*) FROM upload_failures').fetchone()[0],5);c.db.close()

    def test_legacy_receipts_survive_and_success_status_does_not_claim_full_sync(self):
        with tempfile.TemporaryDirectory() as root:
            runtime=self.runtime(root);self.seed(root,40);runtime.stop=LimitedStop(1);sent=[]
            c=Collector(Path(root)/'collector.db');c.acknowledge(runtime.destination,['0']);c.db.close()
            class Opener:
                def open(self,req,timeout):
                    ids=[e['id'] for e in json.loads(req.data)['events']];sent.extend(ids)
                    return io.BytesIO(json.dumps({'ids':ids}).encode())
            with patch('sessionlens.desktop.urllib.request.build_opener',return_value=Opener()):runtime.upload()
            self.assertNotIn('0',sent);self.assertEqual(len(sent),30)
            self.assertEqual(runtime.status['upload'],'本批已接收 · 继续同步其余记录')


if __name__=='__main__':unittest.main()
