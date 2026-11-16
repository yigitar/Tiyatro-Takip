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

Konsol penceresi açmadan başlatmanın üç yolu vardır; hepsi aynı başlatıcıyı (`pc/launcher.py`) kullanır:

1. **Önerilen: `pc\tiyatro_takip.pyw` dosyasına çift tıkla.** Windows `.pyw` dosyalarını `pyw.exe` -> `pythonw.exe` ile açar (diğer botların `pythonw` ile çalışmasıyla aynı yöntem); hiçbir pencere açılmaz, imzasız `.exe` ya da yeni sistem görevi gerekmez. Çift tıklamada Windows'un genel Python'u açar; başlatıcı uygulamayı proje `.venv`'inin `pythonw.exe`'sinde, ayrık ve penceresiz (`CREATE_NO_WINDOW`, boş std tutamaçları) bir alt süreç olarak başlatıp kendisi çıkar. Proje venv'i içinden çalıştırılırsa uygulama aynı süreçte açılır. İstersen bu dosyaya kendin kısayol (`.lnk`) yapabilirsin; depo hiçbir kısayolu Masaüstü/Başlat menüsüne yazmaz.
2. `pc\start.bat`: `start "" ...pythonw.exe pc\tiyatro_takip.pyw` çalıştırıp hemen `exit` eder (yedek yöntem; .bat'ı Explorer açtığı için pencere çok kısa bir an görünebilir, kalmaz).
3. Windows açılışında başlatma (Ayarlar menüsü): `HKCU\...\Run` altına `"<venv>\Scripts\pythonw.exe" "<depo>\pc\tiyatro_takip.pyw"` yazılır (aynı başlatıcı, aynı penceresiz yöntem). Eski sürümle yazılmış `-c ...` komutlu değer çalışmaya devam eder; kutuyu kapatıp açınca yenisiyle değişir.

Depo kökünden `python -m pc.app` de olur. Uygulama tek kopya çalışır (adlandırılmış Windows mutex'i; süreç çökse de işletim sistemi bırakır, eski kilit dosyası olmaz): ikinci başlatma (çift tıklama, .bat ya da Run anahtarı) açık pencereyi öne getirir (bulamazsa "zaten çalışıyor" der) ve çıkar. Pencereyi kapatmak uygulamayı kapatır, küçültmek çalışmaya devam ettirir. Başlatıcının kendisi bir hatayla düşerse de `pc/local/crash.log` + hata penceresi çıkar (aşağıya bak).

## Kullanım

- Pencere düzeni (dalga 12): sabit/durum parçaları solda dar bir kenar çubuğundadır (genişlik ~280 px, DPI ile ölçeklenir; içerik sığmazsa kendi kaydırma çubuğu çıkar): bilgi değerleri (Son tarama, Mod, Sonraki açılış, GitHub, PC kaydı, Bekleyen bildirim) alt alta anahtar/değer, altında "Listeyi güncelle" ve "Duraklat", sonra üç kaynak kartı alt alta (başlık satırında simge, ad, "Kaynağı aç" / "Portalı aç"; uzun ad ikinci satıra sarar), en altta "Açıklama". Sağ tarafta Oyunlar | Etkinlik günlüğü sekmeleri vardır; liste tam genişlik ve yüksekliğin en az %55'ini alır (sürüklenebilir ayırıcı korunur), altındaki ayrıntı iki sütundur: solda başlık/özellikler/Aktif/hedef ekleme düğmeleri + seans tablosu ve altında Özet, sağda Hedefler kutusu ve altında Oyuncular + Yapım; ayrıntı alanı ~820 px'ten darsa bloklar alt alta dizilir, sığmayan içerik kaydırılır. Pencere 1000 px'ten (DPI ile ölçekli) darsa kenar çubuğu üstte bir şeride dönüşür (durum | kartlar yan yana, Açıklama altında; yüksekliği pencerenin ~%40'ıyla sınırlı, aşarsa kaydırılır). Özet metnindeki boş satır yığınları (boşluk/nbsp/CR dahil) en fazla bir boş satıra indirilir ve baştaki/sondaki boşluklar kırpılır (yalnız gösterimde, kayıtlı veri değişmez; Telegram oyun ekranı özeti zaten tek satıra indirir).
- Liste: varsayılan sıra en yakın seans, sonra başlıktır (takip durumuna bakılmaz: kutuyu işaretlemek satırı yerinden oynatmaz; "Takip" başlığına tıklayarak takipte olanları öne alabilirsin); ilk sütun ☑/☐ takip kutusudur (tıklayınca değişir). KALKTI işareti son "Listeyi güncelle"ye göredir; açılmamış yeni öğelerin adı kalın mor görünür. Italik gri-mavi değerler kaynaktan alınmamış, türetilmiş/varsayımdır (ayrıntı: ana README).
- Liste sütunları (dalga 13): iki başlık hücresinin arasındaki çizgiyi sürükleyerek genişliği değiştir (imleç yatay ok olur; en az genişlik var, sıralama tetiklenmez); çizgiye çift tıklamak sütunu içeriğine (başlık + tüm satırlar, üst sınırlı) sığdırır. Başlığı basılı tutup başka yere sürükleyince sütun taşınır (mavi bırakma çizgisi + gölge başlık); kısa tıklama eskisi gibi sıralar. Başlığa sağ tıklayınca menü: "Sütunları varsayılana sıfırla" ve sütun göster/gizle kutuları (en az bir sütun kalır; Takip dahil hepsi taşınabilir/gizlenebilir, menüden geri gelir). Sıra, gizlenenler ve elle verilen genişlikler bu bilgisayarda `pc/local/ui.json` içinde `columns` altında (sütun anahtarıyla) saklanır; bilinmeyen anahtarlar yok sayılır, yeni sütunlar varsayılan yerine eklenir. Elle genişlik verilmeyen sütunlar kalan alana sığar (varsayılan genişlikler orantılı daralır, en az genişliğin altına inmez); yalnız en az genişlikler bile sığmazsa (veya elle verilen genişlikler pencereyi aşarsa) yatay kaydırma çubuğu çıkar ve başlık gövdeyle birlikte kayar.
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

Başlatıcılar konsolsuz (pythonw) çalıştığı için uygulama sessizce kapanmaz: başlatma ya da
çalışma sırasında herhangi bir hata olursa bir hata penceresi açılır ve ayrıntı (jetonlar
maskelenmiş) şu dosyalara yazılır:

- `pc/local/crash.log` (depo klasörü altında; yazılamazsa `%TEMP%/tiyatro-takip-crash.log`)
- `pc/local/app.log` (normal çalışma günlüğü)

Pencerede dosyanın tam yolu gösterilir. `python -m pc.app` ya da `python -m pc` aynı koruma
ile başlar.

## Dosyalar (hepsi `pc/local/` altında, git'e girmez)

`app.log` (dönen günlük, jetonlar maskelenir), `crash.log`, `ui.json` (pencere boyutu — sürüm numarasıyla; sürüm değişince eski boyut bir kez atılır, varsayılan ekranın ~%92'sidir —, ses ayarı),
`state.json`, `plays.json`, `notified.json` (yerel durum).
