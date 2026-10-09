"""Pure, source-grounded parsing. No network access or model inference."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_DECODER = json.JSONDecoder()
_URL = re.compile(r"https?://[^\s<>\"']+")
_MEDIA_REPORT = re.compile(r"\b(video|image)\s*\((?:(\d+(?:\.\d+)?)s\s*)?(\d+)x(\d+)\s+(\w+)\)", re.I)


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def evidence(source: str, path: str, line: int, raw: bytes | str) -> dict:
    raw = raw.encode("utf-8") if isinstance(raw, str) else raw
    return {"source": source, "path": path, "line": line,
            "sha256": hashlib.sha256(raw).hexdigest()}


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(content_text(x.get("text", x)) if isinstance(x, dict)
                         else content_text(x) for x in value)
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def interaction_answers(text: str) -> list[dict]:
    """Read the documented interaction tool wrapper, retaining its raw body."""
    body = text.split("The user answered:", 1)[-1].split(". Read the answers carefully", 1)[0]
    if "The user answered:" not in text:
        return []
    return [{"question": q, "answer": a} for q, a in
            re.findall(r'"(.*?)"="(.*?)"(?=\s*[,.;]|\s*$)', body, re.S)]


def parse_arguments(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {"raw": value}
    return copy.deepcopy(value) if value is not None else {}


def parse_assignment(text: str) -> list[dict]:
    """Only explicit time-stamped assignment sections are user requirements."""
    sections = list(re.finditer(r"^##\s+\[([^\]]+)\]\s*需求\s*$", text, re.M))
    result = []
    for i, match in enumerate(sections):
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        value = text[match.end():end].strip()
        if value:
            result.append({"text": value, "time": match.group(1),
                           "source": "assignment", "line": text[:match.start()].count("\n") + 1})
    return result


def _log_time(line: str, path: str) -> str | None:
    stamp = re.search(r":(\d{2})(\d{2})/(\d{2})(\d{2})(\d{2})\.(\d+):", line)
    year = re.search(r"saman_(\d{4})\.", Path(path).name)
    if not stamp or not year:
        return None
    mm, dd, hh, mi, ss, fraction = stamp.groups()
    try:
        # Chromium's local clock has no timezone field. Use this machine's local
        # timezone and retain that uncertainty in the parser's source descriptor.
        parsed = dt.datetime(int(year[1]), int(mm), int(dd), int(hh), int(mi), int(ss),
                             int((fraction + "000000")[:6])).astimezone()
        return parsed.isoformat()
    except ValueError:
        return None


def _native_identity(data: dict) -> dict:
    nested = data.get("input", {})
    raw = nested.get("sandboxLogId") if isinstance(nested, dict) else None
    try:
        nested = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        nested = {}
    if not isinstance(nested, dict):
        nested = {}
    return {
        "sessionId": data.get("conversation_id") or nested.get("conversation_id"),
        "sandboxId": data.get("sandbox_id") or data.get("sand_box_id") or nested.get("sandbox_id"),
        "callId": data.get("tcid") or data.get("tool_call_id") or nested.get("tcid"),
        "agentId": data.get("agent_id") or nested.get("agent_id"),
    }


def parse_native_line(line: str, path: str, number: int) -> dict | None:
    """Decode both proper JSON and Chromium's literal-quoted JSON console lines."""
    marker = "[agent-task] "
    obj = None
    if marker in line:
        body = line.split(marker, 1)[1].strip()
        try:
            first, _ = _DECODER.raw_decode(body[1:] if body.startswith('"{') else body)
            obj = json.loads(first) if isinstance(first, str) else first
        except (ValueError, TypeError):
            return None
        if not isinstance(obj, dict) or not isinstance(obj.get("data", {}), dict):
            return None
        data = obj.get("data", {})
        event = obj.get("event", "unknown")
        identity = _native_identity(data)
    else:
        # Native file/tool lines expose an explicit conversation + call ID. This
        # is an identity join, never proximity-based association.
        match = re.search(r'(?:\[lid=|sandbox_log_id=)(\{.*?\})', line)
        if not match:
            return None
        try:
            identity = _native_identity(json.loads(match[1]))
        except ValueError:
            return None
        if not identity.get("callId"):
            return None
        tail = re.search(r'file_path=\{[^\n]*?tail="([^"\n]+)"\}', line)
        if tail:
            data = {"name": "Read", "fileName": tail[1], "filePathCoverage": "basename_only",
                    "status": "started", "phase": "native_file_read"}
            event = "toolcall"
        else:
            # Preserve useful execution/permission facts, without advertising
            # them as complete sandbox/process evidence.
            facts = {}
            for key in ("execution_mode", "permission_mode", "requested_run_in_sandbox", "result"):
                value = re.search(r"\b" + key + r"=([^,\s]+)", line)
                if value:
                    facts[key] = value[1]
            if not facts:
                return None
            data = {"nativeFacts": facts, "phase": "native_execution"}
            event = "sandbox"
    return {**identity, "event": event, "data": data, "time": _log_time(line, path),
            "timeSource": "native_log_local_clock", "evidence": evidence("native_log", path, number, line)}


