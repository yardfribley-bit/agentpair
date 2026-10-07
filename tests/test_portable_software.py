"""Portable recipes and completion evidence, plus real ZIP guards on PowerShell.

The installation fixtures contain inert bytes and mock Start-Process. A Windows
process regression test compiles its own tiny local EXE with the OS compiler.
No test downloads or executes a third-party package.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile

from agentpair.software_install import SoftwareCatalog, portable_recipe_fingerprint, verify_receipt


RECIPE = {
    'id': 'sessionlens-windows', 'platform': 'Windows', 'installer': 'portable_zip',
    'name': 'SessionLens', 'version': 'build-2026-10-07-abcdef1',
    'url': 'https://example.com/releases/SessionLens-Windows.zip', 'sha256': 'a' * 64,
    'entryPoint': 'SessionLens/SessionLens.exe',
}


def receipt(recipe=RECIPE):
    directory = r'C:\Users\test\AppData\Local\AgentPair\Software\sessionlens-windows'
    return {'state': 'completed', 'evidence': {
        'softwareId': recipe['id'], 'verified': True, 'installedVersion': recipe['version'],
        'sha256': recipe['sha256'], 'recipeFingerprint': portable_recipe_fingerprint(recipe),
        'entryPoint': recipe['entryPoint'], 'installDirectory': directory,
        'checkedBinary': True, 'checkedBinaryPath': directory + r'\SessionLens\SessionLens.exe',
        'checkedBinarySha256': 'b' * 64, 'selfTestArgument': '--self-test', 'selfTestExitCode': 0,
        'versionSource': 'pinned_recipe', 'binaryVersionVerified': False, 'launchVerified': False,
    }}


class PortableRecipeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = SoftwareCatalog(Path(self.tmp.name) / 'software-catalog.json')

    def test_registers_portable_without_registry_name_or_caller_commands(self):
        clean = self.catalog.register({**RECIPE, 'command': 'untrusted', 'arguments': ['--evil']})
        self.assertEqual(clean, RECIPE)
        self.assertEqual(self.catalog.get(RECIPE['id']), RECIPE)
        self.assertTrue(verify_receipt(clean, receipt(clean)))

    def test_requires_pinned_https_and_version(self):
        for patch in ({'sha256': ''}, {'sha256': 'A' * 64}, {'url': 'http://example.com/a.zip'},
                      {'url': 'https://user:pass@example.com/a.zip'}, {'url': 'https://example.com/a.zip#x'},
                      {'url': 'https://example.com/\na.zip'}, {'version': ''}, {'version': ' '},
                      {'version': 'v1\nfake'}, {'version': None}, {'installer': 'zip'}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.catalog.register({**RECIPE, **patch})

    def test_entry_point_is_a_safe_relative_executable(self):
        paths = ('../SessionLens.exe', '/SessionLens.exe', 'C:/SessionLens.exe', 'C:SessionLens.exe',
                 '//server/share/SessionLens.exe', 'SessionLens\\SessionLens.exe',
                 'SessionLens/../../evil.exe', 'SessionLens/./SessionLens.exe', 'SessionLens//SessionLens.exe',
                 'SessionLens/SessionLens.exe:stream', 'NUL/SessionLens.exe', 'COM1.exe', 'LPT¹.exe', 'CONOUT$.exe',
                 'SessionLens/CON .exe', 'SessionLens/NUL .txt/SessionLens.exe',
                 'SessionLens. /SessionLens.exe', 'SessionLens./SessionLens.exe', 'SessionLens/<file>.exe',
                 'SessionLens/SessionLens.exe/', 'SessionLens/SessionLens.dll', '', None)
        for entry in paths:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.catalog.register({**RECIPE, 'entryPoint': entry})
        with self.assertRaises(ValueError):
            self.catalog.register({**RECIPE, 'id': 'con'})

    def test_recipe_fingerprint_is_stable_across_cache_routing(self):
        cached = {**RECIPE, 'officialUrl': RECIPE['url'], 'url': 'https://cache.example.com/packages/' + 'a' * 64}
        self.assertEqual(portable_recipe_fingerprint(RECIPE), portable_recipe_fingerprint(cached))
        self.assertTrue(verify_receipt(cached, receipt(RECIPE)))
        self.assertTrue(verify_receipt(RECIPE, receipt(cached)))
        for patch in ({'version': 'another-build'}, {'entryPoint': 'SessionLens/Other.exe'},
                      {'name': 'Other'}, {'url': 'https://other.example.com/release.zip'},
                      {'displayName': 'SessionLens'}, {'sha256': 'c' * 64}):
            with self.subTest(patch=patch):
                self.assertFalse(verify_receipt({**RECIPE, **patch}, receipt(RECIPE)))

    def test_receipt_requires_binary_self_test_and_honest_version_basis(self):
        for patch in ({'sha256': 'c' * 64}, {'recipeFingerprint': 'c' * 64},
                      {'checkedBinary': False}, {'checkedBinarySha256': ''},
                      {'checkedBinaryPath': r'C:\elsewhere\SessionLens.exe'},
                      {'installDirectory': r'AgentPair\Software\sessionlens-windows'},
                      {'installDirectory': r'\\server\share\AgentPair\Software\sessionlens-windows'},
                      {'entryPoint': 'SessionLens/Other.exe'}, {'selfTestExitCode': 1},
                      {'selfTestExitCode': False}, {'selfTestExitCode': '0'},
                      {'selfTestArgument': '--version'}, {'installedVersion': 'made-up'},
                      {'versionSource': 'exe_self_report'}, {'binaryVersionVerified': True},
                      {'launchVerified': True}, {'verified': False}):
            value = receipt()
            value['evidence'].update(patch)
            with self.subTest(patch=patch):
                self.assertFalse(verify_receipt(RECIPE, value))
        for field in ('sha256', 'recipeFingerprint', 'entryPoint', 'checkedBinary', 'checkedBinaryPath',
                      'checkedBinarySha256', 'selfTestArgument', 'selfTestExitCode', 'installDirectory',
                      'versionSource', 'binaryVersionVerified', 'launchVerified'):
            value = receipt()
            del value['evidence'][field]
            with self.subTest(missing=field):
                self.assertFalse(verify_receipt(RECIPE, value))
        self.assertFalse(verify_receipt(RECIPE, {'evidence': None}))
        self.assertFalse(verify_receipt(RECIPE, None))

    def test_directory_existence_style_receipt_is_insufficient(self):
        old = {'state': 'completed', 'evidence': {'softwareId': RECIPE['id'], 'verified': True,
                                                'installedVersion': RECIPE['version'], 'sha256': RECIPE['sha256']}}
        self.assertFalse(verify_receipt(RECIPE, old))


POWERSHELL = (shutil.which('powershell') if os.name == 'nt' else None) or shutil.which('pwsh')
SCRIPT = Path(__file__).resolve().parents[1] / 'windows' / 'software-install.ps1'


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(POWERSHELL, 'PowerShell runtime is required for ZIP extraction tests')
class PortablePowerShellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / 'release.zip'
        self.destination = self.root / 'extracted'

    def make_zip(self, entries):
        with zipfile.ZipFile(self.archive, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, contents, mode in entries:
                item = zipfile.ZipInfo(name)
                if mode:
                    item.create_system = 3
                    item.external_attr = mode << 16
                item.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(item, contents)

    def run_ps(self, code):
        path = self.root / 'run.ps1'
        # Match windows/build.ps1: Windows PowerShell 5.1 needs a UTF-8 BOM.
        source = self.root / 'software-install.ps1'
        source.write_text(SCRIPT.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
        path.write_text("$ErrorActionPreference='Stop'\n[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)\n. " + ps_quote(source) + '\n' + code, encoding='utf-8-sig')
        environment = os.environ.copy()
        if os.name == 'nt' and Path(POWERSHELL).name.lower() == 'powershell.exe':
            # A pwsh CI shell exports its own PS7 module paths. A separately
            # launched Windows PowerShell 5.1 must build its normal defaults.
            environment = {key: value for key, value in environment.items() if key.upper() != 'PSMODULEPATH'}
        return subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-File', str(path)],
                              capture_output=True, text=True, encoding='utf-8', timeout=30, env=environment)

    def extract(self, limits=''):
        return self.run_ps('Expand-AgentPairPortableZip ' + ps_quote(self.archive) + ' ' + ps_quote(self.destination)
                           + " 'SessionLens/SessionLens.exe' " + limits)

    def test_extracts_archive_and_hashes_actual_binary(self):
        self.make_zip([('SessionLens/SessionLens.exe', b'inert fixture', stat.S_IFREG | 0o755),
                       ('SessionLens/assets/file.txt', b'data', stat.S_IFREG | 0o644)])
        result = self.extract()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), hashlib.sha256(b'inert fixture').hexdigest())
        self.assertEqual((self.destination / 'SessionLens' / 'assets' / 'file.txt').read_bytes(), b'data')

    def test_file_hash_uses_framework_stream_and_releases_file_handle(self):
        payload = self.root / 'payload.bin'
        contents = bytes(range(256)) * 16384
        payload.write_bytes(contents)
        result = self.run_ps("function Get-FileHash {throw 'Get-FileHash must not be required'}\n"
                             + 'Get-AgentPairFileSha256 ' + ps_quote(payload) + '\n'
                             + '[IO.File]::AppendAllText(' + ps_quote(payload) + ",'tail')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), hashlib.sha256(contents).hexdigest())
        self.assertEqual(payload.read_bytes(), contents + b'tail')

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell module paths are Windows-specific')
    def test_windows_powershell_has_its_native_module_path(self):
        if Path(POWERSHELL).name.lower() != 'powershell.exe':
            self.skipTest('Windows PowerShell 5.1 is unavailable')
        result = self.run_ps("if($PSVersionTable.PSVersion.Major -ne 5){throw 'Expected Windows PowerShell 5.1'}\n"
                             + "$native=Join-Path $PSHOME 'Modules'\n"
                             + "if($env:PSModulePath.Split(';') -notcontains $native){throw 'Native module path missing'}\n"
                             + '(Get-Command Get-FileHash -ErrorAction Stop).Name')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'Get-FileHash')

    def test_rejects_unsafe_names_before_writing_any_file(self):
        names = ('../escape.exe', '/escape.exe', 'C:/escape.exe', 'C:escape.exe',
                 '\\\\server\\share\\escape.exe', 'SessionLens/../../escape.exe', 'SessionLens\\..\\escape.exe',
                 'SessionLens/file.exe:stream', 'SessionLens/NUL.exe', 'SessionLens/COM1.txt',
                 'SessionLens/NUL .txt', 'SessionLens/CON .exe',
                 'SessionLens./file.exe', 'SessionLens /file.exe', '.agentpair-portable.json')
        for name in names:
            with self.subTest(name=name):
                self.make_zip([('SessionLens/SessionLens.exe', b'inert', 0), (name, b'bad', 0)])
                result = self.extract()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.destination.exists())
                self.assertFalse((self.root / 'escape.exe').exists())

    def test_rejects_links_special_files_duplicates_and_collisions(self):
        for additional in ([('SessionLens/link', b'outside', stat.S_IFLNK | 0o777)],
                           [('SessionLens/pipe', b'', stat.S_IFIFO | 0o600)],
                           [('sessionlens/sessionlens.EXE', b'duplicate', 0)],
                           [('SessionLens', b'parent is file', 0)]):
            with self.subTest(additional=additional):
                self.make_zip([('SessionLens/SessionLens.exe', b'inert', 0), *additional])
                self.assertNotEqual(self.extract().returncode, 0)
                self.assertFalse(self.destination.exists())
        self.make_zip([('SessionLens/SessionLens.exe', b'inert', 0)])
        with zipfile.ZipFile(self.archive, 'a') as archive:
            reparse = zipfile.ZipInfo('SessionLens/reparse')
            reparse.create_system = 0
            reparse.external_attr = 0x400
            archive.writestr(reparse, b'link payload')
        self.assertNotEqual(self.extract().returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_enforces_archive_entry_count_file_total_and_ratio_limits(self):
        self.make_zip([('SessionLens/SessionLens.exe', b'inert binary', 0), ('SessionLens/data', b'12345', 0)])
        for limits in ('-maxArchiveBytes 1', '-maxEntries 1', '-maxFileBytes 4', '-maxTotalBytes 15'):
            with self.subTest(limits=limits):
                self.assertNotEqual(self.extract(limits).returncode, 0)
                self.assertFalse(self.destination.exists())
        self.make_zip([('SessionLens/SessionLens.exe', b'inert', 0), ('SessionLens/bomb', b'0' * 100000, 0)])
        self.assertNotEqual(self.extract().returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_missing_or_empty_entry_point_is_not_success(self):
        for name, binary in (('SessionLens/not-the-entry.exe', b'inert'), ('SessionLens/SessionLens.exe', b'')):
            self.make_zip([(name, binary, 0)])
            self.assertNotEqual(self.extract().returncode, 0)
            self.assertFalse(self.destination.exists())

    def test_existing_or_linked_destination_is_not_overwritten(self):
        self.make_zip([('SessionLens/SessionLens.exe', b'inert', 0)])
        self.destination.mkdir()
        keep = self.destination / 'keep.txt'
        keep.write_text('preserve me')
        self.assertNotEqual(self.extract().returncode, 0)
        self.assertEqual(keep.read_text(), 'preserve me')
        keep.unlink()
        self.destination.rmdir()
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            self.destination.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symbolic link creation requires OS privileges')
        result = self.extract()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((outside / 'SessionLens').exists())
        self.assertTrue(self.destination.is_symlink())

    def install(self, exit_code=0, setup=''):
        self.make_zip([('SessionLens/SessionLens.exe', b'inert fixture', stat.S_IFREG | 0o755)])
        recipe = {**RECIPE, 'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        recipe_path = self.root / 'recipe.json'
        recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
        work = self.root / 'logs'
        work.mkdir(exist_ok=True)
        code = f"""
