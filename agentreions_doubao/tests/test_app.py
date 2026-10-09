"""UI contract checks with explicitly synthetic input; never shown as live data."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit
from PySide6.QtCore import QCoreApplication, QEvent
from agentreions_doubao.app import CollectorWorker, MainWindow, feedback_pairs, path_name, font_probe, initialize_fonts


def fixture():
    return {"schemaVersion": 1, "revision": 1, "coverage": {"localOnly": True},
            "collector": {"state": "watching", "sourcePaths": [], "errors": []},
            "sessions": [{"id": "test-one", "title": "测试任务", "status": "generated_unverified",
                          "originalRequest": "先生成一秒视频", "effectiveRequest": {"text": "改为五秒测试动画"},
                          "requestHistory": [{"text": "先生成一秒视频", "source": "trajectory_user"},
                                             {"text": "改为五秒测试动画", "source": "interaction_tool_return"}],
                          "steps": [{"id": "u1", "kind": "user_request", "summary": "先生成一秒视频", "label": "提出需求"},
                                    {"id": "c1", "kind": "tool_call", "toolName": "text_to_video", "label": "生成视频",
                                     "arguments": {"duration": "5", "ratio": "16:9", "model_version": "test-model", "prompt": "测试提示词"},
                                     "result": None, "artifacts": [], "status": "pending"}]}]}


class DesktopContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(snapshot=fixture(), start_collector=False)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def labels(self):
        return "\n".join(item.text() for item in self.window.findChildren(QLabel))

    def test_requirement_revision_visible_and_not_hidden_behind_confirmation(self):
        self.assertEqual(self.window.request_text.text(), "改为五秒测试动画")
        self.assertIn("最初需求：先生成一秒视频", self.window.request_update.text())

    def test_new_session_does_not_steal_selected_task(self):
        data = fixture()
        data["revision"] = 2
        other = copy.deepcopy(data["sessions"][0])
        other["id"] = "test-two"
        other["title"] = "另一个任务"
        data["sessions"].insert(0, other)
        self.window.accept_snapshot(data)
        self.assertEqual(self.window.selected_session_id, "test-one")

    def test_completed_return_updates_same_call_without_reselecting(self):
        self.window.select_step(1)
        data = fixture()
        data["revision"] = 2
        data["sessions"][0]["steps"][1]["result"] = {"status": "failed", "error": "测试错误"}
        data["sessions"][0]["steps"][1]["status"] = "failed"
        self.window.accept_snapshot(data)
        self.assertEqual(self.window.selected_index, 1)
        self.assertIn("工具实际返回", self.labels())

    def test_generic_video_tool_parameters_and_replay_pause(self):
        self.window.select_step(1)
        self.assertIn("5 秒", self.labels())
        self.assertIn("test-model", self.labels())
        self.window.toggle_play()
        self.assertTrue(self.window.playing)
        self.window.select_step(1, manual=True)
        self.assertFalse(self.window.playing)

    def test_unchanged_poll_skips_snapshot_rebuild(self):
        class FakeCollector:
            calls = 0
            def scan_once(self):
                return {"revision": 1, "sourceCount": 1, "errors": []}
            def snapshot(self, **kwargs):
                self.calls += 1
                return fixture()
        worker = CollectorWorker()
        worker.collector = FakeCollector()
        worker.poll()
        worker.poll()
        self.assertEqual(worker.collector.calls, 1)

    def test_wrapper_preserves_exact_explicit_answer(self):
        self.assertEqual(feedback_pairs('The user answered: "问题"="是". Read carefully.'), [("问题", "是")])

    def test_full_arguments_prompt_and_return_are_direct_copyable_panels(self):
        data = fixture()
        step = data["sessions"][0]["steps"][1]
        step.update(toolName="image_to_video", category="image_to_video", callId="image-call",
                    status="completed", result="video generated: https://example.invalid/output.mp4",
                    inputReferences=[{"kind": "image", "url": "https://example.invalid/input.png", "matchStatus": "unresolved"}])
        step["arguments"]["prompt"] = "完整提示词" * 100
        step["arguments"]["ratio"] = "3:4"
        data["revision"] = 2
        self.window.accept_snapshot(data)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(self.window.detail_tabs.tabText(self.window.detail_tabs.currentIndex()), "工具详情")
        arguments = self.window.findChild(QPlainTextEdit, "toolArguments")
        result = self.window.findChild(QPlainTextEdit, "toolResult")
        prompt = self.window.findChild(QPlainTextEdit, "toolPrompt")
        self.assertIsNotNone(arguments)
        self.assertIsNotNone(result)
        self.assertEqual(prompt.toPlainText(), step["arguments"]["prompt"])
        self.assertIn('"ratio": "3:4"', arguments.toPlainText())
        self.assertEqual(result.toPlainText(), step["result"])
        self.assertIn("https://example.invalid/input.png", self.labels())
        self.assertIn("image-call", self.labels())

    def test_cdn_output_and_tool_name_never_become_http_interface(self):
        self.window.detail_tabs.setCurrentIndex(2)
        self.assertIn("本次制作尚未取得可关联的 HTTP 请求记录", self.labels())
        self.assertIsNone(self.window.findChild(QPlainTextEdit, "requestBody"))

    def test_real_http_metadata_absent_body_is_marked_unavailable(self):
        data = fixture()
        data["revision"] = 2
        data["sessions"][0]["httpEvidence"] = {"records": [{"url": "https://example.invalid/api", "method": "POST", "statusCode": 200,
            "requestBody": {"status": "not_recorded", "value": None},
            "responseBody": {"status": "opaque_protobuf", "value": None}, "source": {"path": "capture.log", "line": 3}}]}
        self.window.accept_snapshot(data)
        self.assertIn("POST  https://example.invalid/api", self.labels())
        self.assertIn("HTTP 请求体：源记录未保存正文", self.labels())
        self.assertIn("二进制 protobuf", self.labels())

    def test_runs_keep_same_source_session_but_separate_tool_records(self):
        data = fixture()
        data["revision"] = 2
        latest = copy.deepcopy(data["sessions"][0])
        latest.update(id="shared:run:new", runId="shared:run:new", sourceSessionId="shared", sourceRunIndex=1,
                      title="根据图片制作视频", originalRequest="根据图片制作视频", effectiveRequest={"text": "根据图片制作视频"})
        latest["steps"][1]["id"] = "new-call"
        latest["steps"][1]["toolName"] = "image_to_video"
        data["sessions"].insert(0, latest)
        self.window.accept_snapshot(data)
        self.window.session_list.setCurrentRow(0)
        self.assertIn("image_to_video", self.labels())
        self.assertIn("来源会话  shared  ·  同一会话第 2 次制作", self.window.run_source.text())
        self.assertEqual(self.window.tool_selector.count(), 1)
        self.assertNotIn("text_to_video", self.window.tool_selector.currentText())

    def test_windows_path_basename_does_not_depend_on_host_platform(self):
        self.assertEqual(path_name(r"C:\Users\User\Videos\result.mp4"), "result.mp4")

    def test_native_status_only_does_not_claim_full_tool_return(self):
        data = fixture();data["revision"] = 2
        step = data["sessions"][0]["steps"][1]
        step.update(toolName="Read", nativeOnly=True, arguments={"fileName": "SKILL.md", "filePathCoverage": "basename_only"},
                    result={"status": "success", "contentCoverage": "not_recorded_in_native_log"})
        self.window.accept_snapshot(data)
        self.assertIn("完整工具返回正文：本机日志未保存", self.labels())
        self.assertIn("调用参数 · 日志已记录字段", self.labels())

    def test_prior_worker_epoch_cannot_replace_current_snapshot(self):
        before = self.window.snapshot_data
        self.window.worker_epoch = 2
        stale = fixture();stale["revision"] = 999;stale["_collectorEpoch"] = 1
        self.window.accept_snapshot(stale)
        self.assertIs(self.window.snapshot_data, before)

    def test_directory_setting_is_local_to_explicit_db(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "doubao"
            directory.mkdir()
            window = MainWindow(db_path=Path(root) / "app" / "test.sqlite3", snapshot=fixture(), start_collector=False)
            try:
                with patch("agentreions_doubao.app.QFileDialog.getExistingDirectory", return_value=str(directory)):
                    window.choose_source_directory()
                self.assertEqual(window.settings_path, (Path(root) / "app" / "settings.json").resolve())
                self.assertTrue(window.settings_path.is_file())
                self.assertEqual(window.source_roots, [directory.resolve()])
            finally:
                window.close()
                window.deleteLater()


class WindowsFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import agentreions_doubao.app as module
        self.previous_globals = (module.UI_FONT, module.MONO_FONT, module.STYLE)
        self.previous_report = self.app.property("doubaoFontDiagnostics")
        self.previous_font = self.app.font()

    def tearDown(self):
        import agentreions_doubao.app as module
        module.UI_FONT, module.MONO_FONT, module.STYLE = self.previous_globals
        self.app.setProperty("doubaoFontDiagnostics", self.previous_report)
        self.app.setFont(self.previous_font)

    @staticmethod
    def good_probe(font, sample):
        return {"passed": True, "missingGlyphs": 0, "glyphCount": len(sample),
                "requestedFamilies": font.families(), "resolvedFamilies": font.families()}

    def test_windows_registers_system_fonts_and_records_selected_family(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root); (directory / "msyh.ttc").touch()
            diagnostics = directory / "font-report.json"
            with patch("agentreions_doubao.app.QFontDatabase.addApplicationFont", return_value=7) as add, \
                 patch("agentreions_doubao.app.QFontDatabase.applicationFontFamilies", return_value=["Microsoft YaHei"]), \
                 patch("agentreions_doubao.app.font_probe", side_effect=self.good_probe):
                report = initialize_fonts(self.app, platform="win32", fonts_dir=directory,
                                          asset_dirs=[], diagnostics_path=diagnostics, force=True)
            self.assertEqual(add.call_args.args, (str(directory / "msyh.ttc"),))
            self.assertEqual(report["selectedUiFamily"], "Microsoft YaHei")
            self.assertEqual(report["registrationCount"], 1)
            self.assertTrue(json.loads(diagnostics.read_text(encoding="utf-8"))["passed"])
            self.assertIn("Microsoft YaHei", self.app.font().families())

    def test_windows_uses_open_font_asset_when_system_font_has_no_chinese(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root); (directory / "segoeui.ttf").touch()
            assets = directory / "assets"; assets.mkdir(); (assets / "NotoSansSC.ttf").touch()
            def coverage(font, sample):
                report = self.good_probe(font, sample)
                report["passed"] = font.family() == "Noto Sans SC"
                report["missingGlyphs"] = 0 if report["passed"] else 4
                return report
            with patch("agentreions_doubao.app.QFontDatabase.addApplicationFont", side_effect=[1, 2]), \
                 patch("agentreions_doubao.app.QFontDatabase.applicationFontFamilies", side_effect=[["Segoe UI"], ["Noto Sans SC"]]), \
                 patch("agentreions_doubao.app.font_probe", side_effect=coverage):
                report = initialize_fonts(self.app, platform="win32", fonts_dir=directory,
                                          asset_dirs=[assets], force=True)
            self.assertEqual(report["selectedUiFamily"], "Noto Sans SC")
            self.assertEqual(report["registrations"][-1]["origin"], "bundled_open_font")

    def test_registration_failure_is_reported_and_cannot_be_cached_as_success(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root); (directory / "msyh.ttc").touch()
            diagnostics = directory / "font-report.json"
            with patch("agentreions_doubao.app.QFontDatabase.addApplicationFont", return_value=-1):
                with self.assertRaisesRegex(RuntimeError, "no registered font"):
                    initialize_fonts(self.app, platform="win32", fonts_dir=directory,
                                     asset_dirs=[], diagnostics_path=diagnostics, force=True)
            report = json.loads(diagnostics.read_text(encoding="utf-8"))
            self.assertEqual(report["registrationCount"], 0)
            self.assertFalse(report["passed"])
            with self.assertRaises(RuntimeError):
                initialize_fonts(self.app, platform="win32")

    def test_glyph_zero_failure_is_not_accepted_even_with_registered_family(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root); (directory / "msyh.ttc").touch()
            with patch("agentreions_doubao.app.QFontDatabase.addApplicationFont", return_value=3), \
                 patch("agentreions_doubao.app.QFontDatabase.applicationFontFamilies", return_value=["Microsoft YaHei"]), \
                 patch("agentreions_doubao.app.font_probe", return_value={"passed": False, "missingGlyphs": 10, "glyphCount": 10}):
                with self.assertRaises(RuntimeError):
                    initialize_fonts(self.app, platform="win32", fonts_dir=directory, asset_dirs=[], force=True)

    def test_real_qt_glyph_probe_detects_missing_glyph(self):
        supported = font_probe(self.app.font(), "image_to_video")
        self.assertTrue(supported["passed"])
        missing = font_probe(self.app.font(), "\U0010ffff")
        self.assertFalse(missing["passed"])
        self.assertGreater(missing["missingGlyphs"], 0)
        multiline = font_probe(self.app.font(), "ASCII\n\U0010ffff\tthird line")
        self.assertFalse(multiline["passed"])
        self.assertGreater(multiline["missingGlyphs"], 0)

    def test_packaged_acceptance_rejects_empty_and_square_font_reports(self):
        from build_windows import verify_font_diagnostics
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fonts.json"
            report = {"platform": "win32", "passed": True, "registrationCount": 1,
                      "familiesAfter": ["Test Font"], "selectedUiFamily": "Test Font", "selectedMonoFamily": "Test Font",
                      "selectedProbes": {"ui": {"passed": True, "missingGlyphs": 0, "glyphCount": 12},
                                         "monospace": {"passed": True, "missingGlyphs": 0, "glyphCount": 12}},
                      "renderedControlsPassed": True,
                      "renderedControlProbes": [{"objectName": name, "passed": True, "missingGlyphs": 0, "glyphCount": 9}
                                                for name in ("toolPrompt", "toolArguments", "toolResult")]}
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertTrue(verify_font_diagnostics(path, require_tool_controls=True)["passed"])
            report["renderedControlProbes"][0]["missingGlyphs"] = 9
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "glyph rendering failed"):
                verify_font_diagnostics(path, require_tool_controls=True)


if __name__ == "__main__":
    unittest.main()
