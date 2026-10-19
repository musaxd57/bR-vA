"""Create a reviewable manual source ZIP without accessing a GitHub account."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent
DIRECTORIES = {'briva_core', 'templates', 'static', 'fonts', 'migrations', 'tests', 'docs'}
DENIED_PARTS = {'instance', 'uploads', '.git', '__pycache__', 'node_modules',
                '.verification-data', '.analysis-evidence', '.local-backups', '.local-exports'}
DENIED_EXTENSIONS = {'.zip', '.rar', '.7z', '.db', '.sqlite', '.sqlite3', '.log', '.pyc', '.pyo', '.pdf'}
TEXT_EXTENSIONS = {'.py', '.js', '.html', '.css', '.txt', '.md', '.json', '.ini', '.mako', '.cmd', '.toml', '.yml', '.yaml'}
SECRET = re.compile(rb'(?:AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-|live_)[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def source_paths():
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                            cwd=ROOT, check=True, capture_output=True)
    selected = []
    for name in sorted(set(result.stdout.decode('utf-8').split('\0')) - {''}):
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or set(relative.parts) & DENIED_PARTS:
            continue
        if relative.name.startswith('.env') and relative.name != '.env.example':
            continue
        if relative.suffix.lower() in DENIED_EXTENSIONS:
            continue
        if len(relative.parts) > 1 and relative.parts[0] not in DIRECTORIES:
            continue
        if len(relative.parts) == 1 and relative.suffix.lower() not in TEXT_EXTENSIONS and relative.name not in {'.gitignore', '.env.example'}:
            continue
        path = ROOT / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError('Source path is not a regular workspace file: ' + relative.as_posix())
        selected.append(relative)
    return selected


def main():
    files = source_paths()
    if not {Path('app.py'), Path('briva_core/service.py'), Path('static/js/workbench.js'), Path('.env.example')} <= set(files):
        raise SystemExit('Required source files are missing; no package created.')
    payloads, entries = {}, []
    for relative in files:
        raw = (ROOT / relative).read_bytes()
        if (relative.suffix.lower() in TEXT_EXTENSIONS or relative.name.startswith('.')) and SECRET.search(raw):
            raise SystemExit('A possible embedded credential needs review in: ' + relative.as_posix())
        name = 'bR-vA/' + relative.as_posix()
        payloads[name] = raw
        entries.append({'path': relative.as_posix(), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    manifest = {'created_utc': datetime.now(timezone.utc).isoformat(), 'purpose': 'Manual GitHub source upload; no account access.',
        'files': entries, 'file_count': len(entries),
        'excluded': ['Real .env/keys', 'Databases and uploads', 'Test evidence and backups', 'Git history', 'Virtual environments and caches', 'Original archives and unrelated private documents'],
        'verification': 'Every ZIP entry compared byte-for-byte with its captured source and SHA256.'}
    payloads['bR-vA/EXPORT-MANIFEST.json'] = json.dumps(manifest, indent=2, ensure_ascii=False).encode('utf-8')
    directory = ROOT / '.local-exports'
    directory.mkdir(exist_ok=True)
    name = 'BRIVA-kaynak-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.zip'
    target = directory / name
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in payloads.items():
            archive.writestr(name, raw)
    with zipfile.ZipFile(target) as archive:
        if set(archive.namelist()) != set(payloads):
            raise SystemExit('ZIP manifest mismatch; inspect the retained package.')
        for name, raw in payloads.items():
            if archive.read(name) != raw:
                raise SystemExit('ZIP content verification failed; package retained for review.')
    result = {'path': str(target), 'bytes': target.stat().st_size, 'source_file_count': len(entries),
        'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'verified': True,
        'github_connection': False, 'pushed': False}
    with target.with_suffix('.manifest.json').open('x', encoding='utf-8') as handle:
        json.dump({'package': result, 'manifest': manifest}, handle, indent=2, ensure_ascii=False)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