$env:LOCALAPPDATA={ps_quote(self.root / 'local')}
$recipe=Get-Content -LiteralPath {ps_quote(recipe_path)} -Raw | ConvertFrom-Json
$script:runCount=0
function Start-Process {{
    param($FilePath,$ArgumentList,$WorkingDirectory,$RedirectStandardOutput,$RedirectStandardError,[switch]$PassThru)
    if($ArgumentList.Count -ne 1 -or $ArgumentList[0] -cne '--self-test') {{throw 'Unexpected process arguments'}}
    $script:runCount++
    $child=[PSCustomObject]@{{ExitCode={exit_code};HasExited=$true}}
    $child | Add-Member ScriptMethod WaitForExit {{param([Nullable[int]]$milliseconds);if($null -ne $milliseconds){{return $true}}}}
    $child | Add-Member ScriptMethod Dispose {{}}
    return $child
}}
{setup}
try {{
    $result=Invoke-AgentPairPortableInstall $recipe {ps_quote(self.archive)} $recipe.sha256 {ps_quote(work)} {{param($value)}}
    @{{result=$result;runCount=$script:runCount}} | ConvertTo-Json -Depth 8
}} catch {{
    @{{error=[string]$_.Exception.Message;runCount=$script:runCount}} | ConvertTo-Json -Depth 8
}}
"""
        process = self.run_ps(code)
        self.assertEqual(process.returncode, 0, process.stderr)
        return recipe, json.loads(process.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Install directory is Windows-specific')
    def test_successful_install_returns_checked_binary_and_self_test_receipt(self):
        recipe, result = self.install()
        self.assertEqual(result['runCount'], 1)
        self.assertTrue(verify_receipt(recipe, result['result']))
        self.assertTrue(Path(result['result']['evidence']['checkedBinaryPath']).is_file())
        self.assertFalse(result['result']['evidence']['binaryVersionVerified'])
        self.assertFalse(result['result']['evidence']['launchVerified'])

    @unittest.skipUnless(os.name == 'nt', 'Install directory is Windows-specific')
    def test_unrelated_directory_is_never_overwritten_or_executed(self):
        setup = r"""
