BRIVA V2 STOK VE SATIŞ ÇEKİRDEĞİ
Yerel geliştirme sözleşmesi — 19 Ekim 2026

Bu paket, eski tabloları değiştirmeden yeni ve sıfır stoklu katalog açar.
Kaynak dosya, eski stok, cari veya satış geçmişini silen bir yordam içermez.
Eski katalog yeni çekirdeğe kendiliğinden aktarılmaz.

ENTEGRASYON
  from briva_core import register_core, create_core_schema
  register_core(app)        -> yalnız /api/v2 rotalarını kaydeder
  create_core_schema()      -> açık uygulama bağlamında yalnız CORE_TABLE_NAMES
                              içindeki eksik tabloları oluşturur

Kimlik legacy User kaydından alınır. Şirket/rol/izin gövdeden veya istemcinin
önbelleğinden seçilmez. Ana uygulamanın briva_security katmanı oturum, CSRF,
sunucu oturum yetkisi ve MFA işlemlerini sağlar. Core bağımsız test uygulamaları
bu güvenlik katmanının yerine geçmez. Auth tabloları ayrıca oluşturulmalıdır.

KESİN İŞ KURALLARI
- En küçük stok birimi model + renk + beden varyantındaki bir parçadır.
- Katalog, üretim planı, barkod veya etiket basımı stok yaratmaz.
- Yalnız kabul edilen fiilî parçalar ve açık sayım farkları stoğa girer.
- Konuma/rafa taşıma çıkış + giriş hareketidir; toplam fiziksel stok korunur.
- Stok, rezervasyon ve kapalı paket miktarları negatif olamaz.
- Fiziksel stok >= rezervasyon + kapalı paket miktarıdır.
- Açık kullanılabilir = fiziksel - rezervasyon - kapalı paket miktarı.
- Karantina fiziksel sayılır, kullanılabilir stoğa katkısı sıfırdır.
- Aynı varyanttaki yüz kıyafet aynı tekli barkodunu taşıyabilir.
- Tekli barkod bir parçayı; seri barkodu değişmez reçete sürümünü bulur.
- Fiziksel paket barkodu belirli bir kapalı paketi bir kez satar.
- Soyut seri barkodunu okutmak fiziksel toplama yapıldığını kanıtlamaz.
- Parça fiyatı her zaman parça başınadır; seri fiyatı bileşenlerden hesaplanır.
- Fiyat, stok ve raf barkodun içinde saklanmaz.
- Liste fiyatından sapma sahibi yetkisi ve açıklama ister; eski satış fiyatı
  değişmez. Para Decimal/NUMERIC, en fazla iki ondalıktır. Gizli kur dönüşümü yok.
- Fiyatı eksik para birimi için önce katalogda fiyat tanımlanır.
- Başarılı satış, stok, ödeme, cari ve denetim kayıtları tek transaction içindedir.
- İade eski satırı silmez; özgün satışa bağlı ters stok/cari hareketi oluşturur.
- Sonradan yapılan genel tahsilatlar müşteri carisine uygulanır. Sale.paid ve
  balance_due satış anındaki ödeme/cariye aktarılan tutarın değişmez kaydıdır;
  bunlar güncel müşteri bakiyesi veya fatura bazında yaşlandırma değildir.

BARKOD KURALI
GET/PUT /api/v2/barcode-rules
  {"mode":"alphanumeric","prefix":"BRV"}
  {"mode":"numeric","prefix":"200"}

Harfli: önek + U/R/P/L türü + 12 basamaklı global hedef kimliği.
Sayısal: 3 basamak önek + tür + 8 basamak global kimlik + mod10 kontrol rakamı.
Önek kuralı yalnız yeni kimliklere uygulanır. Eski barkodlar değiştirilmez.
Bu şirket içi Code128 numaralandırmasıdır; GS1 tahsisli GTIN iddiası değildir.
Global UNIQUE barkod ve UNIQUE(kind,target_id) kısıtları çakışmayı engeller.
Veritabanı primary key tahsisi kullanılır; istemci rastgele kimlik üretmez.

TÜM MUTASYONLAR
Content-Type: application/json
Idempotency-Key: 8–100 ASCII harf/rakam/_.:- (öneri UUID)
X-CSRF-Token: ana uygulamanın oturum CSRF anahtarı
X-Briva-Actor: özgün taslağın kullanıcı kimliği
X-Briva-Company: encodeURIComponent(özgün şirket adı)

Son iki başlık birlikte gönderilir. Gönderildiyse mevcut DB kimliğiyle
eşleşmesi zorunludur. Başka sekmede kullanıcı değişse bile eski taslak yeni
kullanıcı adına çalıştırılmaz. Başlıklar kimliği seçmez, eşleşmeyi doğrular.

