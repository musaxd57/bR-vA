# BRIVA — yerel mağaza ve depo uygulaması

Flask/Python uygulaması; model, renk, beden ve konum bazında ortak stok ve cari defteri kullanır. Yeni katalog **sıfır stokla** başlar. Excel içe aktarımı başlangıç iş akışının parçası değildir.

## Bu bilgisayarda açma

`start-local.cmd` dosyasını çalıştırın. Çalışan ortam `.venv-local`, adres `http://127.0.0.1:5000/workbench` olur. Zaten çalışan 5000 sunucusunun yanına ikinci sunucu açmayın.

PowerShell alternatifi:

```powershell
.\.venv-local\Scripts\python.exe manage.py upgrade-local
.\.venv-local\Scripts\python.exe app.py
```

`upgrade-local` yalnız eksik yeni tabloları oluşturur; eski satırları taşımaya, silmeye veya stokları yeniden hesaplayıp yazmaya çalışmaz. Gerçek ve sentetik test veritabanları ayrıdır. Bu makinenin çalışma ortamı ayrıntıları `YEREL-KURULUM.txt` içindedir.

## Baştan yeni kurulum

Python 3.11–3.12 ile ayrı sanal ortam hazırlayın, `requirements.txt` kurun, sonra:

```text
python manage.py init
python manage.py create-admin --username KULLANICI --company MARKA
python manage.py configure-local-auth
python app.py
```

`init` mevcut veritabanının üzerine yazmayı reddeder. Yönetici parolası terminalde iki kez sorulur; 12–256 karakter olmalıdır. `.env.example` yalnız boş örnek ayarlardır; gerçek anahtar içermez. MFA anahtarı hazırlamak iki aşamalı doğrulamayı açmaz. İşletme sahibi `/account-security` ekranından doğrulayıcı uygulamasını kendisi kurar.

## Günlük iş akışı

1. Aynı şirket altında Merter/Galata mağaza, depo ve alt raflarını tanımlayın.
2. Modelin renklerini ve bedenlerini topluca oluşturun. Her varyantın barkodu oluşur; stok hâlâ sıfırdır.
3. Örneğin S:1 / M:2 / L:2 / XL:1 seri reçetesini tanımlayın. Reçetenin bileşimi sonradan değişmez; yeni dağılım yeni sürümdür.
4. Her kıyafete kendi renk/beden etiketi yapıştırın. Tek gömleğin poşeti seri paketi sayılmaz. Çok parçalı fiziksel paket ayrı tanımlanır.
5. Atölye planı ve etiket basımı stok oluşturmaz. Mal kabulde gerçekten gelen beden miktarlarını girin; tam seriyi çarpanla, teklemeleri beden tablosuyla birlikte kaydedin.
6. Birim barkodu bir parçayı, seri barkodu reçete bileşenlerini, fiziksel paket barkodu kendi içeriğini temsil eder. Satış bunları aynı beden/konum stoklarından düşer.
7. Müşterinin özel beden karışımı tekli satırlar olarak eklenebilir. Fiyatlar parça başınadır; işletme sahibi gerekçeyle fiyat değiştirir, satıştaki fiyat korunur.
8. Çok raflı görev stokları gerçek raflarda rezerve eder. Önce rafı, sonra doğru ürünü gerçek adet kadar okutun. Eksik toplama tamamlanmadan satışa izin verilmez.
9. İade özgün satışa bağlıdır; stok ve cari aynı işlemde güncellenir. Farklı dövizlerin bakiyeleri birbirine eklenmez.

Stok ekranı tam seri kapasitesi ve seri dışında kalan beden adetlerini gösterir. Alt raflar seçilirse bu kapasite raflardan toplanabilir miktardır. Paketli ve rezerve miktarlar ayrıca izlenir. Karantinadaki kusurlu ürün satışa açılamaz.

## Fotoğraf, etiket ve işlem belgesi

`/product-photos` model fotoğraflarını şirket oturumuyla açar. Fotoğraflar sınırlandırılmış JPEG biçimine dönüştürülür, kamera/konum metaverisi çıkarılır ve geçmiş fotoğraflar korunur. Görselden otomatik stok yazan AI bağlı değildir.

Etiketler Code 128 olarak çizilir; barkodda kimlik bulunur, değişebilir stok/fiyat/raf bulunmaz. Markaya özel kural yalnız yeni kodları etkiler. Baskı öncesinde 203/300 DPI ve etiket ölçüsü kontrol edilir; dar çizgi 2 pikselden küçükse baskı engellenir. İşletme içi 13 haneli kod GS1 tahsisi anlamına gelmez. Gerçek yazıcı/okuyucu pilotu hâlâ gereklidir.

Satış sonucundaki işlem belgesi A4 veya 80 mm olarak açılabilir. Eski satış fiyatlarını ve ilk tahsilatı korur; sonraki genel cari tahsilatını satışa paylaştırılmış gibi göstermez. Bu çıktı e-fatura entegrasyonu değildir.

## Güvenlik ve eski kayıtlar

