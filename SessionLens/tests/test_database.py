import sqlite3
import tempfile
import unittest
from pathlib import Path

from sessionlens.database import connection


class ConnectionTests(unittest.TestCase):
    def assert_closed(self, db):
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')

    def test_success_commits_and_releases_database_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'database.db'
            with connection(path, timeout=1) as db:
                db.execute('CREATE TABLE items(value TEXT)')
                db.execute('INSERT INTO items VALUES(?)', ('saved',))
            self.assert_closed(db)
            with connection(path) as reader:
                self.assertEqual(reader.execute('SELECT value FROM items').fetchall(), [('saved',)])
            path.unlink()

    def test_body_failure_rolls_back_and_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'database.db'
            with connection(path) as db:
                db.execute('CREATE TABLE items(value TEXT)')
            with self.assertRaisesRegex(ValueError, 'stop transaction'):
                with connection(path) as failed:
                    failed.execute('INSERT INTO items VALUES(?)', ('discarded',))
                    raise ValueError('stop transaction')
            self.assert_closed(failed)
            with connection(path) as reader:
                self.assertEqual(reader.execute('SELECT count(*) FROM items').fetchone()[0], 0)
            path.unlink()

    def test_commit_failure_also_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'database.db'
            with self.assertRaises(sqlite3.IntegrityError):
                with connection(path) as db:
                    db.execute('PRAGMA foreign_keys=ON')
                    db.execute('CREATE TABLE parents(id INTEGER PRIMARY KEY)')
                    db.execute('CREATE TABLE children(parent INTEGER REFERENCES parents(id) DEFERRABLE INITIALLY DEFERRED)')
                    db.execute('INSERT INTO children VALUES(1)')
            self.assert_closed(db)
            with connection(path) as reader:
                self.assertEqual(reader.execute('SELECT count(*) FROM children').fetchone()[0], 0)
            path.unlink()

    def test_sqlite_connection_options_preserve_read_only_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'database.db'
            with connection(path) as db:
                db.execute('CREATE TABLE items(value TEXT)')
            with connection(path.as_uri() + '?mode=ro', uri=True) as reader:
                self.assertEqual(reader.execute('SELECT count(*) FROM items').fetchone()[0], 0)
                with self.assertRaises(sqlite3.OperationalError):
                    reader.execute('INSERT INTO items VALUES(?)', ('blocked',))
            self.assert_closed(reader)
            path.unlink()
