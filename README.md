# Tiyatro Takip

Biletinial üzerindeki Kocaeli şehir tiyatrosu seanslarını izleyen, yeni oyun ve bilet satışı
değişikliklerinde Telegram ve bilgisayar uygulaması üzerinden bildirim gönderen bir bottur.

## Bilmen gerekenler

- **`data` dalı herkese açıktır.** Depo herkese açık olduğundan `data` dalındaki
  `config.json` ve `state.json` da açıktır: takip ettiğin oyunlar, hedeflerin (gün, saat,
  kişi sayısı) ve seans durumları herkes tarafından görülebilir.
- **Duraklat, bildirimleri atar.** Duraklatılmışken oluşan bildirimler gönderilmez ve
  "Devam" denince de gönderilmez. Devam ettiğinde hâlâ satışta olan bir seans için
  yeniden "Bilet alınabilir" bildirimi GELMEZ; yalnızca sonraki değişiklikler bildirilir.
- **Satış bildirimi kuralları:**
  - Aynı seans için "Bilet alınabilir" bildirimi en az 3 dakika arayla tekrarlanır;
    tükenip iade ya da sepet süresi dolmasıyla yeniden açılan seanslar yeniden bildirilir.
  - GitHub taraması gecikirse PC uygulaması "Bilet alınabilir" bildirimini 15 sn sonra,
    diğer bildirimleri 60 sn sonra kendisi gönderir (aynı bildirimi iki taraf göndermez).
  - Satış açılış saatinden 10 dk önce başlayıp 4 saat sonrasına kadar, ayrıca satış
    açıldıktan sonra 10 dk ve seans tükendikten sonra 30 dk boyunca ilgili oyun sayfası
    yaklaşık 15 sn'de bir okunur.
  - Saat sınırları iki uçta da dahildir: "≤20:00" 20:00 seanslarını kapsar.

## Başiskele rezervasyon portalı

Uygulama, Başiskele Belediyesi'nin ücretsiz rezervasyon sayfasını
(`https://rezervasyon.basiskele.bel.tr/etkinlikler`) de izler. Bu sayfa yalnızca
rezervasyonu **şu anda açık** olan etkinlikleri listeler; çoğu zaman liste boştur.

- **Ne yapar:** Her normal taramada (5 dk) sayfayı okur. Liste boştan doluya geçerse
  "etkinlik açıldı", doluyken içeriği değişirse "güncellendi" bildirimi gelir (etkinlik
  adları ve bağlantıları ile). Sayfa üst üste 3 kez okunamazsa "okunamıyor" uyarısı gelir.
  İlk bakışta liste boşsa sessizce başlangıç olarak kaydedilir. PC uygulamasının
  "Oyunlar" sekmesinde tek satırlık bir "Portallar" şeridi (boş / N etkinlik açık, son
  kontrol saati, [Aç] düğmesi) ve Telegram ana menüsünde ile "Yaklaşan açılışlar"
  ekranında bir durum satırı vardır. Duraklat bu bildirimleri de atar.
- **Ne yapmaz:** Yalnızca ücretsiz rezervasyon etkinliklerini kapsar. Başiskele
  salonlarında oynayan, Biletinial'dan biletle satılan Şehir Tiyatroları oyunları zaten
  Biletinial taramasıyla izlenir. Belediyenin Instagram duyuruları izlenmez.
- **Sınırlama:** Etkinlik listesi dolu olduğunda sayfanın nasıl göründüğü henüz
  gözlemlenmedi; bu yüzden algılama, sayfanın tasarımına bağlı kalmayan genel bir
  yöntemle yapılır (bağlantı/düğmeler ve boş olmayan metin satırları). Tasarım değişirse
  yanlış "açıldı" ya da kaçan açılış olabilir; ilk gerçek açılışta sonuç kontrol edilmelidir.
- **Ayar:** `config.json` içindeki `portals` listesi (en çok 5 portal; `id`, `name`,
  `url` (https), `enabled`). Anahtar yoksa Başiskele portalı varsayılan olarak açıktır.
  Telegram menüsü ve PC uygulaması bu listeye dokunmaz.

## Testleri çalıştırma

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```
