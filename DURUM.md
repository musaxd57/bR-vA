> TARİHSEL BAŞLANGIÇ RAPORU: Aşağıdaki küçük hazırlık sürümü açıklaması önceki durumu korur. 19 Ekim 2026 yerel geliştirmelerinde yeni stok/cari çekirdeği, barkod, güvenlik ve çalışma ekranı eklendi. Güncel kullanım README.md; kanıt ve sınırlar docs/ altındadır. Kaynak migrasyon ağacı artık tek uç gösterir; bu, eski geçiş zincirinin doğrulandığı anlamına gelmez.

# Bu sürümün durumu

Tarih: 19 Ekim 2026. Kaynak: sağlanan `envaner.rar`. Bu paket, kaynak yükleme ve yerel geliştirme başlangıcını düzeltir; bitmiş bir ERP veya canlıya hazır mağaza sistemi iddiası taşımaz.

## Tamamlanan değişiklikler

1. Kaynak dosyalar; gerçek veritabanı, yüklenen fotoğraflar/Excel dosyaları, loglar, gizli yapılandırma ve eski Git geçmişinden ayrıldı. Orijinal dosyalar değiştirilmedi.
2. Ekranların mevcut 19 şablonu, 14 CSS dosyası, 14 JavaScript dosyası ve PDF fontları korundu. Font lisansı eklendi.
3. Kod içindeki sabit oturum anahtarı kaldırıldı. `SECRET_KEY` ortamdan veya yerel `.env` dosyasından okunur; eksik/kısa anahtarla başlangıç reddedilir.
4. Sabit parolalarla otomatik demo hesap/ürün/müşteri oluşturan başlangıç bölümü kaldırıldı. Yönetici yerel kurulum komutuyla ve kullanıcının seçtiği parolayla oluşturulur.
5. Uygulama importunun kendiliğinden tablo oluşturması kaldırıldı. Yeni boş veritabanı için açık bir `manage.py init` komutu eklendi; mevcut dosyayı üzerine yazmaz.
6. AI anahtarı ve Groq paketi olmadan temel uygulamanın açılması sağlandı.
7. Ürün eklerken desteklenmeyen `shelf` argümanının barkod fonksiyonuna gönderilmesi düzeltildi.
8. Para birimi seçiminde her seçimin USD olarak saklanmasına neden olan atama düzeltildi. TL, USD, EURO ve ardışık seçimler test edildi. Başlangıçtaki varsayılan para birimi değiştirilmedi.
9. Yeni müşteri borç alanının varsayılanı `[]` yerine `{}` yapıldı. Bu değişiklik eski müşteri satırlarını dönüştürmez.
10. Eski `update.py` içindeki koşulsuz `stamp head`, kabuk üzerinden migrate mesajı ve otomatik upgrade kaldırıldı. Yeni dosya yalnız migrasyon uçlarını gösterir.

## Yapılan doğrulama

| Kontrol | Sonuç |
|---|---|
| Gerçek para birimi handler'ı ve sepete ekleme, TL/USD/EURO/ardışık geçiş | 4/4 regresyon başarılı |
| Login sayfası, gerçek admin girişi + ürün kaydı, yeni müşteri borç tipi, eksik/kısa SECRET_KEY | 4/4 regresyon başarılı |
| Taze kurulum, tekrar kurulumda veri korunması, parola hash'i ile admin oluşturma, mevcut admini değiştirmeme, salt okunur migrasyon komutu | 5/5 izole kurulum kontrolü başarılı |

Testler sentetik veriyle çalıştırıldı. Gerçek mağaza verisi üzerinde dönüşüm, tarayıcıda görsel doğrulama, canlı barındırma, ödeme/e-fatura, barkod yazıcısı, Groq API'si ve yoğun eşzamanlı kullanım denenmedi.

## Hâlâ ele alınması gereken işler

Önceki incelemede belirlenen ve bu küçük hazırlık sürümünde çözülmeyen başlıca konular:

- Stok hareketlerinin tek kaynaktan, atomik işlemle güncellenmesi; ürün/beden/raf miktarlarının uyumu.
- Sıfır/negatif miktar, parça/seri fiyatlandırması, indirim ve yetersiz stok doğrulamaları.
- Sipariş onayı/geri alma ve iade sırasında satış, stok ve cari hareketlerinin birlikte tutarlı kalması.
- Eski müşteri borç formatları, karma para birimi, tahsilat ve bakiye mutabakatı. Yeni varsayılanı düzeltmek bu kayıtları onarmaz.
- Şirket/lokasyon kapsamı ve kullanıcı rol/yetki denetimleri; AI işlem yetkileri.
- Excel yükleme akışındaki dosya yolları ve iş sahipliği denetimleri; erişim ve CSRF korumaları.
- Barkodların düzenlemede kararlı kalması ve kod çakışması denetimi.
- Üç uçlu migrasyon geçmişinin onarılması, eski veriden deneme geçişi ve geri dönüş planı.
- Merter/Galata lokasyonları ve toptan/perakende satış kanallarının ortak stok modeliyle tanımlanması.
- Ürün maliyeti, kâr, gider, müşteri profili ve ajans katkısını ölçen güvenilir raporlar.

Bu işlerin ardından gerçek verilerle sayım ve satış pilotu yapılabilir. Kamuya açık dağıtım bu paketin kapsamına dahil değildir.
