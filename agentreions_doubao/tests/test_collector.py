import json
import os
import platform
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentreions_doubao.collector import Collector, LocalCollector


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "Doubao"
        self.system = self.source / "Profile 1/.doubao/agent_mode/workspace/.sessions/task/agents/main/system"
        self.system.mkdir(parents=True)
        self.trajectory = self.system / "trajectory.jsonl"
        self.trajectory.write_text("")
        self.logs = self.source / "sdk_storage/log"
        self.logs.mkdir(parents=True)
        self.collector = Collector(self.base / "state/observations.sqlite3", [self.source])

    def tearDown(self):
        self.collector.close()
        self.temp.cleanup()

    def append(self, value, newline=True):
        with self.trajectory.open("a") as stream:
            stream.write(json.dumps(value) + ("\n" if newline else ""))

    def test_complete_line_commit_and_incremental_no_duplicates(self):
        self.append({"role": "user", "content": "动画 A"}, newline=False)
        self.collector.scan_once()
        self.assertEqual(self.collector.snapshot()["sessions"], [])
        with self.trajectory.open("a") as stream:
            stream.write("\n")
        self.collector.scan_once()
        state = self.collector.snapshot()
        self.assertEqual(state["sessions"][0]["originalRequest"], "动画 A")
        revision = state["revision"]
        result = self.collector.scan_once()
        self.assertEqual(result["recordsAdded"], 0)
        if platform.system() == "Windows":
            self.assertLessEqual(result["bytesRead"], 256)
        else:
            self.assertEqual(result["bytesRead"], 0)
        self.assertEqual(result["revision"], revision)

    def test_same_size_rewrite_and_truncation_use_new_source_generation(self):
        self.append({"role": "user", "content": "A"})
        self.collector.scan_once()
        self.trajectory.write_text(json.dumps({"role": "user", "content": "B"}) + "\n")
        self.collector.scan_once()
        state = self.collector.snapshot()
        self.assertEqual(state["sessions"][0]["originalRequest"], "B")
        self.assertEqual(len(state["sessions"][0]["steps"]), 1)

    def test_same_size_rewrite_with_original_mtime_is_not_lost(self):
        self.append({"role": "user", "content": "A"})
        self.collector.scan_once()
        original = self.trajectory.stat()
        self.trajectory.write_text(json.dumps({"role": "user", "content": "B"}) + "\n", encoding="utf-8")
        os.utime(self.trajectory, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(self.trajectory.stat().st_size, original.st_size)
        result = self.collector.scan_once()
        self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], "B")
        self.assertEqual(result["recordsAdded"], 1)

    def test_windows_unchanged_metadata_requires_bounded_content_check(self):
        self.append({"role": "user", "content": "A"})
        with patch("agentreions_doubao.collector.platform.system", return_value="Windows"):
            self.collector.scan_once()
            original = self.trajectory.stat()
            self.trajectory.write_text(json.dumps({"role": "user", "content": "B"}) + "\n", encoding="utf-8")
            os.utime(self.trajectory, ns=(original.st_atime_ns, original.st_mtime_ns))
            # Force the exact cache collision even on POSIX, whose ctime
            # normally changes after overwrite: Windows cannot rely on ctime.
            same = self.trajectory.stat()
            self.collector._last_seen_stat[str(self.trajectory.resolve())] = (
                f"{same.st_dev}:{same.st_ino}", same.st_mtime_ns, same.st_size, same.st_ctime_ns)
            changed = self.collector.scan_once()
            self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], "B")
            self.assertEqual(changed["recordsAdded"], 1)
            idle = self.collector.scan_once()
            self.assertEqual(idle["recordsAdded"], 0)
            self.assertEqual(idle["bytesRead"], min(same.st_size, 128))

    def test_short_source_prefix_hash_stays_consistent_when_append_crosses_128_bytes(self):
        self.append({"role": "user", "content": "short"})
        self.collector.scan_once()
        for _ in range(4):
            self.append({"role": "assistant", "content": "longer" * 15})
            self.collector.scan_once()
        state = self.collector.snapshot()
        self.assertEqual(len(state["sessions"][0]["steps"]), 5)
        generations = self.collector._db.execute("SELECT DISTINCT generation FROM records WHERE kind='trajectory'").fetchall()
        self.assertEqual(len(generations), 1)

    def test_assignment_same_size_unchanged_mtime_windows_content_update_is_visible(self):
        self.append({"role": "user", "content": "B"})
        assignment = self.system / "assignment.md"
        assignment.write_text("## [2026-10-09T00:00:00Z] 需求\nA\n", encoding="utf-8")
        with patch("agentreions_doubao.collector.platform.system", return_value="Windows"):
            self.collector.scan_once()
            original = assignment.stat()
            assignment.write_text("## [2026-10-09T00:00:00Z] 需求\nB\n", encoding="utf-8")
            os.utime(assignment, ns=(original.st_atime_ns, original.st_mtime_ns))
            same = assignment.stat()
            self.collector._last_seen_stat[str(assignment.resolve())] = (
                f"{same.st_dev}:{same.st_ino}", same.st_mtime_ns, same.st_size, same.st_ctime_ns)
            changed = self.collector.scan_once()
            self.assertEqual(changed["recordsAdded"], 1)
            request = self.collector.snapshot()["sessions"][0]["requestHistory"][0]
            self.assertEqual(request["time"], "2026-10-09T00:00:00Z")
            idle = self.collector.scan_once()
            self.assertEqual(idle["recordsAdded"], 0)
            self.assertLessEqual(idle["bytesRead"], 512)

    def test_budget_limited_complete_lines_resume_without_stat_cache_poisoning(self):
        for index in range(5):
            self.append({"role": "assistant", "content": str(index)})
        for _ in range(15):
            with self.collector._db:
                _, count = self.collector._read_source(self.trajectory, "trajectory", 82)
            self.assertLessEqual(count, 82)
        rows = self.collector._db.execute("SELECT COUNT(*) FROM records WHERE kind='trajectory'").fetchone()[0]
        self.assertEqual(rows, 5)
        cursor = self.collector._db.execute("SELECT offset FROM cursors WHERE path=?", (str(self.trajectory.resolve()),)).fetchone()
        self.assertEqual(cursor[0], self.trajectory.stat().st_size)

    def test_tiny_budget_partial_line_completes_across_polls(self):
        self.append({"role": "user", "content": "a complete source line longer than ten bytes"})
        for _ in range(30):
            with self.collector._db:
                _, count = self.collector._read_source(self.trajectory, "trajectory", 10)
            self.assertLessEqual(count, 10)
        self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], "a complete source line longer than ten bytes")

    def test_windows_middle_rewrite_metadata_changed_reingests_new_generation(self):
        content = "p" * 200 + "A" + "q" * 200
        self.append({"role": "user", "content": content})
        with patch("agentreions_doubao.collector.platform.system", return_value="Windows"):
            self.collector.scan_once()
            raw = self.trajectory.read_bytes()
            rewritten = raw.replace(b"A", b"B")
            self.assertEqual(raw[:128], rewritten[:128])
            self.assertEqual(raw[-128:], rewritten[-128:])
            self.trajectory.write_bytes(rewritten)
            self.collector.scan_once()
            self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], content.replace("A", "B"))

    def test_windows_same_stat_middle_rewrite_full_integrity_check_survives_restart(self):
        content = "p" * 200 + "A" + "q" * 200
        self.append({"role": "user", "content": content})
        with patch("agentreions_doubao.collector.platform.system", return_value="Windows"):
            self.collector.scan_once()
            original = self.trajectory.stat()
            self.trajectory.write_bytes(self.trajectory.read_bytes().replace(b"A", b"B"))
            os.utime(self.trajectory, ns=(original.st_atime_ns, original.st_mtime_ns))
            same = self.trajectory.stat()
            signature = self.collector._signature(same)
            self.collector._last_seen_stat[str(self.trajectory.resolve())] = signature
            self.collector._trusted_signature[str(self.trajectory.resolve())] = signature
            self.collector.scan_once()
            self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], content.replace("A", "B"))
            original = self.trajectory.stat()
            self.trajectory.write_bytes(self.trajectory.read_bytes().replace(b"B", b"C"))
            os.utime(self.trajectory, ns=(original.st_atime_ns, original.st_mtime_ns))
            self.collector.close()
            self.collector = Collector(self.base / "state/observations.sqlite3", [self.source])
            self.collector.scan_once()
            self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], content.replace("A", "C"))

    def test_large_periodic_integrity_sweep_continues_with_small_budget(self):
        content = "p" * 40000 + "A" + "q" * 40000
        self.append({"role": "user", "content": content})
        self.collector.scan_once()
        original = self.trajectory.stat()
        self.trajectory.write_bytes(self.trajectory.read_bytes().replace(b"A", b"B"))
        os.utime(self.trajectory, ns=(original.st_atime_ns, original.st_mtime_ns))
        same = self.trajectory.stat()
        key = str(self.trajectory.resolve())
        signature = self.collector._signature(same)
        self.collector._last_seen_stat[key] = signature
        self.collector._trusted_signature[key] = signature
        self.collector._next_integrity_at[key] = 0
        with patch("agentreions_doubao.collector.platform.system", return_value="Windows"):
            for _ in range(50):
                with self.collector._db:
                    _, count = self.collector._read_source(self.trajectory, "trajectory", 4096)
                self.assertLessEqual(count, 4096)
            self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], content.replace("A", "B"))

    def test_assignment_middle_rewrite_is_fully_hashed_under_budget(self):
        self.append({"role": "user", "content": "p" * 200 + "B" + "q" * 200})
        assignment = self.system / "assignment.md"
        original_text = "## [2026-10-09T00:00:00Z] 需求\n" + "p" * 200 + "A" + "q" * 200 + "\n"
        assignment.write_text(original_text, encoding="utf-8")
        with patch("agentreions_doubao.collector.platform.system", return_value="Windows"):
            self.collector.scan_once()
            original = assignment.stat()
            assignment.write_text(original_text.replace("A", "B"), encoding="utf-8")
            os.utime(assignment, ns=(original.st_atime_ns, original.st_mtime_ns))
            same = assignment.stat()
            self.collector._last_seen_stat[str(assignment.resolve())] = self.collector._signature(same)
            for _ in range(20):
                with self.collector._db:
                    _, count = self.collector._assignment(assignment, 31)
                self.assertLessEqual(count, 31)
            request = self.collector.snapshot()["sessions"][0]["requestHistory"][0]
            self.assertEqual(request["time"], "2026-10-09T00:00:00Z")

    def test_poll_total_budget_includes_assignment_and_integrity_reads(self):
        self.append({"role": "user", "content": "p" * 400})
        (self.system / "assignment.md").write_text("## [2026-10-09T00:00:00Z] 需求\n" + "p" * 400 + "\n", encoding="utf-8")
        with patch("agentreions_doubao.collector.MAX_READ_BYTES", 82), patch("agentreions_doubao.collector.platform.system", return_value="Windows"):
            for _ in range(60):
                result = self.collector.scan_once()
                self.assertLessEqual(result["bytesRead"], 82)
        self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], "p" * 400)

    def test_continuous_appends_keep_tail_live_while_prefix_validation_resumes(self):
        def line(index):
            raw = json.dumps({"role": "assistant", "content": str(index)}).encode("utf-8")
            return raw + b" " * (127-len(raw)) + b"\n"

        # The committed prefix is an exact multiple of the small poll budget;
        # previously a sweep finishing at this boundary restarted every append.
        self.trajectory.write_bytes(b"".join(line(i) for i in range(32)))
        self.collector.scan_once()
        self.collector.close()
        self.collector = Collector(self.base / "state/observations.sqlite3", [self.source])
        key = str(self.trajectory.resolve())
        with patch("agentreions_doubao.collector.platform.system", return_value="Windows"), \
             patch("agentreions_doubao.collector.SMALL_SOURCE_BYTES", 32):
            for index in range(40):
                with self.trajectory.open("ab") as stream:
                    stream.write(line(index+32))
                with self.collector._db:
                    added, count = self.collector._read_source(self.trajectory, "trajectory", 256)
                self.assertEqual(added, 1)
                self.assertLessEqual(count, 256)
                cursor = self.collector._db.execute("SELECT offset,generation FROM cursors WHERE path=?", (key,)).fetchone()
                self.assertEqual(cursor["offset"], self.trajectory.stat().st_size)
                self.assertEqual(cursor["generation"], 0)
                if index < 31:
                    state = self.collector._integrity_states[key]
                    self.assertEqual(state["offset"], 4096)
                    self.assertNotIn("ranges", state)
                if index == 31:
                    self.assertNotIn(key, self.collector._integrity_states)
            # Appending after the completed sweep reads only the new tail.
            self.assertEqual(count, 128)
        self.assertEqual(self.collector._db.execute("SELECT COUNT(*) FROM records").fetchone()[0], 72)

    def test_partial_line_buffer_cap_does_not_lose_evicted_source_data(self):
        paths = []
        for index in range(4):
            system = self.source / f"Profile 1/.doubao/agent_mode/workspace/.sessions/task{index}/agents/main/system"
            system.mkdir(parents=True)
            path = system / "trajectory.jsonl"
            path.write_text(json.dumps({"role": "user", "content": "x" * 80}) + "\n", encoding="utf-8")
            paths.append(path)
        with patch("agentreions_doubao.collector.MAX_PENDING_BYTES", 150):
            for path in paths:
                with self.collector._db:
                    self.collector._read_source(path, "trajectory", 60)
                self.assertLessEqual(sum(len(item["raw"]) for item in self.collector._pending_sources.values()), 150)
            for path in paths:
                for _ in range(4):
                    with self.collector._db:
                        _, count = self.collector._read_source(path, "trajectory", 50)
                    self.assertLessEqual(count, 50)
                    self.assertLessEqual(sum(len(item["raw"]) for item in self.collector._pending_sources.values()), 150)
        self.assertEqual(self.collector._db.execute("SELECT COUNT(*) FROM records").fetchone()[0], 4)

    def test_rotation_to_new_inode_not_confused_with_append(self):
        self.append({"role": "user", "content": "before"})
        self.collector.scan_once()
        self.trajectory.rename(self.system / "old.jsonl")
        self.append({"role": "user", "content": "after"})
        self.collector.scan_once()
        self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], "after")

    def test_state_restart_persists_cursor(self):
        self.append({"role": "user", "content": "restart"})
        self.collector.scan_once()
        self.collector.close()
        self.collector = Collector(self.base / "state/observations.sqlite3", [self.source])
        self.assertEqual(self.collector.scan_once()["recordsAdded"], 0)
        self.assertEqual(self.collector.snapshot()["sessions"][0]["originalRequest"], "restart")

    def test_http_evidence_incremental_exact_call_and_missing_body(self):
        self.append({'role':'user','content':'HTTP association test'})
        self.append({'role':'assistant','tool_calls':[{'id':'video-call', 'function':{
            'name':'image_to_video','arguments':{'duration':'5','prompt':'This is tool input.'}}}]})
        log = self.logs/'saman_netlog_2026.1009.0.log'
        entries = [
            {'url':'https://example.test/generate','method':'POST','call_id':'video-call',
             'response_code':200,'request_body':{'duration':5}},
            {'url':'https://example.test/session','session_id':'task','response_code':200},
            {'url':'https://example.test/unrelated','response_code':200}]
        log.write_text('\n'.join(json.dumps(x) for x in entries)+'\n', encoding='utf-8')
        self.collector.scan_once()
        snapshot = self.collector.snapshot()
        records = snapshot['sessions'][0]['httpEvidence']['records']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['association']['callId'], 'video-call')
        self.assertEqual(records[0]['requestBody']['value'], {'duration':5})
        self.assertEqual(records[0]['responseBody']['status'], 'not_recorded')
        self.assertFalse(snapshot['sessions'][0]['httpEvidence']['coverage']['toolArgumentsAreHttpBody'])
        self.assertEqual(snapshot['coverage']['http']['parsedHttpRecords'], 3)
        idle = self.collector.scan_once()
        self.assertEqual(idle['recordsAdded'], 0)
        self.assertEqual(idle['revision'], snapshot['revision'])
        if platform.system() == 'Windows':
            # Windows rechecks these small files to catch same-metadata
            # rewrites; reads are counted without reinserting any record.
            self.assertEqual(idle['bytesRead'], sum(path.stat().st_size for path, _ in self.collector._source_paths))
        else:
            self.assertEqual(idle['bytesRead'], 0)

    def test_snapshot_cache_is_isolated_from_callers_and_updates_on_append(self):
        self.append({"role": "user", "content": "original"})
        self.collector.scan_once()
        state = self.collector.snapshot()
        state["sessions"][0]["title"] = "modified by UI"
        self.assertEqual(self.collector.snapshot()["sessions"][0]["title"], "original")
        self.append({"role": "assistant", "content": "new"})
        self.collector.scan_once()
        self.assertEqual(len(self.collector.snapshot()["sessions"][0]["steps"]), 2)

    def test_same_native_session_projects_two_production_runs_latest_first(self):
        self.append({"role": "user", "content": "原始文字生成需求"})
        self.append({"role": "assistant", "tool_calls": [{"id": "old", "function": {"name": "text_to_video", "arguments": {"duration": "5"}}}]})
        self.append({"role": "tool", "tool_call_id": "old", "content": "video (5s 1280x720 mp4) generated.https://example.test/old.mp4"})
        self.collector.scan_once()
        original_id = self.collector.snapshot()["sessions"][0]["id"]
        self.append({"role": "user", "content": "\n\n使用参考图做下一段"})
        self.append({"role": "assistant", "tool_calls": [{"id": "new", "function": {"name": "image_to_video", "arguments": {"image_reference_url_list": ["https://example.test/input.png"], "duration": "5", "ratio": "3:4"}}}]})
        self.collector.scan_once()
        snapshot = self.collector.snapshot()
        new, old = snapshot["sessions"]
        self.assertEqual(new["status"], "running")
        self.assertEqual(old["status"], "generated_unverified")
        self.assertEqual(old["id"], original_id)
        self.assertEqual(new["sourceSessionId"], old["sourceSessionId"])
        self.assertEqual(new["sourceRunIndex"], 1)
        self.assertEqual(new["contextLinkage"]["previousRunId"], original_id)
        self.assertFalse(any(step.get("artifacts") for step in new["steps"]))
        self.assertNotIn("text_to_video", new["tools"])
        self.assertEqual(new["steps"][-1]["inputReferences"][0]["url"], "https://example.test/input.png")
        self.assertEqual(snapshot["coverage"]["totalSourceSessions"], 1)
        self.assertEqual(snapshot["coverage"]["totalRunsInSelectedSources"], 2)

    def test_bad_complete_json_is_visible_error_without_stalling_good_line(self):
        self.trajectory.write_text('not-json\n' + json.dumps({"role": "user", "content": "good"}) + "\n")
        self.collector.scan_once()
        state = self.collector.snapshot()
        self.assertTrue(state["collector"]["errors"])
        self.assertEqual(state["sessions"][0]["originalRequest"], "good")

    def test_native_sandbox_identity_join_and_ambiguity(self):
        self.append({"role": "user", "content": "video"})
        lines = [
            {"event": "toolcall", "data": {"name": "Read", "tcid": "read", "sandbox_id": "box", "status": "success"}},
            {"event": "sandbox", "data": {"input": {"sandboxLogId": json.dumps({"conversation_id": "task", "sandbox_id": "box", "tcid": "read"})}}},
        ]
        log = self.logs / "saman_2026.1009.0.log"
        log.write_text("\n".join("[agent-task] " + json.dumps(x) for x in lines) + "\n")
        self.collector.scan_once()
        session = self.collector.snapshot()["sessions"][0]
        self.assertEqual(session["tools"], ["Read"])
        with log.open("a") as stream:
            stream.write('[agent-task] ' + json.dumps({"event": "sandbox", "data": {"conversation_id": "other", "sandbox_id": "box", "tcid": "othercall"}}) + "\n")
        self.collector.scan_once()
        session = self.collector.snapshot()["sessions"][0]
        self.assertNotIn("Read", session["tools"])

    def test_native_rewrite_projects_only_active_source_generation(self):
        self.append({"role": "user", "content": "video"})
        value = {"event": "toolcall", "data": {"name": "Read", "tcid": "call", "conversation_id": "task",
                 "sandbox_id": "box", "status": "success"}}
        log = self.logs / "saman_2026.1009.0.log"
        raw = "[agent-task] " + json.dumps(value) + "\n"
        log.write_text(raw, encoding="utf-8")
        self.collector.scan_once()
        self.assertIn("Read", self.collector.snapshot()["sessions"][0]["tools"])
        log.write_text(raw.replace('"Read"', '"Edit"'), encoding="utf-8")
        self.collector.scan_once()
        session = self.collector.snapshot()["sessions"][0]
        self.assertEqual(session["tools"], ["Edit"])
        self.assertEqual(len([step for step in session["steps"] if step.get("toolName")]), 1)

    def test_snapshot_display_is_bounded_and_shows_truncation(self):
        for i in range(25):
            self.append({"role": "assistant", "content": str(i)})
        self.collector.scan_once()
        session = self.collector.snapshot(step_limit=5)["sessions"][0]
        self.assertEqual(session["totalSteps"], 25)
        self.assertEqual(len(session["steps"]), 5)
        self.assertTrue(session["truncated"])

    def test_verify_import_only_existing_local_file(self):
        media = self.base / "clip.mp4"
        media.write_bytes(b"synthetic-test-only")
        with self.assertRaises(ValueError):
            self.collector.register_artifact_verification("https://example.test/v", self.base / "absent.mp4", {"method": "ffprobe"})
        with self.assertRaises(ValueError):
            self.collector.register_artifact_verification("https://example.test/v", media, {"duration": 5})
        before = self.collector.revision
        self.collector.register_artifact_verification("https://example.test/v", media, {"method": "ffprobe", "duration": 5})
        self.assertGreater(self.collector.revision, before)

    def test_db_never_writes_into_application_source(self):
        with self.assertRaises(ValueError):
            Collector(self.source / "database.sqlite3", [self.source])
        state = self.collector.snapshot()
        self.assertTrue(state["coverage"]["localOnly"])
        self.assertEqual(state["coverage"]["upload"], "disabled")


if __name__ == "__main__":
    unittest.main()
