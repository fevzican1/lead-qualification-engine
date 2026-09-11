"""Nirvana Oracle dispatch hub — GitHub cron gecikmesini kökten kesen orkestratör.

GitHub Actions cron'u best-effort'tur (yoğunlukta 15dk -> 20-45dk gecikebilir).
Oracle VM 7/24 açık olduğu için bu betik systemd timer ile her 5 dakikada bir
çalışır ve DISPATCH_MATRIX'te süresi dolmuş TÜM GitHub modüllerini GitHub API
(workflow_dispatch) ile TAM zamanında tetikler. GitHub cron'ları yedek katman
olarak kalır; her workflow'un kendi concurrency kilidi çift çalışmayı önler.

- Token: GITHUB_DISPATCH_TOKEN > GH_TOKEN > GITHUB_TOKEN (Actions:write yetkili PAT).
- Matris + kilit: her workflow için son başarılı dispatch zamanı state dosyasında;
  minimum aralık dolmadan tekrar tetiklenmez (GitHub cron ile çakışma olmaz).
- Hata: token yok / API hatası → Telegram'a sahibine GÜNDE EN FAZLA 1 uyarı.
- Oracle kotasına dokunmaz: api.github.com'a tik başına en fazla 1-2 POST. Sıfır maliyet.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

REPO = os.getenv("GITHUB_REPOSITORY", "fevzican1/lead-qualification-engine")
STATE = (Path(os.getenv("NIRVANA_STATE_DIR", "/opt/devsolve/nirvana/state"))
         / "dispatch_state.json")
ENV_PATH = Path(os.getenv("NIRVANA_ENV", "/opt/devsolve/.env"))
WARN_EVERY_S = 24 * 3600

# Oracle'dan yönetilen tüm GitHub modülleri (workflow dosyası -> min aralık sn).
# GitHub cron'ları yedek olarak aynı ritimde çalışır — çakışma concurrency kilidiyle önlenir.
DISPATCH_MATRIX: dict[str, int] = {
    "discovery-pipeline.yml": 5 * 60,        # yakıt omurgası: her 5 dk
    "nirvana-stealth-form.yml": 15 * 60,     # form ateşleme: her 15 dk
    "pipeline-watchdog.yml": 15 * 60,        # schedule-bekeci: 15 dk
    "payload_optimizer.yml": 60 * 60,        # payload optimizasyonu: saatlik
    "enterprise-feed.yml": 6 * 3600,         # feed üretimi: 6 saatte bir
    "nirvana-heavy.yml": 6 * 3600,           # A→B→C zinciri: 6 saatte bir
    "nirvana-proof.yml": 24 * 3600,          # kanıt kartı: günlük
    "nirvana-strategy.yml": 24 * 3600,       # strateji pivotu: günlük
    "nirvana-meta.yml": 24 * 3600,           # meta orkestratör: günlük
}


def _read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, val = line.partition("=")
                out[key.strip()] = val.strip()
    except OSError:
        pass
    return out


def _token() -> str:
    for key in ("GITHUB_DISPATCH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        val = (os.getenv(key) or "").strip()
        if val and val != "null":
            return val
    return _read_env().get("GITHUB_DISPATCH_TOKEN", "").strip()


def _load_state() -> dict[str, object]:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, object]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE)


def _telegram(msg: str) -> None:
    """Ops kanalına uyarı — hata asla timer'ı bozmaz."""
    env = _read_env()
    token = env.get("TELEGRAM_NOTIFY_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_NOTIFY_CHAT_ID") or env.get("TELEGRAM_OWNER_CHAT_ID") or ""
    if not token or not chat:
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": msg}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=8)
    except OSError:
        pass


def _dispatch_one(workflow: str, token: str) -> dict[str, object]:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/dispatches",
        data=json.dumps({"ref": "master"}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "nirvana-dispatch-hub",
                 "X-GitHub-Api-Version": "2022-11-28"},
        method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return {"status": resp.status}


def run_hub(*, force: bool = False) -> dict[str, object]:
    now = time.time()
    state = _load_state()
    last_warn = float(state.get("last_warn", 0) or 0)
    dispatched: list[str] = []
    skipped = 0
    failed: dict[str, str] = {}

    token = _token()
    if not token:
        failed["__token__"] = ("no_token — GH_DISPATCH_TOKEN secret'ı "
                               "(Actions:write PAT) eklenmeli")
    else:
        for workflow, min_gap in DISPATCH_MATRIX.items():
            record = state.get(workflow) if isinstance(state.get(workflow), dict) else {}
            last_ok = float((record or {}).get("last_ok", 0) or 0)
            if not force and now - last_ok < min_gap:
                skipped += 1
                continue
            try:
                res = _dispatch_one(workflow, token)
                state[workflow] = {"last_ok": now, "status": res.get("status")}
                dispatched.append(workflow)
            except Exception as exc:  # noqa: BLE001 — tek hata tüm hub'ı bozmaz
                failed[workflow] = str(exc)[:120]
                rec = dict(record or {})
                rec["last_error"] = str(exc)[:120]
                rec["last_error_at"] = now
                state[workflow] = rec

    state["last_run"] = now
    _save_state(state)

    if failed and now - last_warn > WARN_EVERY_S:
        state["last_warn"] = now
        _save_state(state)
        detail = "; ".join(f"{k}: {v}" for k, v in failed.items())[:300]
        _telegram(f"⚠️ Nirvana dispatch hub uyarısı: {detail}")

    return {"dispatched": dispatched, "skipped": skipped,
            "failed": failed, "managed": len(DISPATCH_MATRIX)}


if __name__ == "__main__":
    print(json.dumps(run_hub(), ensure_ascii=False, default=str))
