# Üretime Hazırlık Raporu — Nirvana Whitelabel Otonom Ortaklık Sistemi

> Kapsam: `main` dalı (commit `78a4233`) üzerindeki kod tabanı. Bu rapor, `FINAL_REPORT.md`,
> `FINAL_SUMMARY.md` ve `docs/B2B-RETAINER-RAPOR-v2.md` belgelerinde eksik kalan başlıkları
> tek çatı altında toplar. Her bölüm, repodaki **gerçek dosyalara** referans verir; repoda
> karşılığı olmayan iddialar "Açık Nokta" olarak işaretlenmiştir.

## 1. Kod Kalitesi, Test Disiplini ve Statik Analiz Güvencesi
- **Pytest:** `tests/` altında birim, entegrasyon, sözleşme (`tests/contracts/`), VLA (`tests/vla/`) ve iz (`tests/trace/`) testleri. Yapılandırma: `pyproject.toml`, `tests/conftest.py`.
- **AST manifest linter:** `tools/manifest_ast_linter.py` + `tests/test_manifest_ast_linter.py`.
- **Manifest kapıları:** `manifest_check.py` (strict preflight), `scripts/evaluate_manifest.py`, politika dosyaları `policies/opa/manifest.rego`, `policies/cue/manifest.cue`.
- **CI:** `.github/workflows/ci.yml` (sıfır-bypass pytest koşusu).
- **Açık Nokta:** Kökte geçici dosyalar (`_check_modules*.py`, `_extract_*.py`, `*.txt` çıktı dökümleri, `tests/contracts/*.bak/*.orig`) repoya girmiş; üretim öncesi temizlenmeli.

## 2. Dış Dünya Dayanıklılığı, Anti-Spam ve Ağ Düzeyi Güvenlik Mimarisi
- **TLS/JA4 parmak izi uyumu:** `nirvana/http_client.py` (curl_cffi tarayıcı taklidi) + `tests/test_http_client.py`; `nirvana/fingerprint_rotator.py`.
- **Devre kesici ve retry:** `circuit_breaker.py`, `http_client.py` (pybreaker + tenacity, üstel geri çekilme).
- **Anti-spam kadansı:** `nirvana/anti_spam_cadence.py`, `flood_guard.py`, `pacing.py`, `optout.py` (STOP/Unsubscribe zorunluluğu `form_submitter.py`).
- **Yetki sınırı:** `authorized_targets.txt`, `risk_guard.py`, `SECURITY.md`.

## 3. Oracle Cloud Infrastructure (OCI) Always Free Altyapı Entegrasyonu
- Kurulum: `oracle/nirvana_oracle_install.sh`, `scripts/oracle-setup.sh`, `scripts/deploy_oci_production.sh`, `scripts/oracle_bluegreen_deploy.sh`.
- Systemd birimleri: `oracle/nirvana-*.service|timer` (delivery, deliveryworker, dispatch, watchdog, idleguard, captcha, linkedin, pipeline, salesbot), `deploy/whitelabel-engine.service`.
- GitHub → OCI dağıtımı: `.github/workflows/nirvana-oracle-deploy.yml`.

## 4. Canlı Hizmet Testleri ve Saniyelik Kendi Kendini Onaran Sistem Mimarisi
- Denetleyici: `nirvana/supervisor.py` (süreç sağlık turu + yeniden başlatma), systemd `Restart=always` emniyet ağı.
- Olay güdümlü onarım: `eda/rulebook.yml`, `eda/self_healing_engine.py`, playbook'lar `eda/playbooks/remediate_delivery_worker.yml`, `scale_delivery_worker.yml`, `reset_payment_circuit.yml`, `restart_webchat.yml`.
- Canlı senaryo testleri: `tests/test_delivery_live.py`, `nirvana/live_scenarios.py`, `nirvana/service_readiness.py`.
- **Not:** "saniyelik" tespit süresi, supervisor tur aralığına bağlıdır (FINAL_SUMMARY: 60 sn tur + systemd anlık restart). 500 ms iddiası kodda ölçülmüş değildir.

