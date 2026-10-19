"""Eski migrasyon geçmişinin durumunu gösterir; veritabanını değiştirmez."""
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory


if __name__ == "__main__":
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    print("Mevcut migrasyon uçları: " + ", ".join(heads))
    print("Eski otomatik stamp/migrate/upgrade akışı kaldırıldı; veritabanı değiştirilmedi.")
    if len(heads) != 1:
        print("Birden çok uç var. Eski veriyi taşımadan önce migrasyon geçmişi birleştirilmeli ve yedek kopyada denenmeli.")
