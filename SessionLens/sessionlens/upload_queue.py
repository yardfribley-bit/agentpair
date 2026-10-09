"""Durable, bounded upload scheduling over the collector's original evidence.

Only metadata is indexed. JSON is decoded when an event first enters the index
or is selected for an upload; an idle queue never rescans historical payloads.
The caller owns the SQLite connection and collection transaction. No network
requests are made here.
"""
from datetime import datetime, timezone
import json
import math
import time


MAX_BATCH_EVENTS = 30
MAX_REQUEST_BYTES = 2096000


def encode_batch(items):
    """Match the receiver's existing schema and the desktop JSON encoding."""
    return json.dumps({'schemaVersion': 1, 'events': items}, ensure_ascii=False).encode('utf-8')


EMPTY_REQUEST_BYTES = len(encode_batch([]))


def source_seconds(value):
    """ISO, Unix seconds or milliseconds; missing evidence stays NULL.

    Naive ISO timestamps use UTC consistently across machines. Collection time,
    file mtime and rowid are never used as substitutes for source time.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                value = parsed.timestamp()
            except (ValueError, OverflowError, OSError):
                return None
    if not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        return None
    if abs(value) >= 100_000_000_000:
        value /= 1000
    return float(value) if 0 < value < 253402300800 else None


class UploadQueue:
    """Three latest slots followed by one historical slot, across calls.

    Each lane rotates sources independently. The historical slot alternates
    oldest known timestamps and unknown timestamps (rowid order), so neither
    stream can starve the other. Successful receipts use the existing deliveries
    table, preserving acknowledgement across upgrades and process restarts.
    """

    def __init__(self, db):
        self.db = db
        # Initialize at collector construction, before its ingestion transaction.
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS upload_index(
                id TEXT PRIMARY KEY, event_rowid INTEGER NOT NULL,
                source TEXT NOT NULL, source_time REAL, source_timestamp TEXT,
                bytes INTEGER NOT NULL, quarantine TEXT);
            CREATE INDEX IF NOT EXISTS upload_source_time
                ON upload_index(source,source_time,event_rowid);
            CREATE INDEX IF NOT EXISTS upload_source_rowid
                ON upload_index(source,event_rowid);
            CREATE TABLE IF NOT EXISTS upload_index_state(
                name TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS upload_schedule(
                destination TEXT PRIMARY KEY, phase INTEGER NOT NULL,
                history_unknown INTEGER NOT NULL, latest_source TEXT,
                historical_source TEXT, unknown_source TEXT);
            CREATE TABLE IF NOT EXISTS upload_failures(
                destination TEXT NOT NULL, id TEXT NOT NULL,
                attempts INTEGER NOT NULL, retry_at REAL NOT NULL,
                last_error TEXT, PRIMARY KEY(destination,id));
            CREATE TABLE IF NOT EXISTS upload_channels(
                destination TEXT NOT NULL, source TEXT NOT NULL,
                attempts INTEGER NOT NULL, retry_at REAL NOT NULL,
                last_error TEXT, PRIMARY KEY(destination,source));
        ''')

    @staticmethod
    def _metadata(rowid, identity, event):
        body = json.dumps(event, ensure_ascii=False).encode('utf-8')
        size = len(body)
        source = event.get('source')
        source = source if isinstance(source, str) and source else 'unknown'
        stamp = event.get('timestamp')
        seconds = source_seconds(stamp)
        quarantine = None
        if event.get('id') != identity:
            quarantine = 'event_id_mismatch'
        elif size + EMPTY_REQUEST_BYTES > MAX_REQUEST_BYTES:
            quarantine = 'oversize: request requires %d bytes' % (size + EMPTY_REQUEST_BYTES)
        return (identity, rowid, source, seconds,
                str(stamp) if stamp is not None else None, size, quarantine)

    def register(self, rowid, identity, event, size=None):
        """Register newly inserted evidence within the caller's transaction.

        ``size`` is accepted for the Collector integration; canonical serialized
        bytes are measured here so older compact JSON and UTF-8 are also exact.
        This method does not commit and does not advance the backfill cursor.
        """
        self.db.execute('INSERT OR IGNORE INTO upload_index VALUES(?,?,?,?,?,?,?)',
                        self._metadata(rowid, identity, event))

    def index(self, limit=1000, max_bytes=4*1024*1024, max_seconds=.1, recent=False):
        """Backfill at most ``limit`` rows, atomically persisting the cursor.

        Returns rows traversed, including already registered live records.
        Calling this repeatedly at the end of the database decodes no JSON.
        New records registered directly can upload while this cursor catches up.
        """
        limit = max(0, int(limit))
        if not limit:
            return 0
        checkpoint = 'recent_rowid' if recent else 'rowid'
        state = self.db.execute('SELECT value FROM upload_index_state WHERE name=?',(checkpoint,)).fetchone()
        cursor = state[0] if state else (self.db.execute('SELECT coalesce(max(rowid),0)+1 FROM events').fetchone()[0] if recent else 0)
        forward=self.db.execute("SELECT value FROM upload_index_state WHERE name='rowid'").fetchone() if recent else None
        floor=forward[0] if forward else 0
        rows = self.db.execute('''SELECT e.rowid,e.id,
            CASE WHEN i.id IS NULL THEN e.event END
            FROM events e LEFT JOIN upload_index i ON i.id=e.id
            WHERE '''+('e.rowid<? AND e.rowid>? ORDER BY e.rowid DESC LIMIT ?' if recent else 'e.rowid>? ORDER BY e.rowid LIMIT ?'),
            (cursor,floor,limit) if recent else (cursor,limit))
        staged = []
        count = 0
        processed_bytes = 0
        deadline = time.monotonic()+max(0,float(max_seconds))
        # Parsing large historical events must not hold SQLite's writer lock
        # while the separate collector worker tries to register new evidence.
        # Stream payloads instead of retaining a limit-sized list of potentially
        # large records. Only compact prepared metadata is staged in memory.
        try:
            for rowid, identity, body in rows:
                if body is not None:
                    processed_bytes += len(body.encode('utf-8'))
                    try:
                        event = json.loads(body)
                        if not isinstance(event, dict):
                            raise ValueError('Event object required')
                        staged.append(self._metadata(rowid, identity, event))
                    except (ValueError, TypeError, UnicodeError):
                        staged.append((identity, rowid, 'unknown', None, None,
                                       len(body.encode('utf-8')), 'invalid_json'))
                cursor = rowid
                count += 1
                # A record is atomic. Yield between records even if one large
                # retained event alone exceeds this background work budget.
                if processed_bytes>=max_bytes or time.monotonic()>=deadline:
                    break
        finally:
            rows.close()
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO upload_index VALUES(?,?,?,?,?,?,?)', staged)
            if count or state is None:
                self.db.execute('INSERT OR REPLACE INTO upload_index_state VALUES(?,?)', (checkpoint,cursor))
        return count

    def backfill(self, limit=500, max_bytes=4*1024*1024, max_seconds=.05):
        """Seed recent legacy evidence while guaranteeing oldest recovery.

        This is metadata preparation, separate from source-time upload ranking.
        Two durable frontiers converge; directly registered live rows remain
        eligible immediately. A dormant frontier lends its turn to the other.
        """
        state=self.db.execute("SELECT value FROM upload_index_state WHERE name='backfill_phase'").fetchone()
        phase=state[0] if state else 0
        recent=phase<3
        count=self.index(limit,max_bytes,max_seconds,recent=recent)
        if not count:count=self.index(limit,max_bytes,max_seconds,recent=not recent)
        with self.db:self.db.execute("INSERT OR REPLACE INTO upload_index_state VALUES('backfill_phase',?)",((phase+1)%4,))
        return count

    @staticmethod
    def _sources(sources):
        return sorted(set(sources)) if sources is not None else None

    def _candidate(self, destination, sources, lane, last_source, excluded, budget, now):
        # Independent round-robin cursors avoid resonances between source count
        # and the fixed lane ratio (e.g. one source owning every history slot).
        if last_source in sources:
            start = (sources.index(last_source) + 1) % len(sources)
        else:
            start = 0
        order = 'i.source_time DESC,i.event_rowid DESC' if lane == 'latest' else (
            'i.source_time ASC,i.event_rowid ASC' if lane == 'historical' else 'i.event_rowid ASC')
        stamp_filter = 'i.source_time IS NULL' if lane == 'unknown' else 'i.source_time IS NOT NULL'
        exclude = ' AND i.id NOT IN (%s)' % ','.join('?' for _ in excluded) if excluded else ''
        for offset in range(len(sources)):
            source = sources[(start + offset) % len(sources)]
            row = self.db.execute('''SELECT i.id,i.bytes,i.source FROM upload_index i
                JOIN events e ON e.id=i.id
                LEFT JOIN deliveries d ON d.destination=? AND d.id=i.id
                LEFT JOIN upload_failures f ON f.destination=? AND f.id=i.id
                LEFT JOIN upload_channels c ON c.destination=? AND c.source=i.source
                WHERE i.source=? AND i.quarantine IS NULL AND d.id IS NULL
                AND (f.retry_at IS NULL OR f.retry_at<=?)
                AND (c.retry_at IS NULL OR c.retry_at<=?)
                AND i.bytes<=? AND ''' + stamp_filter + exclude +
                ' ORDER BY ' + order + ' LIMIT 1',
                [destination, destination, destination, source, now, now, budget] + list(excluded)).fetchone()
            if row:
                return row
        return None

    def next_batch(self, destination, sources=None, limit=MAX_BATCH_EVENTS,
                   now=None, max_bytes=MAX_REQUEST_BYTES):
        """Return a bounded upload list; no acknowledgement happens here.

        Failure deadlines and lane/source cursors survive restart. Advancing
        selection cursors before success prevents a bad source monopolizing the
        next request. Evidence is only removed from pending by acknowledge().
        """
        now = time.time() if now is None else now
        limit = min(MAX_BATCH_EVENTS, max(0, int(limit)))
        max_bytes = min(MAX_REQUEST_BYTES, int(max_bytes))
        sources = self._sources(sources)
        if sources is None:
            sources = [row[0] for row in self.db.execute('SELECT DISTINCT source FROM upload_index ORDER BY source')]
        if not sources or not limit or max_bytes <= EMPTY_REQUEST_BYTES:
            return []
        state = self.db.execute('''SELECT phase,history_unknown,latest_source,
            historical_source,unknown_source FROM upload_schedule WHERE destination=?''', (destination,)).fetchone()
        phase, history_unknown, latest, historical, unknown = state or (0, 1, None, None, None)
        cursors = {'latest': latest, 'historical': historical, 'unknown': unknown}
        result = []
        excluded = []
        size = EMPTY_REQUEST_BYTES
        with self.db:
            while len(result) < limit:
                history_lanes = ['unknown', 'historical'] if history_unknown else ['historical', 'unknown']
                lanes = ['latest'] + history_lanes if phase < 3 else history_lanes + ['latest']
                remaining = max_bytes - size - (2 if result else 0)
                selected = None
                for lane in lanes:
                    # A due history slot must inspect the actual oldest ready
                    # candidate, even when it needs an otherwise empty batch.
                    # Filtering by the remaining bytes would let continuous
                    # small fresh events permanently bypass large history.
                    candidate_budget = (max_bytes - EMPTY_REQUEST_BYTES
                                        if phase == 3 and lane != 'latest' else remaining)
                    selected = self._candidate(destination, sources, lane, cursors[lane], excluded,
                                               candidate_budget, now)
                    if selected:
                        break
                if selected is None:
                    break
                identity, indexed_size, source = selected
                if indexed_size > remaining:
                    # Keep phase and lane/source cursors at this history slot.
                    # The next call starts with an empty envelope and sends
                    # this legal record before admitting fresh arrivals.
                    break
                excluded.append(identity)
                body = self.db.execute('SELECT event FROM events WHERE id=?', (identity,)).fetchone()[0]
                try:
                    event = json.loads(body)
                    if not isinstance(event, dict) or event.get('id') != identity:
                        raise ValueError('Invalid event identity')
                    actual_size = len(json.dumps(event, ensure_ascii=False).encode('utf-8'))
                except (ValueError, TypeError, UnicodeError):
                    self.db.execute("UPDATE upload_index SET quarantine='invalid_json' WHERE id=?", (identity,))
                    continue
                if actual_size != indexed_size:
                    quarantine = ('oversize: request requires %d bytes' % (actual_size + EMPTY_REQUEST_BYTES)
                                  if actual_size + EMPTY_REQUEST_BYTES > MAX_REQUEST_BYTES else None)
                    self.db.execute('UPDATE upload_index SET bytes=?,quarantine=? WHERE id=?',
                                    (actual_size, quarantine, identity))
                if actual_size > remaining:
                    if (result and phase == 3 and lane != 'latest'
                            and actual_size <= max_bytes - EMPTY_REQUEST_BYTES):
                        break
                    continue
                result.append(event)
                size += actual_size + (2 if len(result) > 1 else 0)
                cursors[lane] = source
                if lane != 'latest':
                    history_unknown = int(lane != 'unknown')
                phase = (phase + 1) % 4
            if result:
                self.db.execute('INSERT OR REPLACE INTO upload_schedule VALUES(?,?,?,?,?,?)',
                                (destination, phase, history_unknown, cursors['latest'],
                                 cursors['historical'], cursors['unknown']))
        return result

    def acknowledge(self, destination, ids, expected_ids=None):
        """Persist only an exact, duplicate-free receipt for known evidence.

        Pass the requested IDs as ``expected_ids`` when handling a server reply.
        A partial or forged receipt raises without writing any deliveries.
        """
        ids = list(ids)
        expected = list(expected_ids) if expected_ids is not None else ids
        if (any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)
                or len(set(expected)) != len(expected) or set(ids) != set(expected)):
            raise ValueError('Upload receipt must match all requested event IDs exactly')
        if not ids:
            return
        slots = ','.join('?' for _ in ids)
        found = self.db.execute('''SELECT e.id,i.quarantine FROM events e
            LEFT JOIN upload_index i ON i.id=e.id WHERE e.id IN (''' + slots + ')', ids).fetchall()
        if len(found) != len(ids) or any(row[1] for row in found):
            raise ValueError('Receipt includes unknown or quarantined evidence')
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO deliveries VALUES(?,?)', [(destination, i) for i in ids])
            self.db.executemany('DELETE FROM upload_failures WHERE destination=? AND id=?',
                                [(destination, i) for i in ids])
            self.db.execute('''DELETE FROM upload_channels WHERE destination=? AND source IN
                (SELECT source FROM upload_index WHERE id IN (''' + slots + '))', [destination] + ids)

    def fail(self, destination, ids, error='', now=None, channel=False):
        """Back off these records (or their explicit source channels) durably.

        Transient failures do not quarantine evidence or acknowledge it. Retry
        delays grow from one second to five minutes; other records stay ready.
        """
        now = time.time() if now is None else now
        ids = list(dict.fromkeys(ids))
        if not ids:
            return
        table, key = ('upload_channels', 'source') if channel else ('upload_failures', 'id')
        if channel:
            slots = ','.join('?' for _ in ids)
            keys = [row[0] for row in self.db.execute('SELECT DISTINCT source FROM upload_index WHERE id IN (' + slots + ')', ids)]
        else:
            keys = ids
        with self.db:
            for value in keys:
                old = self.db.execute('SELECT attempts FROM ' + table + ' WHERE destination=? AND ' + key + '=?',
                                      (destination, value)).fetchone()
                attempts = (old[0] if old else 0) + 1
                retry_at = now + min(300, 2 ** min(attempts - 1, 9))
                self.db.execute('INSERT OR REPLACE INTO ' + table + ' VALUES(?,?,?,?,?)',
                                (destination, value, attempts, retry_at, str(error)[:500]))

    def stats(self, destination, sources=None, now=None):
        """Compact queue health; no evidence JSON is decoded for status."""
        now = time.time() if now is None else now
        sources = self._sources(sources)
        source_filter = ''
        parameters = [destination, destination, destination]
        if sources is not None:
            source_filter = ' AND i.source IN (%s)' % (','.join('?' for _ in sources) or 'NULL')
            parameters.extend(sources)
        rows = self.db.execute('''SELECT i.source,
            count(*),sum(d.id IS NULL),
            sum(d.id IS NULL AND i.quarantine IS NULL AND coalesce(f.retry_at,0)<=?
                AND coalesce(c.retry_at,0)<=?),
            sum(d.id IS NULL AND i.quarantine IS NULL AND (coalesce(f.retry_at,0)>?
                OR coalesce(c.retry_at,0)>?)),
            sum(d.id IS NULL AND i.quarantine IS NOT NULL),
            sum(d.id IS NULL AND i.quarantine LIKE 'oversize:%'),
            sum(d.id IS NULL AND i.source_time IS NULL),
            min(CASE WHEN d.id IS NULL THEN i.source_time END),
            max(CASE WHEN d.id IS NULL THEN i.source_time END)
            FROM upload_index i
            LEFT JOIN deliveries d ON d.destination=? AND d.id=i.id
            LEFT JOIN upload_failures f ON f.destination=? AND f.id=i.id
            LEFT JOIN upload_channels c ON c.destination=? AND c.source=i.source
            WHERE 1=1''' + source_filter + ' GROUP BY i.source ORDER BY i.source',
            [now, now, now, now] + parameters).fetchall()
        keys = ('indexed', 'pending', 'ready', 'retrying', 'quarantined', 'oversize', 'unknown_timestamp')
        by_source = {}
        for row in rows:
            values = {key: int(value or 0) for key, value in zip(keys, row[1:8])}
            values.update(oldest_source_time=row[8], newest_source_time=row[9])
            by_source[row[0]] = values
        result = {key: sum(values[key] for values in by_source.values()) for key in keys}
        state = self.db.execute("SELECT value FROM upload_index_state WHERE name='rowid'").fetchone()
        total = self.db.execute('SELECT count(*) FROM events').fetchone()[0]
        indexed = self.db.execute('SELECT count(*) FROM upload_index').fetchone()[0]
        unindexed = max(0, total - indexed)
        details_filter = ''
        details_parameters = [destination]
        if sources is not None:
            details_filter = ' AND i.source IN (%s)' % (','.join('?' for _ in sources) or 'NULL')
            details_parameters.extend(sources)
        quarantined = self.db.execute('''SELECT i.id,i.source,i.bytes,i.quarantine
            FROM upload_index i LEFT JOIN deliveries d ON d.destination=? AND d.id=i.id
            WHERE d.id IS NULL AND i.quarantine IS NOT NULL''' + details_filter +
            ' ORDER BY i.event_rowid LIMIT 20', details_parameters).fetchall()
        errors = self.db.execute('''SELECT i.source,f.id,f.attempts,f.retry_at,f.last_error
            FROM upload_failures f JOIN upload_index i ON i.id=f.id
            WHERE f.destination=?''' + details_filter +
            ' ORDER BY f.retry_at DESC LIMIT 20', details_parameters).fetchall()
        channel_filter = ''
        if sources is not None:
            channel_filter = ' AND source IN (%s)' % (','.join('?' for _ in sources) or 'NULL')
        channel_errors = self.db.execute('''SELECT source,attempts,retry_at,last_error
            FROM upload_channels WHERE destination=?''' + channel_filter +
            ' ORDER BY retry_at DESC LIMIT 20', details_parameters).fetchall()
        result.update(sources=by_source, index_cursor=state[0] if state else 0,
                      unindexed=unindexed, index_complete=unindexed == 0,
                      quarantine_details=[dict(zip(('id', 'source', 'bytes', 'reason'), row)) for row in quarantined],
                      errors=[dict(zip(('source', 'id', 'attempts', 'retry_at', 'error'), row)) for row in errors],
                      channel_errors=[dict(zip(('source', 'attempts', 'retry_at', 'error'), row)) for row in channel_errors])
        return result
