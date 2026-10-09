import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sessionlens.upload_queue import (
    EMPTY_REQUEST_BYTES, MAX_REQUEST_BYTES, UploadQueue, encode_batch, source_seconds,
)


class UploadQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'collector.db'
        self.db = sqlite3.connect(self.path)
        self.db.executescript('''
            CREATE TABLE events(id TEXT PRIMARY KEY,session TEXT,event TEXT);
            CREATE TABLE deliveries(destination TEXT,id TEXT,PRIMARY KEY(destination,id));
        ''')
        self.queue = UploadQueue(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add(self, identity, timestamp=None, source='codex', payload='', register=True):
        event = {'id': identity, 'sessionId': 'fixture-session', 'source': source,
                 'timestamp': timestamp, 'payload': payload}
        body = json.dumps(event, ensure_ascii=False)
        with self.db:
            cursor = self.db.execute('INSERT INTO events VALUES(?,?,?)',
                                     (identity, 'fixture-session', body))
            if register:
                self.queue.register(cursor.lastrowid, identity, event, len(body.encode()))
        return event

    def restart(self):
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.queue = UploadQueue(self.db)

    def ack(self, items, destination='receiver'):
        ids = [event['id'] for event in items]
        self.queue.acknowledge(destination, ids, expected_ids=ids)

    def test_latest_is_source_time_not_late_arriving_rowid(self):
        self.add('actual-latest', '2026-10-08T12:00:00+08:00')
        self.add('late-imported-old', '2024-10-08T12:00:00+08:00')
        self.add('middle', '2025-10-08T12:00:00+08:00')
        first = self.queue.next_batch('receiver', limit=1)
        self.assertEqual([event['id'] for event in first], ['actual-latest'])
        self.ack(first)
        self.assertEqual(self.queue.next_batch('receiver', limit=1)[0]['id'], 'middle')

    def test_legacy_metadata_backfill_prioritizes_recent_and_preserves_oldest_share(self):
        for n in range(12):self.add(f'legacy-{n}',1_700_000_000+n,register=False)
        order=[]
        for _ in range(4):
            before={r[0] for r in self.db.execute('SELECT id FROM upload_index')}
            self.assertEqual(self.queue.backfill(limit=1,max_seconds=10),1)
            order.extend(r[0] for r in self.db.execute('SELECT id FROM upload_index') if r[0] not in before)
            self.restart()
        self.assertEqual(order,['legacy-11','legacy-10','legacy-9','legacy-0'])
        while self.queue.backfill(limit=2,max_seconds=10):pass
        self.assertEqual(self.queue.stats('receiver')['unindexed'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM events').fetchone()[0],12)
        self.assertEqual(self.queue.backfill(),0)

    def test_recent_migration_meets_existing_forward_checkpoint_without_gaps(self):
        for n in range(8):self.add(f'legacy-{n}',1_700_000_000+n,register=False)
        self.assertEqual(self.queue.index(limit=3,max_seconds=10),3)
        for _ in range(3):self.queue.backfill(limit=2,max_seconds=10)
        self.add('live',1_800_000_000)
        self.restart()
        while self.queue.backfill(limit=2,max_seconds=10):pass
        self.assertEqual(self.queue.stats('receiver')['unindexed'],0)
        self.assertEqual(self.queue.next_batch('receiver',limit=1)[0]['id'],'live')

    def test_historical_index_yields_at_byte_budget_and_recovers_all_records(self):
        for n in range(4):
            self.add(f'backfill-{n}', 1_700_000_000+n, payload='x'*2000, register=False)
        self.assertEqual(self.queue.index(limit=100,max_bytes=3000,max_seconds=10),2)
        self.assertEqual(self.queue.stats('receiver')['unindexed'],2)
        self.restart()
        self.assertEqual(self.queue.index(limit=100,max_bytes=3000,max_seconds=10),2)
        self.assertEqual(self.queue.stats('receiver')['unindexed'],0)
        self.assertEqual(self.queue.index(),0)
        self.assertEqual(len(self.queue.next_batch('receiver')),4)

    def test_historical_index_zero_time_budget_still_commits_one_atomic_record(self):
        for n in range(3):
            self.add(f'timed-{n}', 1_700_000_000+n, register=False)
        self.assertEqual(self.queue.index(limit=100,max_seconds=0),1)
        self.assertEqual(self.queue.stats('receiver')['index_cursor'],1)
        self.assertEqual(self.queue.stats('receiver')['unindexed'],2)
        self.assertEqual(self.queue.index(limit=100,max_seconds=0),1)

    def test_latest_and_historical_orders_use_true_timestamps(self):
        for year in (2024, 2025, 2026, 2023, 2022, 2021):
            self.add(str(year), f'{year}-01-01T00:00:00Z')
        first = self.queue.next_batch('receiver', limit=4)
        self.assertEqual([event['id'] for event in first], ['2026', '2025', '2024', '2021'])

    def test_sources_are_fair_even_with_unequal_backlogs(self):
        for source, count in (('codex', 100), ('workbuddy', 10)):
            for n in range(count):
                self.add(f'{source}-{n}', 1_700_000_000 + n, source)
        items = self.queue.next_batch('receiver', limit=30)
        counts = {source: sum(e['source'] == source for e in items) for source in ('codex', 'workbuddy')}
        self.assertEqual(counts, {'codex': 20, 'workbuddy': 10})
        first = items[:8]
        self.assertEqual(sum(e['source'] == 'codex' for e in first), 4)
        self.assertEqual(sum(e['source'] == 'workbuddy' for e in first), 4)

    def test_both_sources_get_historical_slots_without_cycle_resonance(self):
        for source in ('codex', 'workbuddy'):
            for n in range(10):
                self.add(f'{source}-{n}', 1_700_000_000 + n, source)
        items = self.queue.next_batch('receiver', limit=8)
        self.assertEqual([items[n]['id'] for n in (3, 7)], ['codex-0', 'workbuddy-0'])

    def test_continuous_fresh_arrivals_do_not_starve_history_across_restart(self):
        self.add('oldest', '2020-01-01T00:00:00Z')
        observed = []
        for n in range(4):
            for k in range(3):
                self.add(f'new-{n}-{k}', 1_800_000_000 + n * 3 + k)
            items = self.queue.next_batch('receiver', limit=1)
            observed.extend(e['id'] for e in items)
            self.ack(items)
            self.restart()
        self.assertEqual(observed[-1], 'oldest')
        self.assertTrue(all(identity.startswith('new-') for identity in observed[:3]))

    def assert_large_history_survives_continuous_fresh(self, timestamp):
        base = {'id': 'old-large', 'sessionId': 'fixture-session', 'source': 'codex',
                'timestamp': timestamp, 'payload': ''}
        payload = 'x' * (MAX_REQUEST_BYTES - len(encode_batch([base])))
        event = self.add('old-large', timestamp, payload=payload)
        self.assertEqual(len(encode_batch([event])), MAX_REQUEST_BYTES)
        for n in range(120):
            self.add(f'seed-{n}', 1_900_000_000 + n)
        batches = []
        for round_number in range(4):
            items = self.queue.next_batch('receiver')
            self.assertLessEqual(len(items), 30)
            self.assertLessEqual(len(encode_batch(items)), MAX_REQUEST_BYTES)
            batches.append([item['id'] for item in items])
            self.ack(items)
            for n in range(30):
                self.add(f'fresh-{round_number}-{n}', 1_900_001_000 + round_number * 30 + n)
            self.restart()
        self.assertEqual(len(batches[0]), 3)
        self.assertEqual(batches[1], ['old-large'])
        self.assertEqual(sum('old-large' in batch for batch in batches), 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries WHERE id=?',
                                         ('old-large',)).fetchone()[0], 1)

    def test_max_size_known_history_gets_empty_batch_despite_continuous_fresh(self):
        self.assert_large_history_survives_continuous_fresh(1_000_000_000)

    def test_max_size_unknown_history_gets_empty_batch_despite_continuous_fresh(self):
        self.assert_large_history_survives_continuous_fresh(None)

    def test_unknown_timestamp_is_never_latest_and_both_history_streams_progress(self):
        self.add('old-known', 1_500_000_000)
        self.add('unknown', None)
        for n in range(20):
            self.add(f'fresh-{n}', 1_900_000_000 + n)
        items = self.queue.next_batch('receiver', limit=8)
        self.assertEqual(items[3]['id'], 'unknown')
        self.assertEqual(items[7]['id'], 'old-known')
        self.assertNotIn('unknown', [items[n]['id'] for n in (0, 1, 2, 4, 5, 6)])
        metadata = self.db.execute('SELECT source_time FROM upload_index WHERE id=?', ('unknown',)).fetchone()
        self.assertIsNone(metadata[0])
        self.assertEqual(self.queue.stats('receiver')['unknown_timestamp'], 1)

    def test_invalid_and_nonfinite_timestamps_are_unknown(self):
        for value in (None, '', 'not-a-date', True, float('nan'), float('inf'), -1, 10 ** 1000):
            self.assertIsNone(source_seconds(value))
        self.assertEqual(source_seconds(1_700_000_000_000), 1_700_000_000)
        self.assertEqual(source_seconds('2023-11-14T22:13:20Z'), 1_700_000_000)

    def test_record_failure_bypasses_other_records_and_survives_restart(self):
        self.add('bad-newest', 1_900_000_000)
        self.add('good-same-source', 1_800_000_000)
        self.add('good-other-source', 1_700_000_000, 'workbuddy')
        first = self.queue.next_batch('receiver', limit=1, now=100)
        self.assertEqual(first[0]['id'], 'bad-newest')
        self.queue.fail('receiver', ['bad-newest'], 'fixture rejected', now=100)
        self.restart()
        items = self.queue.next_batch('receiver', now=100.5)
        self.assertEqual({e['id'] for e in items}, {'good-same-source', 'good-other-source'})
        self.assertEqual(self.queue.stats('receiver', now=100.5)['retrying'], 1)
        self.ack(items)
        self.assertEqual(self.queue.next_batch('receiver', now=101)[0]['id'], 'bad-newest')
        self.queue.fail('receiver', ['bad-newest'], now=101)
        self.assertEqual(self.queue.next_batch('receiver', now=102), [])
        self.assertEqual(self.queue.stats('receiver')['errors'][0]['attempts'], 2)

    def test_channel_backoff_only_blocks_that_source_and_is_persistent(self):
        self.add('codex-new', 1_900_000_000)
        self.add('codex-old', 1_800_000_000)
        self.add('workbuddy', 1_700_000_000, 'workbuddy')
        self.queue.fail('receiver', ['codex-new'], now=100, channel=True)
        self.restart()
        self.assertEqual([e['id'] for e in self.queue.next_batch('receiver', now=100.5)], ['workbuddy'])
        self.assertEqual(self.queue.stats('receiver', now=100.5)['retrying'], 2)
        self.assertEqual(self.queue.stats('receiver', now=100.5)['channel_errors'][0]['source'], 'codex')

    def test_exact_ack_survives_restart_and_respects_destination(self):
        for n in range(4):
            self.add(str(n), 1_700_000_000 + n)
        items = self.queue.next_batch('receiver')
        ids = [e['id'] for e in items]
        for receipt in (ids[:-1], ids + ['forged'], ids + [ids[0]]):
            with self.assertRaises(ValueError):
                self.queue.acknowledge('receiver', receipt, expected_ids=ids)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
        self.queue.acknowledge('receiver', list(reversed(ids)), expected_ids=ids)
        self.restart()
        self.assertEqual(self.queue.next_batch('receiver'), [])
        self.assertEqual(len(self.queue.next_batch('another-destination')), 4)

    def test_total_request_utf8_bytes_include_envelope_and_separators(self):
        for n in range(5):
            self.add(str(n), 1_700_000_000 + n, payload='测' * 200_000)
        items = self.queue.next_batch('receiver', limit=100)
        self.assertEqual(len(items), 3)
        self.assertLessEqual(len(encode_batch(items)), MAX_REQUEST_BYTES)
        self.ack(items)
        self.assertEqual(len(self.queue.next_batch('receiver')), 2)
        # A boundary-sized event is accepted exactly, with no hidden envelope
        # allowance that might cause the receiver to reject the batch.
        base = self.add('boundary', 1_900_000_000)
        filler = MAX_REQUEST_BYTES - len(encode_batch([base]))
        base['payload'] = 'x' * filler
        self.db.execute('DELETE FROM events WHERE id=?', ('boundary',))
        self.db.execute('DELETE FROM upload_index WHERE id=?', ('boundary',))
        self.add('boundary', 1_900_000_000, payload=base['payload'])
        selected = self.queue.next_batch('boundary-only', sources=['codex'], limit=1)
        self.assertEqual(selected[0]['id'], 'boundary')
        self.assertEqual(len(encode_batch(selected)), MAX_REQUEST_BYTES)

    def test_thirty_event_cap_and_size_skips_large_candidate_until_next_batch(self):
        for n in range(50):
            self.add(str(n), 1_700_000_000 + n)
        self.assertEqual(len(self.queue.next_batch('receiver', limit=100)), 30)
        # Budget can fit newest plus tiny oldest, but not the next newest big
        # record. That large record remains eligible for its own later batch.
        self.add('large-newest', 1_900_000_002, payload='x' * 200)
        self.add('large-second', 1_900_000_001, payload='x' * 200)
        self.add('small-third', 1_900_000_000)
        items = self.queue.next_batch('size-test', max_bytes=500, limit=3)
        self.assertEqual(items[0]['id'], 'large-newest')
        self.assertNotIn('large-second', [e['id'] for e in items])
        self.assertIn('small-third', [e['id'] for e in items])
        self.assertLessEqual(len(encode_batch(items)), 500)
        self.ack(items, 'size-test')
        self.assertEqual(self.queue.next_batch('size-test', limit=1)[0]['id'], 'large-second')

    def test_oversize_is_quarantined_visible_and_not_acknowledged(self):
        base = self.add('oversize', 1_900_000_000)
        filler = MAX_REQUEST_BYTES - len(encode_batch([base])) + 1
        self.db.execute('DELETE FROM events WHERE id=?', ('oversize',))
        self.db.execute('DELETE FROM upload_index WHERE id=?', ('oversize',))
        self.add('oversize', 1_900_000_000, payload='x' * filler)
        self.add('small', 1_800_000_000)
        items = self.queue.next_batch('receiver')
        self.assertEqual([e['id'] for e in items], ['small'])
        self.ack(items)
        stats = self.queue.stats('receiver')
        self.assertEqual((stats['pending'], stats['oversize'], stats['quarantined']), (1, 1, 1))
        self.assertEqual(stats['quarantine_details'][0]['id'], 'oversize')
        self.assertIn('request requires', stats['quarantine_details'][0]['reason'])
        with self.assertRaises(ValueError):
            self.queue.acknowledge('receiver', ['oversize'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 1)

    def test_incremental_backfill_does_not_block_live_events_or_redecode_idle_json(self):
        for n in range(7):
            self.add(f'history-{n}', 1_500_000_000 + n, register=False)
        self.add('live', 1_900_000_000)
        self.assertEqual(self.queue.next_batch('receiver', limit=1)[0]['id'], 'live')
        self.assertEqual(self.queue.stats('receiver')['unindexed'], 7)
        self.assertFalse(self.queue.stats('receiver')['index_complete'])
        self.assertEqual(self.queue.index(2), 2)
        self.restart()
        self.assertEqual(self.queue.stats('receiver')['index_cursor'], 2)
        self.assertEqual(self.queue.index(2), 2)
        self.assertEqual(self.queue.index(100), 4)
        self.assertTrue(self.queue.stats('receiver')['index_complete'])
        with patch('sessionlens.upload_queue.json.loads', side_effect=AssertionError('Historical JSON rescan')):
            self.assertEqual(self.queue.index(100), 0)
            self.assertEqual(self.queue.stats('receiver')['pending'], 8)

    def test_registration_rolls_back_with_evidence_and_legacy_deliveries_are_respected(self):
        event = {'id': 'rolled-back', 'source': 'codex', 'timestamp': 1_900_000_000}
        with self.assertRaises(RuntimeError):
            with self.db:
                cursor = self.db.execute('INSERT INTO events VALUES(?,?,?)',
                                         ('rolled-back', 'fixture', json.dumps(event)))
                self.queue.register(cursor.lastrowid, 'rolled-back', event)
                raise RuntimeError('fixture rollback')
        self.assertEqual(self.queue.stats('receiver')['indexed'], 0)
        self.add('legacy-ack', 1_900_000_000, register=False)
        with self.db:
            self.db.execute('INSERT INTO deliveries VALUES(?,?)', ('receiver', 'legacy-ack'))
        self.queue.index()
        self.assertEqual(self.queue.next_batch('receiver'), [])

    def test_source_filters_and_empty_filters(self):
        self.add('codex', 1_900_000_000)
        self.add('workbuddy', 1_900_000_001, 'workbuddy')
        self.assertEqual(self.queue.next_batch('receiver', sources=[]), [])
        self.assertEqual(self.queue.stats('receiver', sources=[])['pending'], 0)
        self.assertEqual([e['id'] for e in self.queue.next_batch('receiver', sources=['workbuddy'])], ['workbuddy'])


if __name__ == '__main__':
    unittest.main()
