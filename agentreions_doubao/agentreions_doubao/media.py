"""Explicit local media import and independent verification; no uploads."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def verify_local_image(path: str | Path) -> dict:
    """Measure a local input image with Qt; do not infer it from a filename."""
    from PySide6.QtGui import QImageReader
    path = Path(path).expanduser().resolve(strict=True)
    reader = QImageReader(str(path))
    image_format = bytes(reader.format()).decode('ascii', 'replace')
    image = reader.read()
    if image.isNull():
        raise ValueError('文件不是可读取的图片。')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'status': 'verified', 'method': 'QImageReader', 'path': str(path),
            'verifiedAt': datetime.now(timezone.utc).isoformat(), 'width': image.width(),
            'height': image.height(), 'format': image_format,
            'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def verify_local_video(path: str | Path, poster_dir: str | Path | None = None) -> dict:
    path = Path(path).expanduser().resolve(strict=True)
    probe = shutil.which('ffprobe')
    if not probe:
        raise RuntimeError('本机尚未安装 ffprobe，视频属性未核验。')
    result = subprocess.run([probe, '-v', 'error', '-show_format', '-show_streams',
                             '-of', 'json', str(path)], check=True,
                            capture_output=True, text=True, timeout=30)
    value = json.loads(result.stdout)
    video = next((s for s in value.get('streams', []) if s.get('codec_type') == 'video'), None)
    if video is None:
        raise ValueError('文件中未发现视频轨道。')
    duration = value.get('format', {}).get('duration') or video.get('duration')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    metadata = {'status': 'verified', 'method': 'ffprobe', 'path': str(path),
                'verifiedAt': datetime.now(timezone.utc).isoformat(),
                'duration': float(duration) if duration is not None else None,
                'width': video.get('width'), 'height': video.get('height'),
                'codec': video.get('codec_name'), 'frameRate': video.get('avg_frame_rate'),
                'audioTracks': sum(s.get('codec_type') == 'audio' for s in value.get('streams', [])),
                'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg and poster_dir is not None:
        folder = Path(poster_dir).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        poster = folder / (digest.hexdigest()[:20] + '.png')
        subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(path),
                        '-frames:v', '1', '-vf', 'scale=640:-2', str(poster)],
                       check=True, capture_output=True, timeout=30)
        poster.chmod(0o600)
        metadata['posterPath'] = str(poster)
    return metadata


def download_generated_video(url: str, directory: str | Path,
                             max_bytes: int = 64 * 1024 * 1024) -> Path:
    """Only call after an explicit user download action, never during collection."""
    return download_media_artifact(url, directory, suffix='.mp4', max_bytes=max_bytes)


def download_media_artifact(url: str, directory: str | Path, suffix: str = '.png',
                            max_bytes: int = 64 * 1024 * 1024) -> Path:
    """Explicit, inbound artifact import; never a collector background action."""
    if suffix not in {'.png', '.jpg', '.webp', '.mp4'}:
        raise ValueError('不支持此素材文件类型。')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or not parsed.hostname:
        raise ValueError('仅允许不带登录凭据的 HTTPS 产物地址。')
    folder = Path(directory).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (hashlib.sha256(url.encode()).hexdigest()[:20] + suffix)
    temporary = path.with_suffix('.partial')
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'agentreions_doubao/0.1'})
        with urllib.request.urlopen(request, timeout=20) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != 'https':
                raise ValueError('产物重定向到了非 HTTPS 地址。')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                size = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError('视频超过本次 64 MB 下载上限。')
                    stream.write(chunk)
        temporary.replace(path)
        path.chmod(0o600)
        return path
    finally:
        if temporary.exists():
            temporary.unlink()
