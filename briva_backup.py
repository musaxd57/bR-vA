"""Non-destructive local SQLite snapshots and restoration checks.

No function deletes, replaces, or repairs a file. Encryption/session keys are
not copied into database backups and must be preserved separately.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid


def _readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('Veritabanı dosyası bulunamadı.')
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)


def inspect_snapshot(path):
    path = Path(path).resolve()
    with closing(_readonly(path)) as connection:
        integrity = [row[0] for row in connection.execute('PRAGMA integrity_check')]
        foreign_key_issues = connection.execute('PRAGMA foreign_key_check').fetchall()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        mismatches = []
        if {'briva_stock_balance', 'briva_stock_movement'}.issubset(tables):
            actual = {(row[0], row[1], row[2]): tuple(row[3:]) for row in connection.execute(
                'SELECT company_id, variant_id, location_id, quantity, reserved, packaged FROM briva_stock_balance')}
            expected = {(row[0], row[1], row[2]): tuple(row[3:]) for row in connection.execute(
                'SELECT company_id, variant_id, location_id, SUM(quantity_delta), SUM(reserved_delta), SUM(packaged_delta) '
                'FROM briva_stock_movement GROUP BY company_id, variant_id, location_id')}
            for key in sorted(set(actual) | set(expected)):
                balance, ledger = actual.get(key, (0, 0, 0)), expected.get(key, (0, 0, 0))
                invalid = min(balance) < 0 or balance[0] < balance[1] + balance[2]
                if balance != ledger or invalid:
                    mismatches.append({'variant_id': key[1], 'location_id': key[2],
                                       'balance': balance, 'ledger': ledger, 'invalid_balance': invalid})
        else:
            actual = None
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return {
        'path': str(path), 'bytes': path.stat().st_size, 'sha256': digest.hexdigest(),
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'sqlite_integrity_ok': integrity == ['ok'], 'table_count': len(tables),
        'foreign_key_issue_count': len(foreign_key_issues),
        'inventory_checked': actual is not None, 'inventory_balance_count': len(actual) if actual is not None else None,
        'inventory_mismatch_count': len(mismatches), 'inventory_mismatches': mismatches[:100],
        'ok': integrity == ['ok'] and not foreign_key_issues and not mismatches,
        'limitations': ['Database file only; MFA encryption key and SECRET_KEY are not included.',
                        'No filesystem attachments or external database are restored by this command.',
                        'Inventory allocations and account business rules require the application health tests.'],
    }


def copy_snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError('Kaynak ve hedef aynı olamaz.')
    if not destination.parent.is_dir():
        raise ValueError('Hedef üst klasörü mevcut olmalı.')
    # O_EXCL protects an existing database even if another process creates it
    # between validation and opening. Failure leaves its new artifact retained.
    with closing(_readonly(source)) as original:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(destination, timeout=30)) as target:
            original.backup(target)
    report = inspect_snapshot(destination)
    report['source_path'] = str(source)
    with destination.with_name(destination.name + '.report.json').open('x', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    return report


def backup_local(source, root):
    destination_dir = Path(root).resolve() / '.local-backups'
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    return copy_snapshot(source, destination_dir / f'data-{stamp}-{uuid.uuid4().hex[:8]}.db')


def restore_check(source, root):
    directory = Path(root).resolve() / '.verification-data' / ('restore-' + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    return copy_snapshot(source, directory / 'data.db')
