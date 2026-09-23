"""Çift motor + 4 katman koruma + iç aktarım testleri (tamamen çevrimdışı)."""
from __future__ import annotations

import json
import time

import pytest

import config


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """State + hot_fuel.db GERÇEK depoya yazılmasın (izolasyon)."""
    from nirvana import hot_fuel, protection, registry

    monkeypatch.setattr(registry, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hot_fuel, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hot_fuel, "DB_PATH", tmp_path / "state" / "hot_fuel.db")
    monkeypatch.setattr(protection, "PROXY_FILE", tmp_path / "state" / "proxies.txt")
    monkeypatch.setattr(protection, "_file_cache", {"mtime": -1.0, "rows": []})
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return tmp_path


# --- Katman 1: jitter & rate-limiting ---------------------------------------

def test_jitter_uniform_3_to_9_seconds():
    from nirvana import protection

    lo, hi = protection.jitter_bounds()
    assert (lo, hi) == (3.0, 9.0)
    samples = [protection.jitter_seconds() for _ in range(200)]
    assert all(lo <= s <= hi for s in samples)
    assert len(set(samples)) > 5  # uniform: tek değere yapışmaz


def test_human_delay_sleeps_uniform_and_never_raises():
    from nirvana import protection

    slept: list[float] = []
    seconds = protection.human_delay(sleep=slept.append)
    assert 3.0 <= seconds <= 9.0
    assert slept == [seconds]

    def _boom(_):  # sleep patlarsa hattı düşürmez (fail-open)
        raise RuntimeError("sleep kapalı")

    assert protection.human_delay(sleep=_boom) >= 3.0


# --- Katman 2: SQLite circuit breaker (hot_fuel.db) -------------------------

def test_circuit_opens_after_threshold_and_closes_on_success():
    from nirvana import protection

    url = "https://flaky.example/contact"
    assert protection.allow(url) is True  # bilinmeyen domain: izin

    for _ in range(3):
        protection.record(url, False, error="http_503")
    assert protection.allow(url) is False  # 3 hata -> şalter AÇIK

    protection.record("https://flaky.example/", True)
    assert protection.allow(url) is True  # başarı -> kapanır


def test_circuit_half_open_after_cooldown():
    from nirvana import protection

    url = "https://cool.example/"
    protection.record(url, False, threshold=1, cooldown_s=0.05)
    assert protection.allow(url) is False
    time.sleep(0.07)
    assert protection.allow(url) is True  # soğuma bitti -> yarı-açık deneme


def test_circuit_health_board_shape():
    from nirvana import protection

    protection.record("https://open.example/", False, threshold=1, cooldown_s=60)
    board = protection.health()
    assert board["open"] >= 1 and board["worst"]
    assert board["worst"][0]["root"] == "open.example"


# --- Katman 3: proxy havuzu rotasyonu ---------------------------------------

def test_proxy_pool_rotation_random_pick(monkeypatch):
    from nirvana import protection

    monkeypatch.setenv("PROXY_POOL", "http://p1:8080,http://p2:3128")
    picks = {protection.pick_proxy() for _ in range(100)}
    assert picks == {"http://p1:8080", "http://p2:3128"}  # rastgele rotasyon
    assert len(protection.proxies()) == 2


def test_proxy_pool_from_file_with_comments(tmp_path, monkeypatch):
    from nirvana import protection

    monkeypatch.delenv("PROXY_POOL", raising=False)
    proxy_file = tmp_path / "state" / "proxies.txt"
    proxy_file.write_text("# yorum\nhttp://a:1\n\nhttp://b:2\n", encoding="utf-8")
    monkeypatch.setattr(protection, "PROXY_FILE", proxy_file)
    monkeypatch.setattr(protection, "_file_cache", {"mtime": -1.0, "rows": []})
    assert set(protection.proxies()) == {"http://a:1", "http://b:2"}


def test_proxy_pool_empty_means_direct_connection(monkeypatch):
    from nirvana import protection

    monkeypatch.delenv("PROXY_POOL", raising=False)
    assert protection.proxies() == []
    assert protection.pick_proxy() is None  # boş havuz -> doğrudan ($0)


# --- Katman 4: header çeşitlendirmesi ---------------------------------------

def test_random_headers_uses_current_ua_pool():
    from nirvana import protection
    from nirvana.fingerprint_rotator import UA_POOL

    uas = {protection.random_headers()["User-Agent"] for _ in range(60)}
    assert uas <= set(UA_POOL)
    assert len(uas) > 1  # her çağrıda rastgele seçim


def test_ua_pool_contains_current_2026_signatures():
    from nirvana.fingerprint_rotator import UA_POOL

    joined = " ".join(UA_POOL)
    assert "Chrome/131" in joined and "Firefox/133" in joined


def test_prefilter_headers_rotate():
    import prefilter

    uas = {prefilter._headers()["User-Agent"] for _ in range(40)}
    assert len(uas) > 1


# --- Çift motor: CAPTCHA/WAF -> captcha_queue (ana hat asla kilitlenmez) ----

