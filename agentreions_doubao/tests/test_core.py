import json
import unittest

from agentreions_doubao.core import (build_runs, build_session, interaction_answers, media_artifacts,
                                     parse_assignment, parse_native_line, tool_kind)


def record(number, value, agent="main"):
    return {"id": f"row:{number}", "agentId": agent, "sequence": number, "value": value,
            "evidence": {"source": "trajectory", "path": "/fixture/trajectory.jsonl", "line": number, "sha256": "fixture"}}


class CoreTests(unittest.TestCase):
    def test_assignment_only_explicit_user_sections(self):
        source = "# Assignment\nCreated: yesterday\n## [2026-10-09T00:00:00Z] 需求\n做一段动画\n\n## [2026-10-09T00:01:00Z] 需求\n改成五秒\n"
        result = parse_assignment(source)
        self.assertEqual([x["text"] for x in result], ["做一段动画", "改成五秒"])
        self.assertEqual(result[0]["line"], 3)

    def test_chromium_literal_quoted_native_json(self):
        obj = {"event": "toolcall", "data": {"name": "image_to_video", "tcid": "c", "sandbox_id": "box", "status": "received"}}
        line = '[1:2:1009/001212.311939:INFO:CONSOLE(0)] [agent-task] "' + json.dumps(obj) + '", source: x'
        event = parse_native_line(line, "/log/saman_2026.1009.0.log", 10)
        self.assertEqual(event["callId"], "c")
        self.assertEqual(event["sandboxId"], "box")
        self.assertTrue(event["time"].startswith("2026-10-09T00:12:12"))
        self.assertEqual(event["evidence"]["line"], 10)

    def test_native_identity_nested_string_and_malformed_scalar(self):
        obj = {"event": "sandbox", "data": {"input": {"sandboxLogId": json.dumps({"conversation_id": "task", "tcid": "a"})}}}
        self.assertEqual(parse_native_line("[agent-task] " + json.dumps(obj), "log", 1)["sessionId"], "task")
        obj["data"]["input"]["sandboxLogId"] = "42"
        self.assertIsNone(parse_native_line("[agent-task] " + json.dumps(obj), "log", 1)["sessionId"])
        self.assertIsNone(parse_native_line('[agent-task] "bad', "log", 1))

    def test_native_filename_is_only_basename(self):
        line = '[1:2:1009/000000.000000:INFO:x] [lid={"conversation_id":"task","tcid":"read"}] file_path={len=120, hash=x, tail="SKILL.md"}'
        event = parse_native_line(line, "/log/saman_2026.1009.0.log", 5)
        self.assertEqual(event["data"]["fileName"], "SKILL.md")
        self.assertEqual(event["data"]["filePathCoverage"], "basename_only")
        self.assertNotIn("path", event["data"])

    def test_tool_return_matches_explicit_agent_and_call_id(self):
        rows = [record(1, {"role": "user", "content": "生成测试视频"}),
                record(2, {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "text_to_video", "arguments": '{"prompt":"测试","duration":"5"}'}}]}),
                record(3, {"role": "tool", "tool_call_id": "c", "content": "video (5s 1280x720 mp4) generated.https://example.test/out"})]
        session = build_session("s", rows, [], [])
        call = session["steps"][-1]
        self.assertEqual(call["arguments"]["duration"], "5")
        self.assertEqual(call["prompt"], "测试")
        self.assertEqual(call["status"], "completed")
        self.assertEqual(len(call["evidence"]), 2)
        self.assertEqual(session["status"], "generated_unverified")
        self.assertEqual(call["artifacts"][0]["verified"], {})
        self.assertEqual(call["artifacts"][0]["reported"]["duration"], 5)

    def test_cross_agent_duplicate_call_is_not_borrowed(self):
        rows = [record(1, {"role": "assistant", "tool_calls": [{"id": "same", "function": {"name": "text_to_video", "arguments": {}}}]}),
                record(2, {"role": "tool", "tool_call_id": "same", "content": "not main's return"}, agent="child")]
        result = build_session("s", rows, [], [])
        self.assertEqual(result["steps"][0]["status"], "pending")
        self.assertEqual(result["steps"][1]["status"], "unpaired")

    def test_confirmation_feedback_is_readable_and_explicitly_sourced(self):
        raw = 'The user answered: "要做什么？"="做一个五秒 SSH 动画". Read the answers carefully — they may request changes.'
        rows = [record(1, {"role": "user", "content": "原始需求"}),
                record(2, {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "interaction.ask", "arguments": {"questions": []}}}]}),
                record(3, {"role": "tool", "tool_call_id": "a", "content": raw})]
        session = build_session("s", rows, [], [])
        self.assertEqual(session["originalRequest"], "原始需求")
        feedback = session["steps"][-1]
        self.assertEqual(feedback["summary"], "做一个五秒 SSH 动画")
        self.assertEqual(feedback["source"], "interaction_tool_return")
        self.assertEqual(feedback["rawContent"], raw)
        self.assertEqual(len(interaction_answers(raw)), 1)

    def test_new_media_tool_names_are_not_a_fixed_text_to_video_task(self):
        expected = {"image_gen": "image_generation", "image_edit": "image_edit", "image_to_video": "image_to_video",
                    "media_to_video": "media_to_video", "video_edit": "video_edit", "present_files": "delivery"}
        for name, category in expected.items():
            self.assertEqual(tool_kind(name), category)
        self.assertEqual(tool_kind("some_new_tool"), "tool")

    def test_generated_media_differs_from_delivery_and_supports_media_to_video(self):
        rows = [record(1, {"role": "assistant", "tool_calls": [{"id": "g", "function": {"name": "media_to_video", "arguments": {"input_image_ids": ["image-1"]}}}]}),
                record(2, {"role": "tool", "tool_call_id": "g", "content": "video (5s 1280x720 mp4) generated.https://example.test/clip.mp4"})]
        verified = {"https://example.test/clip.mp4": {"path": "/local/clip.mp4", "metadata": {"status": "verified", "method": "ffprobe"}}}
        self.assertEqual(build_session("s", rows, [], [], verified)["status"], "generated_verified")
        rows.extend([record(3, {"role": "assistant", "tool_calls": [{"id": "d", "function": {"name": "present_files", "arguments": {"files": ["https://example.test/clip.mp4"]}}}]}),
                     record(4, {"role": "tool", "tool_call_id": "d", "content": "presented"})])
        self.assertEqual(build_session("s", rows, [], [], verified)["status"], "delivered_verified")
        self.assertEqual(build_session("s", rows, [], [])["status"], "delivered_unverified")

    def test_empty_media_artifact_does_not_imply_verification(self):
        rows = [record(1, {"role": "assistant", "tool_calls": [{"id": "g", "function": {"name": "text_to_video", "arguments": {}}}]}),
                record(2, {"role": "tool", "tool_call_id": "g", "content": "completed without reference"})]
        self.assertEqual(build_session("s", rows, [], [], {})["status"], "observed")

    def test_unrelated_present_files_does_not_deliver_new_video(self):
        rows = [record(1, {"role": "assistant", "tool_calls": [{"id": "g", "function": {"name": "text_to_video", "arguments": {}}}]}),
                record(2, {"role": "tool", "tool_call_id": "g", "content": "video (5s 1280x720 mp4) generated.https://example.test/new.mp4"}),
                record(3, {"role": "assistant", "tool_calls": [{"id": "d", "function": {"name": "present_files", "arguments": {"files": ["https://example.test/other.png"]}}}]}),
                record(4, {"role": "tool", "tool_call_id": "d", "content": "presented"})]
        session = build_session("s", rows, [], [])
        self.assertEqual(session["status"], "generated_unverified")
        self.assertEqual(session["steps"][0]["artifacts"][0]["delivery"]["status"], "not_observed")

    def test_media_pipeline_links_exact_native_ids_and_delivers_final_version(self):
        rows = []
        for cid, name, args, result in (
            ("a", "image_gen", {"output_image_id": "image-A"}, "image (1280x720 png) generated.https://example.test/A.png"),
            ("b", "image_to_video", {"input_image_ids": ["image-A"], "output_video_id": "video-B"}, "video (5s 1280x720 mp4) generated.https://example.test/B.mp4"),
            ("c", "video_edit", {"input_video_ids": ["video-B"], "output_video_id": "video-C"}, "video (5s 1280x720 mp4) generated.https://example.test/C.mp4"),
            ("d", "present_files", {"video_ids": ["video-C"]}, "presented"),
        ):
            rows.append(record(len(rows)+1, {"role": "assistant", "tool_calls": [{"id": cid, "function": {"name": name, "arguments": args}}]}))
            rows.append(record(len(rows)+1, {"role": "tool", "tool_call_id": cid, "content": result}))
        session = build_session("s", rows, [], [])
        self.assertEqual(len(session["mediaRelations"]), 3)
        self.assertEqual([x["association"] for x in session["mediaRelations"]], ["exact_native_id"] * 3)
        self.assertEqual(session["status"], "delivered_unverified")
        self.assertEqual(session["steps"][0]["artifacts"][0]["role"], "intermediate")
        self.assertEqual(session["steps"][2]["artifacts"][0]["role"], "final")

    def test_missing_media_reference_is_visible_and_not_linked_by_time(self):
        rows = [record(1, {"role": "assistant", "tool_calls": [{"id": "e", "function": {"name": "video_edit", "arguments": {"input_video_ids": ["unknown-video"]}}}]}),
                record(2, {"role": "tool", "tool_call_id": "e", "content": "completed"})]
        session = build_session("s", rows, [], [])
        self.assertEqual(session["mediaRelations"], [])
        self.assertEqual(session["steps"][0]["inputReferences"][0]["matchStatus"], "unresolved")

    def test_native_insertion_after_last_assistant_does_not_index_past_end(self):
        event = {"callId": "read", "agentId": "native", "event": "toolcall", "time": "2026-10-09T01:00:00Z",
                 "data": {"name": "Read", "status": "success"}, "evidence": {"source": "native_log"}}
        session = build_session("s", [record(1, {"role": "assistant", "content": "说明"})], [], [event])
        self.assertEqual(len(session["steps"]), 2)

    def test_native_different_agent_cannot_fail_another_agents_call(self):
        rows = [record(1, {"role": "assistant", "tool_calls": [{"id": "same", "function": {"name": "text_to_video", "arguments": {}}}]}, agent="a")]
        event = {"callId": "same", "agentId": "b", "event": "toolcall", "time": "2026-10-09T01:00:00Z",
                 "data": {"name": "text_to_video", "status": "failed"}, "evidence": {"source": "native_log"}}
        session = build_session("s", rows, [], [event])
        self.assertEqual(next(x for x in session["steps"] if not x.get("nativeOnly"))["status"], "pending")
        self.assertEqual(next(x for x in session["steps"] if x.get("nativeOnly"))["status"], "failed")

    def test_native_namespace_alias_requires_explicit_session_and_unique_agent(self):
        rows = [record(1, {"role": "assistant", "tool_calls": [{"id": "same", "function": {"name": "text_to_video", "arguments": {}}}]}, agent="m_local")]
        event = {"sessionId": "s", "callId": "same", "agentId": "1a7fdd97-f41e-4a9a-8f1b-eefd51114177", "event": "toolcall",
                 "data": {"name": "text_to_video", "status": "success"}, "evidence": {"source": "native_log"}}
        session = build_session("s", rows, [], [event])
        self.assertEqual(session["steps"][0]["status"], "completed")
        conflicting = dict(event, agentId="2a7fdd97-f41e-4a9a-8f1b-eefd51114177")
        session = build_session("s", rows, [], [event, conflicting])
        self.assertEqual(next(x for x in session["steps"] if not x.get("nativeOnly"))["status"], "pending")

    def test_source_timestamp_order_normalizes_timezone_offsets(self):
        rows = [record(1, {"role": "user", "content": "需求", "time": "2026-10-08T16:12:08Z"})]
        event = {"callId": "read", "event": "toolcall", "time": "2026-10-09T00:11:00+08:00",
                 "data": {"name": "Read", "status": "success"}, "evidence": {"source": "native_log"}}
        session = build_session("s", rows, [], [event])
        self.assertEqual(session["createdAt"], "2026-10-09T00:11:00+08:00")
        self.assertEqual(session["updatedAt"], "2026-10-08T16:12:08Z")

    def test_second_original_user_after_media_return_has_independent_run_and_no_old_success(self):
        rows = [record(1, {"role": "user", "content": "第一条媒体需求"}),
                record(2, {"role": "assistant", "tool_calls": [{"id": "old", "function": {"name": "text_to_video", "arguments": {"duration": "5"}}}]}),
                record(3, {"role": "tool", "tool_call_id": "old", "content": "video (5s 1280x720 mp4) generated.https://example.test/old.mp4"}),
                record(4, {"role": "user", "content": "\n\n根据图片做新视频"})]
        runs = build_runs("source", rows, [], [])
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["status"], "generated_unverified")
        self.assertEqual(runs[1]["status"], "running")
        self.assertEqual(runs[1]["sourceSessionId"], "source")
        self.assertEqual(runs[1]["contextLinkage"]["previousRunId"], runs[0]["id"])
        self.assertEqual(len(runs[1]["steps"]), 1)
        self.assertFalse(any(step.get("artifacts") for step in runs[1]["steps"]))

    def test_interaction_feedback_does_not_start_new_run_or_use_keyword_list(self):
        rows = [record(1, {"role": "user", "content": "q"}),
                record(2, {"role": "assistant", "tool_calls": [{"id": "ask", "function": {"name": "interaction.ask", "arguments": {}}}]}),
                record(3, {"role": "tool", "tool_call_id": "ask", "content": 'The user answered: "confirm"="重新生成一个". Read the answers carefully.'}),
                record(4, {"role": "assistant", "tool_calls": [{"id": "gen", "function": {"name": "text_to_video", "arguments": {}}}]}),
                record(5, {"role": "user", "content": "a completely unrelated arbitrary phrase"})]
        # The first media call has not returned, so no completed production run
        # boundary can be proved, regardless of words in the next user message.
        self.assertEqual(len(build_runs("s", rows, [], [])), 1)

    def test_new_run_failed_call_does_not_inherit_prior_generated_state(self):
        rows = [record(1, {"role": "user", "content": "one"})]
        for index, cid, failure in ((2, "a", False), (5, "b", True)):
            rows.append(record(index, {"role": "assistant", "tool_calls": [{"id": cid, "function": {"name": "text_to_video", "arguments": {}}}]}))
            rows.append(record(index+1, {"role": "tool", "tool_call_id": cid, "content": "failed" if failure else "video generated.https://example.test/a.mp4", "is_error": failure}))
            if not failure:
                rows.append(record(4, {"role": "user", "content": "two"}))
        runs = build_runs("s", rows, [], [])
        self.assertEqual([run["status"] for run in runs], ["generated_unverified", "failed"])

    def test_auxiliary_native_events_require_explicit_assignment_interval_or_call_id(self):
        rows = [record(1, {"role": "user", "content": "one"}),
                record(2, {"role": "assistant", "tool_calls": [{"id": "old", "function": {"name": "text_to_video", "arguments": {}}}]}),
                record(3, {"role": "tool", "tool_call_id": "old", "content": "completed"}),
                record(4, {"role": "user", "content": "\n\nsecond"})]
        event = {"sessionId": "s", "callId": "read", "event": "toolcall", "time": "2026-10-09T01:20:02+08:00",
                 "data": {"name": "Read", "status": "success"}, "evidence": {"source": "native_log"}}
        runs = build_runs("s", rows, [], [event])
        self.assertEqual(len(runs[1]["sourceSessionEvidence"]), 1)
        self.assertNotIn("Read", runs[1]["tools"])
        assignments = [{"text": "one", "time": "2026-10-08T16:12:08Z"}, {"text": "second", "time": "2026-10-08T17:19:59Z"}]
        runs = build_runs("s", rows, assignments, [event])
        self.assertIn("Read", runs[1]["tools"])
        self.assertNotIn("Read", runs[0]["tools"])
        self.assertEqual(runs[1]["steps"][1]["runAssociation"], "explicit_assignment_interval_and_session_identity")

    def test_video_edit_can_reference_previous_run_exact_media_id(self):
        rows = [record(1, {"role": "user", "content": "make"}),
                record(2, {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "text_to_video", "arguments": {"output_video_id": "video-A"}}}]}),
                record(3, {"role": "tool", "tool_call_id": "a", "content": "video generated.https://example.test/a.mp4"}),
                record(4, {"role": "user", "content": "edit"}),
                record(5, {"role": "assistant", "tool_calls": [{"id": "b", "function": {"name": "video_edit", "arguments": {"input_video_ids": ["video-A"], "output_video_id": "video-B"}}}]}),
                record(6, {"role": "tool", "tool_call_id": "b", "content": "video generated.https://example.test/b.mp4"})]
        runs = build_runs("s", rows, [], [])
        self.assertEqual(runs[1]["contextLinkage"]["parentRunIds"], [runs[0]["id"]])
        self.assertEqual(runs[1]["steps"][1]["inputReferences"][0]["fromRunId"], runs[0]["id"])
        self.assertEqual(runs[1]["mediaRelations"][0]["association"], "exact_native_id")

    def test_input_material_local_path_requires_explicit_verification_import(self):
        rows = [record(1, {"role": "assistant", "tool_calls": [{"id": "i", "function": {"name": "image_to_video", "arguments": {"image_reference_url_list": ["https://example.test/input.png"]}}}]})]
        unverified = build_session("s", rows, [], [])["steps"][0]["inputReferences"][0]
        self.assertIsNone(unverified["path"])
        verified = {"https://example.test/input.png": {"path": "/local/input.png", "metadata": {"status": "verified", "method": "sha256_content_match", "sha256": "known", "inputSourceEvidence": [{"source": "native_file_name"}]}}}
        reference = build_session("s", rows, [], [], verified)["steps"][0]["inputReferences"][0]
        self.assertEqual(reference["path"], "/local/input.png")
        self.assertEqual(reference["verified"]["method"], "sha256_content_match")
        self.assertEqual(reference["pathAssociation"], "explicit_imported_local_verification")

    def test_url_return_does_not_imply_verified_media_properties(self):
        self.assertEqual(media_artifacts("Please consult https://example.test/docs"), [])
        artifact = media_artifacts({"video_url": "https://example.test/clip.mp4"})[0]
        self.assertEqual(artifact["reported"], {})
        self.assertEqual(artifact["verified"], {})

    def test_native_auxiliary_read_survives_without_trajectory_tool(self):
        ev = {"sessionId": "s", "callId": "read", "event": "toolcall", "time": "2026-10-09T00:00:01Z",
              "data": {"name": "Read", "fileName": "SKILL.md", "status": "success"},
              "evidence": {"source": "native_log", "path": "log", "line": 10, "sha256": "x"}}
        result = build_session("s", [], [], [ev])
        step = result["steps"][0]
        self.assertTrue(step["nativeOnly"])
        self.assertEqual(step["arguments"]["filePathCoverage"], "basename_only")
        self.assertEqual(step["result"]["contentCoverage"], "not_recorded_in_native_log")


if __name__ == "__main__":
    unittest.main()
