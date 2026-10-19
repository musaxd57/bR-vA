"""Briva geliştirme kopyası için boş veritabanı ve ilk kullanıcı kurulumu."""
import argparse
import getpass
import os
from pathlib import Path
import secrets

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
        password = getpass.getpass("Yeni parola (en az 10 karakter; yazarken görünmez): ")
        if len(password) < 10:
            raise SystemExit("Parola en az 10 karakter olmalı.")
        if password != getpass.getpass("Parolayı tekrar yazın: "):
            raise SystemExit("Parolalar aynı değil; kullanıcı oluşturulmadı.")
        add_user(username=username, password=password, company=company, role="admin")
    print("Yönetici oluşturuldu. Başlatmak için: python app.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Yeni ve boş yerel veritabanı oluşturur.")
    create = commands.add_parser("create-admin", help="Parolayı terminalde sorarak yönetici oluşturur.")
    create.add_argument("--username", required=True)
    create.add_argument("--company", required=True)
    args = parser.parse_args()
    if args.command == "init":
        init_local()
    elif args.command == "create-admin":
        create_admin(args.username, args.company)


if __name__ == "__main__":
    main()
