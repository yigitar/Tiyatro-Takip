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
- **Tek liste (PC):** Ayrı sekme yoktur; "Oyunlar" listesi üç kaynağın (Biletinial, Başiskele,
  VKV) kayıtlarını birlikte tutar. "Kaynak" sütunu sıralanabilir; Başiskele/VKV
  satırlarında "Seans" etkinliğin tarih-saatini, "Durum özeti" rezervasyon durumunu
  ("rezervasyon 19 Kas 12:00'da açılacak" / "rezervasyon açık" / "doldu" / "duyuru
  bekleniyor") gösterir. Ayrıntıda "Etkinlik tarihi" ile "Rezervasyon açılışı" ayrı yazılır;
  VKV için telefon/e-posta/bağlantı bloğu ve "Sayfayı aç" vardır.
- **Boş Kitle/Tür kuralı:** Kitle: sayfa/kategori "çocuk" diyorsa, "çocuk"
  etiketi varsa veya yaş sınırı 12 ve altındaysa **Çocuk**; sayfa "yetişkin" diyorsa,
  yaş 13+ ise ya da hiçbir çocuk işareti yoksa **Yetişkin**. Tür: sayfadaki tür alanı
  (kitle sözcükleri atılır); yalnızca kitle yazıyorsa başlık/açıklamadaki açık tür
  sözcüğü (müzikal, komedi, dram, trajedi, monolog, stand-up, operet, kabare); o da
  yoksa tiyatro etkinliği için "Tiyatro". "—" yalnızca tiyatro olmayan ve türü hiç
  belli olmayan kayıtlarda kalır.
- **Takip kutusu (varsayılan: KAPALI):** Yeni görünen her oyun ve her etkinlik (Biletinial,
  Başiskele, VKV) **takipsiz** başlar; istediklerini sen açarsın. Listede ilk sütun
  "Takip" bir kutudur (☑ / ☐): hücreye tıklamak oyunlar ve Başiskele/VKV etkinlikleri için
  takibi açıp kapatır (ayrıntıyı açmaya gerek yok; ayrıntıdaki "Aktif" kutusu aynı
  anahtardır). Telegram: "Diğer kaynaklar" ekranı. Takipsiz etkinlik listede kalır, bildirim
  göndermez (yeni-etkinlik bildirimi dahil). Seçimler `config.json` içindeki `event_follow`
  (etkinlik) ve `plays` (oyun) anahtarlarında tutulur. **Önceden takipte olanlar
  değişmez:** eski sürümde varsayılanla (tiyatro/müzikal) takipte sayılan etkinliklerin
  kaydındaki varsayılan korunur, açık seçimler olduğu gibi kalır.
  Bir etkinlik takipteyken bildirim gelmesi için kaynağın rezervasyon açılış saatini
  yayınlaması gerekir; yayınlanmadıysa ("duyuru bekleniyor") bildirim gelmez.
- **PC listesi sütunları (hepsi sıralanabilir):** Takip (☑/☐; tıklanınca takibi değiştirir),
  Oyun, Sayfa ("Oyun ↗" düğmesi sayfayı açar), Kaynak, Kitle, Tür,
  **Zaman** (yalnızca "gündüz" = 17:00'den önce başlayan seans, "gece" = 17:00 ve sonrası,
  ikisi de varsa "gündüz+gece"; seans saatlerinden hesaplanır, kitle burada yazmaz),
  Süre, Yaş, Fiyat, **Seans** ("3 seans: 28 Kas 15:00, 28 Kas 20:00, +1": ilk iki seans,
  kalanı "+N"; sıralama en erken seansın tarih-saatine göredir), **Rez. açılış**
  ("16 Kas Pzt 10:00 (yarın)"), **Hedef**, Durum özeti.
  - *Rez. açılış:* oyunda, "yakında" durumundaki seansların en erken `opens_at` zamanı
    (göreli ipucuyla); yoksa herhangi bir seans satıştaysa "açık"; yaklaşan tüm seanslar
    tükenmişse "tükendi"; aksi halde "—". Etkinlikte: açılış zamanı / "açık" /
    "açılmış olabilir" / "doldu" / "duyuru bekleniyor". Sıralama: "açık" önce, sonra en erken
    açılış zamanı; değeri olmayanlar sona gider.
  - *Hedef:* "var (N)" (N = kendi eklediğin hedef sayısı) ya da "yok → tüm seanslar".
    Etkinliklerde hedef kavramı olmadığı için "—".
- **Kaynaktan alınan / türetilmiş değerler:** Kaynak sayfada yazan değerler (başlık, tarih-saat,
  fiyat, yaş, süre, tür alanı, özet, oyuncular, rezervasyon metni, durum) normal yazılır;
  bizim kuralımızla ya da varsayımla üretilenler **italik ve gri-mavi** yazılır ve listenin
  altında "normal = kaynaktan alınan, italik = türetilmiş/varsayım" açıklaması durur.
  Türetilmiş sayılanlar: Kitle (sayfa/kategori kitleyi açıkça yazmıyorsa; yaş/etiket/başlık
  kuralı ya da "çocuk işareti yok → Yetişkin"), Tür (tür alanı boşsa; başlık/açıklamadan
  bulunan sözcük ya da "Tiyatro"), Zaman (her zaman hesaplanır), Hedef "yok → tüm seanslar",
  "—" ve "duyuru bekleniyor" gibi boşluk göstergeleri, etkinliklerde "açık"/"açılmış olabilir".
  Alan bazlı bayrak `pc/viewmodel.py` içindeki `Row.derived` kümesinde tutulur (arayüz tahmin
  etmez).
- **Yeni öğe vurgusu:** Son yenilemeden/taramadan sonra listeye giren ve henüz açılmamış
  (satırına tıklanmamış) oyun/etkinliklerin adı **kalın ve mor** görünür; satırı bir kez
  seçince normale döner. "Görüldü" bilgisi bu bilgisayarda `pc/local/ui.json` içinde
  (`seen`) tutulur, depoya gitmez. Güncellemeden sonraki ilk çalıştırmada, veri yüklendikten
  sonraki ilk dolu liste başlangıç sayılır: o listedeki her şey görülmüş kabul edilir
  (renk yağmuru yok); yalnızca sonradan çıkanlar yeni olur.
- **Bağlantılar:** Her satırda "Oyun ↗" düğmesi oyunun/etkinliğin sayfasını açar. Oyun
  ayrıntısındaki seans tablosunda her seansın kendi "Seans ↗" düğmesi vardır (Biletinial
  seans bağlantısı durumda saklıdır); bağlantısı olmayan seansta düğme "Oyun ↗" olup oyun
  sayfasını açar ve tablonun altında bunu söyleyen bir not görünür. Başiskele/VKV'de seans
  başına ayrı sayfa yoktur: etkinlik ayrıntısında "Etkinlik sayfası ↗" düğmesi ve bu notun
  kendisi bulunur. Kaynaklar şeridinde her kaynağın yanında "Kaynağı aç" düğmesi vardır
  (Biletinial mekân sayfası, Başiskele en yeni program haberi — bilinmiyorsa belediye ana
  sayfası, VKV Etkinlikler listesi); Başiskele satırında ayrıca "Portalı aç" durur.
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
  İlk bakışta liste boşsa sessizce başlangıç olarak kaydedilir. PC uygulamasında portal
  bilgisi Başiskele kaynak satırına eklenir ("rezervasyon portalı: boş / N etkinlik açık")
  ve satırda belediyenin rezervasyon sayfasını tarayıcıda açan "Portalı aç" düğmesi
  bulunur; Telegram ana menüsünde ile "Yaklaşan açılışlar"
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
