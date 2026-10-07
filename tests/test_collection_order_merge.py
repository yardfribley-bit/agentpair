"""Source order and real missing predecessors across two retrieval windows."""
import unittest

from agentpair.collection_assistant import normalize_turn_order


def turn(identity, records=(), source='workbuddy', session='s', device='d'):
    ids = ['sessionlens:' + identity, *('sessionlens:' + value for value in records)]
    return {'turnId': identity, 'recordId': ids[0], 'recordIds': ids[:], 'allRecordIds': ids[:],
            'source': source, 'sessionId': session, 'deviceId': device, 'seq': 0,
            'records': [{'recordId': value, 'seq': i} for i, value in enumerate(ids)],
            'hasTurnGapBefore': False}


def meta(identity, offset, predecessor=None, epoch=0, file='same-file', timestamp=0, receipt=0):
    return ('sessionlens:' + identity, {'fileIdentity': file, 'byteStart': offset,
            'previousUserId': predecessor, 'epoch': epoch, 'timestamp': timestamp, 'seq': receipt})


class CollectionOrderMergeTests(unittest.TestCase):
    def test_two_window_positions_do_not_make_future_reply_a_prior_plan(self):
        first = turn('u1', ['proposal', 'late-reply'])
        second = turn('u2', ['write'])
        metadata = dict([meta('u1', 0, receipt=99), meta('proposal', 100, receipt=98),
                         meta('u2', 200, predecessor='u1', receipt=3),
                         meta('write', 300, receipt=2), meta('late-reply', 400, receipt=1)])
        normalize_turn_order([first, second], metadata)
        self.assertEqual(first['seq'], 0)
        self.assertEqual(second['seq'], 2)
        self.assertEqual([record['seq'] for record in first['records']], [0, 1, 4])
        self.assertLess(first['records'][1]['seq'], second['seq'])
        self.assertGreater(first['records'][2]['seq'], second['seq'])
        self.assertFalse(second['hasTurnGapBefore'])

    def test_disjoint_windows_detect_omitted_user_even_with_consecutive_local_positions(self):
        first, second = turn('u1'), turn('u9')
        metadata = dict([meta('u1', 0), meta('u9', 900, predecessor='u8')])
        normalize_turn_order([first, second], metadata)
        self.assertEqual((first['seq'], second['seq']), (0, 1))
        self.assertTrue(second['hasTurnGapBefore'])

    def test_first_selected_round_keeps_missing_prior_user_gap(self):
        selected = turn('middle')
        normalize_turn_order([selected], dict([meta('middle', 400, predecessor='not-selected')]))
        self.assertTrue(selected['hasTurnGapBefore'])

    def test_mirrored_user_predecessor_inside_prior_round_is_not_false_gap(self):
        first, second = turn('u1', ['mirror-u1']), turn('u2')
        metadata = dict([meta('u1', 0), meta('mirror-u1', 20), meta('u2', 100, predecessor='mirror-u1')])
        normalize_turn_order([first, second], metadata)
        self.assertFalse(second['hasTurnGapBefore'])

    def test_rotation_epoch_precedes_byte_offset_and_upload_order(self):
        first, second = turn('old', ['old-call']), turn('new', ['new-call'])
        metadata = dict([meta('old', 900, epoch=0, receipt=99), meta('old-call', 950, epoch=0, receipt=98),
                         meta('new', 0, epoch=1, predecessor='old', receipt=2),
                         meta('new-call', 50, epoch=1, receipt=1)])
        normalize_turn_order([first, second], metadata)
        self.assertEqual([record['seq'] for record in first['records']], [0, 1])
        self.assertEqual([record['seq'] for record in second['records']], [2, 3])

    def test_different_files_use_timestamp_without_borrowing_another_agent_predecessor(self):
        old = turn('old', ['old-result'])
        other = turn('codex', source='codex')
        new = turn('new')
        metadata = dict([meta('old', 0, file='file1', timestamp=10, receipt=99),
                         meta('old-result', 10, file='file1', timestamp=15, receipt=98),
                         meta('codex', 0, file='codex-file', timestamp=18, receipt=3),
                         meta('new', 0, file='file2', timestamp=20, receipt=1, predecessor='old')])
        normalize_turn_order([old, other, new], metadata)
        self.assertLess(old['seq'], old['records'][1]['seq'])
        self.assertLess(old['records'][1]['seq'], new['seq'])
        self.assertFalse(new['hasTurnGapBefore'])


if __name__ == '__main__':
    unittest.main()
