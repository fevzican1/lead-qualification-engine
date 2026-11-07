# Üretime Hazırlık Raporu — Nirvana Whitelabel Otonom Ortaklık Sistemi (Doğrulanmış Sürüm)

> **Kapsam:** `main` dalı, HEAD `113cc43` (PR #1 merge'ü: `8a748bd` + `78a4233`).
> **Amaç:** `docs/URETIME-HAZIRLIK-RAPORU.md` içindeki 12 bölümü repodaki gerçek dosyalarla tek tek eşleştirmek,
> tutarsızlıkları düzeltmek ve Abonelik Teslimat Motoru kurulumunu doğrulamak.
> **Yöntem:** Dosya varlık taraması (55 referans), kaynak kod incelemesi (grep), pytest denemesi.

## Özet — Önceki rapora göre düzeltmeler
| # | Bulgu | Durum |
|---|---|---|
| 1 | `payment_safety.py` kökte; `nirvana/payment_safety.py` **yok** | Rapor yolu doğru (kök), ancak "nirvana/" altında sanılmamalı |
| 2 | `nirvana/supervisor.py` → `HEARTBEAT_INTERVAL_S = 0.5` (500 ms) | Önceki rapordaki "500 ms kodda yok" ifadesi **yanlış**; değer kodda tanımlı, ancak canlı ölçüm yok |
| 3 | `config.py` ödeme kapıları sayısal `PRICE_USD`/`PAYMENT_AMOUNT = 5000`; etiket `PRICE_LABEL = "€5.000"` | €5.000 yalnızca gösterim etiketi; kapı değişken adları USD |
| 4 | `main` ile `master` ortak geçmiş paylaşmıyor (sığ klonda merge-base yok; main'de 141, master'da 50+ bağımsız commit) | Varsayılan dal hâlâ `master` — Açık Nokta |
| 5 | Kökte 30 geçici dosya (`_*.py`, `*.txt`, `tests/contracts/*.bak` / `*.orig`, `tools/_tmp_violation_test.py`) | Açık Nokta |
| 6 | pytest bu sandbox'ta yapılandırma nedeniyle koşturulamadı (aşağıya bkz.) | Açık Nokta |

## 1. Kod Kalitesi, Test Disiplini ve Statik Analiz Güvencesi
- ✅ `tests/` (50+ test modülü), `tests/contracts/`, `tests/vla/`, `tests/trace/`, `pyproject.toml`, `tests/conftest.py` mevcut.
- ✅ `tools/manifest_ast_linter.py`, `tests/test_manifest_ast_linter.py`, `manifest_check.py`, `scripts/evaluate_manifest.py`, `policies/opa/manifest.rego`, `policies/cue/manifest.cue`, `.github/workflows/ci.yml` mevcut.
- ⚠️ **Açık Nokta – geçici dosyalar (30 adet):** `_check_modules.py`, `_check_modules2.py`, `_env_probe.py`, `_extract_all.py`, `_extract_failures.py`, `_extract_full.py`, `_fix_registry_test.py`, `_inspect_api.py`, `_inspect_pact.py`, `_read_config_lines.py`, `_manifest_out.txt`, `_pytest_full.txt`, `output.txt`, `ptest.txt`, `pytest_results.txt`, `test_output.txt`, `test_result.txt`, `pact_*.txt`, `payment_test.txt`, `contract_pack_orig.txt`, `tests/contracts/conftest.py.bak|.orig`, `tests/contracts/test_payment_contract.py.bak`, `tools/_tmp_violation_test.py`, kökte `test_pact_api.py`.
- ⚠️ **Açık Nokta – pytest yapılandırması:** Temiz bir ortamda `pytest` + `pytest-asyncio` kurulumuyla `tests/test_tenant_isolation.py` toplama hatası (`'asyncio' not found in markers`) ve ardından `asyncio_default_fixture_loop_scope` uyarısının hata olarak yükseltilmesi (INTERNALERROR) gözlendi. `pyproject.toml`'a `asyncio_default_fixture_loop_scope = "function"` ve `asyncio` marker'ı eklenmeli; `requirements.txt`'e test bağımlılıkları sabitlenmeli.

## 2. Dış Dünya Dayanıklılığı, Anti-Spam ve Ağ Düzeyi Güvenlik
- ✅ `nirvana/http_client.py`, `tests/test_http_client.py`, `nirvana/fingerprint_rotator.py`, `circuit_breaker.py`, `nirvana/anti_spam_cadence.py`, `flood_guard.py`, `pacing.py`, `optout.py`, `form_submitter.py`, `authorized_targets.txt`, `risk_guard.py`, `SECURITY.md` — tamamı mevcut.

## 3. OCI Always Free Altyapı Entegrasyonu
- ✅ `oracle/nirvana_oracle_install.sh`, `scripts/oracle-setup.sh`, `scripts/deploy_oci_production.sh`, `scripts/oracle_bluegreen_deploy.sh`, `deploy/whitelabel-engine.service`, `.github/workflows/nirvana-oracle-deploy.yml` mevcut.
- ✅ `oracle/` altında 33 systemd birimi; önceki rapordaki listeye ek olarak: `autopilot`, `fuel`, `jobwatch`, `quality`, `sentinel`, `supervisor`, `webchat` birimleri ve `oracle/deploy_webchat.sh`, `oracle/webchat-nginx.conf`, `oracle/tmux_autopilot.sh`, `oracle/manifest_preflight.sh`.

## 4. Canlı Hizmet Testleri ve Saniyelik Self-Healing
- ✅ `nirvana/supervisor.py`: `HEARTBEAT_INTERVAL_S = 0.5` (500 ms kalp atışı, `time.sleep(self.heartbeat_interval_s)` döngüsü); `oracle/nirvana-supervisor.service|.timer` emniyet ağı.
- ✅ `eda/rulebook.yml`, `eda/self_healing_engine.py`; playbook'lar: `remediate_delivery_worker.yml`, `scale_delivery_worker.yml`, `reset_payment_circuit.yml`, `restart_webchat.yml` ve raporda eksik olan **`rollout_vla_model.yml`**.
- ✅ `tests/test_delivery_live.py`, `nirvana/live_scenarios.py`, `nirvana/service_readiness.py` mevcut.
- ⚠️ **Açık Nokta:** 500 ms kodda bir yapılandırma değeridir; üretimde uçtan uca tespit→onarım süresi ölçülmemiştir.

## 5. Multi-Tenant Veri İzolasyonu
- ✅ `nirvana/tenant_isolation.py`, `nirvana/tenant_db.py`, `sql/01_tenant_schema.sql`, `nirvana/celery_app.py`, `tests/test_tenant_isolation.py`.
- ✅ `nirvana/celery_tenant.py` mühürleme mekanizması doğrulandı: `inject_tenant_headers()` görev başlığına `tenant_id` + `tenant_schema` yazar; `bind_tenant_from_headers()` işçi tarafında bağlamı geri yükler; `@tenant_task` dekoratörü `_tenant_headers`'ı ayıklayıp bağlar.

## 6. Uçtan Uca Müşteri Senaryosu (€5.000)
- ✅ `lead_discovery.py`, `qualification_analyzer.py`, `form_submitter.py`, `nirvana/micro_audit_proof.py`, `nirvana/financial_shock_card.py`, `core/profit_recipe.py`, `telegram_sales_bot.py`, `nirvana/webchat_api.py`, `nirvana/self_serve_close.py`, `nirvana/payment.py`, `payment_safety.py` (kök), `nirvana/contract_pack.py`.
- ℹ️ `config.py`: `PRICE_USD = 5000`, `ENTERPRISE_RETAINER_USD = 5000`, `PAYMENT_AMOUNT = 5000`, `PRICE_LABEL = "€5.000"` (yorum: "Nirvana retainer: €5.000 EUR aylık"). Para birimi tutarlılığı için değişken adları netleştirilmeli.

## 7. GitHub Actions Orkestrasyonu
- ✅ `.github/workflows/` altında 24 iş akışı. Raporda geçmeyen ek akışlar: `discover-cc-eu|global|platform|tr.yml`, `discover-tranco-sitemap.yml`, `discover.yml`, `harvest-shard.yml`, `payload_optimizer.yml`, `oracle-debug-salesbot.yml`, `oracle-diagnose.yml`.
- ✅ `nirvana/registry.py`, `nirvana/nirvana.yaml`, `nirvana/github_orchestrator.py`, `nirvana/meta_orchestrator.py` mevcut (`nirvana/` altında 86 dosya).

## 8. Günde 400 Form ve Anti-Stuck
- ✅ `nirvana/slot_gate.py`, `nirvana/state/daily_form_count.json`, `nirvana/watchdog_quota_agent.py`, `auto_runner.py`, `feed_ingest.py`, `nirvana/queue_fuel_guard.py`, `nirvana/supply_guard.py`, `nirvana/idle_guard.py`.

## 9. GitHub Actions Self-Healing ve Retry
- ✅ `pipeline-watchdog.yml`, `discovery-watchdog.yml`, `refill-on-low.yml`, `keepalive.yml`, `nirvana/keepalive_guard.py`, `eda/playbooks/reset_payment_circuit.yml`.

## 10. Uçtan Uca Senaryo Güvencesi
- ✅ `tests/contracts/test_payment_contract.py`, `test_webchat_contract.py`, `test_pact_contracts.py`; `tests/trace/tracetest-*.yaml` (3 adet); `otel/collector.yaml`, `otel/instrumentation.py`; `tests/test_gates_25_29.py`.
- ⚠️ `tests/contracts/test_minimal.py` 14 baytlık boş/yer tutucu dosya — kaldırılmalı veya doldurulmalı.

## 11. Abonelik Teslimat Motoru Kurulumu — Doğrulama Sonuçları
| Katman | Dosya | Varlık | İçerik doğrulaması |
|---|---|---|---|
| Ödeme kapısı | `nirvana/payment.py`, `payment_safety.py` (kök) | ✅ | `def renewal_message(name="")` satır 46 |
| İş kuyruğu | `nirvana/state/delivery_jobs.json`, `nirvana/delivery_runner.py` | ✅ | — |
| Teslimat işçisi | `nirvana/delivery_worker.py` | ✅ | `SERVICES` sözlüğünde 4 hizmet (`infra-sweep`, `contact-audit`, `renewal-monitor`, `vla-audit`) ve dağıtım tablosu `_svc_*` fonksiyonlarına bağlı; `REPORTS_NAME = "delivery_reports.json"` |
| Rapor deposu | `nirvana/state/delivery_reports.json` | ✅ | — |
| Çift kanal teslim | `nirvana/dual_delivery.py` | ✅ | — |
| Pipeline | `workers/delivery_pipeline.py` | ✅ | — |
| Tenant güvenliği | `nirvana/celery_tenant.py`, `nirvana/tenant_isolation.py` | ✅ | Başlık mühürleme + işçide geri yükleme (Bölüm 5) |
| Aylık değer + yenileme | `nirvana/retention_agent.py`, `nirvana/retainer_report_agent.py`, `nirvana-strategy.yml` | ✅ | cron `17 9 1 * *` (aylık) ve `17 3 * * *` (günlük) doğrulandı |
| OCI çalışma zamanı | `oracle/nirvana-delivery.service/.timer`, `oracle/nirvana-deliveryworker.service/.timer` | ✅ | deliveryworker: `ExecStart=... -m nirvana.runner delivery_worker`, timer `OnCalendar=*-*-* 03/2:19:00 UTC` (2 saatte bir); delivery timer `Mon 06:00 UTC` (haftalık) |
| Self-healing | `eda/playbooks/remediate_delivery_worker.yml`, `scale_delivery_worker.yml` | ✅ | — |
| Doğrulama | `tests/test_delivery_worker.py`, `tests/test_delivery_live.py`, `tests/test_payoneer_retainer_config.py` | ✅ | Dosyalar mevcut; pytest koşusu bu ortamda doğrulanamadı (Bölüm 1) |
| Manifest kapısı | `manifest_check.py` | ✅ | Satır 213–216: `set(_dw.SERVICES) != {"infra-sweep","contact-audit","renewal-monitor","vla-audit"}` kontrolü |

**Sonuç:** Bölüm 11 tablosundaki tüm dosyalar repoda mevcut ve kritik içerik kontrolleri (4 hizmet, `renewal_message()`, tenant mühürleme, manifest hizmet kümesi kapısı) **kod düzeyinde doğrulandı**. Testlerin yeşil geçtiği ve OCI timer'larının canlıda etkin olduğu **doğrulanmadı**.

**Açık Noktalar (Bölüm 11):**
- Payoneer'da otomatik tekrarlayan tahsilat yoktur; yenileme aylık ödeme linkiyle yapılır.
- `src/delivery/engine.py`, `src/delivery/tenant_binding.py`, `.github/workflows/modules/delivery-worker.yml`, `tests/test_delivery_health.py` repoda yoktur; işlevler yukarıdaki modüllerle karşılanır.
- `oracle/nirvana-deliveryworker.service` için `Restart=` direktifi yoktur (timer ile tetiklenen oneshot); çökme onarımı EDA playbook'una ve bir sonraki timer turuna bağlıdır.

## 12. Sonuç ve Yayına Alım Yol Haritası
1. **Dal birleştirme (kritik):** Varsayılan dal `master` ve bot commit'leriyle ilerliyor; `main` ile ortak geçmiş yok. Ya varsayılan dalı `main` yapın ya da bot iş akışlarını `main`'e yönlendirin.
2. **Temizlik:** Bölüm 1'deki 30 geçici dosyayı silip `.gitignore`'a ekleyin; `tests/contracts/test_minimal.py`'yi düzeltin.
3. **Test altyapısı:** `pyproject.toml`'a pytest-asyncio ayarlarını ekleyin, `requirements-dev.txt` oluşturun; CI'da `tests/test_delivery_worker.py tests/test_payoneer_retainer_config.py tests/test_tenant_isolation.py` koşusunu zorunlu yapın.
4. **Canlı aktivasyon:** `sudo bash oracle/nirvana_oracle_install.sh && sudo systemctl enable --now nirvana-deliveryworker.timer nirvana-delivery.timer`, ardından `python manifest_check.py` ve `tests/test_delivery_live.py`.
5. **Tahsilat kararı:** Tekrarlayan ödeme API'si gerekip gerekmediğine karar verin.
6. **Metrik:** Supervisor 500 ms kalp atışının üretimdeki gerçek tespit→onarım süresini ölçüp rapora işleyin.
7. **Para birimi tutarlılığı:** `PRICE_USD`/`ENTERPRISE_RETAINER_USD` adlarını EUR modeline göre yeniden adlandırın.
