import json
from pathlib import Path
import tempfile
import unittest

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
        self.assertEqual(self.collector.scan_once()['recordsAdded'], 0)
        self.assertEqual(self.collector.scan_once()['bytesRead'], 0)

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
