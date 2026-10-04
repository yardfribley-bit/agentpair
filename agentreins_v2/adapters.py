"""Original adapters; no AgentReins source copied. Untrusted contents are evidence."""
import base64
import datetime
import hashlib
import json
import re
import time
from pathlib import Path
from .store import digest


MAX_RECORD = 8 * 1024 * 1024
MAX_TRACE = 8 * 1024 * 1024


def stamp(value):
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def event(source, identity, kind, data, task=None, session=None, when=None):
    return {"event_id": digest([source, identity]), "kind": kind, "task_id": task,
            "session_id": session, "observed_at": when or time.time(), "data": data}


def user_query(message):
    content = message.get("content", "")
    if isinstance(content, list):
        content = "\n".join(v.get("text", "") for v in content if isinstance(v, dict))
    if not isinstance(content, str):
        return None
    queries = re.findall(r"<user_query>(.*?)</user_query>", content, re.S)
    return queries[-1] if queries else None


def network_events(record, source):
    if record.get("host", "").lower() != "copilot.tencent.com" or record.get("path") not in {
            "/v1/chat/completions", "/v2/chat/completions", "/v3/chat/completions"}:
        return []
    raw = base64.b64decode(record["requestBodyBase64"], validate=True)
    if len(raw) > MAX_RECORD:
        raise ValueError("decoded request exceeds limit")
    sha = hashlib.sha256(raw).hexdigest()
    if sha != record.get("requestSHA256"):
        raise ValueError("request hash mismatch")
    body = json.loads(raw)
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise ValueError("invalid request messages")
    flow = record.get("flowID") or sha
    when = stamp(record.get("observedAt"))
    result, tasks, current, call_tasks = [], [], None, {}
    # A query-history fingerprint groups repeated context across requests. It is
    # explicitly a projection identity, NOT an observed WorkBuddy session ID.
    for i, message in enumerate(body["messages"]):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            query = user_query(message)
            if query is not None:
                tasks.append(query)
                current = "query-" + digest(tasks)[:24]
                result.append(event(source, ["task", current], "task.query", {
                    "query": query, "correlation": "query_history_projection",
                    "session_status": "not_observed"}, current, when=when))
        for call in message.get("tool_calls", []) or []:
            call_id = call.get("id")
            if not call_id:
                continue
            call_tasks[call_id] = current
            function = call.get("function", {})
            result.append(event(source, ["tool.call", call_id, digest(function)], "tool.call", {
                "tool_call_id": call_id, "name": function.get("name"),
                "content": function.get("arguments"), "relation": "native_tool_call_id",
                "request_flow_id": flow}, current, when=when))
        if message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            result.append(event(source, ["tool.result", call_id, digest(message)], "tool.result", {
                "tool_call_id": call_id, "content": message.get("content"),
                "relation": "native_tool_call_id" if call_id in call_tasks else "unlinked",
                "included_in_model_request": True, "request_flow_id": flow},
                call_tasks.get(call_id, current), when=when))
    result.append(event(source, ["request", flow, sha], "model.request", {
        "flow_id": flow, "model": body.get("model"), "destination": record["host"] + record["path"],
        "body_sha256": sha, "decoded_bytes": len(raw), "message_count": len(body["messages"]),
        "wire_bytes": record.get("capturedWireBodyBytes"), "content": {"_model_request": body},
        "content_encoding": "normalized_json_manifest", "original_wire_bytes_retained": False,
        "quality": "decoded_body_hash_verified", "relation": "request_contains_messages"}, current, when=when))
    return result


class JSONLTail:
    def __init__(self, path, pipeline, since=0, batch_lines=12):
        self.path, self.pipeline = Path(path), pipeline
        self.source = "network:" + str(self.path)
        self.since, self.batch_lines = since, batch_lines
        self.bytes_read = 0

    def poll(self):
        if not self.path.exists():
            return False
        stat = self.path.stat()
        epoch = str(stat.st_dev) + ":" + str(stat.st_ino)
        old = self.pipeline.cursor(self.source) or {}
        offset = old.get("offset", 0)
        events = []
        if old and (old.get("epoch") != epoch or stat.st_size < offset):
            events.append(event(self.source, ["rotation", epoch, stat.st_mtime_ns], "coverage.change",
                                {"reason": "source_rotated_or_truncated", "previous_offset": offset}))
            offset = 0
        start = offset
        if stat.st_size == offset and not events:
            return False
        # No read of a whole growing log, even on historical import.
        with self.path.open("rb") as f:
            f.seek(offset)
            for _ in range(self.batch_lines):
                position = f.tell()
                line = f.readline(MAX_RECORD + 1)
                self.bytes_read += len(line)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    if len(line) > MAX_RECORD:
                        # Hold cursor until newline arrives, discard in bounded chunks.
                        while line and not line.endswith(b"\n"):
                            line = f.readline(MAX_RECORD)
                            self.bytes_read += len(line)
                        if not line:
                            break
                        events.append(event(self.source, [epoch, position, "oversize"], "coverage.gap",
                                            {"reason": "record_exceeds_limit", "offset": position}))
                        offset = f.tell()
                        continue
                    break  # a partial last record must never advance the durable cursor
                offset = f.tell()
                try:
                    record = json.loads(line)
                    when = stamp(record.get("observedAt"))
                    if when is None:
                        raise ValueError("missing/untrusted capture time")
                    if when >= self.since:
                        events.extend(network_events(record, self.source))
                except (ValueError, KeyError, TypeError) as exc:
                    events.append(event(self.source, [epoch, position, "invalid"], "coverage.gap",
                                        {"reason": str(exc)[:160], "offset": position}))
        if offset == start and not events:
            return False
        return self.pipeline.submit(self.source, {"epoch": epoch, "offset": offset}, events)


