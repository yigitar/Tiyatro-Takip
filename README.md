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

## Takip kuralı: hedefsiz oyun = tüm seanslar

Takibi açık bir oyunun hiç hedefi yoksa, o oyunun **herhangi bir seansı** satışa açılınca
(iade ile yeniden açılma dahil) bildirim gelir. Takibi açarken otomatik hedef eklenmez;
gün, saat ve kişi sayısına göre daraltmak istersen hedef eklersin.

## Diğer kaynaklar: Başiskele Belediyesi ve VKV Gölcük

- **Başiskele:** Belediyenin haber RSS'inden (`https://www.basiskele.bel.tr/rss`) aylık
  "Kültür-Sanat" program haberi bulunur; haberdeki her tarihli satır bir etkinliktir
  (tarih, saat, ad, tür, mekân). Yeni bir etkinlik görülünce bir kez "yeni etkinlik
  duyuruldu" bildirimi gelir. Haber açıkça bir rezervasyon/bilet açılış saati söylüyorsa
  açılıştan 1 gün ve 10 dk önce hatırlatma gelir; haberde böyle bir saat yoksa hatırlatma
  gönderilmez. İlk okuma sessiz başlangıçtır. Instagram izlenmez.
- **VKV Ford Otosan Gölcük:** `https://www.vkvfordkultur.org.tr/Etkinlikler` sayfasından
  yalnızca Tiyatro ve Çocuk Tiyatrosu etkinlikleri izlenir (sayfa o ayı gösterir; sonraki
  ay sayfaya yansıyınca okunur). Etkinlik sayfası "… saat 12:00'da rezervasyona
  açılacaktır" diyorsa açılış saati duyurusu, açılıştan 1 gün ve 10 dk önce hatırlatma
  gelir; açılış saati gelince (sayfa hâlâ eski cümleyi gösterse bile) ve cümle
  kalkınca "rezervasyon açıldı" bildirimi gelir. Açılış saatinin 1 dk öncesinden 30 dk
  sonrasına kadar etkinlik sayfası yaklaşık 60 sn'de bir okunur. "Dolmuştur" olan bir
  etkinlik yeniden açılırsa "yeniden açıldı" bildirimi gelir. Etkinlikler ücretsizdir;
  sitede çevrimiçi rezervasyon bağlantısı **yoktur**, bu yüzden her VKV bildirimi
  telefon (0262) 435 6700, e-posta info@vkvfordkultur.org.tr ve etkinlik bağlantısını içerir.
- **Takip düğmesi:** Her etkinlik tek tek açılıp kapatılır (PC: "Diğer kaynaklar" sekmesinde
  Takip sütunu; Telegram: "Diğer kaynaklar" ekranı). Varsayılan: tiyatro/müzikal açık,
  diğerleri (atölye, söyleşi, film) kapalı. Kapalı etkinlik listede gri kalır, bildirim
  göndermez. Seçimler `config.json` içindeki `event_follow` anahtarında tutulur.
- **Kaynak durumu:** Her kaynak (Biletinial, Başiskele, VKV) için ✅ güncel / ⚠️ 30 dakikadan
  eski / ❌ son kontrol başarısız simgesi, "Son kontrol" ve "Son güncelleme" (kaynağın kendi
  içeriğinin değişme zamanı) gösterilir: PC'de Oyunlar sekmesinin üstünde, Telegram'da
  "Kaynaklar" ekranında. "Listeyi güncelle" ve Telegram "Yenile" tüm kaynakları tarar.
- **Sınırlamalar:** VKV'de rezervasyonun "açıldığı" andaki sayfa metni henüz
  gözlemlenmedi (açılmış bir etkinlik sayfasında eski cümle kalıyor olabilir), bu yüzden
  açılış bildirimi saate bağlıdır ve gerekirse "sayfa henüz güncellenmemiş olabilir" notunu
  taşır. Başiskele haberinin tarihleri yılsız yazıldığından yıl, gün adından çıkarılır.

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
- **Doğrulama gecikmesi:** Boştan dolu görünen bir sayfa için "etkinlik açıldı"
  bildirimi, aynı içeriğin bir sonraki taramada da görülmesiyle (en fazla bir tarama,
  yaklaşık 5 dk gecikmeyle) gönderilir; tek seferlik bozuk/hatalı bir sayfa bildirim
  üretmez. "Güncellendi" bildirimi portal başına en çok 6 saatte bir gelir; saat,
  sayaç gibi değişken metinler ve bağlantı sırası dikkate alınmaz.
- **Bilinen boşluk:** Bağlantısı olmayan ve tek satır düz metin olarak gösterilen bir
  etkinlik "boş" sayılır (iki ve daha fazla düz metin satırı ya da bir bağlantı/düğme
  olursa açık sayılır). Hata/bakım/çerez uyarısı gibi sayfalar "boş" sayılır.
- **Ayar:** `config.json` içindeki `portals` listesi (en çok 5 portal; `id`, `name`,
  `url` (https), `enabled`). Anahtar yoksa Başiskele portalı varsayılan olarak açıktır.
  Telegram menüsü ve PC uygulaması bu listeye dokunmaz.

## Testleri çalıştırma

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```
