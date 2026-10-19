# Briva Envanter — GitHub için hazırlanmış geliştirme kopyası

Bu paket, sağlanan `envaner.rar` içindeki Flask uygulamasından hazırlanmıştır. Mevcut HTML şablonları, CSS, JavaScript, Python kaynakları ve migrasyon dosyaları korunmuştur. İlk kurulum ve doğrulanmış birkaç hata düzeltilmiştir.

**Durum:** Yerel geliştirme başlangıcıdır. Canlı mağazaların stok ve cari kayıtlarını devralacak tamamlanmış sürüm değildir. GitHub'a yüklemek, uygulamayı internette çalıştırmaz; barındırma ve gerçek veri geçişi ayrı adımlardır.

## 1. GitHub'a yükleme

ZIP'i bilgisayarınızda açın. İçindeki `briva-envanter` klasöründe `app.py`, `requirements.txt`, `README.md`, `templates` ve `static` görünmelidir. Git deposunun kökünde de bu dosyalar bulunmalıdır.

### Tarayıcıdan, iki küçük yüklemeyle

GitHub'ın tarayıcı yüklemesi dosya başına 25 MiB ve tek işlemde en fazla 100 dosya kabul eder. Bu paketteki tüm dosyalar boyut sınırının çok altındadır; toplam dosya sayısı nedeniyle iki işlem kullanın.

1. Boş bir **private** GitHub deposunu açın. Henüz yoksa `briva-envanter` adıyla oluşturabilirsiniz. Mevcut projenizde başka kodlar varsa önce ayrı bir dal veya yeni depo kullanın.
2. **Add file → Upload files** bölümünü açın. Boş depoda bunun yerine **uploading an existing file** bağlantısı görünebilir.
3. Bilgisayardaki `briva-envanter` klasörünün içine girin. **`migrations` klasörü hariç** dosya ve klasörleri seçip yükleme alanına sürükleyin. ZIP dosyasını veya dıştaki `briva-envanter` klasörünü seçmeyin.
4. Açıklamaya `Briva kaynak kodu ve yerel kurulum` yazıp değişikliği kaydedin.
5. Tekrar **Add file → Upload files** açın. Bu kez yalnız `migrations` klasörünü sürükleyip kaydedin. Klasörün içindekileri ayrı ayrı köke taşımayın.
6. Depoda `app.py`, `requirements.txt`, `README.md`, `static/`, `templates/`, `migrations/` göründüğünü kontrol edin. `.gitignore` ve `.env.example` da paketin parçasıdır.

Kurulumdan sonra oluşacak gerçek `.env`, `instance/data.db`, günlük ve ürün yüklemeleri kaynak pakete dahil değildir. Tarayıcıdan manuel yüklerken bilgisayardaki bu yeni çalışma dosyalarını ayrıca seçmeyin; `.gitignore` yerel Git iş akışını korur.

### Düzenli geliştirme için GitHub Desktop

