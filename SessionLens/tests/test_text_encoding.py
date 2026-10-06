"""Exercise product file IO with a Windows-style legacy text default."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sessionlens.desktop import Runtime, defaults
from sessionlens.embedding import LocalEmbedding
from sessionlens.relay_model import call


_open = io.open


def legacy_text_open(file, mode='r', buffering=-1, encoding=None, errors=None,
                     newline=None, closefd=True, opener=None):
    if 'b' not in mode and encoding in (None, 'locale'):
        encoding = 'cp1252'
    return _open(file, mode, buffering, encoding, errors, newline, closefd, opener)


class TextEncodingTests(unittest.TestCase):
    def test_runtime_status_roundtrip_uses_utf8_with_legacy_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('io.open', legacy_text_open):
                runtime = Runtime(root, defaults())
                runtime.update(knowledge='已整理 3 个任务', embedding_error='原始记录保留')
            raw = (root / 'knowledge-status.json').read_bytes()
            self.assertEqual(json.loads(raw.decode('utf-8'))['knowledge'], '已整理 3 个任务')
            self.assertIn('原始记录保留'.encode('utf-8'), raw)

    def test_model_credentials_read_utf8_without_real_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            credential = Path(tmp) / 'fixture.json'
            credential.write_bytes(json.dumps({'apiKey': 'dummy-凭据'}, ensure_ascii=False).encode('utf-8'))
            config = {'url': 'https://example.invalid/v1', 'name': 'test', 'credentialFile': str(credential)}
            response = MagicMock()
            response.__enter__.return_value = io.BytesIO(b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}')
            with patch('io.open', legacy_text_open), patch('sessionlens.relay_model.urllib.request.build_opener') as opener:
                opener.return_value.open.return_value = response
                self.assertEqual(call(config, 'Fixture', {}), {})
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.get_header('Authorization'), 'Bearer dummy-凭据')

    def test_embedding_manifest_keeps_unicode_instruction_with_legacy_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {}
            for name in ('model.onnx', 'tokenizer.json', 'config.json'):
                value = name.encode('ascii')
                (root / name).write_bytes(value)
                files[name] = hashlib.sha256(value).hexdigest()
            manifest = {'pooling': 'cls', 'dimensions': 512, 'name': 'fixture-model',
                        'queryInstruction': '为这个句子生成表示：', 'files': files}
            (root / 'manifest.json').write_bytes(json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
            ort = SimpleNamespace(disable_telemetry_events=lambda: None,
                                  SessionOptions=SimpleNamespace,
                                  ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=0),
                                  InferenceSession=MagicMock())
            tokenizer = SimpleNamespace(Tokenizer=SimpleNamespace(from_file=MagicMock(return_value=MagicMock())))
            with patch('io.open', legacy_text_open), patch.dict('sys.modules', {'numpy': SimpleNamespace(), 'onnxruntime': ort, 'tokenizers': tokenizer}):
                model = LocalEmbedding({'directory': str(root)})
            self.assertEqual(model.instruction, manifest['queryInstruction'])
            self.assertEqual(model.name, 'fixture-model')


if __name__ == '__main__':
    unittest.main()