def media_artifacts(value: Any) -> list[dict]:
    """Extract returned references; reported metadata is never verified metadata."""
    text = content_text(value)
    report = _MEDIA_REPORT.search(text)
    reported = {}
    kind = "media"
    if report:
        kind = report[1].lower()
        reported = {"width": int(report[3]), "height": int(report[4]), "format": report[5].lower(),
                    "source": "tool_return"}
        if report[2]:
            reported["duration"] = float(report[2])
    result = []
    seen = set()
    for match in _URL.finditer(text):
        url = match[0].rstrip(".,;，。")
        if url in seen:
            continue
        seen.add(url)
        inferred = kind
        lower = url.lower().split("?", 1)[0]
        if lower.endswith((".mp4", ".mov", ".webm")):
            inferred = "video"
        elif lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
            inferred = "image"
        # A returned non-media URL still belongs in the raw result. Only expose
        # it as an artifact if this response describes media or a media suffix.
        if inferred != "media" or any(x in text.lower() for x in ("generated", "video", "image", "视频", "图片")):
            result.append({"kind": inferred, "url": url, "path": None,
                           "reported": copy.deepcopy(reported), "verified": {}})
    if isinstance(value, dict):
        for key in ("localPath", "local_path", "file_path", "path"):
            path = value.get(key)
            if isinstance(path, str) and Path(path).suffix.lower() in (".mp4", ".mov", ".png", ".jpg", ".webm"):
                result.append({"kind": "video" if Path(path).suffix.lower() in (".mp4", ".mov", ".webm") else "image",
                               "url": None, "path": path, "reported": {}, "verified": {}})
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                result.extend(media_artifacts(nested))
    elif isinstance(value, list):
        for nested in value:
            result.extend(media_artifacts(nested))
    unique = {}
    for item in result:
        unique.setdefault((item.get("url"), item.get("path")), item)
    return list(unique.values())


def tool_kind(name: str) -> str:
    lower = name.lower()
    if name == "interaction.ask":
        return "confirmation"
    if lower == "present_files":
        return "delivery"
    if lower == "media_to_video":
        return "media_to_video"
    if lower == "filebatchupload":
        return "material_upload"
    if "video" in lower and any(x in lower for x in ("edit", "modify", "remix")):
        return "video_edit"
    if "video" in lower and any(x in lower for x in ("image", "reference", "frame")):
        return "image_to_video"
    if "video" in lower:
        return "video_generation"
    if "image" in lower and any(x in lower for x in ("edit", "modify")):
        return "image_edit"
    if "image" in lower:
        return "image_generation"
    if lower in ("read", "read_file", "filereadtool"):
        return "file_read"
    return "tool"


def tool_label(name: str) -> str:
    return {"confirmation": "确认需求", "video_edit": "修改视频", "image_to_video": "图片参与视频生成",
            "video_generation": "生成视频", "image_edit": "修改图片", "image_generation": "生成图片",
            "file_read": "读取制作说明", "media_to_video": "参考素材生成视频", "delivery": "交付文件",
            "material_upload": "上传参考素材",
            "tool": "调用工具"}[tool_kind(name)]


