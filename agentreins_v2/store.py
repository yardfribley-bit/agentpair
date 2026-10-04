"""Bounded ingestion, immutable evidence, durable source cursors and content refs."""
import hashlib
import json
import os
import queue
import sqlite3
import threading
import time
from pathlib import Path


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


class Store:
    def __init__(self, directory, quota_bytes=512 * 1024 * 1024):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.blobs = self.root / "content"
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.db = self.root / "events.sqlite3"
        self.quota = quota_bytes
        self.conn = sqlite3.connect(self.db, check_same_thread=False)
        os.chmod(self.db, 0o600)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
          task_id TEXT, session_id TEXT, kind TEXT NOT NULL, observed_at REAL NOT NULL,
          source TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_task ON events(task_id, seq);
        CREATE TABLE IF NOT EXISTS cursors(source TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contents(hash TEXT PRIMARY KEY, bytes INTEGER NOT NULL);
        ''')
        self.conn.commit()
        self.lock = threading.RLock()
        # Count orphan material conservatively; never delete files another viewer
        # or writer may be using. Quota is a logical budget, not a disk hard limit.
        known = {r[0] for r in self.conn.execute("SELECT hash FROM contents")}
        self.orphan_bytes = sum(p.stat().st_size for p in self.blobs.iterdir() if p.name not in known)

    def cursor(self, source):
        with self.lock:
            r = self.conn.execute("SELECT value FROM cursors WHERE source=?", (source,)).fetchone()
        return json.loads(r[0]) if r else None

    def _usage(self):
        content = self.conn.execute("SELECT COALESCE(SUM(bytes),0) FROM contents").fetchone()[0]
        # Logical allocated DB pages include reusable space; WAL is bounded by checkpoint.
        pages = self.conn.execute("PRAGMA page_count").fetchone()[0]
        size = self.conn.execute("PRAGMA page_size").fetchone()[0]
        return self.orphan_bytes + content + pages * size

    def _content(self, value):
        if isinstance(value, dict) and "_model_request" in value:
            request = dict(value["_model_request"])
            messages = request.pop("messages")
            value = {"_request_manifest": True, "fields": request,
                     "messages": [self._content(message) for message in messages]}
        raw = encoded(value)
        sha = hashlib.sha256(raw).hexdigest()
        dest = self.blobs / sha
        if not dest.exists():
            if self._usage() + len(raw) > self.quota:
                raise OSError("storage quota exceeded; source cursor is not advanced")
            temp = self.blobs / (sha + ".tmp")
            fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, dest)
        self.conn.execute("INSERT OR IGNORE INTO contents VALUES (?,?)", (sha, len(raw)))
        return {"sha256": sha, "bytes": len(raw), "encoding": "json-utf8"}

    def commit(self, source, cursor, events):
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                for event in events:
                    if self.conn.execute("SELECT 1 FROM events WHERE event_id=?", (event["event_id"],)).fetchone():
                        continue
                    event = dict(event)
                    payload = dict(event.pop("data", {}))
                    # Each caller separates large original material from metadata.
                    material = payload.pop("content", None)
                    if material is not None:
                        payload["content_ref"] = self._content(material)
                    payload.update(event)
                    raw = encoded(payload)
                    if self._usage() + len(raw) > self.quota:
                        raise OSError("storage quota exceeded; source cursor is not advanced")
                    self.conn.execute(
                        "INSERT OR IGNORE INTO events(event_id,task_id,session_id,kind,observed_at,source,payload) VALUES (?,?,?,?,?,?,?)",
                        (event["event_id"], event.get("task_id"), event.get("session_id"),
                         event["kind"], event.get("observed_at", time.time()), source, raw.decode()))
                if cursor is not None:
                    self.conn.execute("INSERT INTO cursors VALUES (?,?) ON CONFLICT(source) DO UPDATE SET value=excluded.value",
                                      (source, encoded(cursor).decode()))
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def events(self, after=0, task=None, limit=300):
        with self.lock:
            sql = "SELECT seq,payload,source FROM events WHERE seq>?"
            params = [after]
            if task:
                sql += " AND task_id=?"
                params.append(task)
            sql += " ORDER BY seq LIMIT ?"
            params.append(min(max(limit, 1), 1000))
            return [dict(json.loads(p), seq=s, source=source) for s, p, source in self.conn.execute(sql, params)]

    def content(self, sha):
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("invalid content hash")
        raw = (self.blobs / sha).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("content integrity check failed")
        value = json.loads(raw)
        if isinstance(value, dict) and value.get("_request_manifest"):
            return dict(value["fields"], messages=[self.content(r["sha256"]) for r in value["messages"]])
        return value

    def status(self):
        with self.lock:
            return {"events": self.conn.execute("SELECT count(*) FROM events").fetchone()[0],
                    "content_bytes": self.conn.execute("SELECT COALESCE(SUM(bytes),0) FROM contents").fetchone()[0],
                    "used_bytes": self._usage(), "quota_bytes": self.quota,
                    "last_seq": self.conn.execute("SELECT COALESCE(max(seq),0) FROM events").fetchone()[0]}

    def close(self):
        with self.lock:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.close()


class Pipeline:
    """Never block source callbacks; rejected records remain behind durable cursors."""
    def __init__(self, store, max_bytes=16 * 1024 * 1024, max_batches=64):
        self.store = store
        self.queue = queue.Queue(max_batches)
        self.max_bytes = max_bytes
        self.bytes = 0
        self.lock = threading.Lock()
        self.pending = {}
        self.rejected = 0
        self.error = None
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()

    def cursor(self, source):
        with self.lock:
            if source in self.pending:
                return self.pending[source]
        return self.store.cursor(source)

    def submit(self, source, cursor, events):
        size = len(encoded([source, cursor, events]))
        with self.lock:
            if size > self.max_bytes:
                self.error = "one evidence batch exceeds queue byte limit; increase --queue-mib; cursor not advanced"
                self.rejected += 1
                return False
            if self.error or self.bytes + size > self.max_bytes or self.queue.full():
                self.rejected += 1
                return False
            self.bytes += size
            self.pending[source] = cursor
            self.queue.put_nowait((source, cursor, events, size))
        return True

    def _write(self):
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                source, cursor, events, size = item
                if not self.error:
                    try:
                        self.store.commit(source, cursor, events)
                    except Exception as e:
                        self.error = str(e)
                        # Never advance cursors after a failed durable write.
                with self.lock:
                    self.bytes -= size
                    if self.pending.get(source) == cursor:
                        self.pending.pop(source, None)
            finally:
                self.queue.task_done()

    def flush(self):
        self.queue.join()
        if self.error:
            raise OSError(self.error)

    def close(self):
        self.queue.put(None)
        self.thread.join()