Sunucu kimlik, şirket, işlem yetkisi ve CSRF denetimi yapar. Tekrar gönderimde aynı işlem anahtarı ikinci stok/cari hareketini oluşturmaz. Eski stok/cari yazma yolları normal çalışmada kapalıdır; ortam değişkeniyle açılamaz. Uyumluluk yalnız izole `TESTING` ortamındadır. Eski AI ve Excel yazma yolları koşulsuz kapalıdır.

`/storage` eski ürünlerin salt okunur arşividir; miktarları yeni stok toplamına eklenmez. İşlem denetim defteri şirketle sınırlıdır; ortak ham sunucu günlükleri webden servis edilmez. Oturumlar sunucu tarafında iptal edilebilir; çalışan erişimi kapatılırken hesap ve işlem geçmişi korunur. Giriş deneme bütçeleri DB üzerinde süreçler ve yeniden başlatmalar arasında korunur.

Güvenlik ayrıntıları ve kalan sınırlar `docs/GUVENLIK-DURUMU-2026-10-19.txt` içindedir.

## Yedek ve geri açma denemesi

```text
python manage.py backup-local
python manage.py verify-backup --path YEDEK_DOSYASI
python manage.py restore-check --path YEDEK_DOSYASI
```

Yeni dosya oluşturulur; mevcut yedek veya asıl DB üzerine yazılmaz. Bütünlük, yabancı anahtar ve stok defteri denetlenir. Geri açma yeni test klasöründe yapılır. `.env` oturum/MFA anahtarlarını yedekten ayrı ve güvenli koruyun.

## Doğrulama

```powershell
.\.venv-local\Scripts\python.exe tests/run_local_checks.py
```

Koşucu Python testlerini ayrı süreçlerde, Node testlerini ayrıca çalıştırır; kanıtlar silinmeden `.verification-data/local-checks-*/result.json` altında kalır. İşletme DB'sine test ürünü eklenmez. Ayrıca bağımsız ZXing barkod çözümlemesi ve tarayıcıyla sentetik satış/mal kabul/toplama denemeleri vardır. Fiziksel yazıcı/okuyucu, gerçek PostgreSQL eşzamanlılığı ve canlı barındırma doğrulanmış değildir.

## Migrasyon ve dağıtım

Eski Alembic ağacının kaynakta tek ucu `91d9647e4196` olarak görülür. Bu, eski migrasyonların yeni boş DB'ye veya mevcut işletme DB'sine güvenle uygulanabildiğini kanıtlamaz. `update.py` yalnız durumu okur. Yeni yerel kurulum `init`, eksik yeni tablolar `upgrade-local` ile hazırlanır; eski kayıt dönüşümü yapılmaz.

GitHub'a push ve Railway/Vercel/Supabase dağıtımı bu yerel oturumda yapılmadı. Mimari ve kodun kaynağı depoda tutulur; prod veritabanı, HTTPS/proxy, yedekleme, göç ve donanım pilotu dağıtımdan önce ayrıca doğrulanmalıdır. Başlangıç raporu `DURUM.md` tarihsel belgedir; güncel kanıtlar `docs/` ve `.verification-data/` içindedir.


## Hesap bağlamadan GitHub teslim paketi

```text
python export_github.py
```

Komut GitHub'a bağlanmaz, commit veya push yapmaz. Geliştirilmiş kaynakları ve yeni dosyaları `.local-exports/` altında yeni bir ZIP'e alır; her dosyayı SHA256 ve ZIP içeriğiyle doğrular. Gerçek `.env`, veritabanı, ürün yüklemeleri, test verisi, yedekler, `.git` ve sanal ortamlar paket dışıdır. Basit gömülü anahtar taraması da yapılır; bu tam bir secret audit garantisi değildir.

ZIP'i açıp içindeki `bR-vA` klasörünün kaynaklarını kendi GitHub Desktop çalışma kopyanıza alabilir, değişiklikleri kendiniz inceleyerek commit/push yapabilirsiniz. ZIP dosyasını tek başına kaynak kod gibi GitHub'a yüklemek uygulamanın dosyalarını açmaz. Birden fazla dosyalı manuel tarayıcı yüklemesinde GitHub'ın yükleme sınırlarına göre gruplar kullanın.

19 Ekimde asıl `Desktop/envaner` klasörü ayrıca salt okunur karşılaştırıldı. Özgün `storage.css` kompakt liste stilleri geri alındı. Ek eski bakım betikleri, migration ve yapılacaklar notu `docs/original-reference/` altında çalıştırılmayan metin referansları olarak korunur; eski API testindeki gömülü anahtar temizlenmiştir. Güncel uygulama kodunun üzerine eski sürüm kopyalanmadı.

Bağımlılık duyurularını yeniden sorgulama: `python audit_dependencies.py`. Yalnız herkese açık paket adı/sürümü OSV'ye gider. Son sorgu 33 paket/sürüm çifti için eşleşme vermedi; CDN/opsiyonel bileşenler ve yeni duyurular için ayrıca değerlendirme gerekir.
