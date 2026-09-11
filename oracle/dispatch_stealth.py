"""Nirvana Oracle dispatch bekçisi — GitHub cron gecikmesini kökten keser.

GitHub Actions cron'u best-effort'tur (yoğunlukta 15dk -> 20-45dk gecikebilir).
Oracle VM 7/24 açık olduğu için bu betik systemd timer ile tam 15 dakikada bir
nirvana-stealth-form workflow'unu GitHub API (workflow_dispatch) ile tetikler.

- Token: GITHUB_DISPATCH_TOKEN > GH_TOKEN > GITHUB_TOKEN (Actions:write yetkili PAT).
- Kilit: son başarılı dispatch < 12 dk ise atla (GitHub cron ile çift tetiklenme önlenir).
- Hata: token yok / API hatası → Telegram'a sahibine GÜNDE EN FAZLA 1 uyarı.
- Oracle kotasına dokunmaz: api.github.com'a tek POST / 15 dk. Sıfır maliyet.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

REPO = os.getenv("GITHUB_REPOSITORY", "fevzican1/lead-qualification-engine")
WORKFLOW = "nirvana-stealth-form.yml"
STATE = (Path(os.getenv("NIRVANA_STATE_DIR", "/opt/devsolve/nirvana/state"))
         / "dispatch_state.json")
ENV_PATH = Path(os.getenv("NIRVANA_ENV", "/opt/devsolve/.env"))
MIN_GAP_S = 12 * 60
WARN_EVERY_S = 24 * 3600


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
    env = _read_env()
    return env.get("GITHUB_DISPATCH_TOKEN", "").strip()


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
    """Ops kanalına uyarı — sessizce başarısız olmasın; hata dispatch'i bozmaz."""
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


def dispatch(*, force: bool = False) -> dict[str, object]:
    now = time.time()
    state = _load_state()
    last_ok = float(state.get("last_ok", 0) or 0)
    if not force and now - last_ok < MIN_GAP_S:
        return {"dispatched": False, "reason": "recent_dispatch",
                "since_min": round((now - last_ok) / 60, 1)}

    token = _token()
    if not token:
        result: dict[str, object] = {
            "dispatched": False, "reason": "no_token",
            "hint": "GH_DISPATCH_TOKEN repo secret'ı (Actions:write PAT) eklenmeli"}
    else:
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
                data=json.dumps({"ref": "master"}).encode("utf-8"),
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "User-Agent": "nirvana-dispatch-timer",
                         "X-GitHub-Api-Version": "2022-11-28"},
                method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = {"dispatched": resp.status in (200, 204), "status": resp.status}
        except Exception as exc:  # noqa: BLE001 — timer asla çökmez
            result = {"dispatched": False, "reason": f"api_error: {exc}"[:140]}

    if result.get("dispatched"):
        state["last_ok"] = now
        state["last_status"] = result.get("status")
        result["reason"] = "dispatched"
    state["last_attempt"] = now
    state["last_result"] = result.get("reason")
    _save_state(state)

    if not result.get("dispatched"):
        last_warn = float(state.get("last_warn", 0) or 0)
        if now - last_warn > WARN_EVERY_S:
            state["last_warn"] = now
            _save_state(state)
            _telegram(f"⚠️ Nirvana dispatch uyarısı: stealth-form tetiklenemedi "
                      f"({result.get('reason')}). Çözüm: repo secret'ına "
                      f"GH_DISPATCH_TOKEN (Actions:write PAT) ekle.")
    return result


if __name__ == "__main__":
    print(json.dumps(dispatch(), ensure_ascii=False, default=str))