def test_prefilter_captcha_routes_to_background_queue(isolated, monkeypatch):
    import prefilter
    from nirvana import protection, stealth_former as sf
    from nirvana.registry import state_path

    monkeypatch.setattr(config, "ROOT", isolated)
    monkeypatch.setattr(protection, "human_delay", lambda **kw: 0.0)

    def _fake_probe(url: str) -> dict:
        return {
            "url": url, "ok": True, "form_likely": False, "captcha": True,
            "waf_strict": True, "easy_form": False, "stack_hints": [],
            "priority": 0, "turkish": False, "error": None, "status_code": 200,
        }

    monkeypatch.setattr(prefilter, "probe_for_form", _fake_probe)
    jobs, skipped = prefilter.split_and_rank(["https://captcha-site.example/contact"])

    assert jobs == []
    assert skipped[0]["status"] == "skipped_captcha"
    # Ana akıştan düşen hedef ARKA PLANDA kuyrukta — lead kaybı sıfır
    queue = json.loads(state_path(sf.CAPTCHA_QUEUE_NAME).read_text(encoding="utf-8"))
    assert any("captcha-site.example" in str(r.get("url")) for r in queue)
    assert sf.captcha_queue_depth()["queued"] >= 1


def test_circuit_open_blocks_probe_without_breaking_slice(isolated, monkeypatch):
    import prefilter
    from nirvana import protection, stealth_former as sf
    from nirvana.registry import state_path

    monkeypatch.setattr(config, "ROOT", isolated)
    monkeypatch.setattr(protection, "human_delay", lambda **kw: 0.0)
    protection.record("https://bad.example/", False, threshold=1, cooldown_s=600)

    def _fake_probe(url: str) -> dict:
        if not protection.allow(url):
            return {"url": url, "ok": False, "error": "circuit_open", "defer": True,
                    "captcha": False, "waf_strict": False, "form_likely": False,
                    "stack_hints": [], "priority": 0, "turkish": False,
                    "easy_form": False, "status_code": None}
        return {"url": url, "ok": True, "form_likely": True, "captcha": False,
                "waf_strict": False, "easy_form": True, "stack_hints": [],
                "priority": 40, "turkish": True, "error": None, "status_code": 200,
                "easy_score": 70}

    monkeypatch.setattr(prefilter, "probe_for_form", _fake_probe)
    jobs, _skipped = prefilter.split_and_rank(
        ["https://bad.example/contact", "https://good.example/contact"]
    )
    # Circuit açık domain ATLANDI, slice devam etti (kilitlenme/atlama yok)
    assert all("bad.example" not in j["url"] for j in jobs)
    assert any("good.example" in j["url"] for j in jobs)


def test_stealth_former_waf_reject_routes_to_queue(isolated, monkeypatch):
    """WAF engelli hedef ana akışta raporlanır, iş arka plan kuyruğuna devredilir."""
    from nirvana import stealth_former as sf
    from nirvana.registry import state_path

    class _WAFResp:
        status_code = 403
        text = "<html>Access Denied - cloudflare security check</html>"
        headers = {"server": "cloudflare"}
        url = "https://waf-site.example/contact"

    monkeypatch.setattr(config, "ROOT", isolated)
    monkeypatch.setattr(sf.httpx, "get", lambda *a, **kw: _WAFResp())
    result = sf.submit_form("https://waf-site.example/contact", {"email": "a@b.co"})

    assert result["status"] == "WAF_REJECT"
    assert result["route_to"] == "captcha_queue"
    queue = json.loads(state_path(sf.CAPTCHA_QUEUE_NAME).read_text(encoding="utf-8"))
    assert any("waf-site.example" in str(r.get("url")) for r in queue)


def test_kick_captcha_worker_is_fire_and_forget(isolated, monkeypatch):
    """auto_runner arka plan motoru: beklemeden ateşler (ana hat kilitlenmez)."""
    import auto_runner
    from nirvana import stealth_former as sf
    from nirvana.registry import state_path

    monkeypatch.setattr(config, "ROOT", isolated)
    state_path(sf.CAPTCHA_QUEUE_NAME).write_text(
        json.dumps([{"url": "https://x.example/", "status": "queued"}]),
        encoding="utf-8",
    )
    spawned: list[list[str]] = []

    class _Proc:
        pid = 4242

    monkeypatch.setattr(auto_runner.subprocess, "Popen",
                        lambda cmd, **kw: spawned.append(list(cmd)) or _Proc())
    auto_runner._CAPTCHA_KICK_TS[0] = 0.0  # soğuma sıfırla

    auto_runner._kick_captcha_worker()  # asla beklemez, exception fırlatmaz
    assert len(spawned) == 1
    assert "free_captcha_worker" in spawned[0]
    # Soğuma: ikinci çağrı tekrar ateşlemez (systemd timer çakışması önlenir)
    auto_runner._kick_captcha_worker()
    assert len(spawned) == 1