1. [GitHub Desktop](https://desktop.github.com/download/) kurup hesabınıza giriş yapın.
2. GitHub'da deponuz zaten varsa **File → Clone repository** ile onu bilgisayara alın. Yeni başlayacaksanız **File → New repository** ile `briva-envanter` adlı yerel depo oluşturun.
3. ZIP'ten çıkan klasörün **içeriğini**, Desktop'ın oluşturduğu depo klasörüne kopyalayın. `app.py` doğrudan deponun kökünde olmalı; iç içe ikinci bir proje klasörü oluşmamalı.
4. Desktop'taki değişiklikleri inceleyip `Briva kaynak kodu ve yerel kurulum` mesajıyla **Commit to …** seçin.
5. Yeni depoda **Publish repository** seçip **Keep this code private** kutusunu işaretli bırakın. Önceden klonlanmış depoda **Push origin** kullanın.

Paketin eski Git geçmişi dahil edilmedi. Daha önce başka bir depoya eklediğiniz büyük dosya veya anahtar varsa, bu yeni paket o deponun geçmişini kendiliğinden temizlemez.

Resmî kaynaklar: [Dosya sınırları](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github), [tarayıcıdan dosya ekleme](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository), [GitHub Desktop ile başlangıç](https://docs.github.com/en/desktop/overview/creating-your-first-repository-using-github-desktop).

## 2. Bilgisayarda ilk çalıştırma

Python 3.11 veya 3.12 kullanın. Komutları `app.py` dosyasının bulunduğu klasörde çalıştırın. Bağımlılık kurulumu internet bağlantısı gerektirir. Node.js yalnız JavaScript testini çalıştırmak için gerekir; uygulamanın normal çalışması Python üzerindedir.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py init
.\.venv\Scripts\python.exe manage.py create-admin --username isa --company briva
.\.venv\Scripts\python.exe app.py
```

Python 3.12 kuruluysa ilk komutta `-3.11` yerine `-3.12` kullanın. Sanal ortamı etkinleştirme veya PowerShell güvenlik ayarını değiştirme gerekmez.

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py init
.venv/bin/python manage.py create-admin --username isa --company briva
.venv/bin/python app.py
```

`create-admin` parolayı terminalde iki kez sorar. Yazarken karakterlerin görünmemesi normaldir. En az 10 karakter kullanın. `isa` ve `briva` örnek kurulum alanlarıdır; kullanıcı adı ve şirket kodunu ihtiyacınıza göre değiştirebilirsiniz. Merter ve Galata'yı bu aşamada farklı şirketler olarak açmak zorunda değilsiniz; şirket/lokasyon modeli ayrıca ele alınacaktır.

Tarayıcıda **http://127.0.0.1:5000/login** adresini açıp oluşturduğunuz kullanıcıyla giriş yapın. Bu başlatma komutu yalnız kendi bilgisayarınızdaki erişime açıktır ve debug kapalıdır.

### İlk kurulumun yaptığı işlemler

- `manage.py init`, yoksa rastgele oturum anahtarlı bir `.env` oluşturur. Anahtar kaynak kodda veya örnek ayar dosyasında bulunmaz.
- Model tanımlarından yeni ve boş bir `instance/data.db` oluşturur; demo hesap, ürün veya müşteri eklemez.
- Aynı dizinde veritabanı zaten varsa işlemi durdurur ve onu değiştirmez.
- Uygulamayı import etmek artık kendiliğinden tablo oluşturmaz.
- `BRIVA_DATA_DIR` ortam değişkeniyle başka bir veri dizini kullanılabilir. Varsayılan `instance/` dizinidir.

`.env.example` bir açıklama dosyasıdır. Normal ilk kurulumda bunu elle `.env` olarak kopyalamak gerekmez. Elle kopyalarsanız boş `SECRET_KEY` alanını rastgele, en az 32 karakterlik bir değerle doldurmalısınız.

## 3. Paket içinde ne var?

| Bölüm | İçerik |
|---|---|
| `app.py`, `db_utils.py`, `models.py`, `extensions.py` | Mevcut Flask uygulaması ve veri modeli |
| `templates/`, `static/` | Mevcut ekranlar, 14 CSS dosyası ve 14 JavaScript dosyası |
| `fonts/` | PDF'lerde kullanılan iki font ve arşivden çıkarılmış DejaVu lisansı |
| `migrations/` | Eski migrasyon geçmişi; otomatik uygulanmaz |
| `manage.py` | Boş yerel veritabanı ve ilk yönetici kurulumu |
| `requirements.txt`, `requirements-ai.txt` | Temel ve opsiyonel AI bağımlılıkları |
| `.env.example`, `.gitignore` | Örnek ayarlar ve yerel dosyaları Git dışında tutma kuralları |
| `tests/` | Para birimi ve yerel uygulama regresyonları |
| `DURUM.md` | Yapılan değişiklikler, doğrulama kapsamı ve kalan işler |

Orijinal veritabanı, ürün fotoğraf yüklemeleri, Excel dosyaları, `.env`, loglar, font ZIP'i ve eski `.git` klasörü pakete konmadı. Bunlar orijinal arşivde kalır. **Yeni kurulumu açınca eski stokların ve ürün fotoğraflarının görünmemesi bu nedenle beklenir.** Mevcut arşivi veri geçişi için saklayın.

Bazı görsel kaynaklar ve tarayıcı kütüphaneleri orijinaldeki gibi CDN'den gelir; arayüzün tamamı için internet bağlantısı gerekir.

## 4. Doğrulama

Bağımlılıkları kurduktan sonra:

```bash
python tests/test_local_setup.py
node tests/currency_regression.cjs
```

Windows'ta ilk komut için `.\.venv\Scripts\python.exe`, macOS/Linux'ta `.venv/bin/python` kullanabilirsiniz. İki dosyada toplam sekiz regresyon testi vardır. Python testleri geçici sentetik veritabanı kullanır ve dış ağ erişimini engeller; mevcut işletme verisini açmaz. JavaScript testleri gerçek handler fonksiyonlarını sahte DOM ile çalıştırır; tarayıcı görünüm testi değildir.

## 5. Eski veri ve migrasyonlar

Mevcut migrasyon geçmişinde üç uç bulunuyor: `209d329dc4c8`, `677eeb1cf3ad`, `91d9647e4196`. Bu nedenle `manage.py init` eski migrasyonları çalıştırarak kurulum yapmaz; güncel modellerden boş geliştirme veritabanı üretir. `alembic_version` geçmişi de otomatik damgalanmaz.

`python update.py` yalnız migrasyon durumunu gösterir. Eski dosyadaki otomatik `stamp head → migrate → upgrade` zinciri kaldırıldı. Gerçek veriyi taşımadan önce geçmişin nasıl birleştirileceği belirlenmeli, dönüşüm bir yedek üzerinde denenmeli ve stok/cari toplamları karşılaştırılmalıdır. Orijinal veritabanını yeni boş dosyanın üzerine kopyalamak bu çalışmanın yerine geçmez.

## 6. AI modülü

Normal yerel kurulum `GROQ_API_KEY` gerektirmez. Anahtar boşken uygulama açılır ve AI servisi devre dışıdır. Eski AI kaynakları korunmuştur; dış API çağrıları bu paketin doğrulamasına dahil değildir. Etkinleştirme öncesinde `requirements-ai.txt` bağımlılığı ayrıca kurulmalı, sürümü sabitlenmeli ve stok/satış araçlarının kullanıcı yetkileri tamamlanmalıdır.

## 7. Bir sonraki geliştirme sırası

Öncelik, ürün → stok → sipariş → satış → iade akışının tutarlılığıdır. Bunun üzerinde çoklu lokasyon, barkod, müşteri/cari ve yönetim raporları geliştirilebilir. Gerçek satış verisi güvenilir hâle geldikten sonra ajans ölçümü ve müşteri kazanım kanalları bağlanmalıdır. Detaylı kalan teknik işler `DURUM.md` içindedir.
