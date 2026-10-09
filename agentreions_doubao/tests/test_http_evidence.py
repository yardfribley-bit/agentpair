import json
import tempfile
import unittest
from pathlib import Path

from agentreions_doubao.http_evidence import associate_http, parse_http_line, read_http_evidence


class HttpEvidenceTests(unittest.TestCase):
    def parse(self, obj):
        return parse_http_line(json.dumps(obj), "/fixture/log", 4)

    def test_sampled_chromium_record_does_not_invent_method_or_body(self):
        obj = {"url": "https://example.test/api", "response_code": 200,
               "request_body_size_B": 105, "response_body_size_B": 302, "time_spent_ms": 90}
        item = parse_http_line("[1:2:20261009/012000.123456:INFO:x] Request succeeded: " + json.dumps(obj), "log", 1)
        self.assertIsNone(item["method"])
        self.assertEqual(item["time"], "2026-10-09T01:20:00.123456")
        self.assertEqual(item["timeSource"], "recorded_local_clock_without_timezone")
        self.assertEqual(item["requestBody"]["status"], "not_recorded")
        self.assertEqual(item["responseBodyBytes"], 302)
        self.assertIsNone(associate_http(item, "s", ["c"]))

    def test_explicit_call_matches_real_method_and_body(self):
        item = self.parse({"url": "https://example.test/generate", "method": "POST", "request_body": {"tcid": "c", "prompt": "make video"}})
        result = associate_http(item, "s", ["c"])
        self.assertEqual(result["association"]["kind"], "explicit_call_id")
        self.assertEqual(result["requestBody"]["value"]["prompt"], "make video")
        self.assertEqual(result["purpose"], "unknown_http")
        self.assertNotIn("_sourceUrl", result)

    def test_conflicting_session_or_calls_not_borrowed(self):
        item = self.parse({"url": "https://example.test/api", "method": "POST", "conversation_id": "other", "tcid": "c"})
        self.assertIsNone(associate_http(item, "s", ["c"]))
        item = self.parse({"url": "https://example.test/api", "method": "POST", "tcid": "c", "request_body": {"call_id": "other"}})
        self.assertIsNone(associate_http(item, "s", ["c"]))

    def test_media_transfer_never_becomes_generation_endpoint(self):
        url = "https://cdn.example.test/video.mp4"
        item = self.parse({"url": url, "method": "GET"})
        result = associate_http(item, "s", [], [url])
        self.assertEqual(result["purpose"], "artifact_transfer")
        self.assertIsNone(associate_http(item, "s", [], [url + "?other=1"]))

    def test_credentials_omitted_without_dropping_actual_query_parameters(self):
        item = self.parse({"url": "https://user:password@example.test/api?tcid=c&duration=5&access_token=secret",
                           "method": "POST", "request_headers": {"Authorization": "secret", "Content-Type": "application/json"},
                           "request_body": {"password": "secret", "duration": 5}})
        result = associate_http(item, "s", ["c"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("user:password", result["url"])
        self.assertEqual(result["requestBody"]["value"]["duration"], 5)

    def test_protobuf_is_opaque_without_schema(self):
        item = self.parse({"url": "https://example.test/im", "method": "POST", "tcid": "c",
                           "request_headers": {"Content-Type": "application/x-protobuf"}, "request_body": "opaque"})
        self.assertEqual(item["requestBody"]["status"], "opaque_protobuf")
        self.assertIsNone(item["requestBody"]["value"])

    def test_aalg_inventory_and_bounded_scoped_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "net.alaudalog"
            binary.write_bytes(b"AalG\x00\x00\x00\x00Atab\x00\x00")
            log = Path(folder) / "net.log"
            log.write_text(json.dumps({"url": "https://example.test/api", "method": "POST", "tcid": "c"}) + "\n" +
                           json.dumps({"url": "https://example.test/unrelated", "method": "GET"}) + "\n")
            state = read_http_evidence([binary, log], "s", ["c"])
            self.assertEqual(len(state["records"]), 1)
            self.assertEqual(state["unassociatedHttpRecords"], 1)
            self.assertEqual(state["files"][0]["format"], "unsupported_aalG_atab_binary")
            self.assertFalse(state["coverage"]["networkRequestsMade"])

    def test_body_limit_is_explicit(self):
        item = self.parse({"url": "https://example.test/api", "method": "POST", "request_body": "x" * 70000})
        self.assertTrue(item["requestBody"]["truncated"])
        self.assertEqual(item["requestBody"]["originalChars"], 70000)


if __name__ == "__main__":
    unittest.main()