def _tool_summary(step: dict) -> str:
    name, args = step["toolName"], step.get("arguments", {})
    if name == "interaction.ask" and isinstance(args, dict):
        questions = args.get("questions", [])
        return "；".join(str(q.get("display_message", q.get("header", "")))
                         for q in questions if isinstance(q, dict))
    if isinstance(args, dict):
        parts = []
        for key, label in (("duration", "时长"), ("ratio", "比例"), ("model_version", "模型"),
                           ("fileName", "文件")):
            if args.get(key) is not None:
                parts.append(f"{label}：{args[key]}" + (" 秒" if key == "duration" else ""))
        if parts:
            return " · ".join(parts)
    return f"{name} 的参数与返回已关联" if step.get("result") is not None else f"{name} 已发起，等待返回"


def _leaf_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        result = {value}
        result.update(x[0].rstrip(".,;，。") for x in _URL.finditer(value))
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return result
        if parsed != value:
            result.update(_leaf_strings(parsed))
        return result
    if isinstance(value, dict):
        return set().union(*(_leaf_strings(x) for x in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_leaf_strings(x) for x in value)) if value else set()
    return set()


def _media_output_ids(step: dict) -> list[str]:
    args = step.get("arguments", {})
    result = step.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = {}
    ids = []
    for value in (args, result):
        if isinstance(value, dict):
            for key in ("output_video_id", "output_image_id", "video_id", "image_id"):
                identity = value.get(key)
                if isinstance(identity, str) and identity:
                    ids.append(identity)
    return list(dict.fromkeys(ids))


def _is_uuid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value))


def _time_key(value: Any) -> float:
    if not isinstance(value, str):
        return float("inf")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else parsed.astimezone().timestamp()
    except ValueError:
        return float("inf")


def _reference(kind: str, value: str, source: str) -> dict:
    item = {"kind": kind, "id": None, "url": None, "path": None, "source": source}
    if re.match(r"^https?://", value):
        item["url"] = value
    elif value.startswith(("/", "./", "../")) or re.match(r"^[A-Za-z]:[\\/]", value):
        item["path"] = value
    else:
        item["id"] = value
    return item


def _media_references(steps: list[dict]) -> list[dict]:
    """Associate media strictly through native IDs/URLs/paths, never the clock."""
    outputs = {}
    for step in steps:
        inputs, output_refs = [], []
        args = step.get("arguments", {})
        if not isinstance(args, dict):
            args = {}
        for key, kind in (("input_image_ids", "image"), ("input_video_ids", "video"), ("input_audio_ids", "audio"),
                          ("image_reference_url_list", "image")):
            values = args.get(key, [])
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                inputs.extend(_reference(kind, value, "arguments." + key) for value in values
                              if isinstance(value, str) and value)
        if step.get("category") == "delivery":
            for key in ("files", "file_paths", "paths", "file_urls", "video_ids", "image_ids"):
                for value in sorted(_leaf_strings(args.get(key))):
                    inputs.append(_reference("file", value, "arguments." + key))
        for artifact in step.get("artifacts", []):
            item = {"kind": artifact["kind"], "id": None, "url": artifact.get("url"), "path": artifact.get("path"),
                    "source": "tool_return", "availability": "returned"}
            ids = artifact.get("nativeIds", [])
            if len(ids) == 1:
                item["id"] = ids[0]
            output_refs.append(item)
        for identity in _media_output_ids(step):
            if not any(x.get("id") == identity for x in output_refs):
                kind = "image" if step.get("category", "").startswith("image_") and step.get("category") != "image_to_video" else "video"
                output_refs.append({**_reference(kind, identity, "native_output_id"), "availability": "declared"})
        step["inputReferences"], step["outputReferences"] = inputs, output_refs
        if step.get("status") == "completed" and step.get("category") != "delivery":
            for output in output_refs:
                for key in ("id", "url", "path"):
                    if output.get(key):
                        outputs.setdefault((key, output[key]), set()).add(step["id"])
    relations = []
    for step in steps:
        for item in step["inputReferences"]:
            candidates = set()
            matched_keys = []
            for key in ("id", "url", "path"):
                if item.get(key) and (key, item[key]) in outputs:
                    candidates.update(outputs[key, item[key]])
                    matched_keys.append(key)
            candidates.discard(step["id"])
            item["matched"] = len(candidates) == 1
            item["fromStepId"] = next(iter(candidates)) if item["matched"] else None
            item["matchStatus"] = "matched" if item["matched"] else ("ambiguous" if candidates else "unresolved")
            if item["matched"]:
                key = matched_keys[0]
                relations.append({"fromStepId": item["fromStepId"], "toStepId": step["id"], "kind": item["kind"],
                                  "reference": {k: item.get(k) for k in ("id", "url", "path")},
                                  "association": {"id": "exact_native_id", "url": "exact_url", "path": "exact_local_path"}[key]})
    return relations