## 5. Multi-Tenant Veri İzolasyonu ve Müşteri Karışıklığını Önleme
- `nirvana/tenant_isolation.py` (ContextVar tabanlı tenant bağlamı), `nirvana/tenant_db.py`, `sql/01_tenant_schema.sql` (tenant başına şema).
- Kuyruk düzeyi: `nirvana/celery_tenant.py` — Celery görev başlığına tenant kimliği mühürlenir; işçi tarafında bağlam geri yüklenir (`nirvana/celery_app.py`).
- Test: `tests/test_tenant_isolation.py`, manifest `GATE_26_session_isolation`.

## 6. Uçtan Uca Canlı Müşteri Senaryosu ve Otomatik Fırsat-Teslimat Döngüsü (€5.000)
- Keşif → nitelendirme → form: `lead_discovery.py`, `qualification_analyzer.py`, `form_submitter.py`.
- Kanıt ve ikna: `nirvana/micro_audit_proof.py`, `nirvana/financial_shock_card.py`, `core/profit_recipe.py`.
- Satış/kapanış: `telegram_sales_bot.py`, `nirvana/webchat_api.py`, `nirvana/self_serve_close.py`.
- Ödeme: `nirvana/payment.py` (Payoneer linki, `config.py` retainer €5.000/ay), `payment_safety.py`; sözleşme paketi `nirvana/contract_pack.py`.
- Teslimat: bkz. Bölüm 11.

## 7. 40+ Modülün GitHub Actions Üzerinden Modüler ve Kesintisiz Orkestrasyonu
- `.github/workflows/` altında keşif (`discover-*.yml`, `discovery-pipeline.yml`), besleme (`enterprise-feed.yml`, `publish-feed.yml`, `refill-on-low.yml`), Nirvana şeritleri (`nirvana-heavy.yml`, `nirvana-meta.yml`, `nirvana-proof.yml`, `nirvana-strategy.yml`, `nirvana-stealth-form.yml`), bekçiler (`pipeline-watchdog.yml`, `discovery-watchdog.yml`, `keepalive.yml`) ve `ci.yml`.
- Modül kaydı: `nirvana/registry.py`, `nirvana/nirvana.yaml`, `nirvana/github_orchestrator.py`, `nirvana/meta_orchestrator.py`.

## 8. Günde 400 Formun Akıllı Dağılımı ve Anti-Stuck Mekanizması
- Günlük kota: `nirvana/slot_gate.py`, `nirvana/state/daily_form_count.json`, `nirvana/watchdog_quota_agent.py`.
- Takılma önleme: `auto_runner.py` (alt süreç boyunca watchdog kalp atışı), `feed_ingest.py` (`FEED_SCAN_BUDGET_S`), `nirvana/queue_fuel_guard.py`, `nirvana/supply_guard.py`, `nirvana/idle_guard.py` (ayrıntı: `docs/B2B-RETAINER-RAPOR-v2.md` §4).

## 9. Saniyeler İçinde Devreye Giren Self-Healing ve Retry Döngüsü (GitHub Actions)
- `pipeline-watchdog.yml` ve `discovery-watchdog.yml` başarısız/takılı koşuları tespit edip yeniden tetikler; `refill-on-low.yml` kuyruk boşalınca besler; `keepalive.yml` zamanlanmış iş akışlarının devre dışı kalmasını önler (`nirvana/keepalive_guard.py`).
- Uygulama düzeyi retry: tenacity (`nirvana/http_client.py`), devre kesici sıfırlama EDA playbook'u.

## 10. Uçtan Uca Canlı Senaryo Güvencesi (€5.000 Akışı)
- Sözleşme testleri: `tests/contracts/test_payment_contract.py`, `test_webchat_contract.py`, `test_pact_contracts.py`.
- İz doğrulama: `tests/trace/tracetest-payment-delivery.yaml`, `tracetest-delivery-pipeline.yaml`, `tracetest-webchat-flow.yaml`; telemetri `otel/collector.yaml`, `otel/instrumentation.py`.
- Kapılar: `GATE_28_payment_contract`, `GATE_30_contract_trace` (bkz. `FINAL_SUMMARY.md` §2).

## 11. Abonelik Teslimat Motoru Kurulumu
Sistemdeki "abonelik", **aylık €5.000 retainer**'dır; teslimat motoru ödeme doğrulandıktan sonra hizmeti aylık döngüde üretir ve yenilemeyi tetikler. Kodda karşılığı:

| Katman | Dosya | Görev |
|---|---|---|
| Ödeme kapısı | `nirvana/payment.py`, `payment_safety.py` | Payoneer ödeme linki; `renewal_message()` aylık yenileme mesajı |
| İş kuyruğu | `nirvana/state/delivery_jobs.json`, `nirvana/delivery_runner.py` | Doğrulanmış ödeme → teslimat işi oluşturma ve sıraya alma |
| Teslimat işçisi | `nirvana/delivery_worker.py` | 4 hizmet: `infra-sweep`, `contact-audit`, `renewal-monitor`, `vla-audit`; raporları `delivery_reports.json`'a yazar |
| Çift kanal teslim | `nirvana/dual_delivery.py` | Raporu müşteriye iki kanaldan iletir |
| Pipeline | `workers/delivery_pipeline.py` | Teslimat adımlarının uçtan uca zinciri |
| Tenant güvenliği | `nirvana/celery_tenant.py`, `nirvana/tenant_isolation.py` | Her işe tenant kimliği; X firmasının çıktısı Y'ye gidemez |
| Aylık değer + yenileme | `nirvana/retention_agent.py`, `nirvana/retainer_report_agent.py`, `.github/workflows/nirvana-strategy.yml` (cron `17 9 1 * *`) | Aylık değer raporu + yenileme ödeme linki |
| OCI çalışma zamanı | `oracle/nirvana-delivery.service/.timer`, `oracle/nirvana-deliveryworker.service/.timer` | İşçinin sunucuda zamanlanmış/sürekli koşusu |
| Self-healing | `eda/playbooks/remediate_delivery_worker.yml`, `scale_delivery_worker.yml` | İşçi çökmesinde onarım/ölçekleme |
| Doğrulama | `tests/test_delivery_worker.py`, `tests/test_delivery_live.py`, `tests/test_payoneer_retainer_config.py`, `manifest_check.py` (hizmet kümesi kontrolü) | Regresyon ve manifest kapısı |

**Kurulum (OCI):**
```bash
sudo bash oracle/nirvana_oracle_install.sh          # birimleri kurar
sudo systemctl enable --now nirvana-deliveryworker.timer nirvana-delivery.timer
systemctl list-timers 'nirvana-*'                    # zamanlama kontrolü
python -m pytest tests/test_delivery_worker.py tests/test_payoneer_retainer_config.py -q
python manifest_check.py                             # delivery_worker hizmet kümesi kapısı
```

**Açık Noktalar (dürüst durum):**
- Payoneer üzerinde **otomatik tekrarlayan tahsilat yoktur**; yenileme, aylık ödeme linki gönderimiyle yapılır (`ENTERPRISE_OPERATIONS.md`: komutlar tahsilat/abonelik başlatmaz). Gerçek abonelik faturalaması isteniyorsa ödeme sağlayıcısının tekrarlayan ödeme API'si ayrıca bağlanmalıdır.
- Önceki oturumda bahsedilen `src/delivery/engine.py`, `src/delivery/tenant_binding.py`, `.github/workflows/modules/delivery-worker.yml`, `tests/test_delivery_health.py` dosyaları **hiçbir dalda (master/main/nirvana-live) bulunmamaktadır**; o değişiklikler push edilemediği için kaybolmuştur. Aynı işlevler yukarıdaki mevcut modüllerle karşılanmaktadır.

## 12. Sonuç ve Ortaklık Modelinin Yayına Alım Yol Haritası
1. `main` ↔ `master` ayrışmasını giderin: geliştirme `main`'de, `master` bot besleme commit'leriyle ilerliyor; varsayılan dalı netleştirin.
2. Kökteki geçici/çıktı dosyalarını temizleyin (Bölüm 1).
3. OCI'de teslimat timer'larını etkinleştirip `tests/test_delivery_live.py`'yi canlı ortamda koşun.
4. Yenileme için tekrarlayan ödeme ihtiyacına karar verin (Bölüm 11, Açık Noktalar).
5. Supervisor tespit süresini ölçüp rapora gerçek değeri işleyin (Bölüm 4).
