"""Background indexing must not block searches or skip source records."""
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from tests import test_data_center as fixtures

class BackgroundIndexTests(unittest.TestCase):
 setUp=fixtures.DataCenterTests.setUp
 tearDown=fixtures.DataCenterTests.tearDown
 event=fixtures.DataCenterTests.event
 upload=fixtures.DataCenterTests.upload
 context=fixtures.DataCenterTests.context
 find=fixtures.DataCenterTests.find
 def test_raw_source_connections_close_before_index_writes(self):
  self.upload([self.event('source-lock')]);self.context('source-app','fixture')
  active=[];session_connect=self.sessions.connect;device_connect=self.devices.connect;put=self.center._put
  @contextmanager
  def guarded(original):
   with original() as db:
    active.append(1)
    try:yield db
    finally:active.pop()
  def write(*args):
   self.assertEqual(active,[], 'Index writes must not retain source read transactions')
   return put(*args)
  with patch.object(self.sessions,'connect',lambda:guarded(session_connect)),patch.object(self.devices,'connect',lambda:guarded(device_connect)),patch.object(self.center,'_put',side_effect=write):
   with self.center._db() as db:self.center._sync_batch(db)
 def test_batch_advances_only_processed_rows(self):
  self.upload([self.event(str(i)) for i in range(7)])
  with self.center._db() as db:self.center._sync_batch(db,session_limit=2)
  with self.center._db() as db:
   self.assertEqual(db.execute('select count(*) from dc_records').fetchone()[0],2)
   self.assertEqual(int(self.center._state(db,'session_seq')),2)
   self.assertEqual(int(self.center._state(db,'background_pending')),5)
  for _ in range(3):
   with self.center._db() as db:self.center._sync_batch(db,session_limit=2)
  with self.center._db() as db:self.assertEqual(db.execute('select count(*) from dc_records').fetchone()[0],7)
 def test_app_equal_receipts_cross_batch_boundary(self):
  for i in range(5):self.context('app'+str(i),'fixture',received=1700000100)
  for _ in range(3):
   with self.center._db() as db:self.center._sync_batch(db,app_limit=2)
  with self.center._db() as db:self.assertEqual(db.execute("select count(*) from dc_records where collector='applens'").fetchone()[0],5)
  with patch.object(self.center,'_put',wraps=self.center._put) as put:
   with self.center._db() as db:self.center._sync_batch(db,app_limit=2)
   self.assertEqual(put.call_count,0)
 def test_failed_batch_rolls_back_cursor_and_records(self):
  self.upload([self.event(str(i)) for i in range(3)]);original=self.center._put;calls=[]
  def fail(*a):
   calls.append(1)
   if len(calls)==2:raise RuntimeError('fixture fail')
   return original(*a)
  with patch.object(self.center,'_put',side_effect=fail):
   with self.assertRaises(RuntimeError):
    with self.center._db() as db:self.center._sync_batch(db)
  with self.center._db() as db:
   self.assertEqual(db.execute('select count(*) from dc_records').fetchone()[0],0)
   self.assertEqual(self.center._state(db,'session_seq'),'0')
 def test_search_does_not_wait_for_writer_or_sync(self):
  self.upload([self.event('seed')]);self.find()
  self.center._worker={'running':True,'error':None}
  held=threading.Event();release=threading.Event()
  def writer():
   with self.center.lock:held.set();release.wait(4)
  t=threading.Thread(target=writer);t.start();held.wait(1)
  try:
   with patch.object(self.center,'_sync',side_effect=AssertionError('GET must not sync')):
    r=self.find();self.assertEqual(r['total'],1);self.assertEqual(r['coverage']['indexSync']['mode'],'background')
  finally:release.set();t.join()
 def test_sync_error_keeps_results_and_is_visible(self):
  self.upload([self.event('seed')]);self.find();self.center._worker={'running':False,'error':'fixture storage failure'}
  r=self.find();self.assertEqual(r['total'],1);self.assertEqual(r['coverage']['indexSync']['error'],'fixture storage failure')
