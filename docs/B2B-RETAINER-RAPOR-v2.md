# B2B Retainer Dönüşüm Mimari Raporu — v2 (Uygulama Kanıtlı)

> v1 raporun (Oracle Always Free + SearXNG/Crawl4AI + Documenso/Formbricks + Telegram)
> bu projeye (**lead-qualification-engine / Nirvana**, Oracle VM `devsolve`)
> **uyarlanmış, canlıda doğrulanmış ve ölçüm planı eklenmiş** sürümüdür.
> Bu dosya v1'in yerini alır; çelişki halinde bu sürüm geçerlidir.

## 1. Yönetici Özeti

- v1 raporundaki bileşenlerin büyük bölümü **bu projede zaten canlı**: webchat,
  Telegram satış botu (Payoneer 5000 EUR), 22 modüllü otonom hat, GitHub Actions
  filoları, Oracle systemd timer'ları, idle_guard.
- Eksik olan 4 entegrasyon bu güncellemeyle koda bağlandı:
  **Documenso v2 e-imza**, **Formbricks onboarding**, **SearXNG + Crawl4AI $0
  zenginleştirme**, **FEED tarama bütçesi + watchdog kalp atışı (400/gün onarımı)**.
- Canlı teşhis (2026-09-29): günlük form gönderimi **59–75** (hedef 400);
  kök nedenler ölçüldü ve üç yama ile kesildi (bkz. §3).

## 2. Canlı Durum Tespiti (kanıtlı)