def build_session(session_id: str, records: list[dict], assignments: list[dict],
                  native_events: list[dict], verifications: dict | None = None) -> dict:
    """Build a replay from explicit IDs and source order, preserving uncertainty."""
    verifications = verifications or {}
    steps, calls, history = [], {}, []
    assigned = [x for x in assignments if x.get("text")]
    for row in records:
        value = row["value"]
        role = value.get("role")
        text = content_text(value.get("content"))
        ev = row["evidence"]
        time = value.get("timestamp") or value.get("time") or row.get("time")
        base = {"time": time, "status": "recorded", "evidence": [ev], "artifacts": []}
        if role == "user":
            assignment = next((x for x in assigned if x["text"].strip() == text.strip()), None)
            if assignment:
                time = assignment.get("time") or time
                base["time"] = time
                base["evidence"].extend(assignment.get("evidence", []))
            history.append({"text": text, "source": "trajectory_user", "time": time,
                            "evidence": copy.deepcopy(base["evidence"])})
            steps.append({**base, "id": row["id"], "kind": "user_request", "label": "提出需求",
                          "summary": text, "content": text})
        elif role == "assistant":
            if text:
                steps.append({**base, "id": row["id"] + ":message", "kind": "assistant", "label": "豆包说明",
                              "summary": text, "content": text,
                              "associatedCallIds": [x.get("id") for x in value.get("tool_calls", []) if isinstance(x, dict)]})
            for index, call in enumerate(value.get("tool_calls", []) or []):
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or call
                name = str(function.get("name") or "unknown_tool")
                cid = str(call.get("id") or f"unidentified:{row['id']}:{index}")
                args = parse_arguments(function.get("arguments"))
                step = {**copy.deepcopy(base), "id": f"{row['agentId']}:call:{cid}", "kind": "tool_call",
                        "category": tool_kind(name), "label": tool_label(name), "toolName": name,
                        "callId": cid, "arguments": args, "prompt": args.get("prompt") if isinstance(args, dict) else None,
                        "result": None, "status": "pending", "resultTime": None,
                        "agentId": row["agentId"], "sourceSequence": row.get("sequence", 0)}
                calls[(row["agentId"], cid)] = step
                step["summary"] = _tool_summary(step)
                steps.append(step)
        elif role == "tool":
            cid = str(value.get("tool_call_id") or "")
            step = calls.get((row["agentId"], cid))
            if step is None:
                # Retain orphan returns, never borrow another agent's call.
                steps.append({**base, "id": row["id"], "kind": "tool_result", "label": "工具返回",
                              "summary": "收到工具返回，当前记录中未找到对应调用", "callId": cid,
                              "toolName": value.get("name"), "result": value.get("content"), "status": "unpaired"})
                continue
            step["result"] = copy.deepcopy(value.get("content"))
            step["resultTime"] = time
            step["status"] = "failed" if value.get("is_error") else "completed"
            step["evidence"].append(ev)
            step["artifacts"] = media_artifacts(value.get("content"))
            step["summary"] = _tool_summary(step)
            if step["toolName"] == "interaction.ask":
                answers = interaction_answers(text)
                readable = "；".join(x["answer"] for x in answers) or text
                feedback = {**base, "id": row["id"], "kind": "user_feedback", "label": "用户确认或补充",
                            "summary": readable, "content": readable, "rawContent": text,
                            "answers": answers, "callId": cid, "source": "interaction_tool_return"}
                steps.append(feedback)
                history.append({"text": readable, "rawContent": text, "answers": answers,
                                "source": "interaction_tool_return", "time": time, "evidence": [ev]})

    # Native logs may arrive before or after trajectory. Join only unambiguous
    # tcid identities within this session. Keep auxiliary reads even if absent
    # from the app's user-visible dialogue.
    native_calls = {}
    native_agents = {}
    explicit_sessions = {}
    for event in native_events:
        cid = event.get("callId")
        if cid and event.get("agentId"):
            native_agents.setdefault(cid, set()).add(str(event["agentId"]))
        if cid and event.get("sessionId"):
            explicit_sessions.setdefault(cid, set()).add(str(event["sessionId"]))
    for event in native_events:
        cid = event.get("callId")
        if not cid:
            continue
        candidates = [step for (_, call_id), step in calls.items() if call_id == cid]
        if len(candidates) > 1:
            candidates = []
        unique_native_agents = native_agents.get(cid, set())
        native_aid = str(event.get("agentId") or
                         (next(iter(unique_native_agents)) if len(unique_native_agents) == 1 else ""))
        if candidates:
            local_aid = str(candidates[0].get("agentId") or "")
            alias = local_aid.startswith("m_") and _is_uuid(native_aid) and \
                explicit_sessions.get(cid) == {str(session_id)}
            if len(native_agents.get(cid, set())) > 1 or (native_aid and native_aid != local_aid and not alias):
                candidates = []
        data = event.get("data", {})
        name = data.get("name")
        if not candidates and not name:
            api = data.get("api_name", "")
            if "fileReadTool" in api:
                name = "Read"
        if candidates:
            step = candidates[0]
            if native_aid:
                step["nativeAgentId"] = native_aid
                step["nativeAssociation"] = "explicit_session_and_call_id"
        else:
            native_key = (cid, native_aid if len(native_agents.get(cid, set())) > 1 else "")
            if native_key not in native_calls:
                native_calls[native_key] = {"id": f"native:call:{cid}:{native_key[1]}", "kind": "tool_call", "label": tool_label(name or "unknown_tool"),
                    "category": tool_kind(name or "unknown_tool"), "toolName": name or "unknown_tool", "callId": cid,
                    "arguments": {}, "result": None, "prompt": None, "artifacts": [], "evidence": [],
                    "time": event.get("time"), "resultTime": None, "status": "observed",
                    "nativeOnly": True, "nativeFacts": [],
                    "nativeAgentId": native_aid or None, "nativeAssociation": "unpaired_native_call"}
            step = native_calls[native_key]
            if name and step["toolName"] == "unknown_tool":
                step.update(toolName=name, label=tool_label(name), category=tool_kind(name))
        step.setdefault("nativeEvents", []).append({"event": event["event"], "data": data,
                                                    "time": event.get("time")})
        step["evidence"].append(event["evidence"])
        status = data.get("status")
        if not step.get("time") and event.get("time"):
            step["time"] = event["time"]
        if data.get("fileName"):
            step["arguments"]["fileName"] = data["fileName"]
            step["arguments"]["filePathCoverage"] = "basename_only"
        if data.get("nativeFacts"):
            step.setdefault("nativeFacts", []).append(data["nativeFacts"])
        if status in ("failed", "error"):
            step["status"] = "failed"
            step["resultTime"] = event.get("time")
        elif status == "success":
            if step["status"] != "failed":
                step["status"] = "completed"
            step["resultTime"] = step.get("resultTime") or event.get("time")
            if step.get("nativeOnly"):
                step["result"] = {"status": "success", "contentCoverage": "not_recorded_in_native_log"}
        step["summary"] = _tool_summary(step)
    # Timestamped native auxiliary operations get inserted ahead of the next
    # timestamped dialogue event, while un-timestamped dialogue stays in source
    # order. No synthetic execution time is assigned.
    for step in sorted(native_calls.values(), key=lambda x: _time_key(x.get("time"))):
        position = next((i for i, current in enumerate(steps)
                         if current.get("time") and step.get("time") and _time_key(current["time"]) > _time_key(step["time"])), len(steps))
        if position and position < len(steps) and steps[position - 1].get("kind") == "assistant" and \
                steps[position].get("callId") in steps[position - 1].get("associatedCallIds", []):
            position -= 1
        steps.insert(position, step)
    for step in steps:
        output_ids = _media_output_ids(step)
        if len(step.get("artifacts", [])) == 1:
            step["artifacts"][0]["nativeIds"] = output_ids
        for artifact in step.get("artifacts", []):
            item = verifications.get(artifact.get("url")) or verifications.get(artifact.get("path"))
            if item:
                artifact["path"] = item.get("path")
                artifact["verified"] = copy.deepcopy(item.get("metadata", {}))
    media_relations = _media_references(steps)
    for step in steps:
        for reference in step.get("inputReferences", []):
            item = verifications.get(reference.get("url")) or verifications.get(reference.get("path"))
            if item:
                reference["path"] = item.get("path")
                reference["verified"] = copy.deepcopy(item.get("metadata", {}))
                reference["pathAssociation"] = "explicit_imported_local_verification"
    original = next((h["text"] for h in history if h["source"] == "trajectory_user"), None)
    if original is None and assigned:
        original = assigned[0]["text"]
        history[:0] = assigned
    times = [x.get("time") for x in steps + assigned if x.get("time")]
    media_calls = [x for x in steps if x.get("kind") == "tool_call" and
                   x.get("category") in ("video_generation", "image_to_video", "media_to_video", "video_edit", "image_generation", "image_edit")]
    if any(x.get("status") == "pending" for x in media_calls):
        status = "running"
    elif media_calls and all(x.get("status") == "failed" for x in media_calls):
        status = "failed"
    elif any(x.get("artifacts") for x in media_calls):
        # A generated URL is a generation result; it does not prove that a
        # delivery tool presented that file to the user.
        delivery = [x for x in steps if x.get("category") == "delivery" and x.get("status") == "completed"]
        produced = [a for x in media_calls for a in x.get("artifacts", [])]
        media_step_ids = {step["id"] for step in media_calls}
        final_artifacts = []
        for artifact in produced:
            references = {str(x) for x in (artifact.get("url"), artifact.get("path")) if x}
            references.update(artifact.get("nativeIds", []))
            consumed = any(references & {str(value) for value in relation["reference"].values() if value}
                           for relation in media_relations if relation["toStepId"] in media_step_ids)
            artifact["role"] = "intermediate" if consumed else "final"
            if not consumed:
                final_artifacts.append(artifact)
            matched = [step for step in delivery if references &
                       (_leaf_strings(step.get("arguments")) | _leaf_strings(step.get("result")))]
            artifact["delivery"] = {"status": "delivered" if matched else "not_observed",
                                    "callIds": [step["callId"] for step in matched],
                                    "association": "exact_url_path_or_native_media_id" if matched else None}
        all_delivered = bool(final_artifacts) and all(a["delivery"]["status"] == "delivered" for a in final_artifacts)
        prefix = "delivered" if all_delivered else "generated"
        complete = bool(produced) and all(x.get("artifacts") for x in media_calls if x.get("status") != "failed") and \
            all(a.get("verified", {}).get("status") == "verified" for a in produced)
        status = prefix + ("_verified" if complete else "_unverified")
    else:
        status = "observed"
    meaningful = [h for h in history if len(h.get("text", "")) > 12]
    effective = meaningful[-1] if meaningful else (history[-1] if history else None)
    return {"id": session_id, "title": ((effective or {}).get("text") or original or "豆包本地任务")[:80], "createdAt": min(times, key=_time_key) if times else None,
            "updatedAt": max(times, key=_time_key) if times else None, "status": status, "originalRequest": original,
            "effectiveRequest": copy.deepcopy(effective), "isMediaTask": bool(media_calls),
            "mediaRelations": media_relations,
            "requestHistory": history, "steps": steps, "processes": [], "connections": [],
            "tools": sorted({s["toolName"] for s in steps if s.get("toolName")}),
            "coverage": {"dialogue": "recorded_trajectory", "toolCallAssociation": "explicit_call_id",
                         "hiddenReasoning": "not_captured", "modelInput": "not_captured",
                         "mediaProperties": "tool_report_plus_explicit_local_verification"}}