def test_kick_captcha_worker_noop_when_queue_empty(isolated, monkeypatch):
    import auto_runner
    from nirvana import stealth_former as sf
    from nirvana.registry import state_path

    monkeypatch.setattr(config, "ROOT", isolated)
    state_path(sf.CAPTCHA_QUEUE_NAME).write_text("[]", encoding="utf-8")
    calls: list[int] = []
    monkeypatch.setattr(auto_runner.subprocess, "Popen",
                        lambda *a, **kw: calls.append(1))
    auto_runner._CAPTCHA_KICK_TS[0] = 0.0

    auto_runner._kick_captcha_worker()
    assert calls == []  # kuyruk boş -> dokunulmaz


# --- Yakıt kıtlığının önlenmesi: hot_fuel.prime_queue -----------------------

def test_prime_queue_feeds_domain_store_without_typeerror(isolated, monkeypatch):
    """Regression: enqueue() keyword-only — positional çağrı 0 satır ekliyordu."""
    import domain_store
    from nirvana import hot_fuel

    monkeypatch.setattr(domain_store, "QUEUE_PATH", isolated / "unprocessed_leads.json")
    monkeypatch.setattr(domain_store, "PROCESSED_PATH", isolated / "processed.json")
    monkeypatch.setattr(domain_store, "BUDGET_PATH", isolated / "http_budget.json")

    hot_fuel.push([
        {"url": "https://fueled.example/contact", "easy_score": 90,
         "source": "import_targets"},
    ])
    pushed = hot_fuel.prime_queue(limit=10)

    assert pushed == 1
    assert domain_store.queue_depth() == 1


# --- import_targets: dış CSV/TXT besleme (mükerrer engelli) -----------------

def test_import_targets_csv_dedup_and_normalize(isolated, tmp_path):
    import import_targets

    csv_file = tmp_path / "batch.csv"
    csv_file.write_text(
        "url,easy_score\n"
        "https://a.example/contact,85\n"
        "a.example,85\n"          # normalize edilip farklı URL olabilir
        "https://b.example,70\n"
        "not a url!,80\n",        # geçersiz -> atlanır
        encoding="utf-8",
    )
    result = import_targets.import_files([str(csv_file)])

    assert result["files"] == 1
    assert result["read"] == 4
    assert result["added"] >= 2
    # İkinci aktarım: hepsi mükerrer, eklenen 0 (PRIMARY KEY korur)
    again = import_targets.import_files([str(csv_file)])
    assert again["added"] == 0
    assert again["deduped"] >= result["normalized"]


def test_import_targets_txt_skips_comments_and_dupes(isolated, tmp_path):
    import import_targets

    txt = tmp_path / "list.txt"
    txt.write_text(
        "# yorum satiri\n"
        "https://c.example\n"
        "d.example\n"
        "https://c.example\n",  # dosya içi mükerrer
        encoding="utf-8",
    )
    result = import_targets.import_files([str(txt)])
    assert result["normalized"] == 2
    assert result["deduped"] >= 1
    assert result["added"] == 2


def test_import_targets_dry_run_writes_nothing(isolated, tmp_path):
    import import_targets
    from nirvana import hot_fuel

    txt = tmp_path / "list.txt"
    txt.write_text("https://e.example\n", encoding="utf-8")
    result = import_targets.import_files([str(txt)], dry_run=True)
    assert result["dry_run"] is True and result["added"] == 0
    assert hot_fuel.status()["depth"] == 0


def test_import_targets_cli_reports_json(isolated, tmp_path, capsys):
    import import_targets

    txt = tmp_path / "cli.txt"
    txt.write_text("https://cli.example\n", encoding="utf-8")
    code = import_targets.main([str(txt)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["added"] == 1


def test_import_targets_missing_file_fails_cleanly(isolated, tmp_path):
    import import_targets

    result = import_targets.import_files([str(tmp_path / "yok.csv")])
    assert result["files"] == 0 and result["added"] == 0


# --- Riskli kod taraması: agresif brute-force/honeypot-atlatma YOK ----------

@pytest.mark.parametrize("name", [
    "auto_runner.py", "prefilter.py", "form_submitter.py", "pipeline.py",
    "lead_discovery.py", "collector.py", "import_targets.py",
])
def test_no_aggressive_bruteforce_or_honeypot_bypass(name):
    """Oracle IP ban riski: brute-force / honeypot-atlatma / zorlamalı saldırı YOK."""
    text = (config.ROOT / name).read_text(encoding="utf-8").lower()
    banned = (
        "bruteforce", "brute_force", "brute-force",
        "honeypot_bypass", "bypass_honeypot",
        "wordlist", "sql injection", "exploit_payload",
    )
    for token in banned:
        assert token not in text, f"{name}: yasaklı agresif desen: {token}"


def test_masking_layers_preserved():
    """Maskeleme/TLS taklidi + insan simülasyonu KORUNUR (silinmemeli)."""
    from nirvana import honeypot_human_sim as hps
    from nirvana import net_stealth

    assert net_stealth.enabled() is True  # curl_cffi TLS taklidi hattı aktif
    assert hps.JITTER_PROFILE  # insan simülasyonu jitter profili yerinde

