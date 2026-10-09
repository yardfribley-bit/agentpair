"""Read already-recorded HTTP evidence without making requests or guessing APIs.

Supports Chromium's `Request succeeded/failed/lagged: {...}` log envelope and
JSON records with explicit HTTP fields. Proprietary AalG/Atab files are reported
as unsupported, not interpreted as protobuf. Tool arguments are not HTTP bodies.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_BODY_CHARS = 65536
_IDENTITIES = {"conversation_id": "sessionId", "session_id": "sessionId", "sessionId": "sessionId",
               "tcid": "callId", "tool_call_id": "callId", "call_id": "callId", "callId": "callId",
               "request_id": "requestId", "requestId": "requestId", "task_id": "taskId", "taskId": "taskId"}
_SECRET = re.compile(r"^(?:authorization|cookie|set-cookie|password|passwd|access_key|access_token|refresh_token|token|mstoken|api_key|secret|session_key)$", re.I)
_SAFE_HEADERS = {"content-type", "content-length", "x-request-id", "x-log-id", "x-tt-logid", "x-tt-log-id"}


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[credential omitted]" if _SECRET.fullmatch(str(k)) else _safe(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


def _body(value: Any, content_type: str | None) -> dict:
    if value is None:
        return {"status": "not_recorded", "value": None}
    if content_type and "protobuf" in content_type.lower():
        return {"status": "opaque_protobuf", "value": None, "reason": "No verified wire schema or decoder is available."}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            if len(value) > MAX_BODY_CHARS:
                return {"status": "captured_text", "value": value[:MAX_BODY_CHARS], "truncated": True, "originalChars": len(value)}
            return {"status": "captured_text", "value": value}
    rendered = json.dumps(value, ensure_ascii=False)
    if len(rendered) > MAX_BODY_CHARS:
        return {"status": "captured_json", "value": json.dumps(_safe(value), ensure_ascii=False)[:MAX_BODY_CHARS],
                "truncated": True, "originalChars": len(rendered)}
    return {"status": "captured_json", "value": _safe(value)}


def _identities(value: Any, out: dict[str, set[str]], depth: int = 0) -> None:
    if depth > 16:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _IDENTITIES and isinstance(child, (str, int)) and str(child):
                out.setdefault(_IDENTITIES[key], set()).add(str(child))
            elif isinstance(child, (dict, list)):
                _identities(child, out, depth + 1)
            elif key in {"request_body", "response_body", "requestBody", "responseBody", "sandboxLogId"} and isinstance(child, str):
                try:
                    _identities(json.loads(child), out, depth + 1)
                except ValueError:
                    pass
    elif isinstance(value, list):
        for child in value:
            _identities(child, out, depth + 1)


def _headers(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(k).lower(): v for k, v in value.items() if str(k).lower() in _SAFE_HEADERS}


def parse_http_line(line: str, path: str, number: int) -> dict | None:
    """Parse an actual HTTP log, keeping absent method/body fields absent."""
    text = line.strip()
    match = re.search(r"Request (succeeded|failed|lagged):\s*(\{.*)$", text)
    if match:
        text = match[2]
    try:
        obj, _ = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    url = obj.get("url") or obj.get("request_url")
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        port = parts.port
    except ValueError:
        return None
    method = obj.get("method") or obj.get("http_method") or obj.get("request_method")
    method = str(method).upper() if method is not None else None
    if method is not None and method not in METHODS:
        method = None
    # Do not mistake an arbitrary JSON document containing a URL for HTTP.
    if not method and not any(k in obj for k in ("response_code", "status_code", "request_body_size_B", "response_body_size_B", "request_body", "response_body")):
        return None
    identities: dict[str, set[str]] = {}
    _identities(obj, identities)
    query = parse_qsl(parts.query, keep_blank_values=True)
    for key, val in query:
        if key in _IDENTITIES:
            identities.setdefault(_IDENTITIES[key], set()).add(val)
    query_safe = [(key, "[credential omitted]" if _SECRET.fullmatch(key) else val) for key, val in query]
    host = parts.hostname + (f":{port}" if port else "")
    safe_url = urlunsplit((parts.scheme, host, parts.path, urlencode(query_safe), ""))
    request_headers = _headers(obj.get("request_headers"))
    response_headers = _headers(obj.get("response_headers"))
    time = None
    stamp = re.search(r":(\d{8})/(\d{6})\.(\d+):", line)
    if stamp:
        try:
            time = datetime.strptime(stamp[1] + stamp[2], "%Y%m%d%H%M%S").replace(
                microsecond=int((stamp[3] + "000000")[:6])).isoformat()
        except ValueError:
            pass
    return {"method": method, "url": safe_url, "host": parts.hostname, "path": parts.path,
            "time": time, "timeSource": "recorded_local_clock_without_timezone" if time else "not_recorded",
            "queryParameters": [{"name": key, "value": val} for key, val in query_safe],
            "statusCode": obj.get("response_code", obj.get("status_code")),
            "requestHeaders": request_headers, "responseHeaders": response_headers,
            "requestBody": _body(obj.get("request_body", obj.get("requestBody")), request_headers.get("content-type")),
            "responseBody": _body(obj.get("response_body", obj.get("responseBody")), response_headers.get("content-type")),
            "requestBodyBytes": obj.get("request_body_size_B"), "responseBodyBytes": obj.get("response_body_size_B"),
            "elapsedMs": obj.get("time_spent_ms"), "identities": {key: sorted(values) for key, values in identities.items()},
            "outcome": match[1] if match else obj.get("outcome"), "networkErrorCode": obj.get("code"),
            "source": {"path": path, "line": number, "sha256": hashlib.sha256(line.encode()).hexdigest()},
            # Used for exact equality only; never display a credential URL.
            "_sourceUrl": url}


def associate_http(record: dict, session_id: str, call_ids: Iterable[str] = (), artifact_urls: Iterable[str] = ()) -> dict | None:
    """Only explicit IDs or exact known media URLs associate an HTTP record."""
    ids = record.get("identities", {})
    sessions = set(ids.get("sessionId", []))
    if sessions and sessions != {str(session_id)}:
        return None
    calls = set(ids.get("callId", []))
    matched_calls = calls.intersection(str(x) for x in call_ids)
    if matched_calls and len(calls) != 1:
        return None
    if matched_calls:
        association = {"kind": "explicit_call_id", "callId": next(iter(matched_calls))}
        purpose = "unknown_http"
    elif sessions == {str(session_id)}:
        association = {"kind": "explicit_session_id", "sessionId": str(session_id)}
        purpose = "unknown_http"
    elif record.get("_sourceUrl") in set(artifact_urls):
        association = {"kind": "exact_artifact_url"}
        purpose = "artifact_transfer"
    else:
        return None
    result = {k: v for k, v in record.items() if not k.startswith("_")}
    result.update(association=association, purpose=purpose)
    return result


def read_http_evidence(paths: Iterable[str | Path], session_id: str, call_ids: Iterable[str] = (),
                       artifact_urls: Iterable[str] = (), max_bytes: int = 8 * 1024 * 1024) -> dict:
    """Bounded read-only scan. An empty match is not an empty network history."""
    call_ids, artifact_urls = tuple(call_ids), tuple(artifact_urls)
    records, files, errors = [], [], []
    parsed_count = bytes_read = 0
    for value in paths:
        path = Path(value)
        try:
            with path.open("rb") as stream:
                prefix = stream.read(16)
                stream.seek(0)
                if prefix.startswith(b"AalG"):
                    files.append({"path": str(path), "format": "unsupported_aalG_atab_binary", "bytes": path.stat().st_size})
                    continue
                files.append({"path": str(path), "format": "text_log", "bytes": path.stat().st_size})
                for number, raw in enumerate(stream, 1):
                    bytes_read += len(raw)
                    if bytes_read > max_bytes:
                        errors.append({"path": str(path), "reason": "scan_byte_limit"})
                        break
                    if len(raw) > MAX_LINE_BYTES:
                        errors.append({"path": str(path), "line": number, "reason": "line_byte_limit"})
                        continue
                    parsed = parse_http_line(raw.decode("utf-8", "replace"), str(path), number)
                    if parsed is None:
                        continue
                    parsed_count += 1
                    scoped = associate_http(parsed, session_id, call_ids, artifact_urls)
                    if scoped:
                        records.append(scoped)
        except OSError as exc:
            errors.append({"path": str(path), "reason": type(exc).__name__})
        if bytes_read > max_bytes:
            break
    return {"records": records, "files": files, "errors": errors, "parsedHttpRecords": parsed_count,
            "unassociatedHttpRecords": parsed_count - len(records),
            "coverage": {"networkRequestsMade": False, "scope": "existing_local_logs_only",
                         "association": "explicit_ids_or_exact_media_url", "completeHttpCapture": False,
                         "toolArgumentsAreHttpBody": False, "opaqueProtocolsDecoded": False}}
