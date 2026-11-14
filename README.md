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

## Testleri çalıştırma

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```