$target=Join-Path $env:LOCALAPPDATA 'AgentPair\Software\sessionlens-windows'
[IO.Directory]::CreateDirectory($target) | Out-Null
[IO.File]::WriteAllText((Join-Path $target 'unrelated.txt'),'preserve me')
"""
        _, result = self.install(setup=setup)
        self.assertIn('not an AgentPair-managed', result['error'])
        self.assertEqual(result['runCount'], 0)
        self.assertEqual((self.root / 'local' / 'AgentPair' / 'Software' / RECIPE['id'] / 'unrelated.txt').read_text(), 'preserve me')

    @unittest.skipUnless(os.name == 'nt', 'Install directory is Windows-specific')
    def test_failed_self_test_restores_previous_owned_installation(self):
        setup = r"""
$target=Join-Path $env:LOCALAPPDATA 'AgentPair\Software\sessionlens-windows'
[IO.Directory]::CreateDirectory($target) | Out-Null
@{managedBy='AgentPair';installer='portable_zip';softwareId='sessionlens-windows'} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target '.agentpair-portable.json')
[IO.File]::WriteAllText((Join-Path $target 'previous.txt'),'original version')
"""
        _, result = self.install(exit_code=9, setup=setup)
        self.assertIn('self-test failed: 9', result['error'])
        self.assertEqual(result['runCount'], 1)
        target = self.root / 'local' / 'AgentPair' / 'Software' / RECIPE['id']
        self.assertEqual((target / 'previous.txt').read_text(), 'original version')
        self.assertFalse((target / 'SessionLens' / 'SessionLens.exe').exists())

    def test_fingerprint_matches_python_with_utf8_and_cache_url(self):
        recipe = {**RECIPE, 'name': '会话记录 SessionLens', 'officialUrl': RECIPE['url'],
                  'url': 'https://cache.example.com/packages/' + RECIPE['sha256']}
        path = self.root / 'recipe.json'
        path.write_text(json.dumps(recipe), encoding='utf-8')
        result = self.run_ps('$recipe=Get-Content -LiteralPath ' + ps_quote(path)
                             + ' -Raw | ConvertFrom-Json\nGet-AgentPairPortableFingerprint $recipe')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), portable_recipe_fingerprint(recipe))

    @unittest.skipUnless(os.name == 'nt', 'Native process handle regression is Windows-specific')
    def test_self_test_obtains_exit_code_from_real_local_windowed_fixture(self):
        compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
        if not compiler.is_file():
            self.skipTest('Windows .NET Framework compiler is unavailable')
        source = self.root / 'Fixture.cs'
        binary = self.root / 'SelfTestFixture.exe'
        source.write_text('class Fixture { static int Main(string[] args) { '
                          'return args.Length == 1 && args[0] == "--self-test" ? 0 : 9; } }')
        compiled = subprocess.run([str(compiler), '/nologo', '/target:winexe', '/out:' + str(binary), str(source)],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        result = self.run_ps('Invoke-AgentPairPortableSelfTest ' + ps_quote(binary) + ' ' + ps_quote(digest)
                             + ' ' + ps_quote(self.root) + ' {param($value)} | ConvertTo-Json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), 0)


if __name__ == '__main__':
    unittest.main()
