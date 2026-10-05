# Yerel Telegram Bot API (Local Bot API Server) — $0 kurulum ve aktivasyon

Bu sistem, yoğun müşteri trafiğinde Telegram'ın **bulut API** limitlerine
takılmamak için opsiyonel olarak **kendi makinesinde çalışan** telegram-bot-api
sunucusunu kullanabilir. Sunucu tanımlı değilse veya sağlıksızsa sistem
**otomatik olarak bulut API'ye düşer** — satış hattı hiçbir koşulda durmaz.

## Limit karşılaştırması

| Kriter | Bulut API | Yerel Bot API (Local Mode) |
|---|---|---|
| Ağ sınırı / hız | Dakikada ~30 mesaj | Yerel sunucu hızı (sınırsız işlem) |
| Dosya yükleme limiti | 20 MB | 2.000 MB (2 GB) |
| Flood wait | Doğrudan API engeli / geçici ban | Sunucu tarafında akıllı bekleme |
| Token doğrulama | Sürekli dış sunucu bağlantısı | Yerel bağlantı (kesintisiz erişim) |

Her iki modda da `flood_guard.py` hız sınırı + `RetryAfter` kapısı aktiftir:
mod seçimi yalnızca **tavanı** değiştirir, güvenlik ağını kaldırmaz.

## 1) my.telegram.org anahtarlarını al

`my.telegram.org` → *API development tools* → `api_id` ve `api_hash`.
Bu iki değer **asla repoya yazılmaz**; yalnızca Oracle `/opt/devsolve/.env` içinde tutulur.

## 2) Sunucuyu başlat (Docker, aynı VM)

```bash
cd /opt/devsolve
# .env içine anahtarları ekle (yalnızca bu makinede):
#   TELEGRAM_API_ID=1234567
#   TELEGRAM_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
docker compose --profile local-api up -d telegram-bot-api
# /var/lib/telegram-bot-api altında veri hacmi oluşur; port yalnızca loopback'e açılır.
curl -s http://127.0.0.1:8081/bot<TOKEN>/getMe
```

Konteyner `TELEGRAM_LOCAL=1` ile çalışır (local mode): dosyalar diske yazılır,
20 MB sınırı kalkar, indirme/yükleme yerel diskten yapılır.

## 3) Botu yerel moda al

```bash
# /opt/devsolve/.env
TELEGRAM_BOT_API_BASE_URL=http://127.0.0.1:8081
```

Sonra satış botunu yeniden başlat:

```bash
sudo systemctl restart nirvana-salesbot.service
journalctl -u nirvana-salesbot.service -n 20 --no-pager -o cat | grep -i "Telegram API"
# Beklenen: "Telegram API: local (yerel telegram-bot-api saglikli ...)"
```

Doğrulama (mesaj göndermeden):

```bash
cd /opt/devsolve && .venv/bin/python -c "from telegram_bot_api import status_line; print(status_line())"
```

## 4) Geri alma (fail-safe)

`TELEGRAM_BOT_API_BASE_URL` satırını sil ve servisi yeniden başlat → bulut API.
Sunucu ayakta ama sağlıksızsa (getMe hata veriyorsa) sistem zaten kendiliğinden
buluta düşer ve journal'a `Local Bot API unreachable — falling back to cloud API` yazar.

## 5) CI üzerinden anahtar geçirmek (opsiyonel)

`nirvana-oracle-deploy.yml` bu üç değeri boş geçirir (secret tanımlı değilse
`.env`'deki mevcut değerler korunur). GitHub Secret olarak tanımlayıp workflow'un
`env:` bloğuna eklerseniz deploy otomatik yazar:

```yaml
TG_API_ID: ${{ secrets.TELEGRAM_API_ID }}
TG_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
TG_BOT_API_BASE_URL: ${{ secrets.TELEGRAM_BOT_API_BASE_URL }}
```

Not: Secret adı GitHub'da yoksa editör "Context access might be invalid" uyarısı
verir; önce `gh secret set TELEGRAM_API_ID` ile secret'ı oluşturun.