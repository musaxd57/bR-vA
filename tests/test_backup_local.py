"""Real retained SQLite snapshots; no app import, cleanup or file deletion."""
from pathlib import Path
import sqlite3
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from briva_backup import backup_local, copy_snapshot, inspect_snapshot, restore_check


class LocalBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / '.verification-data' / ('backup-test-' + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.source = self.directory / 'source.db'
        with sqlite3.connect(self.source) as connection:
            connection.execute('CREATE TABLE retained_note (id INTEGER PRIMARY KEY, note TEXT NOT NULL)')
            connection.execute('INSERT INTO retained_note(note) VALUES (?)', ('Sentetik kayıt',))

    def test_live_sqlite_backup_and_restoration_retain_all_files(self):
        report = backup_local(self.source, self.directory)
        self.assertTrue(report['ok'])
        self.assertTrue(self.source.is_file())
        restored = restore_check(report['path'], self.directory)
        self.assertTrue(restored['ok'])
        with sqlite3.connect(restored['path']) as connection:
            self.assertEqual(connection.execute('SELECT note FROM retained_note').fetchone()[0], 'Sentetik kayıt')
        self.assertTrue(Path(report['path']).is_file())
        self.assertTrue(Path(report['path'] + '.report.json').is_file())

    def test_existing_destination_is_never_replaced(self):
        destination = self.directory / 'existing.db'
        destination.write_bytes(b'KEEP-EXISTING-FILE')
        with self.assertRaises(FileExistsError):
            copy_snapshot(self.source, destination)
        self.assertEqual(destination.read_bytes(), b'KEEP-EXISTING-FILE')

    def test_same_path_and_missing_source_do_not_mutate_source(self):
        original = self.source.read_bytes()
        with self.assertRaises(ValueError):
            copy_snapshot(self.source, self.source)
        with self.assertRaises(ValueError):
            copy_snapshot(self.directory / 'missing.db', self.directory / 'new.db')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse((self.directory / 'new.db').exists())

    def test_inventory_ledger_discrepancy_is_reported_without_repair(self):
        with sqlite3.connect(self.source) as connection:
            connection.execute('CREATE TABLE briva_stock_balance (company_id TEXT, variant_id INTEGER, location_id INTEGER, quantity INTEGER, reserved INTEGER, packaged INTEGER)')
            connection.execute('CREATE TABLE briva_stock_movement (company_id TEXT, variant_id INTEGER, location_id INTEGER, quantity_delta INTEGER, reserved_delta INTEGER, packaged_delta INTEGER)')
            connection.execute("INSERT INTO briva_stock_balance VALUES ('SYNTHETIC',1,1,7,0,0)")
            connection.execute("INSERT INTO briva_stock_movement VALUES ('SYNTHETIC',1,1,5,0,0)")
        report = inspect_snapshot(self.source)
        self.assertFalse(report['ok'])
        self.assertEqual(report['inventory_mismatch_count'], 1)
        with sqlite3.connect(self.source) as connection:
            self.assertEqual(connection.execute('SELECT quantity FROM briva_stock_balance').fetchone()[0], 7)

    def test_snapshot_captures_committed_state_while_another_connection_is_open(self):
        connection = sqlite3.connect(self.source)
        self.addCleanup(connection.close)
        connection.execute("INSERT INTO retained_note(note) VALUES ('İkinci kayıt')")
        connection.commit()
        report = backup_local(self.source, self.directory)
        with sqlite3.connect(report['path']) as target:
            self.assertEqual(target.execute('SELECT COUNT(*) FROM retained_note').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
