"""Short-lived SQLite transactions that also release their database handles."""
from contextlib import contextmanager
import sqlite3


@contextmanager
def connection(*args, **kwargs):
    """Keep sqlite3's commit/rollback behavior and close even after failures."""
    db = sqlite3.connect(*args, **kwargs)
    try:
        with db:
            yield db
    finally:
        db.close()