Aynı anahtar ve gövde aynı sonucu döndürür (replayed:true). Başka içerik veya
başka kullanıcının aynı anahtarı 409'dur. İşlem başarısızsa request kaydı da geri
alınır; aynı anahtarla düzeltme/tekrar mümkün olur. Yanıtın ağda kaybolduğu
belirsiz durumda yeni anahtar oluşturmayın; aynı anahtarla kontrol edin.

Başarı: {"data":{...},"replayed":false} — 201; tekrar 200.
Hata: {"error":{"code":"...","message":"...","details":{...}}}
Ana güvenlik katmanı ayrıca kendi JSON hata biçimini döndürebilir.

YAZMA UÇLARI — /api/v2 ALTINDA
POST /catalog/models
  {model_code,name,description?,colors:[{code,name}],sizes:["S","M",...],
   unit_prices:{TRY:"100.00",USD:"10.00"}}
  Renk × beden varyantlarını sıfır stokla açar; en fazla 1000 varyant/işlem.

PATCH /catalog/models/<id>         {name?,description?} — model kodu kalıcıdır
PATCH /catalog/variants/<id>/prices {prices:{TRY:"..."},reason}
POST /catalog/recipes
  {model_id,color_code,name,components:[{variant_id,quantity}]}
  Aynı ailede yeni sürüm yaratır; eskisi değiştirilmez.

POST /locations {code,name,kind,parent_id?}
  kind: store,warehouse,shelf,receiving,quarantine
POST /production/plans {reference,workshop?,items:[{barcode,quantity}]}
POST /receipts
  {location_id,reference,plan_id?,items:[{variant_id,quantity}],
   declared?:[...],defective?:[...],quarantine_location_id?}
  items yalnız satılabilir kabul edilen parçalardır. Kusurlu varsa ayrı karantina
  konumu zorunludur. Tamamı kusurlu kabulde items:[] kullanılabilir.
POST /transfers {from_location_id,to_location_id,reference?,items:[{variant_id,quantity}]}

POST /stock-counts
  {location_id,reference,reason,items:[{variant_id,expected_quantity,counted_quantity}]}
  Sıfıra sayım geçerlidir. Önizlemeden sonra fiziksel miktar değişmişse409.
  Rezervasyon/paket miktarının altına sayılamaz. Fark yeni hareket olarak kaydolur.

POST /customers {name,phone?}
POST /payments {customer_id,currency,amount,method,direction:"in"|"out",reference?}
  method: cash,card,transfer. Geri ödeme mevcut müşteri alacağını aşamaz.
POST /sales
  {location_id,customer_id?,currency,items:[{barcode,quantity,unit_price?,price_reason?}],
   payment:{amount,method},expected_total?,picking_task_id?}
  Doğrudan varyant için {variant_id,quantity} de desteklenir. Seri ve tekli
  aynı sepette aynı beden ihtiyaçlarına açılır. expected_total güncel fiyatla
  farklıysa pricing_changed409, stok yazılmaz. Veresiye müşteri ister.
POST /sales/<id>/returns
  {location_id,items:[{sale_item_id,quantity}],reason,refund_amount?,refund_method?}
  Misafir satışının iadesi tam geri ödeme ister. Cari müşteride iade alacağı
  bırakılabilir; eksi bakiye müşteri alacağıdır ve gizlenmez.

POST /packages {location_id,recipe_id VEYA items:[{variant_id,quantity}]}
POST /packages/<id>/open {reason?}
POST /packages/<id>/transfer {to_location_id,reference?}
  Paketleme/açma yalnız açık/paketlenmiş ayrımını değiştirir, fiziksel toplamı değil.

POST /picking/tasks
  {location_id,items:[{barcode,quantity}],include_descendants?:false}
POST /picking/<id>/scan {barcode,quantity?:1,location_id?}
POST /picking/<id>/cancel {reason?}
  Exact varsayılandır. Raf görevinde önce kaynak raf barkodu okutulur.
  include_descendants:true yalnız store/warehouse kökünde açıktır; aynı kökün
  kendi stokları ve alt rafları tahsis edilir. Başka mağaza/depo kökü ve karantina
  hariçtir. En fazla500 kaynak konum/işlem. Her kullanıcı kendi son doğruladığı
  raf üzerinden parça okutur. Toplanan miktar hem raf tahsisine hem toplam
  satıra yazılır. Fazla veya yanlış beden409. Task tamamen hazır olmadan satış
  yapılamaz. Satış gerçek kaynak raflardan düşer, kaynaklar snapshot'ta korunur.
  Kısmi teslim için şu an açık görevi iptal edip fiilî miktarla yeni görev açılır.

OKUMA VE SAYFALAMA
GET /bootstrap -> user{id,username},company,permissions,barcode_count,
  models,recipes,catalog_pagination,locations,customers,plans,receipts,sales,
  packages,picking,barcode_rules.
