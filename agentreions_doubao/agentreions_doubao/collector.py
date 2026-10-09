"""Incremental, local-only Doubao reader with one SQLite writer.

The source files are opened read-only. No telemetry, upload, URL download or
cloud model is present in this module.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import platform
import sqlite3
import threading
import time
from pathlib import Path

from .core import build_runs, evidence, iso_now, parse_assignment, parse_native_line
from .http_evidence import parse_http_line, associate_http

SCHEMA_VERSION = 1
MAX_READ_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 2 * 1024 * 1024
MAX_SESSION_ROWS = 10000
INTEGRITY_INTERVAL_SECONDS = 30.0
SMALL_SOURCE_BYTES = 64 * 1024
INTEGRITY_CHUNK_BYTES = 64 * 1024
MAX_PENDING_BYTES = 4 * 1024 * 1024


def default_data_dir() -> Path:
    if platform.system() == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "agentreions_doubao"
    return Path.home() / "Library/Application Support/agentreions_doubao"


def discover_source_roots() -> list[Path]:
    if platform.system() == "Darwin":
        return [Path.home() / "Library/Application Support/Doubao"]
    if platform.system() == "Windows":
        # Candidate Electron/Chromium application locations; Windows has not yet
        # been validated against an installed Doubao desktop version.
        roots = []
        for name in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(name)
            if base:
                roots.extend(Path(base) / item for item in ("Doubao", "doubao", "豆包"))
        return roots
    return []


class Collector:
    def __init__(self, db_path=None, source_roots=None):
        self._lock = threading.RLock()
        self._closed = False
        self._last_discovery = 0.0
        self._source_paths = []
        self._rotate = 0
        self._errors = []
        self._last_read_at = None
        self._last_scan_bytes = 0
        self._last_scan_records = 0
        self._last_seen_stat = {}
        self._trusted_signature = {}
        self._assignment_boundaries = {}
        self._pending_sources = {}
        self._integrity_states = {}
        self._next_integrity_at = {}
        self._snapshot_cache = {}
        self.source_roots = [Path(x).expanduser().resolve() for x in
                             (discover_source_roots() if source_roots is None else source_roots)]
        self.db_path = Path(db_path or default_data_dir() / "observations.sqlite3").expanduser().resolve()
        # Never place collector state inside the observed application's tree.
        for root in self.source_roots:
            if self.db_path == root or root in self.db_path.parents:
                raise ValueError("Collector database must be outside the Doubao source directory")
        parent_created = not self.db_path.parent.exists()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Restrict this application's own newly-created directory, without
        # changing a caller's pre-existing Documents or temporary directory.
        if parent_created or self.db_path.parent == default_data_dir().resolve():
            with contextlib.suppress(OSError):
                self.db_path.parent.chmod(0o700)
        self._db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO meta VALUES ('revision','0');
            CREATE TABLE IF NOT EXISTS cursors (
                path TEXT PRIMARY KEY, kind TEXT NOT NULL, generation INTEGER NOT NULL,
                inode TEXT NOT NULL, offset INTEGER NOT NULL, line INTEGER NOT NULL,
                prefix_hash TEXT NOT NULL, checkpoint_hash TEXT NOT NULL, mtime_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL, generation INTEGER NOT NULL,
                line INTEGER NOT NULL, kind TEXT NOT NULL, session_id TEXT, agent_id TEXT,
                call_id TEXT, sandbox_id TEXT, time TEXT, payload TEXT NOT NULL,
                UNIQUE(path,generation,line,kind)
            );
            CREATE INDEX IF NOT EXISTS records_session ON records(session_id,kind,path,generation,line);
            CREATE INDEX IF NOT EXISTS records_call ON records(call_id,sandbox_id);
            CREATE TABLE IF NOT EXISTS assignments (
                path TEXT PRIMARY KEY, session_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                digest TEXT NOT NULL, mtime_ns INTEGER NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS verifications (
                reference TEXT PRIMARY KEY, path TEXT NOT NULL, metadata TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS integrity_ranges (
                path TEXT NOT NULL, generation INTEGER NOT NULL, start INTEGER NOT NULL,
                end INTEGER NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(path,generation,start)
            );
        """)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(cursors)")}
        for name in ("file_size", "ctime_ns"):
            if name not in columns:
                self._db.execute(f"ALTER TABLE cursors ADD COLUMN {name} INTEGER NOT NULL DEFAULT -1")
        self._db.commit()
        with contextlib.suppress(OSError):
            self.db_path.chmod(0o600)

    @property
    def revision(self):
        return int(self._db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def _bump_revision(self):
        self._db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    def _error(self, path, message):
        item = {"path": str(path), "message": str(message), "time": iso_now()}
        if not self._errors or self._errors[-1]["path"] != item["path"] or self._errors[-1]["message"] != item["message"]:
            self._errors.append(item)
        self._errors = self._errors[-20:]

    @staticmethod
    def _session_identity(path):
        parts = Path(path).parts
        try:
            index = parts.index(".sessions")
            agent = parts.index("agents", index + 2)
            return parts[index + 1], parts[agent + 1]
        except (ValueError, IndexError):
            return None, None

    def _discover(self, force=False):
        if not force and time.monotonic() - self._last_discovery < 3:
            return
        self._last_discovery = time.monotonic()
        paths = []
        for root in self.source_roots:
            if not root.exists():
                continue
            profiles = list(root.glob("Profile *")) + [root / "Default", root]
            for profile in profiles:
                sessions = profile / ".doubao/agent_mode/workspace/.sessions"
                if not sessions.is_dir():
                    continue
                for path in sessions.glob("*/agents/*/system/trajectory.jsonl"):
                    paths.append((path, "trajectory"))
                for path in sessions.glob("*/agents/*/system/assignment.md"):
                    paths.append((path, "assignment"))
            for path in (root / "sdk_storage/log").glob("saman_*.log"):
                if "netlog" in path.name:
                    paths.append((path, "http"))
                elif "repair" not in path.name:
                    paths.append((path, "native"))
        unique = {(str(p.resolve()), kind): (p.resolve(), kind) for p, kind in paths}
        paths = list(unique.values())
        paths.sort(key=lambda item: item[0].stat().st_mtime if item[0].exists() else 0, reverse=True)
        # Bounded discovery, with visible coverage rather than silent all-history
        # claims. Source capacity can be increased in a later scheduling phase.
        if len(paths) > 600:
            self._error("discovery", f"Source scan is limited to 600 recent files; {len(paths)-600} older files are pending")
            paths = paths[:600]
        self._source_paths = paths

    @staticmethod
    def _hash_prefix(stream, size=128):
        stream.seek(0)
        return hashlib.sha256(stream.read(size)).hexdigest()

    @staticmethod
    def _hash_checkpoint(stream, offset):
        stream.seek(max(0, offset - 128))
        return hashlib.sha256(stream.read(min(offset, 128))).hexdigest()

    @staticmethod
    def _boundary_hashes(stream, offset):
        """Read at most 256 bytes; count actual I/O including overlap once."""
        size = min(128, max(0, offset))
        stream.seek(0)
        prefix_bytes = stream.read(size)
        prefix = hashlib.sha256(prefix_bytes).hexdigest()
        if offset <= 128:
            return prefix, prefix, len(prefix_bytes)
        stream.seek(offset - size)
        checkpoint_bytes = stream.read(size)
        return prefix, hashlib.sha256(checkpoint_bytes).hexdigest(), len(prefix_bytes) + len(checkpoint_bytes)

    @staticmethod
    def _signature(stat):
        return (f"{stat.st_dev}:{stat.st_ino}", stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns)

    def _hold_pending(self, path, signature, start, raw):
        key = str(path)
        self._pending_sources.pop(key, None)
        if not raw:
            return
        while self._pending_sources and sum(len(x["raw"]) for x in self._pending_sources.values()) + len(raw) > MAX_PENDING_BYTES:
            oldest = next(iter(self._pending_sources))
            self._pending_sources.pop(oldest)
        if len(raw) <= MAX_PENDING_BYTES:
            self._pending_sources[key] = {"signature": signature, "start": start, "raw": raw}

    def _budgeted_bytes(self, path, signature, start, budget, maximum):
        pending = self._pending_sources.pop(str(path), None)
        raw = pending["raw"] if pending and pending["signature"] == signature and pending["start"] == start else b""
        physical_start = start + len(raw)
        amount = min(max(0, budget), max(0, maximum-len(raw)), max(0, signature[2]-physical_start))
        with path.open("rb") as stream:
            stream.seek(physical_start)
            fresh = stream.read(amount)
        return raw + fresh, len(fresh), physical_start + len(fresh) >= signature[2]

    def _assignment(self, path, budget=MAX_READ_BYTES):
        path = Path(path).resolve()
        stat = path.stat()
        signature = self._signature(stat)
        if platform.system() != "Windows" and self._last_seen_stat.get(str(path)) == signature:
            return 0, 0
        if stat.st_size > 256 * 1024:
            self._error(path, "Assignment exceeds 256 KiB; it was not silently truncated")
            return 0, 0
        raw, read_bytes, eof = self._budgeted_bytes(path, signature, 0, budget, 256 * 1024)
        if not eof:
            self._hold_pending(path, signature, 0, raw)
            return 0, read_bytes
        # On Windows the complete <=256 KiB source digest is checked, not just
        # its endpoints; mid-file replacement with unchanged timestamps counts.
        digest = hashlib.sha256(raw).hexdigest()
        self._last_seen_stat[str(path)] = signature
        old = self._db.execute("SELECT digest,mtime_ns FROM assignments WHERE path=?", (str(path),)).fetchone()
        if old and old["digest"] == digest:
            return 0, read_bytes
        session, agent = self._session_identity(path)
        if not session or not agent:
            return 0, read_bytes
        sections = parse_assignment(raw.decode("utf-8", "replace"))
        for section in sections:
            section["evidence"] = [evidence("assignment", str(path), section.pop("line"), raw)]
        self._db.execute("""INSERT INTO assignments VALUES (?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET digest=excluded.digest,mtime_ns=excluded.mtime_ns,payload=excluded.payload""",
            (str(path), session, agent, digest, stat.st_mtime_ns, json.dumps(sections, ensure_ascii=False)))
        return 1, read_bytes

    def _reset_cursor(self, path, kind, stat, old):
        generation = old["generation"] + 1
        empty = hashlib.sha256(b"").hexdigest()
        self._db.execute("""UPDATE cursors SET generation=?,inode=?,offset=0,line=0,prefix_hash=?,checkpoint_hash=?,
            mtime_ns=?,file_size=?,ctime_ns=? WHERE path=?""",
            (generation, f"{stat.st_dev}:{stat.st_ino}", empty, empty, stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, str(path)))
        self._db.execute("INSERT OR REPLACE INTO integrity_ranges VALUES (?,?,?,?,?)", (str(path), generation, 0, 0, empty))
        self._last_seen_stat.pop(str(path), None)
        self._trusted_signature.pop(str(path), None)
        self._pending_sources.pop(str(path), None)
        self._integrity_states.pop(str(path), None)
        self._next_integrity_at.pop(str(path), None)
        self._bump_revision()
        return self._db.execute("SELECT * FROM cursors WHERE path=?", (str(path),)).fetchone()

    def _verify_integrity(self, path, old, signature, budget):
        key = str(path)
        state = self._integrity_states.get(key)
        if state is None or state["inode"] != signature[0] or state["generation"] != old["generation"]:
            # Freeze the target at the beginning of this sweep. Appends may
            # advance the cursor while this older prefix is checked in slices.
            state = {"inode": signature[0], "offset": old["offset"], "generation": old["generation"],
                     "position": 0, "interval": None, "hash": hashlib.sha256()}
            self._integrity_states[key] = state
        read_bytes = 0
        with path.open("rb") as stream:
            while state["position"] < state["offset"]:
                if state["interval"] is None:
                    interval = self._db.execute("""SELECT start,end,digest FROM integrity_ranges
                        WHERE path=? AND generation=? AND start=? AND end<=?""",
                        (key, state["generation"], state["position"], state["offset"])).fetchone()
                    if interval is None or interval["end"] <= interval["start"]:
                        return "missing_baseline", read_bytes
                    # One interval, rather than every historical range, stays
                    # resident while the sweep resumes under the poll budget.
                    state["interval"] = dict(interval)
                interval = state["interval"]
                remaining = interval["end"] - state["position"]
                if remaining:
                    if read_bytes >= budget:
                        return "pending", read_bytes
                    amount = min(remaining, INTEGRITY_CHUNK_BYTES, budget-read_bytes)
                    stream.seek(state["position"])
                    raw = stream.read(amount)
                    read_bytes += len(raw)
                    if not raw:
                        return "changed", read_bytes
                    state["hash"].update(raw)
                    state["position"] += len(raw)
                if state["position"] == interval["end"]:
                    if state["hash"].hexdigest() != interval["digest"]:
                        return "changed", read_bytes
                    state["interval"] = None
                    state["hash"] = hashlib.sha256()
        self._integrity_states.pop(key, None)
        self._trusted_signature[key] = signature
        self._next_integrity_at[key] = time.monotonic() + INTEGRITY_INTERVAL_SECONDS
        return "valid", read_bytes

    def _read_source(self, path, kind, budget):
        path = Path(path).resolve()
        stat = path.stat()
        signature = self._signature(stat)
        inode = signature[0]
        key = str(path)
        small_windows = platform.system() == "Windows" and stat.st_size <= SMALL_SOURCE_BYTES
        due = small_windows or key in self._integrity_states or time.monotonic() >= self._next_integrity_at.get(key, 0)
        if not due and self._last_seen_stat.get(key) == signature:
            return 0, 0
        old = self._db.execute("SELECT * FROM cursors WHERE path=?", (key,)).fetchone()
        if old:
            metadata_changed_same_size = old["file_size"] == stat.st_size and (
                old["mtime_ns"] != stat.st_mtime_ns or
                (platform.system() != "Windows" and old["ctime_ns"] != stat.st_ctime_ns))
            if old["inode"] != inode or stat.st_size < old["offset"] or metadata_changed_same_size:
                old = self._reset_cursor(path, kind, stat, old)
        # Growing sources are not revalidated in full on every append. The
        # periodic sweep checks the committed prefix, with a fixed target, while
        # the unread tail receives most of each poll's byte budget first.
        grew = old is not None and stat.st_size > old["file_size"]
        integrity_requested = old is not None and (due or (
            not grew and self._trusted_signature.get(key) != signature))
        reserve = min(INTEGRITY_CHUNK_BYTES, budget // 4) if integrity_requested and old["offset"] else 0
        read_budget = max(0, budget - reserve)
        offset, line, generation = (old["offset"], old["line"], old["generation"]) if old else (0, 0, 0)
        raw, fresh_bytes, eof = self._budgeted_bytes(path, signature, offset, read_budget, CHUNK_BYTES)

        def finish(added):
            total = fresh_bytes
            if integrity_requested:
                checked, checked_bytes = self._verify_integrity(path, old, signature, budget-total)
                total += checked_bytes
                if checked in ("changed", "missing_baseline"):
                    current = self._db.execute("SELECT * FROM cursors WHERE path=?", (key,)).fetchone()
                    self._reset_cursor(path, kind, stat, current)
                    if total < budget:
                        replacement_added, replacement_bytes = self._read_source(path, kind, budget-total)
                        return added + replacement_added, total + replacement_bytes
                    return added, total
            if eof:
                # Only actual source EOF may populate this fast cache; a short
                # read caused by the budget still has a suffix to collect.
                self._last_seen_stat[key] = signature
            return added, total

        last_newline = raw.rfind(b"\n")
        if last_newline < 0:
            if len(raw) == CHUNK_BYTES:
                self._error(path, "One source line exceeds 2 MiB; complete-line collection is paused for this file")
            self._hold_pending(path, signature, offset, raw)
            return finish(0)
        complete = raw[:last_newline + 1]
        session, agent = self._session_identity(path)
        added = 0
        for source_line in complete.splitlines():
            line += 1
            if not source_line.strip():
                continue
            value = None
            if kind == "trajectory":
                try:
                    parsed = json.loads(source_line)
                    if isinstance(parsed, dict):
                        value = {"value": parsed, "evidence": evidence("trajectory", str(path), line, source_line)}
                    else:
                        self._error(path, f"Line {line}: trajectory value is not an object")
                except (ValueError, UnicodeDecodeError) as exc:
                    self._error(path, f"Line {line}: invalid completed JSON ({type(exc).__name__})")
            elif kind == "native":
                value = parse_native_line(source_line.decode("utf-8", "replace"), str(path), line)
            elif kind == "http":
                value = parse_http_line(source_line.decode("utf-8", "replace"), str(path), line)
            if value is None:
                continue
            sid = session if kind == "trajectory" else value.get("sessionId")
            aid = agent if kind == "trajectory" else value.get("agentId")
            call_id = value.get("callId")
            sandbox = value.get("sandboxId")
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            cur = self._db.execute("""INSERT OR IGNORE INTO records
                (path,generation,line,kind,session_id,agent_id,call_id,sandbox_id,time,payload)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (key, generation, line, kind, sid, aid, call_id, sandbox, value.get("time"), payload))
            added += cur.rowcount
        committed_start = offset
        offset += len(complete)
        prefix = hashlib.sha256(complete[:128]).hexdigest()
        checkpoint = hashlib.sha256(complete[-128:]).hexdigest()
        self._db.execute("""INSERT INTO cursors(path,kind,generation,inode,offset,line,prefix_hash,checkpoint_hash,mtime_ns,file_size,ctime_ns)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET generation=excluded.generation,inode=excluded.inode,
            offset=excluded.offset,line=excluded.line,prefix_hash=excluded.prefix_hash,
            checkpoint_hash=excluded.checkpoint_hash,mtime_ns=excluded.mtime_ns,file_size=excluded.file_size,ctime_ns=excluded.ctime_ns""",
            (key, kind, generation, inode, offset, line, prefix, checkpoint, stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns))
        self._db.execute("INSERT OR REPLACE INTO integrity_ranges VALUES (?,?,?,?,?)",
                         (key, generation, committed_start, offset, hashlib.sha256(complete).hexdigest()))
        self._trusted_signature[key] = signature
        self._hold_pending(path, signature, offset, raw[len(complete):])
        # Continuous appends must not keep postponing the periodic sweep.
        self._next_integrity_at.setdefault(key, time.monotonic() + INTEGRITY_INTERVAL_SECONDS)
        return finish(added)

    def scan_once(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Collector is closed")
            self._discover()
            added, scanned = 0, 0
            source_count = len(self._source_paths)
            ordered = self._source_paths[self._rotate:] + self._source_paths[:self._rotate]
            for index, (path, kind) in enumerate(ordered):
                if scanned >= MAX_READ_BYTES:
                    self._rotate = (self._rotate + index) % max(1, source_count)
                    break
                try:
                    # A small transaction per file prevents readers waiting on
                    # one all-history commit; there is one writer connection.
                    with self._db:
                        changes, byte_count = (self._assignment(path, MAX_READ_BYTES-scanned) if kind == "assignment"
                                               else self._read_source(path, kind, MAX_READ_BYTES - scanned))
                        if changes:
                            self._bump_revision()
                    added += changes
                    scanned += byte_count
                except (OSError, sqlite3.Error, ValueError) as exc:
                    self._error(path, f"{type(exc).__name__}: {exc}")
            else:
                self._rotate = 0
            self._last_read_at = iso_now()
            self._last_scan_bytes, self._last_scan_records = scanned, added
            return {"revision": self.revision, "recordsAdded": added, "bytesRead": scanned,
                    "sourceCount": source_count, "errors": list(self._errors)}

    def poll(self):
        return self.scan_once()

    def register_artifact_verification(self, url, path, metadata):
        """Import an explicitly obtained local probe result; never fetch a URL."""
        local = Path(path).expanduser().resolve()
        if not local.is_file():
            raise ValueError("Verified media must be an existing local file")
        if not isinstance(metadata, dict) or not metadata.get("method"):
            raise ValueError("Verification must name its method, such as ffprobe")
        safe = dict(metadata)
        safe.setdefault("verifiedAt", iso_now())
        with local.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _file_digest(stream)
        safe["sha256"] = digest
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO verifications VALUES (?,?,?)",
                             (str(url or local), str(local), json.dumps(safe, ensure_ascii=False)))
            self._bump_revision()

    def snapshot(self, session_limit=20, step_limit=200):
        with self._lock:
            if self._closed:
                raise RuntimeError("Collector is closed")
            session_limit = max(1, min(int(session_limit), 20))
            step_limit = max(1, min(int(step_limit), 200))
            cache_key = (self.revision, session_limit, step_limit)
            cached = self._snapshot_cache.get(cache_key)
            if cached is not None:
                result = copy.deepcopy(cached)
                result["collector"] = self._collector_status()
                return result
            session_rows = self._db.execute("""SELECT session_id,MAX(id) last_id FROM records
                WHERE kind='trajectory' AND session_id IS NOT NULL GROUP BY session_id ORDER BY last_id DESC LIMIT ?""",
                                           (session_limit,)).fetchall()
            total_sessions = self._db.execute("SELECT COUNT(DISTINCT session_id) FROM records WHERE kind='trajectory'").fetchone()[0]
            verification = {x["reference"]: {"path": x["path"], "metadata": json.loads(x["metadata"])}
                            for x in self._db.execute("SELECT * FROM verifications")}
            http_rows = self._db.execute("""SELECT r.payload FROM records r JOIN cursors c
                ON c.path=r.path AND c.generation=r.generation
                WHERE r.kind='http' ORDER BY r.id DESC LIMIT 1001""").fetchall()
            http_records = [json.loads(row[0]) for row in http_rows[:1000]]
            sessions = []
            for session_row in session_rows:
                sid = session_row["session_id"]
                rows = self._db.execute("""SELECT r.* FROM records r JOIN cursors c ON c.path=r.path AND c.generation=r.generation
                    WHERE r.kind='trajectory' AND r.session_id=? ORDER BY r.path,r.line LIMIT ?""",
                                        (sid, MAX_SESSION_ROWS + 1)).fetchall()
                records = []
                for row in rows[:MAX_SESSION_ROWS]:
                    value = json.loads(row["payload"])
                    records.append({**value, "id": f"trajectory:{row['id']}", "agentId": row["agent_id"], "sequence": row["line"]})
                assignments = []
                for row in self._db.execute("SELECT payload FROM assignments WHERE session_id=?", (sid,)):
                    assignments.extend(json.loads(row["payload"]))
                # Discover explicit sandbox/session mappings from native JSON.
                # An ambiguous sandbox is never automatically associated.
                sandboxes = [x[0] for x in self._db.execute("""SELECT DISTINCT r.sandbox_id FROM records r
                    JOIN cursors c ON c.path=r.path AND c.generation=r.generation
                    WHERE r.kind='native' AND r.session_id=? AND r.sandbox_id IS NOT NULL""", (sid,))]
                valid = []
                for sandbox in sandboxes:
                    owners = [x[0] for x in self._db.execute("""SELECT DISTINCT r.session_id FROM records r
                        JOIN cursors c ON c.path=r.path AND c.generation=r.generation
                        WHERE r.kind='native' AND r.sandbox_id=? AND r.session_id IS NOT NULL""", (sandbox,))]
                    if owners == [sid]:
                        valid.append(sandbox)
                clauses = ["r.session_id=?"]
                params = [sid]
                if valid:
                    clauses.append("r.sandbox_id IN (" + ",".join("?" for _ in valid) + ")")
                    params.extend(valid)
                native_rows = self._db.execute("""SELECT r.payload FROM records r
                    JOIN cursors c ON c.path=r.path AND c.generation=r.generation
                    WHERE r.kind='native' AND (""" + " OR ".join(clauses) + ") ORDER BY r.id LIMIT ?", params + [2000]).fetchall()
                native = [json.loads(row[0]) for row in native_rows]
                for event in native:
                    if not event.get("sessionId") and event.get("sandboxId") in valid:
                        event["sessionId"] = sid
                        event["sessionAssociation"] = "explicit_unique_sandbox_id"
                runs = build_runs(sid, records, assignments, native, verification)
                for run in runs:
                    calls = [step.get('callId') for step in run['steps'] if step.get('callId')]
                    urls = [ref.get('url') for step in run['steps']
                            for ref in step.get('inputReferences', []) + step.get('outputReferences', []) if ref.get('url')]
                    related = [item for record in http_records
                               if (item := associate_http(record, sid, calls, urls)) is not None
                               and item['association']['kind'] != 'explicit_session_id']
                    run['httpEvidence'] = {'records':related, 'coverage':{
                        'scope':'existing_local_logs_only', 'completeHttpCapture':False,
                        'toolArgumentsAreHttpBody':False, 'association':'explicit_call_id_or_exact_media_url',
                        'parsedHttpRecords':len(http_records), 'recordsLimited':len(http_rows)>1000,
                        'sourceSessionOnlyRecords':sum(bool(associate_http(record, sid)) for record in http_records)}}
                for session in reversed(runs):
                    count = len(session["steps"])
                    session["totalSteps"] = count
                    # Keep recent steps while preserving requestHistory separately.
                    session["steps"] = session["steps"][-step_limit:]
                    session["truncated"] = count > step_limit or len(rows) > MAX_SESSION_ROWS
                    session["coverage"]["snapshotLimit"] = {"displayedSteps": len(session["steps"]), "totalSteps": count,
                        "sourceRowsLimited": len(rows) > MAX_SESSION_ROWS, "nativeEventsLimited": len(native_rows) >= 2000}
                    sessions.append(session)
            known_runs = len(sessions)
            sessions = sessions[:session_limit]
            coverage = {"localOnly": True, "upload": "disabled", "dialogue": "native_trajectory_recorded_portion",
                        "toolCalls": "trajectory_and_native_logs_joined_by_explicit_id", "modelInput": "not_captured",
                        "hiddenReasoning": "not_captured", "processes": "not_monitored_by_this_collector",
                        "network": "not_monitored_by_this_collector", "sourcePlatform": platform.system(),
                        "windowsSupport": "source_locations_unverified" if platform.system() == "Windows" else "not_tested",
                        "totalSessions": known_runs, "displayedSessions": len(sessions), "totalSourceSessions": total_sessions,
                        "totalRunsInSelectedSources": known_runs, "sourceSessionLimit": session_limit,
                        "sessionLimit": session_limit, "stepLimit": step_limit,
                        "scope": "local_doubao_workspace_and_agent_task_logs"}
            coverage['http'] = {'scope':'existing_local_logs_only', 'completeHttpCapture':False,
                                'parsedHttpRecords':len(http_records), 'recordsLimited':len(http_rows)>1000}
            result = {"schemaVersion": SCHEMA_VERSION, "revision": self.revision, "coverage": coverage, "sessions": sessions,
                      "collector": self._collector_status()}
            # A single latest projection: bounded independently of task count.
            self._snapshot_cache = {cache_key: copy.deepcopy(result)}
            return result

    def _collector_status(self):
        return {"state": "watching" if self._source_paths else "source_not_found",
                "lastReadAt": self._last_read_at, "sourcePaths": [str(x[0]) for x in self._source_paths],
                "errors": list(self._errors), "bytesReadLastPoll": self._last_scan_bytes,
                "recordsAddedLastPoll": self._last_scan_records, "databasePath": str(self.db_path)}

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True


def _file_digest(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


class LocalCollector(Collector):
    def __init__(self, data_dir=None, source_root=None):
        super().__init__(db_path=Path(data_dir or default_data_dir()) / "observations.sqlite3",
                         source_roots=[source_root] if source_root is not None else None)
