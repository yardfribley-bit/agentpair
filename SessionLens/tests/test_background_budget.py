import tempfile
import unittest
from pathlib import Path
from sessionlens.background_budget import BackgroundBudget
from sessionlens.core import Collector


class BackgroundBudgetTests(unittest.TestCase):
    def test_history_yields_and_resumes_without_moving_evidence_cursor(self):
        now = [0.]
        budget = BackgroundBudget(clock=lambda: now[0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'events.jsonl'
            path.write_text('{"type":"message","sessionId":"s","role":"user","content":"first"}\n')
            collector = Collector(Path(tmp)/'collector.db')
            collector.scan(path, source='workbuddy')
            cursor = collector.db.execute('SELECT offset FROM cursors').fetchone()[0]
            now[0] = 3.
            budget.finish(0.)
            self.assertFalse(budget.ready())
            # The scheduling budget does not pause/modify the live reader.
            with path.open('a') as f:
                f.write('{"type":"message","sessionId":"s","role":"user","content":"live"}\n')
            collector.scan_recent(path, source='workbuddy')
            self.assertEqual(collector.db.execute('SELECT offset FROM cursors').fetchone()[0],cursor)
            now[0] += budget.delay()
            self.assertTrue(budget.ready())
            collector.scan(path,source='workbuddy')
            self.assertEqual(collector.db.execute('SELECT count(*) FROM events').fetchone()[0],2)
            collector.db.close()

    def test_zero_work_and_invalid_fraction(self):
        budget=BackgroundBudget(clock=lambda: 4.)
        budget.finish(4.)
        self.assertTrue(budget.ready())
        for fraction in (0,-1,2):
            with self.assertRaises(ValueError):BackgroundBudget(fraction)
