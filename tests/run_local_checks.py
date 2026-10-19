"""Run the local checks in isolated processes and retain all evidence.

Flask app imports cannot share one process with a different fixture database.
This runner intentionally never removes test data or previous reports.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT / '.verification-data' / ('local-checks-' + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    commands = [('core', [sys.executable, 'tests/run_core_checks.py'])]
    for pattern in ('test_local_setup.py', 'test_legacy_integrity.py', 'test_legacy_audit.py', 'test_current_route_policy.py',
                    'test_security_hooks.py', 'test_security_actual_app.py',
                    'test_auth_sessions_totp.py', 'test_auth_login_limits.py',
                    'test_backup_local.py', 'test_end_to_end_http.py',
                    'test_media_http.py', 'test_documents_http.py'):
        commands.append((pattern.removesuffix('.py'), [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern]))
    commands.append(('javascript', ['node', '--test', '--test-isolation=none',
        'tests/currency_regression.cjs', 'tests/security_dom_regression.cjs', 'tests/workbench_state.cjs',
        'tests/product_photos_state.cjs']))
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'checks': [],
        'limitations': ['Synthetic local SQLite data; no PostgreSQL claim.',
                        'No physical printer, scanner or production deployment verification.',
                        'Test counts reflect checks, not a guarantee of absence of defects.']}
    for name, command in commands:
        started = time.monotonic()
        print('Running ' + name, flush=True)
        try:
            result = subprocess.run(command, cwd=ROOT, env=environment, text=True,
                                    encoding='utf-8', errors='replace', capture_output=True, timeout=180)
            output = result.stdout + result.stderr
            count = re.search(r'Ran (\d+) tests?', output) if name != 'javascript' else re.search(r'ℹ tests (\d+)', output)
            entry = {'name': name, 'exit_code': result.returncode, 'ok': result.returncode == 0,
                     'test_count': int(count.group(1)) if count else None,
                     'seconds': round(time.monotonic() - started, 3)}
            (directory / (name + '.stdout.txt')).write_text(result.stdout, encoding='utf-8')
            (directory / (name + '.stderr.txt')).write_text(result.stderr, encoding='utf-8')
            if result.returncode:
                print(output[-6000:], flush=True)
        except (OSError, subprocess.TimeoutExpired) as error:
            entry = {'name': name, 'ok': False, 'error': str(error),
                     'seconds': round(time.monotonic() - started, 3)}
        report['checks'].append(entry)
        print(name + ': ' + ('PASS' if entry['ok'] else 'FAIL'), flush=True)
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        report['ok'] = all(row['ok'] for row in report['checks'])
        report['test_count'] = sum(row.get('test_count') or 0 for row in report['checks'])
        (directory / 'result.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Evidence: ' + str(directory / 'result.json'), flush=True)
    print('Tests: ' + str(report['test_count']), flush=True)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
