# Tiyatro Takip - PC uygulaması

Oyun listesini, seans durumlarını ve takip kurallarını gösteren, yeni satış açılışlarında
masaüstü penceresi + sesli uyarı veren Tk uygulaması. Telegram bildirimi güvenilir kanaldır
(PC kapalıyken de çalışır); masaüstü penceresi yalnızca ek bir uyarıdır.

## Ayarlar

`pc/local/settings.json` (git'e girmez) ya da ortam değişkenleri:

| settings.json | Ortam değişkeni | Anlamı |
|---|---|---|
| `repo` | `GITHUB_REPOSITORY` | `sahip/depo` (zorunlu) |
| `env_file` | `TOKENS_ENV_FILE` | `ANAHTAR=DEĞER` satırlı gizli anahtar dosyası (isteğe bağlı) |
| `gh_token_key` | `TIYATRO_GH_TOKEN_KEY` | dosyadaki GitHub jetonunun ANAHTAR ADI (varsayılan `TIYATRO_GH_TOKEN`) |
| `tg_token_key` | `TIYATRO_TG_TOKEN_KEY` | Telegram bot jetonunun anahtar adı (varsayılan `TELEGRAM_BOT_TOKEN`) |
| `chat_id_key` | `TIYATRO_CHAT_ID_KEY` | Telegram sohbet kimliğinin anahtar adı (varsayılan `TELEGRAM_CHAT_ID`) |

Örnek `settings.json`:

```json
{"repo": "sahip/depo", "env_file": "C:/yol/tokens.env"}
```

`env_file` verilmezse anahtarlar süreç ortamından okunur. Eksik ayarda uygulama yalnızca
eksik anahtarın adını söyleyen bir hata penceresi gösterir.

## Başlatma

`pc\start.bat` çift tıklanır (varsa `.venv`, yoksa `pythonw`/`pyw` kullanır) ya da depo
kökünden `python -m pc.app`. Uygulama tek kopya çalışır (adlandırılmış Windows mutex'i; süreç
çökse de işletim sistemi bırakır, eski kilit dosyası olmaz): ikinci başlatma açık pencereyi öne
getirir (bulamazsa "zaten çalışıyor" der). Pencereyi kapatmak uygulamayı kapatır, küçültmek çalışmaya devam ettirir.

## Kullanım

- Pencere düzeni (dalga 12): sabit/durum parçaları solda dar bir kenar çubuğundadır (genişlik ~280 px, DPI ile ölçeklenir; içerik sığmazsa kendi kaydırma çubuğu çıkar): bilgi değerleri (Son tarama, Mod, Sonraki açılış, GitHub, PC kaydı, Bekleyen bildirim) alt alta anahtar/değer, altında "Listeyi güncelle" ve "Duraklat", sonra üç kaynak kartı alt alta (başlık satırında simge, ad, "Kaynağı aç" / "Portalı aç"; uzun ad ikinci satıra sarar), en altta "Açıklama". Sağ tarafta Oyunlar | Etkinlik günlüğü sekmeleri vardır; liste tam genişlik ve yüksekliğin en az %55'ini alır (sürüklenebilir ayırıcı korunur), altındaki ayrıntı iki sütundur: solda başlık/özellikler/Aktif/hedef ekleme düğmeleri + seans tablosu ve altında Özet, sağda Hedefler kutusu ve altında Oyuncular + Yapım; ayrıntı alanı ~820 px'ten darsa bloklar alt alta dizilir, sığmayan içerik kaydırılır. Pencere 1000 px'ten (DPI ile ölçekli) darsa kenar çubuğu üstte bir şeride dönüşür (durum | kartlar yan yana, Açıklama altında; yüksekliği pencerenin ~%40'ıyla sınırlı, aşarsa kaydırılır). Özet metnindeki boş satır yığınları (boşluk/nbsp/CR dahil) en fazla bir boş satıra indirilir ve baştaki/sondaki boşluklar kırpılır (yalnız gösterimde, kayıtlı veri değişmez; Telegram oyun ekranı özeti zaten tek satıra indirir).
- Liste: varsayılan sıra en yakın seans, sonra başlıktır (takip durumuna bakılmaz: kutuyu işaretlemek satırı yerinden oynatmaz; "Takip" başlığına tıklayarak takipte olanları öne alabilirsin); ilk sütun ☑/☐ takip kutusudur (tıklayınca değişir). KALKTI işareti son "Listeyi güncelle"ye göredir; açılmamış yeni öğelerin adı kalın mor görünür. Italik gri-mavi değerler kaynaktan alınmamış, türetilmiş/varsayımdır (ayrıntı: ana README).
- Oyun seçilince: Biletinial bağlantısı, seanslar, hedefler (Düzenle / Sil), Aktif anahtarı,
  Yetişkin/Çocuk hedefi ekleme. Değişiklikler Telegram menüsüyle aynı `config.json`'a yazılır.
- Hedef düzenleyici: kişi sayısı, günler, saat sınırları, güne özel saat, hariç tarihler.
- Durum bloğu (sol kenar çubuğu): son tarama, mod (Normal/Hızlı), sonraki açılış, GitHub
  sağlığı, PC kaydı yaşı, bekleyen bildirim, hatalar (üzerine gelince ayrıntı).
- Kaynaklar: Biletinial, Başiskele, VKV için alt alta üç kart (durum simgesi/rengi, son kontrol,
  son güncelleme, etkinlik sayısı, "Kaynağı aç"; Başiskele'de rezervasyon portalı + "Portalı aç").
  "Açıklama" kutusu liste yazı stillerini ve durum simgelerini örnekle açıklar.
- Ayarlar > Sesli uyarı: bildirim penceresindeki bip'i açar/kapatır.
- Ayarlar > Windows açılışında başlat (varsayılan kapalı): `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
  altına `TiyatroTakip` değerini yazar/siler (yalnız bu kullanıcı, yönetici gerekmez; pythonw
  ile konsolsuz). Kutu, kayıt defterinin gerçek durumunu gösterir.
- Ayarlar > Bilgisayarın uyumasını engelle (varsayılan kapalı, `pc/local/ui.json` `keep_awake`):
  uygulama açıkken `SetThreadExecutionState` ile sistemin uykuya geçmesini engeller (ekran yine
  kapanabilir); kapatınca ve çıkışta geri alınır.

## Hata durumunda

`start.bat` konsolsuz (pythonw) çalıştığı için uygulama sessizce kapanmaz: başlatma ya da
çalışma sırasında herhangi bir hata olursa bir hata penceresi açılır ve ayrıntı (jetonlar
maskelenmiş) şu dosyalara yazılır:

- `pc/local/crash.log` (depo klasörü altında; yazılamazsa `%TEMP%/tiyatro-takip-crash.log`)
- `pc/local/app.log` (normal çalışma günlüğü)

Pencerede dosyanın tam yolu gösterilir. `python -m pc.app` ya da `python -m pc` aynı koruma
ile başlar.

## Dosyalar (hepsi `pc/local/` altında, git'e girmez)

`app.log` (dönen günlük, jetonlar maskelenir), `crash.log`, `ui.json` (pencere boyutu — sürüm numarasıyla; sürüm değişince eski boyut bir kez atılır, varsayılan ekranın ~%92'sidir —, ses ayarı),
`state.json`, `plays.json`, `notified.json` (yerel durum).
