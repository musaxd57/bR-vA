"""Start a fresh, retained browser test fixture; never use the real local data."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5010)
    parser.add_argument('--resume', type=Path, help='A previously retained browser fixture directory.')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port == 5000:
        raise SystemExit('Test portu 1024–65535 aralığında ve asıl 5000 portundan farklı olmalı.')
    if args.resume:
        directory = args.resume.resolve()
        boundary = (ROOT / '.verification-data').resolve()
        if directory.parent != boundary or not directory.name.startswith('browser-'):
            raise SystemExit('Yalnız bu projenin browser test dizini yeniden açılabilir.')
        if not (directory / 'data.db').is_file() or not (directory / 'fixture.json').is_file():
            raise SystemExit('Önceki test veritabanı ve fixture bilgisi bulunamadı.')
    else:
        directory = ROOT / '.verification-data' / ('browser-' + uuid.uuid4().hex)
        directory.mkdir(parents=True, exist_ok=False)
    # Only synthetic test encryption; never inherit the real owner's key.
    from cryptography.fernet import Fernet
    key_path = directory / 'auth-encryption.key'
    if key_path.exists():
        encryption_key = key_path.read_text(encoding='ascii').strip()
        Fernet(encryption_key.encode('ascii'))
    else:
        encryption_key = Fernet.generate_key().decode('ascii')
        with key_path.open('x', encoding='ascii') as handle:
            handle.write(encryption_key)
    os.environ.update({
        'BRIVA_DATA_DIR': str(directory),
        'SECRET_KEY': secrets.token_urlsafe(48),
        'GROQ_API_KEY': '',
        'PYTHON_DOTENV_DISABLED': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        'BRIVA_ALLOW_LEGACY_WRITES': '0',
        'BRIVA_AUTH_ENCRYPTION_KEY': encryption_key,
    })
    from app import app, db
    from models import User
    from briva_security import PERMISSIONS
    if args.resume:
        metadata = json.loads((directory / 'fixture.json').read_text(encoding='utf-8'))
        username, password = metadata['username'], metadata['password']
    else:
        username = 'browser_test_owner'
        password = 'Local-test-' + secrets.token_hex(8)
    with app.app_context():
        if Path(db.engine.url.database).resolve() != (directory / 'data.db').resolve():
            raise SystemExit('Test dışındaki veritabanına erişim reddedildi.')
        db.create_all()
        if not args.resume:
            owner = User(username=username, company='BROWSER_SYNTHETIC', role='admin',
                         **{permission: True for permission in PERMISSIONS})
            owner.set_password(password)
            db.session.add(owner)
            db.session.commit()
        elif not User.query.filter_by(username=username, company='BROWSER_SYNTHETIC').first():
            raise SystemExit('Sentetik test kullanıcısı bulunamadı; hesap değiştirilmedi.')
    metadata = {'url': f'http://127.0.0.1:{args.port}', 'username': username,
                'password': password, 'data_directory': str(directory),
                'purpose': 'Synthetic browser fixture only; retained, no cleanup.'}
    if not args.resume:
        with (directory / 'fixture.json').open('x', encoding='utf-8') as handle:
            json.dump(metadata, handle, indent=2)
    print(json.dumps(metadata), flush=True)
    app.run(host='127.0.0.1', port=args.port, debug=False)


if __name__ == '__main__':
    main()
