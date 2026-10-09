"""Build the standalone Windows x64 desktop application on Windows."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent


def write_synthetic_ui_fixture(qa: Path) -> dict:
    """Create isolated, visibly synthetic source records for packaged UI QA.

    No account, observed user data or real media is included. Reserved
    example.invalid references are never downloaded by the local desktop UI.
    """
    source = qa / 'synthetic-source'
    system = source / 'Profile 1/.doubao/agent_mode/workspace/.sessions/synthetic-package-qa/agents/synthetic-agent/system'
    system.mkdir(parents=True, exist_ok=True)
    prompt = (
        '[SYNTHETIC PACKAGE QA — NOT A REAL DOUBAO TASK]\n'
        '这是 Windows 安装包的合成验收提示词，不来自真实用户，不执行任何生成请求。\n'
        '保持参考图布局不变，生成一个五秒、3:4 的演示动画。鼠标从右侧移动到菜单并悬停。\n'
        '用这一段完整文本检查提示词区域可读、可选中、可复制；它不能被截断或藏在折叠项中。'
    )
    image_url = 'https://example.invalid/synthetic-package-input.png'
    video_url = 'https://example.invalid/synthetic-package-output.mp4'
    arguments = {'duration': '5', 'image_reference_url_list': [image_url],
                 'model_version': 'SYNTHETIC_QA_MODEL', 'prompt': prompt, 'ratio': '3:4'}
    returned = f'video (5s 834x1112 mp4) generated.{video_url}'
    rows = [
        {'role': 'user', 'timestamp': '2020-01-01T00:00:00Z',
         'content': '[SYNTHETIC PACKAGE QA] 先生成参考图片，再用图片制作五秒演示视频。这是合成验收数据，不是真实任务。'},
        {'role': 'assistant', 'timestamp': '2020-01-01T00:00:01Z', 'content': '合成步骤：制作测试参考图。',
         'tool_calls': [{'id': 'synthetic-image-call', 'function': {'name': 'image_gen',
             'arguments': {'prompt': '[SYNTHETIC PACKAGE QA] 仅用于解析与显示验收的参考图片参数。', 'ratio': '3:4'}}}]},
        {'role': 'tool', 'timestamp': '2020-01-01T00:00:02Z', 'tool_call_id': 'synthetic-image-call',
         'content': f'image (896x1194 png) generated.{image_url}'},
        {'role': 'assistant', 'timestamp': '2020-01-01T00:00:03Z', 'content': '合成步骤：将参考图片交给图生视频工具。',
         'tool_calls': [{'id': 'synthetic-image-to-video-call', 'function': {'name': 'image_to_video', 'arguments': arguments}}]},
        {'role': 'tool', 'timestamp': '2020-01-01T00:00:04Z', 'tool_call_id': 'synthetic-image-to-video-call', 'content': returned},
    ]
    trajectory = system / 'trajectory.jsonl'
    trajectory.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')
    return {'source': source, 'trajectory': trajectory, 'arguments': arguments, 'result': returned,
            'imageUrl': image_url, 'videoUrl': video_url,
            'expectedMarkers': ['SYNTHETIC PACKAGE QA', 'image_to_video', 'synthetic-image-to-video-call',
                                '5 秒', '3:4', 'SYNTHETIC_QA_MODEL', '完整提示词', '完整调用参数', '完整工具返回']}


def verify_synthetic_collector(fixture: dict, qa: Path) -> dict:
    """Check source-to-display projection using a separate validation database."""
    from agentreions_doubao.collector import Collector
    collector = Collector(db_path=qa / 'synthetic-validation-state/observations.sqlite3', source_roots=[fixture['source']])
    try:
        collector.scan_once()
        snapshot = collector.snapshot()
    finally:
        collector.close()
    if len(snapshot['sessions']) != 1:
        raise RuntimeError('Synthetic fixture did not produce exactly one isolated creation run.')
    run = snapshot['sessions'][0]
    calls = [step for step in run['steps'] if step.get('toolName') == 'image_to_video']
    if len(calls) != 1 or calls[0]['arguments'] != fixture['arguments'] or calls[0]['result'] != fixture['result']:
        raise RuntimeError('Synthetic packaged UI fixture lost its complete tool parameters or return.')
    if not run.get('mediaRelations'):
        raise RuntimeError('Synthetic image-to-video source relationship was not explicitly retained.')
    return {'synthetic': True, 'isRealUserTask': False, 'sourceOnly': str(fixture['source']),
            'records': 5, 'runs': 1, 'tool': 'image_to_video',
            'fullArgumentsMatch': True, 'fullReturnMatch': True, 'explicitMediaRelationship': True,
            'expectedVisibleMarkers': fixture['expectedMarkers'],
            'trajectorySha256': hashlib.sha256(fixture['trajectory'].read_bytes()).hexdigest(),
            'networkRequestsMade': False}


def verify_font_diagnostics(path: Path, *, require_tool_controls: bool = False) -> dict:
    """Reject successful process exits or PNGs whose text has no valid glyphs."""
    if not path.is_file():
        raise RuntimeError('Packaged Windows font diagnostics were not written.')
    report = json.loads(path.read_text(encoding='utf-8'))
    if (report.get('platform') != 'win32' or not report.get('passed')
            or report.get('registrationCount', 0) < 1 or not report.get('familiesAfter')
            or not report.get('selectedUiFamily') or not report.get('selectedMonoFamily')):
        raise RuntimeError('Packaged Windows font initialization did not pass: inspect ' + str(path))
    probes = list(report.get('selectedProbes', {}).values())
    if len(probes) < 2 or any(not probe.get('passed') or probe.get('missingGlyphs') != 0
                              or probe.get('glyphCount', 0) < 1 for probe in probes):
        raise RuntimeError('Packaged Windows UI/JSON fonts contain missing glyph mappings.')
    if require_tool_controls:
        controls = report.get('renderedControlProbes', [])
        if ({item.get('objectName') for item in controls} != {'toolPrompt', 'toolArguments', 'toolResult'}
                or not report.get('renderedControlsPassed')
                or any(not item.get('passed') or item.get('missingGlyphs') != 0
                       or item.get('glyphCount', 0) < 1 for item in controls)):
            raise RuntimeError('Packaged tool-detail prompt/arguments/return glyph rendering failed.')
    return report


def main():
    if sys.platform != 'win32':
        raise SystemExit('Windows binaries must be built on Windows; use the Windows CI job.')
    env = os.environ.copy()
    env['PYINSTALLER_CONFIG_DIR'] = str(ROOT / 'build/pyinstaller-cache')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
               '--name', 'agentreions_doubao', '--distpath', str(ROOT/'dist'),
               '--workpath', str(ROOT/'build'), '--specpath', str(ROOT/'build')]
    icon = ROOT / 'assets/agentreins.ico'
    if icon.is_file():
        command.extend(['--icon', str(icon)])
    fonts = ROOT / 'assets/fonts'
    if not (fonts / 'OFL.txt').is_file() or not any(fonts.glob('*.ttf')):
        raise RuntimeError('Windows builds require the licensed open-font fallback in assets/fonts.')
    # Only the redistributable OFL assets are packaged. Windows system fonts
    # remain on the target computer and are read at runtime, never copied.
    command.extend(['--add-data', str(fonts) + os.pathsep + 'assets/fonts'])
    command.append(str(ROOT/'desktop_main.py'))
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    bundle = ROOT/'dist/agentreions_doubao'
    qa = ROOT/'build/packaged-qa'
    qa.mkdir(parents=True, exist_ok=True)
    empty_source = qa/'empty-source'
    empty_source.mkdir(exist_ok=True)
    runtime_env = env.copy()
    runtime_env['QT_QPA_PLATFORM'] = 'offscreen'
    empty_fonts = qa/'window.fonts.json'
    subprocess.run([str(bundle/'agentreions_doubao.exe'), '--db', str(qa/'observations.sqlite3'),
                    '--source-root', str(empty_source), '--window-size', '1280x800',
                    '--screenshot', str(qa/'window.png'), '--font-diagnostics', str(empty_fonts)],
                    env=runtime_env, check=True, timeout=30)
    verify_font_diagnostics(empty_fonts)
    if not (qa/'window.png').is_file():
        raise RuntimeError('The packaged Windows UI did not produce its verification image.')
    fixture = write_synthetic_ui_fixture(qa)
    acceptance = verify_synthetic_collector(fixture, qa)
    synthetic_image = qa/'synthetic-tool-details.png'
    synthetic_fonts = qa/'synthetic-tool-details.fonts.json'
    subprocess.run([str(bundle/'agentreions_doubao.exe'),
                    '--db', str(qa/'synthetic-packaged-state/observations.sqlite3'),
                    '--source-root', str(fixture['source']), '--window-size', '1280x1600',
                    '--screenshot', str(synthetic_image), '--font-diagnostics', str(synthetic_fonts)],
                    env=runtime_env, check=True, timeout=30)
    fonts_report = verify_font_diagnostics(synthetic_fonts, require_tool_controls=True)
    if not synthetic_image.is_file() or synthetic_image.stat().st_size < 4096:
        raise RuntimeError('The packaged Windows UI did not render the synthetic tool-detail task.')
    # Keep evidence beside build artifacts, outside both the distributable and
    # the default user's data directory. Inspect the screenshot in CI artifacts.
    acceptance.update(packagedExecutableLaunched=True, screenshot=str(synthetic_image),
                      screenshotSha256=hashlib.sha256(synthetic_image.read_bytes()).hexdigest(),
                      emptyDataScreenshot=str(qa/'window.png'),
                      fontDiagnostics=str(synthetic_fonts),
                      fontDiagnosticsSha256=hashlib.sha256(synthetic_fonts.read_bytes()).hexdigest(),
                      fontGlyphVerificationPassed=True,
                      selectedUiFont=fonts_report['selectedUiFamily'],
                      selectedMonoFont=fonts_report['selectedMonoFamily'])
    (qa/'synthetic-ui-acceptance.json').write_text(json.dumps(acceptance, ensure_ascii=False, indent=2), encoding='utf-8')
    release = ROOT/'release'
    release.mkdir(exist_ok=True)
    with zipfile.ZipFile(release/'agentreions_doubao-Windows-x64.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in bundle.rglob('*'):
            if path.is_file():
                archive.write(path, path.relative_to(bundle.parent))
    print('Windows package, empty-data startup, and SYNTHETIC PACKAGE QA tool-detail rendering completed.')


if __name__ == '__main__':
    main()
