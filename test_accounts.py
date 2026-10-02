import tempfile
import unittest
import sqlite3
from unittest.mock import patch
from pathlib import Path
from agentpair.accounts import Accounts


class AccountTests(unittest.TestCase):
    def test_session_survives_restart_and_logout_revokes_every_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'accounts.db'
            one=Accounts(path,'admin')
            token,record=one.issue({'id':'admin','name':'admin','role':'admin'})
            two=Accounts(path,'admin')
            self.assertEqual(two.get(token),record)
            with sqlite3.connect(path) as db:
                self.assertNotEqual(db.execute('SELECT token_hash FROM sessions').fetchone()[0],token)
            two.logout(token)
            self.assertIsNone(one.get(token));self.assertIsNone(Accounts(path,'admin').get(token))

    def test_session_expiry_and_distinct_csrf(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Accounts(Path(directory)/'accounts.db','admin')
            with patch('agentpair.accounts.time.time',return_value=1000):
                token,record=store.issue({'id':'admin','name':'admin','role':'admin'})
                other,second=store.issue({'id':'admin','name':'admin','role':'admin'})
                self.assertNotEqual(record['csrf'],second['csrf'])
            with patch('agentpair.accounts.time.time',return_value=record['expires']):self.assertIsNone(store.get(token))
            self.assertIsNone(store.get('garbage'))

    def test_grant_once_persistent_and_no_overdraft(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'accounts.db'
            store=Accounts(path,'admin')
            uid=store.register('alice','long-password-123')['id']
            self.assertEqual(store.balance(uid)['remainingCNY'],2)
            for _ in range(6):store.reserve(uid,.3)
            self.assertAlmostEqual(store.balance(uid)['remainingCNY'],.2)
            with self.assertRaises(ValueError):store.reserve(uid,.3)
            store=Accounts(path,'admin')
            self.assertAlmostEqual(store.balance(uid)['remainingCNY'],.2)
            store.reserve(uid,.2)
            with self.assertRaises(ValueError):store.reserve(uid,.01)
            self.assertEqual(store.balance(uid)['remainingCNY'],0)
