"""Bounded semantic-field projection of uploaded sessions; original evidence stays intact.

Upload row IDs drive the incremental cursor only. They are not source-message
order or task identity. No desktop, vector runtime, model or network dependency.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import threading

from .collection_view import event_seconds

VERSION = 1
TEXT_LIMIT = 4000
FIELD_LIMIT = 12000
CALL_KINDS = {'tool_call', 'command_execution', 'mcp_execution', 'extension_execution'}
SEMANTIC_KINDS = CALL_KINDS | {'tool_result', 'reasoning', 'user_message', 'assistant_message', 'message', 'file_change'}


def tokens(text, limit=2048):
    """FTS stores English terms and Chinese bigrams, not a pseudo-embedding."""
    value = str(text).lower()
    words = re.findall(r'[a-z0-9][a-z0-9_./:-]*', value)
    for phrase in re.findall(r'[\u4e00-\u9fff]+', value):
        words.extend(phrase[i:i + 2] for i in range(len(phrase) - 1))
        if len(phrase) == 1:
            words.append(phrase)
    return list(dict.fromkeys(w[:200] for w in words if w))[:limit]


def _plain(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(_plain(item) for item in value[:64])
    if isinstance(value, dict):
        for key in ('text', 'content', 'message', 'summary', 'output', 'input', 'arguments', 'command', 'stdout', 'aiTitle'):
            if key in value:
                return _plain(value[key])
        return json.dumps(value, ensure_ascii=False)
    return '' if value is None else str(value)


def _field_text(raw, structured=False):
    if not isinstance(raw, str):
        return _plain(raw)
    if raw.startswith(('[', '{')):
        try:
            value = json.loads(raw)
            return json.dumps(value, ensure_ascii=False) if structured and isinstance(value, dict) else _plain(value)
        except ValueError:
            # SQL returns finite head/tail excerpts. A cut JSON text block is
            # still readable, without fetching the whole megabyte-sized field.
            parts = re.findall(r'"(?:text|content|message|summary|output|command|stdout)"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
            if parts:
                decoded = []
                for part in parts:
                    try:
                        decoded.append(json.loads('"' + part + '"'))
                    except ValueError:
                        continue
                if decoded:
                    return '\n'.join(decoded)
    return raw


def _readable_user(text):
    if re.match(r'\s*(?:#+\s*对话历史摘要\s*)?<(?:cb_summary|conversation_history_summary|task-notification)\b', text):
        return ''
    if text.lstrip().startswith(('The following is the Codex agent history', '# 对话历史摘要')):
        return ''
    queries = re.findall(r'<user_query\b[^>]*>(.*?)</user_query>', text, re.S)
    if queries:
        return queries[-1].strip()
    text = re.sub(r'<(system-reminder|in-app-browser-context)\b[^>]*>.*?</\1>', '', text, flags=re.S)
    if re.match(r'\s*<(?:environment_context|permissions|instructions)\b', text):
        return ''
    return text.strip()


def _cap(text):
    if len(text) <= TEXT_LIMIT:
        return text
    return text[:TEXT_LIMIT // 3] + '\n[片段省略]\n' + text[-(TEXT_LIMIT - TEXT_LIMIT // 3 - 9):]


def _projection_sql():
    """Select semantic fields in SQLite; never materialize the entire raw event."""
    def field(*names):
        paths = ["json_extract(event,'$.payload.item." + name + "')" for name in names]
        paths += ["json_extract(event,'$.payload." + name + "')" for name in names]
        return 'coalesce(' + ','.join(paths) + ",'')"
    kind = "json_extract(event,'$.kind')"
    content = 'CASE WHEN ' + kind + " IN ('tool_call','command_execution','mcp_execution','extension_execution') THEN " + field('arguments', 'input', 'command')
    content += ' WHEN ' + kind + "='tool_result' THEN " + field('output', 'content', 'stdout')
    content += ' WHEN ' + kind + "='reasoning' THEN " + field('content', 'rawContent', 'summary', 'text')
    content += ' ELSE ' + field('content', 'message', 'text', 'output', 'prompt', 'aiTitle') + ' END'
    metadata = {
        'id': "id", 'kind': kind, 'source': "json_extract(event,'$.source')",
        'sessionId': 'session', 'role': "coalesce(json_extract(event,'$.role'),json_extract(event,'$.payload.item.role'),json_extract(event,'$.payload.role'))",
        'name': "coalesce(json_extract(event,'$.name'),json_extract(event,'$.payload.item.name'),json_extract(event,'$.payload.name'))",
        'callId': "coalesce(json_extract(event,'$.callId'),json_extract(event,'$.payload.item.callId'),json_extract(event,'$.payload.item.call_id'),json_extract(event,'$.payload.callId'),json_extract(event,'$.payload.call_id'))",
        'timestamp': "coalesce(json_extract(event,'$.timestamp'),json_extract(event,'$.at'))",
        'sourceId': "coalesce(json_extract(event,'$.payload.item.id'),json_extract(event,'$.payload.id'))",
        'parentId': "coalesce(json_extract(event,'$.payload.item.parentId'),json_extract(event,'$.payload.parentId'),json_extract(event,'$.sourceFields.parentId'))",
        'requestId': "coalesce(json_extract(event,'$.requestId'),json_extract(event,'$.sourceFields.requestId'),json_extract(event,'$.payload.item.providerData.conversationRequestId'),json_extract(event,'$.payload.providerData.conversationRequestId'),json_extract(event,'$.payload.requestId'),json_extract(event,'$.payload.request_id'))",
        'turnId': "coalesce(json_extract(event,'$.turnId'),json_extract(event,'$.sourceFields.turn_id'),json_extract(event,'$.payload.turn_id'),json_extract(event,'$.payload.turnId'))",
        'traceId': "coalesce(json_extract(event,'$.payload.item.providerData.traceId'),json_extract(event,'$.payload.providerData.traceId'))",
        'fileIdentity': "coalesce(json_extract(event,'$.evidence.fileIdentity'),json_extract(event,'$.evidence.path'))",
        'epoch': "json_extract(event,'$.evidence.epoch')",
        'byteStart': "coalesce(json_extract(event,'$.evidence.byteStart'),json_extract(event,'$.evidence.line'))",
    }
    pairs = ','.join("'" + key + "',substr(" + value + ',1,1000)' if key not in ('epoch', 'byteStart') else "'" + key + "'," + value for key, value in metadata.items())
    return ('SELECT rowid AS seq,id,json_object(' + pairs + ') AS metadata,'
            'substr(' + content + ',1,' + str(FIELD_LIMIT) + ') AS head,'
            'CASE WHEN length(' + content + ')>' + str(FIELD_LIMIT) + ' THEN substr(' + content + ',-' + str(FIELD_LIMIT) + ') END AS tail,'
            'length(' + content + ') AS content_length FROM session_events ')


class CollectionKnowledge:
    def __init__(self, path, sessions, devices):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions = sessions
        self.devices = devices
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS collection_records(
              owner TEXT,device TEXT,id TEXT,source TEXT,session TEXT,kind TEXT,role TEXT,
              text TEXT,seq INTEGER,timestamp REAL,metadata TEXT,truncated INTEGER,searchable INTEGER,
              PRIMARY KEY(owner,device,id));
            CREATE INDEX IF NOT EXISTS collection_record_scope ON collection_records(owner,device,source,session,timestamp,seq);
            CREATE INDEX IF NOT EXISTS collection_record_seq ON collection_records(owner,device,seq);
            CREATE VIRTUAL TABLE IF NOT EXISTS collection_fts USING fts5(text);
            CREATE TRIGGER IF NOT EXISTS collection_record_delete AFTER DELETE ON collection_records
              BEGIN DELETE FROM collection_fts WHERE rowid=old.rowid; END;
            CREATE TABLE IF NOT EXISTS collection_cursors(owner TEXT,device TEXT,boundary INTEGER,cursor INTEGER,
              recent INTEGER,fresh INTEGER,aliases TEXT,version INTEGER,PRIMARY KEY(owner,device));
            ''')
        os.chmod(self.path, 0o600)
        self.projection = _projection_sql()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:
                yield db
        finally:
            db.close()

    def _scope(self, owner, device, aliases=None):
        found = self.devices.get(device, owner)
        if not found:
            raise PermissionError('Device unavailable for this account')
        canonical = found['id']
        with self.devices.connect() as db:
            actual = [r[0] for r in db.execute('SELECT alias FROM device_aliases WHERE canonical=? AND owner=?', (canonical, owner))]
        allowed = [canonical, *actual]
        if aliases is not None and any(alias not in allowed for alias in aliases):
            raise PermissionError('Unrelated device alias')
        return canonical, sorted(set(allowed))

    def sync(self, owner, canonical_device, aliases=None, limit=500):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError('Index batch must contain 1 to 500 records')
        device, ids = self._scope(owner, canonical_device, aliases)
        marks = ','.join('?' for _ in ids)
        scope = 'owner=? AND device IN (' + marks + ')'
        args = [owner, *ids]
        signature = json.dumps(ids)
        with self.lock, self.sessions.connect() as raw, self.connect() as db:
            stats = raw.execute('SELECT coalesce(max(rowid),0),count(*) FROM session_events WHERE ' + scope, args).fetchone()
            maximum, total = stats
            state = db.execute('SELECT * FROM collection_cursors WHERE owner=? AND device=?', (owner, device)).fetchone()
            if not state or state['aliases'] != signature or state['version'] != VERSION:
                # An alias change rebuilds only this finite derived scope, never
                # source evidence or another owner's index.
                if state:
                    db.execute('DELETE FROM collection_records WHERE owner=? AND device=?', (owner, device))
                boundary, cursor, recent, fresh = maximum, 0, maximum + 1, maximum
            else:
                boundary, cursor, recent, fresh = (state[key] for key in ('boundary', 'cursor', 'recent', 'fresh'))
            selected = {}
            # Newly uploaded events are prioritized independently of historical
            # backfill. The two history cursors approach each other from either end.
            fresh_budget = max(1, limit // 3)
            live = raw.execute(self.projection + 'WHERE ' + scope + ' AND rowid>? ORDER BY rowid LIMIT ?', [*args, fresh, fresh_budget]).fetchall()
            for row in live:
                selected[row['seq']] = row
                fresh = row['seq']
            left = limit - len(selected)
            recent_budget = max(1, left // 2) if left else 0
            recent_rows = raw.execute(self.projection + 'WHERE ' + scope + ' AND rowid<=? AND rowid<? AND rowid>? ORDER BY rowid DESC LIMIT ?', [*args, boundary, recent, cursor, recent_budget]).fetchall()
            for row in recent_rows:
                selected[row['seq']] = row
                recent = row['seq']
            left = limit - len(selected)
            history = raw.execute(self.projection + 'WHERE ' + scope + ' AND rowid>? AND rowid<? AND rowid<=? ORDER BY rowid LIMIT ?', [*args, cursor, recent, boundary, left]).fetchall()
            for row in history:
                selected[row['seq']] = row
                cursor = row['seq']
            for row in selected.values():
                self._put(db, owner, device, row)
            db.execute('INSERT OR REPLACE INTO collection_cursors VALUES(?,?,?,?,?,?,?,?)', (owner, device, boundary, cursor, recent, fresh, signature, VERSION))
            indexed, searchable = db.execute('SELECT count(*),coalesce(sum(searchable),0) FROM collection_records WHERE owner=? AND device=?', (owner, device)).fetchone()
            return {'indexedRecords': indexed, 'totalRecords': total, 'indexComplete': indexed == total,
                    'searchableRecords': searchable, 'indexedThisBatch': len(selected), 'coverage': 'uploaded_semantic_fields'}

    def _put(self, db, owner, device, row):
        metadata = json.loads(row['metadata'])
        kind, role = metadata.get('kind'), metadata.get('role')
        head = _field_text(row['head'], structured=kind in CALL_KINDS or kind == 'tool_result')
        tail = _field_text(row['tail'], structured=kind in CALL_KINDS or kind == 'tool_result') if row['tail'] else ''
        text = head + ('\n[源字段片段省略]\n' + tail if tail else '')
        user = kind == 'user_message' or kind == 'message' and role == 'user'
        if user:
            text = _readable_user(text)
        if kind not in SEMANTIC_KINDS or kind == 'message' and role not in ('user', 'assistant'):
            text = ''
        truncated = row['content_length'] > FIELD_LIMIT or len(text) > TEXT_LIMIT
        text = _cap(text.strip())
        if kind in CALL_KINDS and metadata.get('name'):
            text = _cap(metadata['name'] + '\n' + text)
        timestamp = event_seconds(metadata.get('timestamp'))
        metadata['timestamp'] = timestamp
        prior = db.execute('SELECT rowid FROM collection_records WHERE owner=? AND device=? AND id=?', (owner, device, row['id'])).fetchone()
        if prior:
            return
        inserted = db.execute('INSERT INTO collection_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (owner, device, row['id'], metadata['source'], metadata['sessionId'], kind, role,
                               text, row['seq'], timestamp, json.dumps(metadata, ensure_ascii=False), int(truncated), int(bool(text))))
        if text:
            db.execute('INSERT INTO collection_fts(rowid,text) VALUES(?,?)', (inserted.lastrowid, ' '.join(tokens(text))))

    @staticmethod
    def _record(row):
        metadata = json.loads(row['metadata'])
        provider = {'conversationRequestId': metadata.get('requestId'), 'traceId': metadata.get('traceId')}
        event = {'id': row['id'], 'source': row['source'], 'sessionId': row['session'], 'kind': row['kind'],
                 'role': row['role'], 'name': metadata.get('name'), 'callId': metadata.get('callId'),
                 'timestamp': row['timestamp'], 'turnId': metadata.get('turnId'), 'requestId': metadata.get('requestId'),
                 'payload': {'id': metadata.get('sourceId'), 'parentId': metadata.get('parentId'), 'providerData': provider},
                 '_seq': row['seq'], '_text': row['text'], '_textTruncated': bool(row['truncated'])}
        result = {'recordId': 'sessionlens:' + row['id'], 'id': row['id'], 'source': row['source'],
                  'sessionId': row['session'], 'kind': row['kind'], 'role': row['role'], 'text': row['text'],
                  'seq': row['seq'], 'timestamp': row['timestamp'], 'truncated': bool(row['truncated']),
                  'searchable': bool(row['searchable']), 'event': event,
                  'fileIdentity': metadata.get('fileIdentity'), 'epoch': metadata.get('epoch'), 'byteStart': metadata.get('byteStart'),
                  'orderBasis': 'timestamp' if row['timestamp'] else 'source_file_offset' if metadata.get('byteStart') is not None else 'upload_sequence_fallback'}
        if row['kind'] == 'user_message' or row['kind'] == 'message' and row['role'] == 'user':
            if row['text']:
                result['prompt'] = row['text']
        return result

    def search(self, owner, device, terms, limit=40):
        canonical, _ = self._scope(owner, device)
        if not isinstance(terms, (list, tuple)) or len(terms) > 16 or any(not isinstance(term, str) or len(term) > 2000 for term in terms):
            raise ValueError('Invalid retrieval terms')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('Invalid retrieval limit')
        limit = min(limit, 40)
        groups = []
        words = []
        for term in terms:
            group = list(dict.fromkeys(word for word in tokens(term, 80) if word not in words))[:120 - len(words)]
            if group:
                groups.append(group)
                words.extend(group)
        if not words:
            return []

        def query(group):
            return ' OR '.join('"' + word.replace('"', '""') + '"' for word in group)

        # Expansion terms must not displace a short original user requirement.
        # Retrieve IDs through bounded independent channels before loading text.
        # BM25 values from different queries are not comparable: fuse ranks.
        queries = list(dict.fromkeys([query(words), *(query(group) for group in groups)]))
        candidates = {}
        quota = min(80, max(12, limit * 2))
        user_filter = " AND (r.kind='user_message' OR (r.kind='message' AND r.role='user'))"
        with self.connect() as db:
            for expression in queries:
                for users in (False, True):
                    rows = db.execute('''SELECT r.rowid AS rowid,r.id,r.source,r.session,r.kind,r.role,r.timestamp,r.seq,
                      bm25(collection_fts) AS rank FROM collection_fts
                      JOIN collection_records r ON r.rowid=collection_fts.rowid
                      WHERE collection_fts MATCH ? AND r.owner=? AND r.device=?''' + (user_filter if users else '') + '''
                      ORDER BY bm25(collection_fts),r.timestamp DESC,r.seq DESC LIMIT ?''',
                      (expression, owner, canonical, quota if not users else min(quota, 24))).fetchall()
                    for position, row in enumerate(rows, 1):
                        found = candidates.setdefault(row['id'], {'row': row, 'score': 0.0, 'channels': set()})
                        # The additional user channel is a reservation, not an
                        # extra relevance vote over otherwise identical text.
                        if expression not in found['channels']:
                            found['score'] += 1 / (20 + position)
                            found['channels'].add(expression)

            def ranked(items):
                return sorted(items, key=lambda item: (-item['score'], -(item['row']['timestamp'] or 0), -item['row']['seq'], item['row']['id']))

            def balanced(items, count):
                # Round-robin source and session buckets. One chat with many
                # tool results cannot occupy every candidate before reranking.
                sources = {}
                for item in ranked(items):
                    row = item['row']
                    source = sources.setdefault(row['source'], {})
                    source.setdefault(row['session'], []).append(item)
                result = []
                while len(result) < count and sources:
                    for source in list(sources):
                        sessions = sources[source]
                        if not sessions:
                            del sources[source]
                            continue
                        session = next(iter(sessions))
                        bucket = sessions.pop(session)
                        result.append(bucket.pop(0))
                        if bucket:
                            sessions[session] = bucket
                        if len(result) == count:
                            break
                return result

            is_user = lambda item: item['row']['kind'] == 'user_message' or item['row']['kind'] == 'message' and item['row']['role'] == 'user'
            selected = balanced([item for item in candidates.values() if is_user(item)], max(1, limit // 3))
            selected_ids = {item['row']['id'] for item in selected}
            selected.extend(balanced([item for item in candidates.values() if item['row']['id'] not in selected_ids], limit - len(selected)))
            if not selected:
                return []
            marks = ','.join('?' for _ in selected)
            rows = db.execute('SELECT * FROM collection_records WHERE owner=? AND device=? AND id IN (' + marks + ')',
                              [owner, canonical, *(item['row']['id'] for item in selected)]).fetchall()
            by_id = {row['id']: row for row in rows}
            return [self._record(by_id[item['row']['id']]) for item in selected if item['row']['id'] in by_id]

    def window(self, owner, device, source, sessionId, recordId, before=8, after=12):
        canonical, _ = self._scope(owner, device)
        if source not in ('codex', 'workbuddy') or not isinstance(sessionId, str) or not isinstance(recordId, str):
            raise ValueError('Invalid session scope')
        if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 35 for value in (before, after)):
            raise ValueError('Window must contain at most 36 user turns')
        if before + after > 35:
            raise ValueError('Window must contain at most 36 user turns')
        identity = recordId.removeprefix('sessionlens:')
        with self.connect() as db:
            anchor = db.execute('SELECT * FROM collection_records WHERE owner=? AND device=? AND source=? AND session=? AND id=?', (owner, canonical, source, sessionId, identity)).fetchone()
            if not anchor:
                raise ValueError('Record outside selected session or not indexed')
            metadata = json.loads(anchor['metadata'])
            scope = 'owner=? AND device=? AND source=? AND session=?'
            args = [owner, canonical, source, sessionId]
            # A finite metadata scan locates USER boundaries, not the N nearest
            # events. A tool result may be hundreds of events after its user.
            if not anchor['timestamp'] and metadata.get('fileIdentity') and metadata.get('byteStart') is not None:
                scope += " AND json_extract(metadata,'$.fileIdentity')=?"
                args.append(metadata['fileIdentity'])
                order_parts = ["coalesce(json_extract(metadata,'$.epoch'),0)", "json_extract(metadata,'$.byteStart')", 'seq']
                anchor_key = (metadata.get('epoch') or 0, metadata['byteStart'], anchor['seq'])
                compare = "(coalesce(json_extract(metadata,'$.epoch'),0),json_extract(metadata,'$.byteStart'),seq)"
            elif anchor['timestamp']:
                order_parts = ['timestamp', 'seq']
                anchor_key = (anchor['timestamp'], anchor['seq'])
                compare = '(timestamp,seq)'
            else:
                order_parts = ['seq']
                anchor_key = (anchor['seq'],)
                compare = 'seq'
            placeholders = '(' + ','.join('?' for _ in anchor_key) + ')' if len(anchor_key) > 1 else '?'
            lightweight = "SELECT owner,device,id,source,session,kind,role,'' AS text,seq,timestamp,metadata,truncated,searchable FROM collection_records WHERE "
            left = db.execute(lightweight + scope + ' AND ' + compare + '<' + placeholders + ' ORDER BY ' + ','.join(part + ' DESC' for part in order_parts) + ' LIMIT 999', [*args, *anchor_key]).fetchall()
            right = db.execute(lightweight + scope + ' AND ' + compare + '>' + placeholders + ' ORDER BY ' + ','.join(order_parts) + ' LIMIT 1000', [*args, *anchor_key]).fetchall()
            rows = [*reversed(left), anchor, *right]
            meta = {row['id']: json.loads(row['metadata']) for row in rows}
            files = {m.get('fileIdentity') for m in meta.values()}
            same_file = len(files) == 1 and None not in files and all(m.get('byteStart') is not None for m in meta.values())
            if same_file:
                rows.sort(key=lambda row: (meta[row['id']].get('epoch') or 0, meta[row['id']]['byteStart'], row['seq']))
            else:
                rows.sort(key=lambda row: (row['timestamp'] or row['seq'], row['seq']))
            at = next(i for i, row in enumerate(rows) if row['id'] == identity)
            users = [i for i, row in enumerate(rows) if row['searchable'] and (row['kind'] == 'user_message' or row['kind'] == 'message' and row['role'] == 'user')]
            previous_users = {rows[position]['id']: rows[users[i-1]]['id'] if i else None for i,position in enumerate(users)}
            prior_users = [i for i in users if i <= at]
            if prior_users:
                current = users.index(prior_users[-1])
                first_user = max(0, current - before)
                last_user = min(len(users) - 1, current + after)
                start, end = users[first_user], users[last_user + 1] if last_user + 1 < len(users) else len(rows)
            else:
                # Do not invent a predecessor. Keep nearby evidence but make
                # the missing user boundary explicit to the caller.
                start, end = max(0, at - 12), min(len(rows), at + 13)
            chosen = rows[start:end]
            total = len(chosen)
            if total > 500:
                # Preserve each chosen user and the anchor, then balanced first
                # and last evidence of every turn. Do not lose a task boundary.
                chosen_ids = {identity} | {rows[i]['id'] for i in users if start <= i < end}
                boundaries = [start, *[i for i in users if start < i < end], end]
                per_turn = max(2, (500 - len(chosen_ids)) // max(1, len(boundaries) - 1))
                for a, b in zip(boundaries, boundaries[1:]):
                    segment = rows[a:b]
                    for row in [*segment[:per_turn // 2], *segment[-(per_turn - per_turn // 2):]]:
                        chosen_ids.add(row['id'])
                chosen = [row for row in chosen if row['id'] in chosen_ids][:500]
            identifiers = [row['id'] for row in chosen]
            marks = ','.join('?' for _ in identifiers)
            full = {row['id']: row for row in db.execute('SELECT * FROM collection_records WHERE owner=? AND device=? AND id IN (' + marks + ')', [owner, canonical, *identifiers])}
        result = [self._record(full[ident]) for ident in identifiers]
        for ordinal, record in enumerate(result):
            record['windowSeq'] = ordinal
            record['event']['_seq'] = ordinal
            if record['id'] in previous_users:
                record['previousUserId'] = previous_users[record['id']]
            if same_file:
                record['orderBasis'] = 'source_file_offset'
            record['windowCoverage'] = {'basis': 'user_turn_boundaries' if prior_users else 'missing_preceding_user',
                                        'selectedRecords': len(result), 'candidateRecords': total,
                                        'truncated': len(result) < total or len(left) == 999 or len(right) == 1000}
        return result
