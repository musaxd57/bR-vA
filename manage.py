"""Briva geliştirme kopyası için boş veritabanı ve ilk kullanıcı kurulumu."""
import argparse
import getpass
import os
from pathlib import Path
import secrets
import json

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent


def data_path():
    load_dotenv(BASE_DIR / ".env")
    return Path(os.environ.get("BRIVA_DATA_DIR") or BASE_DIR / "instance").expanduser().resolve() / "data.db"


def init_local():
    if data_path().exists():
        raise SystemExit("Bu dizinde veritabanı zaten var; değiştirilmedi. Farklı bir BRIVA_DATA_DIR kullanın.")
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("SECRET_KEY=" + secrets.token_urlsafe(48) + "\nBRIVA_DATA_DIR=\nGROQ_API_KEY=\n")
    from app import app, db
    with app.app_context():
        db.create_all()
    print("Boş geliştirme veritabanı oluşturuldu. Eski veriler içe aktarılmadı.")
    print("İlk kullanıcı: python manage.py create-admin --username isa --company briva")


def create_admin(username, company):
    if not data_path().exists():
        raise SystemExit("Önce python manage.py init çalıştırın.")
    username, company = username.strip(), company.strip()
    if not username or not company or len(username) > 100 or len(company) > 100:
        raise SystemExit("Kullanıcı ve şirket alanları 1–100 karakter olmalı.")
    from app import app
    from models import User
    from db_utils import add_user
    with app.app_context():
        if User.query.filter_by(username=username).first():
            raise SystemExit("Bu kullanıcı zaten var; hesabı değiştirilmedi.")
        from briva_security import validate_password, SecurityValidationError
        password = getpass.getpass("Yeni parola (en az 12 karakter; yazarken görünmez): ")
        try:
            validate_password(password)
        except SecurityValidationError as error:
            raise SystemExit(str(error)) from None
        if password != getpass.getpass("Parolayı tekrar yazın: "):
            raise SystemExit("Parolalar aynı değil; kullanıcı oluşturulmadı.")
        add_user(username=username, password=password, company=company, role="admin")
    print("Yönetici oluşturuldu. Başlatmak için: python app.py")


def upgrade_local():
    """Add missing v2 tables; never drop, rewrite or migrate historical rows."""
    if not data_path().is_file():
        raise SystemExit("Önce python manage.py init çalıştırın.")
    from app import app, db
    from briva_core import create_core_schema
    from briva_totp import create_auth_schema
    from briva_media import create_media_schema
    from sqlalchemy import inspect
    with app.app_context():
        before = set(inspect(db.engine).get_table_names())
        if 'user' not in before:
            raise SystemExit('Briva kullanıcı tablosu bulunamadı; veritabanı değiştirilmedi.')
        tables = create_core_schema() + create_auth_schema() + create_media_schema()
        created = [name for name in tables if name not in before]
    print(f"Stok, hesap güvenliği ve ürün fotoğrafları hazır: {len(tables)} tablo, {len(created)} yeni tablo.")
    print("Eski ürün, stok ve cari kayıtları değiştirilmedi; yeni ürünler sıfır stokla açılır.")


def serve_local(port):
    if not 1024 <= port <= 65535:
        raise SystemExit("Yerel port 1024–65535 aralığında olmalı.")
    if not data_path().is_file():
        raise SystemExit("Önce python manage.py init çalıştırın.")
    from app import app
    app.run(host="127.0.0.1", port=port, debug=False)


def configure_local_auth():
    """Provision encryption at rest without enabling MFA or printing its key."""
    env_path = BASE_DIR / '.env'
    if not env_path.is_file():
        raise SystemExit('Önce python manage.py init çalıştırın.')
    from dotenv import dotenv_values
    from cryptography.fernet import Fernet
    configured = os.environ.get('BRIVA_AUTH_ENCRYPTION_KEY') or dotenv_values(env_path).get('BRIVA_AUTH_ENCRYPTION_KEY')
    if configured:
        try:
            Fernet(configured.encode('ascii'))
        except (ValueError, TypeError, UnicodeError):
            raise SystemExit('Mevcut hesap şifreleme anahtarı geçersiz; değiştirilmedi.') from None
        print('Hesap şifreleme anahtarı zaten var; korundu.')
        return
    key = Fernet.generate_key().decode('ascii')
    with env_path.open('a', encoding='utf-8') as handle:
        handle.write('\nBRIVA_AUTH_ENCRYPTION_KEY=' + key + '\n')
    print('Hesap şifreleme anahtarı yerel .env dosyasına eklendi. İki aşamalı doğrulama henüz açılmadı.')
    print('Anahtarı veritabanı yedeğinden ayrı, güvenli bir yerde koruyun. Sunucuyu yeniden başlatın.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Yeni ve boş yerel veritabanı oluşturur.")
    commands.add_parser("upgrade-local", help="Eksik yeni stok tablolarını veri silmeden oluşturur.")
    commands.add_parser('configure-local-auth', help='Yerel MFA şifreleme anahtarını hesabı değiştirmeden hazırlar.')
    commands.add_parser('backup-local', help='Yerel SQLite dosyasının üzerine yazmadan yeni tutarlı yedek alır.')
    verify = commands.add_parser('verify-backup', help='Yedeği salt okunur inceleyerek bütünlüğünü doğrular.')
    verify.add_argument('--path', required=True, type=Path)
    restore = commands.add_parser('restore-check', help='Yedeği yeni test klasörüne açar; asıl veritabanını değiştirmez.')
    restore.add_argument('--path', required=True, type=Path)
    serve = commands.add_parser("serve-local", help="Yalnız bu bilgisayarda yerel sunucuyu başlatır.")
    serve.add_argument('--port', type=int, default=5000)
    create = commands.add_parser("create-admin", help="Parolayı terminalde sorarak yönetici oluşturur.")
    create.add_argument("--username", required=True)
    create.add_argument("--company", required=True)
    args = parser.parse_args()
    if args.command == "init":
        init_local()
    elif args.command == "create-admin":
        create_admin(args.username, args.company)
    elif args.command == "upgrade-local":
        upgrade_local()
    elif args.command == "serve-local":
        serve_local(args.port)
    elif args.command == 'configure-local-auth':
        configure_local_auth()
    elif args.command in {'backup-local', 'verify-backup', 'restore-check'}:
        from briva_backup import backup_local, inspect_snapshot, restore_check
        if args.command == 'backup-local':
            report = backup_local(data_path(), BASE_DIR)
        elif args.command == 'verify-backup':
            report = inspect_snapshot(args.path)
        else:
            report = restore_check(args.path, BASE_DIR)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not report['ok']:
            raise SystemExit('Kontrol sorun buldu; kayıtlar otomatik değiştirilmedi.')


if __name__ == "__main__":
    main()
