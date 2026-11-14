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
kökünden `python -m pc.app`. Uygulama tek kopya çalışır; ikinci başlatma "zaten çalışıyor"
der. Pencereyi kapatmak uygulamayı kapatır, küçültmek çalışmaya devam ettirir.

## Kullanım

- Liste: izlenen oyunlar üstte; YENİ / KALKTI işaretleri son "Listeyi güncelle"ye göredir.
- Oyun seçilince: Biletinial bağlantısı, seanslar, hedefler (Düzenle / Sil), Aktif anahtarı,
  Yetişkin/Çocuk hedefi ekleme. Değişiklikler Telegram menüsüyle aynı `config.json`'a yazılır.
- Hedef düzenleyici: kişi sayısı, günler, saat sınırları, güne özel saat, hariç tarihler.
- Alt durum çubuğu: son tarama, mod (Normal/Hızlı), sonraki açılış, GitHub sağlığı, PC kaydı
  yaşı, bekleyen bildirim, hatalar (üzerine gelince ayrıntı).
- Ayarlar > Sesli uyarı: bildirim penceresindeki bip'i açar/kapatır.

## Dosyalar (hepsi `pc/local/` altında, git'e girmez)

`app.log` (dönen günlük, jetonlar maskelenir), `ui.json` (pencere boyutu, ses ayarı),
`state.json`, `plays.json`, `notified.json` (yerel durum).