_MEDIA_CATEGORIES = {"video_generation", "image_to_video", "media_to_video", "video_edit", "image_generation", "image_edit"}


def _run_identity(session_id: str, first_record: dict | None) -> str:
    ev = (first_record or {}).get("evidence", {})
    identity = f"{ev.get('path', '')}:{ev.get('line', '')}:{(first_record or {}).get('agentId', '')}"
    return f"{session_id}:run:{hashlib.sha256(identity.encode()).hexdigest()[:12]}"


def build_runs(session_id: str, records: list[dict], assignments: list[dict], native_events: list[dict],
               verifications: dict | None = None) -> list[dict]:
    """Partition a native conversation into evidenced media-production runs.

    A new original user row opens a new run only after the preceding run's
    media calls have explicit terminal returns. Interaction tool feedback stays
    in the same run. No prompt keywords or model inference are used.
    """
    agents = {row.get("agentId") for row in records}
    user_agents = {row.get("agentId") for row in records if row.get("value", {}).get("role") == "user"}
    # Multiple independent trajectory files do not carry a total source order.
    # Keep a source-level projection rather than manufacture task boundaries.
    if len(agents) > 1 or len(user_agents) > 1:
        fallback = build_session(session_id, records, assignments, native_events, verifications)
        fallback.update(sourceSessionId=session_id, runId=session_id, sourceRunIndex=0,
                        contextLinkage={"sourceSessionId": session_id, "previousRunId": None, "parentRunIds": []})
        fallback["coverage"]["runSegmentation"] = "unresolved_multiple_agent_source_order"
        return [fallback]
    root_agent = next(iter(user_agents), None)
    buckets, call_owners = [], {}
    current = None
    for row in records:
        value = row["value"]
        role = value.get("role")
        is_user = role == "user" and row.get("agentId") == root_agent
        previous_terminal = current and current["mediaState"] and all(current["mediaState"].values())
        if current is None or (is_user and previous_terminal):
            current = {"records": [], "mediaState": {}, "native": [], "start": None,
                       "startEvidence": [], "firstRecord": row if is_user else None}
            buckets.append(current)
        target = current
        if role == "tool":
            target = call_owners.get((row.get("agentId"), str(value.get("tool_call_id") or "")), current)
        target["records"].append(row)
        if is_user and current["firstRecord"] is None:
            current["firstRecord"] = row
        if is_user and current["start"] is None:
            text = content_text(value.get("content"))
            assignment = next((x for x in assignments if x.get("text", "").strip() == text.strip()), None)
            current["start"] = value.get("timestamp") or value.get("time") or (assignment or {}).get("time")
            current["startEvidence"] = [row["evidence"]] + (assignment or {}).get("evidence", [])
        if role == "assistant":
            for call in value.get("tool_calls", []) or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or call
                cid = str(call.get("id") or "")
                if not cid:
                    continue
                key = (row.get("agentId"), cid)
                call_owners[key] = current
                if tool_kind(str(function.get("name") or "")) in _MEDIA_CATEGORIES:
                    current["mediaState"][key] = False
        elif role == "tool":
            key = (row.get("agentId"), str(value.get("tool_call_id") or ""))
            if key in target["mediaState"]:
                target["mediaState"][key] = True
    if not buckets:
        return []
    unresolved_native = []
    for event in native_events:
        cid = event.get("callId")
        exact = [bucket for bucket in buckets if any(key[1] == cid for key in call_owners if call_owners[key] is bucket)] if cid else []
        association = None
        target = None
        if len(exact) == 1:
            target, association = exact[0], "explicit_trajectory_call_id"
        elif len(buckets) == 1 and event.get("sessionId") == session_id:
            target, association = buckets[0], "single_run_explicit_source_session"
        elif event.get("sessionId") == session_id and event.get("time") and all(bucket["start"] for bucket in buckets):
            # Only actual user timestamps (including an exact assignment match)
            # and a native session/sandbox identity can bound auxiliary calls.
            stamp = _time_key(event["time"])
            starts = [_time_key(bucket["start"]) for bucket in buckets]
            if starts == sorted(starts):
                intervals = [i for i, start in enumerate(starts) if start <= stamp and
                             (i + 1 == len(starts) or stamp < starts[i + 1])]
                if len(intervals) == 1:
                    target, association = buckets[intervals[0]], "explicit_assignment_interval_and_session_identity"
        if target is not None:
            target["native"].append({**event, "runAssociation": association,
                                     "runBoundaryEvidence": target["startEvidence"]})
        else:
            unresolved_native.append(event)
    runs = []
    for index, bucket in enumerate(buckets):
        values = {content_text(row["value"].get("content")).strip() for row in bucket["records"]
                  if row["value"].get("role") == "user"}
        assigned = [x for x in assignments if x.get("text", "").strip() in values]
        run_id = _run_identity(session_id, bucket["firstRecord"])
        run = build_session(session_id, bucket["records"], assigned, bucket["native"], verifications)
        run.update(id=run_id, runId=run_id, sourceSessionId=session_id, sourceRunIndex=index,
                   contextLinkage={"sourceSessionId": session_id, "previousRunId": runs[-1]["id"] if runs else None,
                                   "association": "same_native_session", "parentRunIds": []},
                   sourceSessionEvidence=copy.deepcopy(unresolved_native))
        run["title"] = run["title"].strip()
        if not bucket["mediaState"]:
            confirmations = [s for s in run["steps"] if s.get("category") == "confirmation"]
            run["status"] = "waiting_confirmation" if confirmations and confirmations[-1]["status"] != "completed" else "running"
        for step in run["steps"]:
            if step.get("nativeOnly"):
                associations = {event.get("runAssociation") for event in bucket["native"] if event.get("callId") == step.get("callId")}
                step["runAssociation"] = next(iter(associations)) if len(associations) == 1 else "multiple_native_evidence"
            else:
                step["runAssociation"] = "trajectory_source_sequence"
        run["coverage"]["runSegmentation"] = "original_user_boundary_after_terminal_media_return"
        run["coverage"]["unassignedNativeEventCount"] = len(unresolved_native)
        runs.append(run)
    # Cross-run media dependencies use exact retained references. A continuation
    # conversation alone never claims that the next video used the previous one.
    producers = {}
    for run in runs:
        for step in run["steps"]:
            if step.get("category") == "delivery" or step.get("status") != "completed":
                continue
            for output in step.get("outputReferences", []):
                for key in ("id", "url", "path"):
                    if output.get(key):
                        producers.setdefault((key, output[key]), set()).add((run["id"], step["id"]))
    for run in runs:
        for step in run["steps"]:
            for reference in step.get("inputReferences", []):
                if reference.get("matched"):
                    continue
                candidates, keys = set(), []
                for key in ("id", "url", "path"):
                    if reference.get(key):
                        matches = {item for item in producers.get((key, reference[key]), set()) if item[0] != run["id"]}
                        candidates.update(matches)
                        if matches:
                            keys.append(key)
                if len(candidates) == 1:
                    parent_run, parent_step = next(iter(candidates))
                    reference.update(matched=True, matchStatus="matched", fromStepId=parent_step, fromRunId=parent_run)
                    run["contextLinkage"]["parentRunIds"].append(parent_run)
                    run["mediaRelations"].append({"fromRunId": parent_run, "fromStepId": parent_step, "toRunId": run["id"],
                        "toStepId": step["id"], "kind": reference["kind"], "reference": {key: reference.get(key) for key in ("id", "url", "path")},
                        "association": {"id": "exact_native_id", "url": "exact_url", "path": "exact_local_path"}[keys[0]]})
                elif candidates:
                    reference.update(matched=False, matchStatus="ambiguous")
        run["contextLinkage"]["parentRunIds"] = list(dict.fromkeys(run["contextLinkage"]["parentRunIds"]))
    return runs