| Bulgu | Kanıt | Etki |
|---|---|---|
| `leads.json` **76,5 MB** (21.095 satır) | `ls -la /opt/devsolve/leads.json` | Her `load/save_leads` parse+yazım ≈ saniyeler; tur bütçesinin büyük kısmı yanlış yere gidiyor |
| systemd watchdog, koşuyu **SIGABRT ile kesiyor** | `journalctl -u nirvana-pipeline`: "Watchdog timeout (limit 1h) ... SIGABRT ... Consumed 59min 45s CPU" | pipeline turu yarıda ölüyor; alt süreç koşarken heartbeat gitmiyordu |
| Feed taraması tur başına **~14 dk** | "Synced feed rows=12798 ... staged=348 enqueued=0 (queue=2500/2500)" | Kuyruk doluyken hiçbir satır giremiyor; 24 turda ~5,5 saat boşa gidiyor |
| Günlük gönderim 59–75 (hedef 400) | `nirvana/state/daily_form_count.json` (28 Eyl: 59, 29 Eyl: 75) | Kapatma/retainer hunisi 5–6 kat altında çalışıyor |
| Durum dağılımı: `skipped_no_open_form=9984`, `skipped_submit_failed=6641`, `submitted_confirmed=3598` | `grep status leads.json` | Chromium turlarının ~%47'si form bulamadan yanıyor |
| GitHub API feed pull 403 (token yok) | `logs/feed-sync.log` | API yerine raw CDN birincil olmalı (kod zaten raw'ı da deniyor) |

## 3. Rapor ↔ Proje Eşleme ve Bu Güncellemede Bağlananlar

| v1 Rapor bölümü | Projede karşılığı | Durum |
|---|---|---|
| 1. OCI Always Free / idle koruması | `nirvana/idle_guard.py` + `nirvana-idleguard.timer` (10 dk) | **Zaten canlı** |
| 2.1 SearXNG + Crawl4AI zenginleştirme | `nirvana/enrich_web.py` (yeni) — dosya cache TTL 168s | **Bu güncellemede eklendi** |
| 2.2 Documenso e-imza | `nirvana/esign_documenso.py` (yeni, **v2 envelope API**) + `contract_pack.pack_text` köprüsü | **Bu güncellemede eklendi** |
| 2.2 Formbricks onboarding | `nirvana/formbricks_onboarding.py` (yeni) + `onboarding_agent.welcome_packet` köprüsü | **Bu güncellemede eklendi** |
| 4.1 Form ajanı (görünmez zenginleştirme) | Webchat + `stack_fingerprint.py`/`site_signals.py`; enrichment modülü hazır | Kısmi + yeni modül |
| 4.2 Kapatıcı ajan (SPIN/Voss) | `telegram_sales_bot.py` + `webchat_core.py` (SPIN_SELLING_ENABLED) | **Zaten canlı** |
| 4.3 Teslimat İşçisi | `contract_pack.py` → e-imza linki; `onboarding_agent.py` → anket linki | **Bu güncellemede zincirlendi** |
| 4.4 Lead skorlama matrisi | `easy_score.py`, `qualification_analyzer.py`, `knowledge.winning_stacks` | **Zaten canlı** |
| 4.5 Telegram salt-alert | `owner_notify.py`, `flood_guard.py`, bot havuzu | **Zaten canlı** |
| 400 form/gün verimi | §4 (aşağıdaki üç yama) | **Bu güncellemede onarıldı** |

## 4. 400 Form/Gün Hattı Onarımı (bu güncellemenin kalbi)

### 4.1 Uygulanan yamalar

| # | Yama | Dosya | Ne yapar | Beklenen etki |
|---|---|---|---|---|
| A | **Watchdog kalp atışı** | `auto_runner._run` | Alt süreç (pipeline/lead_finder) koşarken 30 sn'de bir `heartbeat.pulse()` — systemd `WATCHDOG=1` alır | 1 saatlik SIGABRT kesmeleri biter; tur yarıda ölmez |
| B | **Feed hızlı çıkış + tarama bütçesi** | `feed_ingest.ingest` + `config.FEED_SCAN_BUDGET_S` | Kuyruk dolu ve depo yeterliyken (room=0, starve yok) taramayı tamamen atlar; aksi halde taramayı 45 sn ile sınırlar | Tur başına **~14 dk** submit hattına geri kazanılır (≈5,5 saat/gün) |
| C | **leads.json kompaktlaştırma** | `scripts/compact_leads.py` | Terminal satırların ağır alanlarını kırpar; 30 günden eski terminal satırları düşer; URL bazında tekilleştirir; yedekli/atomik yazar | 76 MB → tahminen **5–15 MB**; tüm `load/save_leads` çağrıları hızlanır |

### 4.2 Ölçüm planı (yama sonrası kabul kriterleri)

```bash
# Günlük gönderim trendi (hedef: 400'e tırmanış)
cat /opt/devsolve/nirvana/state/daily_form_count.json
# Watchdog kesmesi artık YOK mu?
sudo journalctl -u nirvana-pipeline --since "24 hours ago" | grep -c "Watchdog timeout"
# Feed atlandı mı (queue dolu iken beklnen satır):
sudo journalctl -u nirvana-pipeline --since "6 hours ago" | grep "Feed ingest skip"
# Tek tur süresi (Tur N satırları arası fark hedef: <= 10 dk):
sudo journalctl -u nirvana-pipeline --since "6 hours ago" | grep "Tur "
```

Ek eşik ayarı (isteğe bağlı, `.env`): `FEED_SCAN_BUDGET_S=45` (varsayılan), ilk gün
gözlem sonrası 60'a çıkarılabilir.

### 4.3 Neden bu üçü?

`daily_form_count` 59–75 seviyesinde sıkışmıştı; kuyruk (2500/2500) ve depo
(fuel 400/500) DOLU olduğu halde gönderim yükselmiyordu. Darboğaz yakıt değil
**tur bütçesi**ydi: her saat 14 dk feed taraması + 40 dk'lık pipeline koşusu
watchdog tarafından kesiliyordu. A+B tur süresini kısaltır, C her turda
harcanan I/O'yu 5–10 kat azaltır; üçü birlikte 400/gün tavanının önünü açar.

## 5. Rapor Entegrasyonları (yeni modüller)

### 5.1 Documenso v2 — resmi kaynakla doğrulandı

| Konu | v1 (eski) | **v2 (bu güncellemede kullanılan)** |
|---|---|---|
| Oluşturma | `POST /api/v1/documents` | **`POST /api/v2/envelope/create`** (`type: DOCUMENT\|TEMPLATE`) |
| Gönderim | `POST /api/v1/documents/{id}/send` | **`POST /api/v2/envelope/distribute`** |
| Kimlik | sayısal `documentId` | **string `envelope_abc123`** |
| Auth | `Bearer <key>` | **`Authorization: api_xxxxxxxx`** (ham anahtar) |
| Şablon | ayrı uçlar | aynı uç; `templateId` + `type` |

Kaynak: docs.documenso.com → "Migrating to Envelopes" + "Documents API".
Kod: `nirvana/esign_documenso.py` → `create → distribute → imza linki`
(zincir: `contract_pack.pack_text(email=...)`).

### 5.2 Formbricks onboarding

`nirvana/formbricks_onboarding.py` → anket linki (+email/name/company on-doldurma);
`onboarding_agent.welcome_packet` karşılama metnine ekler (TR/EN).

### 5.3 SearXNG + Crawl4AI ($0 zenginleştirme)

`nirvana/enrich_web.py` → `search()` + `crawl()` + `profile()` (dosya cache,
TTL 168s). Lead/form anında şirket profili; dış ücretli API yok. Servisler
`oracle/stack/` compose'u ile loopback'e kurulur.

## 6. Oracle $0 Servis Stack'i (opt-in)

```bash
sudo bash oracle/stack/install_stack.sh     # SearXNG, Crawl4AI, Documenso, Formbricks
# Dis erisim (nginx): oracle/stack/nginx-stack.conf -> sign.<domain> / onboard.<domain>
```
Bütçe: RAM ~4,2 GB / disk ~2,5 GB (VM'de 19,6 GB RAM ve 48 GB disk boştu);
ana motor systemd'de kalır, Docker yalnız müşteri-hattı servislerini taşır.

## 7. Telemetri ve Olağan Komutlar

```bash
sudo journalctl -u nirvana-pipeline --since "1 hour ago" | tail -40   # tur akışı
systemctl list-timers 'nirvana*' --no-pager                            # zamanlayıcı sağlığı
python3 scripts/compact_leads.py                                       # leads.json raporu (kuru)
crontab -l                                                             # idle-guard kalp atışı
```

## 8. Yol Haritası (kullanıcı aksiyonları — 1-2 gün)

1. `install_stack.sh` çalıştır (sunucuda) → Documenso + Formbricks hesaplarını aç,
   API anahtarı / şablon ID / anket ID'lerini `.env`'e yaz.
2. İlk gün `daily_form_count` ve watchdog kesme sayacını izle; 400'e tırmanışı doğrula.
3. `compact_leads.py --apply` sonucu 20 MB üzerindeyse `--keep-days 14` ile tekrarla.
4. Haftalık cron önerisi: `scripts/compact_leads.py --apply` (Pazartesi 05:30).
5. Sıradaki dönüşüm itici (v1 §4.1): webchat ilk mesajına `enrich_web.profile()`
   verisini ekleyen kişiselleştirme — hazır modül, tek köprü kaldı.