GET /catalog?after_model_id=&limit=50&variant_budget=5000&q=<model adı/kodu>
GET /catalog?model_id=<sayfa dışındaki tek model>
  En fazla100 model/sayfa; varyant bütçesi1000–5000. Modelin varyantları bölünmez.
  pagination.total_models/total_variants toplamı gösterir; has_more kayıtların
  silindiği anlamına gelmez. Tam barkod çözümü katalog sayfasından bağımsızdır.
GET /barcodes/<code> -> kind/unit/recipe/package/location ve kesin bileşenleri
GET /barcodes/<code>.svg -> oturum gerektiren, çevrimdışı Code128 B/C SVG
  ReportLab4.4.9 encoder sayısal çiftleri sıkıştırır. SVG crispEdges ve bağımsız
  yatay/dikey ölçek kullanır. Fiziksel minmodül/DPI denetimi ayrıca yapılmalıdır.
GET /production/plans/<id> -> bileşenlerin tam ürün/renk/beden/barkod metadatası
GET /packages/<id> -> paket bileşenlerinin tam metadatası

GET /stock?location_id=&model_id=&after_balance_id=&limit=1000&recipe_id=
  balances gerçek konum kimliklerini korur. Totals SQL ile tüm seçilen kayıtları
  kapsar; ilk sayfa toplamı değildir. pagination.next_after_balance_id ile devam.
  En fazla1000 balance/sayfa,500 kapasite çifti,200 reçete. Daraltma gerektiğinde
  capacities_truncated açıkça döner. Yanıt mevcut sayfa/kapasiteler için variants,
  minimal models ve recipes metadatasını da taşır.
GET /stock?location_id=<store/warehouse>&include_descendants=true
  Yalnız seçilen tek şubenin alt rafları birleştirilir. Fiziksel toplam şubenin
  karantina çocuklarını da sayar; kullanılabilir/kapasite karantinayı dışlar.
  Nested başka store/warehouse hem fiziksel hem kullanılabilir kapsamın dışıdır.
  Scope bunu açıklar. Kapasite collectable_from_shelves ve allocation_required
  olarak döner; tek rafta hemen teslim edilebilir stok demek değildir.

GET /picking -> tasks, lines+barcode/model/beden, allocations, source_locations,
  include_descendants,active_location_id (mevcut kullanıcıya ait)
GET /sales/<id> -> sahibin geçmiş yetkisi veya kendi satış kaydı şarttır
GET /customers/<id>/account -> cari yetkisi gerekir
GET /ledger?before_id= -> son200 hareket, keyset cursor
GET /audit -> sahibin denetim yetkisi, son300 olay
GET /health -> stok/rezervasyon/paket defteri ve bakiyeler arasında uzlaştırma
  Sorun bildirir, otomatik düzeltme yapmaz. Yalnız yetkili sahibi görebilir.

İZİNLER
catalog_create=can_add_product; catalog_edit/inventory=can_edit_product;
sales=can_make_sale; history=can_view_sales_history; customers=can_manage_customers;
payments/returns=can_manage_debts; picking=can_orders.
price_override,barcode_rules,audit şimdilik sahibi (admin/owner) yetkisindedir.
Sadece satış yetkisi, bütün şirket satış geçmişini veya müşteri bakiyesini açmaz.

DOĞRULAMA
  .venv-local\Scripts\python.exe tests/run_core_checks.py
Gerekiyorsa bu yerel ortamda PYTHONPATH=.pure-python kullanılır.
Testler özgün app/DB/env dosyasını yüklemez. Bellek DB veya yeni, saklanan
.verification-data klasörlerindeki sentetik DB kullanılır. Temizleme/silme yoktur.
19 Ekim geliştirme artımında89 çekirdek test geçti. Sonuçlar her çalıştırmada yeni
JSON raporuna yazılır. Bu sabit sayı sonraki geliştirmeler için bitiş ölçütü değildir.

Kanıt: .verification-data/core-report-06ef20874fc040f3afb9e7e3f892ca64/result.json
1000 varyant /50 konum /50.000 stok satırı için SQL sayısı ve payload bütçesi ayrı
core-performance raporlarında ölçülür. Ölçüm SQLite sentetiktir, üretim kapasite
vaadi değildir. PostgreSQL eşzamanlılık henüz gerçek sunucuda doğrulanmadı.
Gerçek yazıcı/etiket/okuyucu donanımı da ayrıca pilot test ister.

SINIRLAR
ORM seviyesinde geçmiş hareket düzenleme/silme engellenir; veritabanı yöneticisi
yetkisine karşı kriptografik, değiştirilemez bir kayıt kasası iddiası yoktur.
Sağlık raporu tutarsızlıkları bulmaya yardımcı olur. DB erişimi/backup/geri yükleme
ve oturum güvenliği ana uygulamanın işletim sorumluluğudur. Offline satış,
otomatik AI stok yazımı, kapalı görselden kesin sayım ve bulut dağıtımı yapılmaz.
