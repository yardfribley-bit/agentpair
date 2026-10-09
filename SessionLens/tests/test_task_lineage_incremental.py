import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sessionlens.core import Collector
from sessionlens.supervision import TaskStore
from sessionlens import task_lineage


class IncrementalLineageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'collector.db'
        self.log = Path(self.temp.name) / 'fixture.jsonl'
        self.collector = Collector(self.path)
        self.store = TaskStore(self.path)
        self.other = sqlite3.connect(self.path, timeout=.1)
        self.other.execute('CREATE TABLE fixture_writes(value INTEGER)')
        self.other.commit()

    def tearDown(self):
        self.other.close()
        self.store.close()
        self.collector.db.close()
        self.temp.cleanup()

    def ingest(self, records, repair=True):
        before = self.collector.db.execute('SELECT coalesce(max(rowid),0) FROM events').fetchone()[0]
        with self.log.open('a', encoding='utf-8') as stream:
            for record in records:
                stream.write(json.dumps({'sessionId': 'fixture', **record}, ensure_ascii=False) + '\n')
        self.collector.scan(self.log, source='workbuddy')
        rows = self.store.db.execute('SELECT rowid,id,event FROM events WHERE rowid>? ORDER BY rowid', (before,)).fetchall()
        self.store.advance(records=rows, realtime=True)
        if repair:
            while self.store.repair_links(10):
                pass
        return [row[1] for row in rows]

    def seed(self, tools=2):
        records = [{'type': 'message', 'role': 'user', 'content': '设计登录页面'},
                   {'type': 'message', 'role': 'assistant', 'content': '方案：使用手机号登录'},
                   {'type': 'message', 'role': 'user', 'content': '支持手机号'},
                   {'type': 'message', 'role': 'user', 'content': '做'}]
        for n in range(tools):
            records.extend([{'type': 'function_call', 'name': 'Write', 'callId': str(n), 'arguments': {'path': f'{n}.py'}},
                            {'type': 'function_call_result', 'callId': str(n), 'output': 'fixture written'}])
        return self.ingest(records)

    def projection(self):
        return {table: self.store.db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                for table in ('task_groups', 'task_links', 'task_executions', 'task_step_owners')}

    def test_unchanged_rebuild_only_clears_queue_without_rewriting_relationships(self):
        self.seed(tools=200)
        previous = self.projection()
        with self.store.db:
            task_lineage.enqueue(self.store.db, 'workbuddy', 'fixture', 404)
        before = self.store.db.total_changes
        count = task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture')
        self.assertEqual(count, 3)
        self.assertEqual(self.store.db.total_changes - before, 1)
        self.assertEqual(self.projection(), previous)
        self.assertFalse(self.store.db.in_transaction)

    def test_another_connection_can_write_during_classification(self):
        self.seed()
        classify = task_lineage.classify
        wrote = []

        def writing_classifier(*args):
            self.assertFalse(self.store.db.in_transaction)
            if not wrote:
                with self.other:
                    self.other.execute('INSERT INTO fixture_writes VALUES(1)')
                wrote.append(True)
            return classify(*args)

        with patch('sessionlens.task_lineage.classify', side_effect=writing_classifier):
            self.assertEqual(task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture'), 3)
        self.assertEqual(self.other.execute('SELECT value FROM fixture_writes').fetchall(), [(1,)])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM task_link_queue').fetchone()[0], 0)

    def test_appended_tool_updates_only_its_binding_owner_and_group(self):
        ids = self.seed(tools=100)
        previous = self.projection()
        new = self.ingest([{'type': 'function_call', 'name': 'Write', 'callId': 'new', 'arguments': {'path': 'new.py'}}], repair=False)[0]
        before = self.store.db.total_changes
        task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture')
        self.assertEqual(self.store.db.total_changes - before, 4)
        current = self.projection()
        for table in ('task_links', 'task_executions', 'task_step_owners'):
            self.assertEqual([row for row in current[table] if row[0] != new], previous[table])
        self.assertEqual(self.store.db.execute('SELECT task,turn_event FROM task_step_owners WHERE event=?', (new,)).fetchone(), (ids[0], ids[3]))
        self.assertEqual(self.store.db.execute('SELECT task,turn_event,requirement_event FROM task_executions WHERE event=?', (new,)).fetchone(), (ids[0], ids[3], ids[2]))

    def test_manual_override_during_classification_preserves_projection_and_retries(self):
        ids = self.seed()
        previous = self.projection()
        classify = task_lineage.classify
        wrote = []

        def overriding_classifier(*args):
            if not wrote:
                with self.other:
                    self.other.execute('INSERT INTO task_link_overrides VALUES(?,NULL)', (ids[2],))
                wrote.append(True)
            return classify(*args)

        with patch('sessionlens.task_lineage.classify', side_effect=overriding_classifier):
            self.assertEqual(task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture'), 0)
        self.assertEqual(self.projection(), previous)
        self.assertIsNotNone(self.store.db.execute('SELECT updated FROM task_link_queue').fetchone())
        self.assertEqual(self.other.execute('SELECT target_turn FROM task_link_overrides WHERE turn_task=?', (ids[2],)).fetchone(), (None,))
        task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture')
        self.assertEqual(task_lineage.resolve(self.store.db, ids[2]), ids[2])
        self.assertEqual(task_lineage.resolve(self.store.db, ids[3]), ids[2])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM task_link_queue').fetchone()[0], 0)

    def test_semantic_signature_change_without_new_seq_invalidates_snapshot(self):
        ids = self.seed()
        with self.store.db:
            self.store.db.execute('INSERT INTO task_semantic_links VALUES(?,?,?,?,?,?)',
                                  (ids[2], ids[0], 'revision', 'fixture semantic review', 'before', '[]'))
        task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture')
        previous = self.projection()
        classify = task_lineage.classify
        wrote = []

        def semantic_classifier(*args):
            if not wrote:
                with self.other:
                    self.other.execute('UPDATE task_semantic_links SET signature=?,evidence_turns=? WHERE turn_task=?',
                                       ('after', '["new evidence"]', ids[2]))
                wrote.append(True)
            return classify(*args)

        with patch('sessionlens.task_lineage.classify', side_effect=semantic_classifier):
            self.assertEqual(task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture'), 0)
        self.assertEqual(self.projection(), previous)
        self.assertIsNotNone(self.store.db.execute('SELECT updated FROM task_link_queue').fetchone())

    def test_projection_baseline_change_with_identical_inputs_is_not_silently_cleared(self):
        ids = self.seed()
        classify = task_lineage.classify
        wrote = []

        def baseline_classifier(*args):
            if not wrote:
                # Simulate the projection left by an A -> B -> A review window:
                # compact input is identical again, projected ownership differs.
                with self.other:
                    self.other.execute('UPDATE task_step_owners SET task=? WHERE event=?', (ids[2], ids[4]))
                wrote.append(True)
            return classify(*args)

        with patch('sessionlens.task_lineage.classify', side_effect=baseline_classifier):
            self.assertEqual(task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture'), 0)
        self.assertEqual(self.store.db.execute('SELECT task FROM task_step_owners WHERE event=?', (ids[4],)).fetchone(), (ids[2],))
        self.assertIsNotNone(self.store.db.execute('SELECT updated FROM task_link_queue').fetchone())
        task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture')
        self.assertEqual(self.store.db.execute('SELECT task FROM task_step_owners WHERE event=?', (ids[4],)).fetchone(), (ids[0],))

    def test_unclassified_steps_keep_original_owners_and_only_invalid_projection_is_removed(self):
        ids = self.seed()
        background = self.ingest([{'type': 'message', 'role': 'user', 'content': 'temporary fixture turn'},
                                  {'type': 'function_call', 'name': 'Read', 'callId': 'background', 'arguments': 'fixture'}], repair=False)
        with self.store.db:
            self.store.db.execute('UPDATE tasks SET prompt=? WHERE id=?', ('The following is the Codex agent history fixture', background[0]))
            self.store.db.execute('UPDATE task_steps SET kind=? WHERE event=?', ('解题思路', ids[4]))
        old_owners = self.store.db.execute('SELECT event,task,turn_event FROM task_step_owners WHERE event IN (?,?) ORDER BY event', background).fetchall()
        task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture')
        self.assertEqual(self.store.db.execute('SELECT event,task,turn_event FROM task_step_owners WHERE event IN (?,?) ORDER BY event', background).fetchall(), old_owners)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM task_executions WHERE event=?', (ids[4],)).fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM task_step_owners WHERE event=?', (ids[4],)).fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM task_step_owners WHERE event=?', (background[1],)).fetchone()[0], 1)
        # A legacy owner with an incorrect original turn is repaired even when
        # that background turn intentionally has no classified snapshot.
        with self.store.db:
            self.store.db.execute('UPDATE task_step_owners SET turn_event=? WHERE event=?', (ids[0], background[1]))
            self.store.db.execute('DELETE FROM task_step_owners WHERE event=?', (background[0],))
        task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture')
        for identity in background:
            self.assertEqual(self.store.db.execute('SELECT task,turn_event FROM task_step_owners WHERE event=?', (identity,)).fetchone(),
                             (background[0], background[0]))

    def test_outer_review_transaction_retains_atomicity_and_rollback_ownership(self):
        ids = self.seed()
        previous = self.projection()
        self.store.db.execute('BEGIN IMMEDIATE')
        self.store.db.execute('INSERT INTO task_link_overrides VALUES(?,NULL)', (ids[2],))
        task_lineage.rebuild_session(self.store.db, 'workbuddy', 'fixture')
        self.assertTrue(self.store.db.in_transaction)
        self.assertEqual(task_lineage.resolve(self.store.db, ids[2]), ids[2])
        self.assertEqual(task_lineage.resolve(self.other, ids[2]), ids[0])
        self.store.db.rollback()
        self.assertEqual(self.projection(), previous)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM task_link_overrides').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
