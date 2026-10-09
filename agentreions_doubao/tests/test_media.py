import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentreions_doubao.media import download_generated_video, verify_local_video, verify_local_image


class MediaTests(unittest.TestCase):
    def test_image_properties_come_from_local_pixels(self):
        from PySide6.QtGui import QImage, QColor
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'image.png'
            image = QImage(47, 29, QImage.Format.Format_RGB32)
            image.fill(QColor('blue'))
            self.assertTrue(image.save(str(path)))
            result = verify_local_image(path)
            self.assertEqual((result['width'], result['height']), (47, 29))
            self.assertEqual(result['method'], 'QImageReader')
            self.assertEqual(len(result['sha256']), 64)

    def test_unsecure_artifact_address_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as directory, patch('urllib.request.urlopen') as request:
            for url in ['http://example.com/video.mp4', 'file:///tmp/a.mp4', 'https://user:pass@example.com/v.mp4']:
                with self.assertRaises(ValueError):
                    download_generated_video(url, directory)
            request.assert_not_called()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'local media tools unavailable')
    def test_independent_probe_measures_media_not_tool_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'actual.mp4'
            subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=blue:s=96x64:r=12:d=0.5', '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p', str(target)], check=True, capture_output=True)
            measured = verify_local_video(target, directory)
            self.assertEqual(measured['method'], 'ffprobe')
            self.assertEqual((measured['width'], measured['height']), (96, 64))
            self.assertAlmostEqual(measured['duration'], 0.5, places=2)
            self.assertEqual(measured['audioTracks'], 0)
            self.assertTrue(Path(measured['posterPath']).is_file())
            self.assertEqual(len(measured['sha256']), 64)


if __name__ == '__main__':
    unittest.main()
