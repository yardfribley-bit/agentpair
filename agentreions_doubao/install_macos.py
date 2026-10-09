"""Install a local developer build; optionally import explicitly verified media."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

from agentreions_doubao.collector import Collector, default_data_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-root', type=Path, default=Path('/Applications'))
    parser.add_argument('--verification-manifest', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    source = root / 'dist/agentreions_doubao.app'
    target = args.install_root / source.name
    if not source.is_dir():
        raise SystemExit('Build the app before installing.')
    if target.exists():
        raise SystemExit(f'An existing app is present: {target}. It was not overwritten.')
    args.install_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True)
    collector = Collector()
    try:
        if args.verification_manifest:
            media_dir = default_data_dir() / 'media'
            media_dir.mkdir(parents=True, exist_ok=True)
            media_dir.chmod(0o700)
            for artifact in json.loads(args.verification_manifest.read_text(encoding='utf-8')):
                video = Path(artifact['path'])
                metadata = dict(artifact['metadata'])
                digest = hashlib.sha256(video.read_bytes()).hexdigest()
                if digest != metadata['sha256']:
                    raise ValueError('Verified media has changed; refusing to import it.')
                local = media_dir / video.name
                shutil.copy2(video, local)
                local.chmod(0o600)
                metadata['path'] = str(local)
                poster_path = metadata.get('posterPath')
                if poster_path and Path(poster_path).is_file():
                    poster = media_dir / Path(poster_path).name
                    shutil.copy2(poster_path, poster)
                    poster.chmod(0o600)
                    metadata['posterPath'] = str(poster)
                collector.register_artifact_verification(artifact['url'], str(local), metadata)
        for _ in range(2):
            collector.scan_once()
        state = collector.snapshot()
        print(json.dumps({'installedApp':str(target), 'database':str(collector.db_path),
                          'sessions':len(state['sessions']), 'upload':'disabled'}, ensure_ascii=False))
    finally:
        collector.close()


if __name__ == '__main__':
    main()