def parsed(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def embedded_failures(value):
    """Only flag explicit business errors, not every textual occurrence of 'error'."""
    if isinstance(value, dict):
        raw = value.get("rawResponse", {})
        if isinstance(raw, dict) and raw.get("exitCode") not in (None, 0):
            return True
        if value.get("ok") is False or value.get("status") in ("failed", "error"):
            return True
        return any(embedded_failures(v) for k, v in value.items() if k in ("content", "text", "rawResponse"))
    if isinstance(value, str):
        return bool(re.search(r'"ok"\s*:\s*false|"exit_code"\s*:\s*[1-9]\d*', value))
    return False


def trace_events(document, source):
    trace = document.get("trace", {})
    trace_id = trace.get("traceId")
    if not trace_id or not isinstance(document.get("spans"), list):
        raise ValueError("invalid trace")
    session = trace.get("sessionId")
    # Never turn trace or network IDs into observed session identity.
    task = "trace-" + trace_id
    events = [event(source, [trace_id, "metadata", digest(trace)], "trace.state", {
        "trace_id": trace_id, "state": trace.get("status"), "span_count": len(document["spans"]),
        "worker_pid": trace.get("workerPid"), "started_at": trace.get("startedAt"),
        "ended_at": trace.get("endedAt"), "relation": "native_trace_id"}, task, session)]
    for span in document["spans"]:
        sid = span.get("spanId")
        if not sid or span.get("type") not in ("function", "generation"):
            continue
        inp, out = parsed(span.get("toolInput")), parsed(span.get("toolOutput"))
        kind = "model.response" if span.get("type") == "generation" else "tool.execution"
        revision = digest([span.get("status"), span.get("endedAt"), inp, out])
        data = {"trace_id": trace_id, "span_id": sid, "parent_span_id": span.get("parentId"),
                "name": span.get("toolName") or span.get("name"), "status": span.get("status"),
                "started_at": span.get("startedAt"), "ended_at": span.get("endedAt"),
                "duration_ms": span.get("duration"), "relation": "native_trace_span",
                "content": {"input": inp if kind == "tool.execution" else None, "output": out},
                "input_truncated": isinstance(span.get("toolInput"), str) and inp == span.get("toolInput") and span.get("type") == "generation"}
        events.append(event(source, [sid, revision], kind, data, task, session, stamp(span.get("endedAt"))))
        if kind == "tool.execution" and embedded_failures(out):
            events.append(event(source, [sid, revision, "business_failure"], "finding.tool_failure", {
                "span_id": sid, "name": data["name"], "trace_status": span.get("status"),
                "summary": "Explicit internal failure; outer execution status is not business success",
                "evidence_event_id": events[-1]["event_id"]}, task, session))
        if kind == "tool.execution" and isinstance(inp, dict):
            paths = [inp[k] for k in ("file_path",) if isinstance(inp.get(k), str)]
            if inp.get("files") and isinstance(inp["files"], list):
                paths.extend(x for x in inp["files"] if isinstance(x, str) and x.startswith("/"))
            if isinstance(out, dict):
                raw = out.get("rawResponse", {})
                if isinstance(raw, dict):
                    paths.extend(x.get("localPath") for x in raw.get("videos", []) if isinstance(x, dict))
            for path in paths:
                if not path:
                    continue
                try:
                    p = Path(path)
                    if not p.is_file():
                        continue
                    stat = p.stat()
                    events.append(event(source, [sid, "file", path, stat.st_mtime_ns, stat.st_size], "file.snapshot", {
                        "path": path, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                        "span_id": sid, "trace_id": trace_id, "relation": "referenced_path_post_task_snapshot",
                        "historical_io_observed": False}, task, session))
                except OSError:
                    pass
    return events


class TraceWatch:
    def __init__(self, directory, pipeline, since=0):
        self.root, self.pipeline, self.since = Path(directory), pipeline, since
        self.bytes_read = 0

    def poll(self, max_files=8):
        changed = False
        count = 0
        for path in self.root.glob("*/*.json"):
            source = "trace:" + str(path)
            try:
                stat = path.stat()
                if stat.st_mtime < self.since:
                    continue
                version = [stat.st_ino, stat.st_size, stat.st_mtime_ns]
                if self.pipeline.cursor(source) == version:
                    continue
                if count >= max_files:
                    break
                count += 1
                if stat.st_size > MAX_TRACE:
                    events = [event(source, version, "coverage.gap", {"reason": "trace_exceeds_limit", "bytes": stat.st_size})]
                else:
                    raw = path.read_bytes()
                    self.bytes_read += len(raw)
                    try:
                        document = json.loads(raw)
                    except ValueError:
                        continue  # tolerate an in-progress rewritten snapshot
                    ended = stamp(document.get("trace", {}).get("endedAt"))
                    if ended is not None and ended < self.since:
                        events = []
                    else:
                        events = trace_events(document, source)
                if not self.pipeline.submit(source, version, events):
                    break
                changed = True
            except (OSError, ValueError, TypeError) as exc:
                self.pipeline.submit(source + ":error", None, [event(source, ["error", str(exc)], "coverage.gap", {"reason": str(exc)[:160]})])
        return changed
