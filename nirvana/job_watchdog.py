"""Lane AO — job_watchdog [Oracle VM, light]: İŞ seviyesi bekçi (faso fiso değil).

Kök neden: eski bekçi yalnızca ``systemctl is-active`` bakıyordu. Servis "active"
görünürken kuyruk 0 / yakıt 0 / form 0 kalabiliyordu (canlı arıza: günlerce 0).
Bu lane SERVİSİN DEĞİL İŞİN sağlığına bakar:

- ``queue``  : üretim kuyruğu derinliği (domain_store.queue_depth)
- ``fuel``   : hot_fuel rezervuarındaki hazır yakıt (hedefin altındaysa kırmızı)
- ``forms``  : bugünkü form sayısı (gün ilerlemesine göre beklenen tabanın altı)
- ``webchat``: müşteri kapısı ``/health`` + bugünkü webchat oturum sayısı
  ("müşteri bize ulaşıyor mu?" sorusunun ölçülmüş cevabı)

Davranış (fail-safe):
- Her sinyal için ardışık kırmızı sayacı tutulur; ``JOB_WATCHDOG_STREAK`` (2) tur
  üst üste kırmızı olan sinyal ONARIM gerektirir.
- Onarım = ilgili systemd birimini ``systemctl restart`` (sadece systemd varsa,
  ``JOB_WATCHDOG_RESTART=0`` ile kapatılabilir) + sahibe tek Telegram mesajı
  (sinyal başına saatte en fazla 1; spam yok).
- ``dry_run=True`` hiçbir restart/bildirim yapmaz (kurulum doğrulaması).
- Hiçbir hata ana hattı düşürmez: tüm ölçümler try/except ile korunur.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from nirvana.registry import state_path

logger = logging.getLogger(__name__)

STATE_NAME = "job_watchdog.json"

QUEUE_MIN = int(os.getenv("JOB_WATCHDOG_QUEUE_MIN", "40") or 40)
FUEL_MIN = int(os.getenv("JOB_WATCHDOG_FUEL_MIN", "100") or 100)
STREAK = int(os.getenv("JOB_WATCHDOG_STREAK", "2") or 2)
RESTART_COOLDOWN_S = float(os.getenv("JOB_WATCHDOG_RESTART_COOLDOWN_S", "1800") or 1800)
NOTIFY_COOLDOWN_S = float(os.getenv("JOB_WATCHDOG_NOTIFY_COOLDOWN_S", "3600") or 3600)
WEBCHAT_PORT = int(os.getenv("WEBCHAT_PORT", "8765") or 8765)
FORMS_GRACE_HOUR = int(os.getenv("JOB_WATCHDOG_FORMS_GRACE_HOUR", "3") or 3)

UNIT_FOR_SIGNAL = {
    "queue": "nirvana-pipeline.service",
    "fuel": "nirvana-pipeline.service",
    "forms": "nirvana-pipeline.service",
    "webchat": "nirvana-webchat.service",
}


def _now() -> float:
    return time.time()


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path(STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    path = state_path(STATE_NAME)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def systemd_available() -> bool:
    """Onarım (restart) sadece gerçek systemd ana makinesinde denenir."""
    if os.name != "posix":
        return False
    if not Path("/run/systemd/system").exists():
        return False
    raw = str(os.getenv("JOB_WATCHDOG_RESTART", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def restart_unit(unit: str) -> dict[str, Any]:
    """İlgili birimi yeniden başlat; hata bilgisi raporlanır (asla raise etmez)."""
    try:
        proc = subprocess.run(
            ["systemctl", "restart", unit],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return {"unit": unit, "ok": proc.returncode == 0,
                "code": int(proc.returncode),
                "err": (proc.stderr or "").strip()[:120]}
    except Exception as exc:  # noqa: BLE001 — onarım hatası bekçiyi düşürmez
        return {"unit": unit, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:140]}


# --- ölçümler ----------------------------------------------------------------

def webchat_health(*, port: int | None = None, timeout: float = 5.0) -> bool:
    """Yerel müşteri kapısı /health: 200 dönerse kapı açık."""
    import urllib.request

    url = f"http://127.0.0.1:{int(port or WEBCHAT_PORT)}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 400
    except Exception:  # noqa: BLE001 — kapı kapalı/ayakta değil
        return False


def webchat_traffic(*, now: float | None = None) -> dict[str, Any]:
    """Bugünkü webchat oturumları + son temas damgası (müşteri ulaşıyor mu?)."""
    path = state_path("webchat_sessions.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sessions_today": 0, "sessions_total": 0, "last_seen_s": None}
    rows: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, dict):
                rows.append(value)
            elif isinstance(value, list):
                rows.extend([r for r in value if isinstance(r, dict)])
    elif isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    stamp = _now() if now is None else float(now)
    today = time.strftime("%Y-%m-%d", time.gmtime(stamp))
    sessions_today = 0
    last_seen: float | None = None
    for row in rows:
        raw = row.get("updated_at") or row.get("last_at") or row.get("created_at") or 0
        try:
            ts = float(raw)
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= 0:
            continue
        last_seen = ts if last_seen is None else max(last_seen, ts)
        if time.strftime("%Y-%m-%d", time.gmtime(ts)) == today:
            sessions_today += 1
    return {
        "sessions_today": sessions_today,
        "sessions_total": len(rows),
        "last_seen_s": round(stamp - last_seen, 1) if last_seen else None,
    }


def expected_forms(*, cap: int, now: float | None = None) -> int:
    """Gün ilerlemesine göre beklenen asgari form tabanı (grace saatinden sonra)."""
    stamp = _now() if now is None else float(now)
    hour = time.gmtime(stamp).tm_hour
    if hour < FORMS_GRACE_HOUR:
        return 0
    progress = (hour + 1) / 24.0
    return max(1, int(int(cap) * progress * 0.4))


def measure() -> dict[str, Any]:
    """Dört iş sinyalini ölç (her ölçüm bağımsız korunur)."""
    out: dict[str, Any] = {}
    try:
        import domain_store

        out["queue_depth"] = int(domain_store.queue_depth())
    except Exception as exc:  # noqa: BLE001
        out["queue_depth"] = None
        out["queue_error"] = str(exc)[:100]
    try:
        from nirvana import hot_fuel

        status = hot_fuel.status()
        out["fuel_ready"] = int(status.get("ready") or 0)
        out["fuel_target"] = int(status.get("target") or 0)
    except Exception as exc:  # noqa: BLE001
        out["fuel_ready"] = None
        out["fuel_error"] = str(exc)[:100]
    try:
        import knowledge

        today, hour = knowledge.submit_counts()
        cap = int(knowledge.daily_cap())
        out["forms_today"] = int(today)
        out["forms_hour"] = int(hour)
        out["forms_cap"] = cap
        out["forms_expected"] = expected_forms(cap=cap)
    except Exception as exc:  # noqa: BLE001
        out["forms_today"] = None
        out["forms_error"] = str(exc)[:100]
    out["webchat_health"] = webchat_health()
    out["webchat"] = webchat_traffic()
    return out


def evaluate(sig: dict[str, Any]) -> dict[str, Any]:
    """Sinyal durumu: her biri ok/kırmızı + gerekçe (restart/bildirim kararı için)."""
    checks: dict[str, dict[str, Any]] = {}

    depth = sig.get("queue_depth")
    if depth is None:
        checks["queue"] = {"ok": True, "detail": "ölçülemedi (fail-open)"}
    else:
        checks["queue"] = {
            "ok": int(depth) >= QUEUE_MIN,
            "detail": f"kuyruk {depth} (min {QUEUE_MIN})",
        }

    fuel = sig.get("fuel_ready")
    if fuel is None:
        checks["fuel"] = {"ok": True, "detail": "ölçülemedi (fail-open)"}
    else:
        checks["fuel"] = {
            "ok": int(fuel) >= FUEL_MIN,
            "detail": f"yakıt {fuel}/{sig.get('fuel_target', '?')} (min {FUEL_MIN})",
        }

    today = sig.get("forms_today")
    want = int(sig.get("forms_expected") or 0)
    if today is None:
        checks["forms"] = {"ok": True, "detail": "ölçülemedi (fail-open)"}
    elif want <= 0:
        checks["forms"] = {"ok": True, "detail": f"form {today} (grace saatinde)"}
    else:
        checks["forms"] = {
            "ok": int(today) >= want,
            "detail": f"form bugün {today} (beklenen ≥{want}, kap {sig.get('forms_cap')})",
        }

    health = bool(sig.get("webchat_health"))
    checks["webchat"] = {
        "ok": health,
        "detail": ("/health 200" if health else f"webchat /health yanıtsız (port {WEBCHAT_PORT})"),
    }

    red = [name for name, row in checks.items() if not row.get("ok")]
    return {
        "checks": checks,
        "red": red,
        "ok": not red,
        "webchat_sessions_today": (sig.get("webchat") or {}).get("sessions_today"),
        "webchat_last_seen_s": (sig.get("webchat") or {}).get("last_seen_s"),
    }


# --- karar ve onarım ---------------------------------------------------------

def _notify(msg: str) -> bool:
    try:
        import owner_notify

        owner_notify.send(msg)
        return True
    except Exception:  # noqa: BLE001 — bildirim hatası bekçiyi düşürmez
        logger.info("job_watchdog bildirimi gönderilemedi")
        return False


def self_test() -> int:
    """Kuru çalışma: gerçek restart/bildirim olmadan karar zincirini doğrular."""
    sig = measure()
    verdict = evaluate(sig)
    assert set(verdict["checks"]) == {"queue", "fuel", "forms", "webchat"}
    print(f"JOB_WATCHDOG SELF-TEST OK: ok={verdict['ok']} red={verdict['red']} (eylem yok)")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nirvana.job_watchdog")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--notify", dest="notify", action="store_true", default=True)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    result = run_batch(notify=args.notify and not args.no_notify,
                       dry_run=args.no_notify)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Ölç -> kırmızı say -> (streak dolunca) restart + sahibe tek bildirim."""
    notify = bool(kwargs.get("notify", True))
    dry_run = bool(kwargs.get("dry_run", False))
    allow_restart = bool(kwargs.get("restart", True)) and not dry_run
    sig: dict[str, Any] = kwargs.get("checks") or measure()
    verdict = evaluate(sig)

    state = _load_state()
    signals = state.get("signals") if isinstance(state.get("signals"), dict) else {}
    now = _now()
    restarts: list[dict[str, Any]] = []
    notified: list[str] = []
    notes: list[str] = []
    test_mode = kwargs.get("restart_fn") is not None and not systemd_available()

    for name in ("queue", "fuel", "forms", "webchat"):
        row = dict(signals.get(name) or {})
        check = verdict["checks"][name]
        ok = bool(check.get("ok"))
        row["streak"] = 0 if ok else int(row.get("streak") or 0) + 1
        row["last_state"] = "ok" if ok else "red"
        row["detail"] = str(check.get("detail") or "")
        if not ok and int(row["streak"]) >= STREAK:
            unit = UNIT_FOR_SIGNAL.get(name) or ""
            can_restart = (
                allow_restart and bool(unit)
                and (systemd_available() or test_mode)
                and now - float(row.get("last_restart") or 0) >= RESTART_COOLDOWN_S
            )
            if can_restart:
                result = (kwargs.get("restart_fn") or restart_unit)(unit)
                row["last_restart"] = now
                row["last_restart_result"] = result
                restarts.append({"signal": name, **result})
                notes.append(
                    f"{name} → {unit} restart "
                    f"({'ok' if result.get('ok') else 'HATA: ' + str(result.get('error') or result.get('err') or '')[:80]})"
                )
            if notify and now - float(row.get("last_notify") or 0) >= NOTIFY_COOLDOWN_S:
                traffic = sig.get("webchat") or {}
                msg = (
                    "🛠️ Nirvana iş bekçisi: iş durdu, onarım denendi.\n"
                    f"Sinyal: {name} — {check.get('detail')}\n"
                    f"Streak: {row['streak']} tur üst üste kırmızı\n"
                    + ("\n".join(f"• {n}" for n in notes) + "\n" if notes else
                       "Onarım: systemd kapalı/soğumada — elle kontrol gerekebilir.\n")
                    + f"Webchat bugün: {traffic.get('sessions_today', '?')} oturum"
                )
                sent = (kwargs.get("notify_fn") or _notify)(msg)
                if sent:
                    row["last_notify"] = now
                    notified.append(name)
        signals[name] = row

    state["signals"] = signals
    state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    state["last_ok"] = verdict["ok"]
    state["last_red"] = verdict["red"]
    state["last_detail"] = {name: verdict["checks"][name]["detail"] for name in verdict["checks"]}
    state["webchat_sessions_today"] = verdict.get("webchat_sessions_today")
    state["webchat_last_seen_s"] = verdict.get("webchat_last_seen_s")
    if not dry_run:
        _save_state(state)

    return {
        "lane": "job_watchdog",
        "dry_run": dry_run,
        "ok": verdict["ok"],
        "red": verdict["red"],
        "checks": verdict["checks"],
        "restarts": restarts,
        "notified": notified,
        "streaks": {name: int((signals.get(name) or {}).get("streak") or 0)
                    for name in signals},
        "webchat": sig.get("webchat") or {},
        "queue_depth": sig.get("queue_depth"),
        "fuel_ready": sig.get("fuel_ready"),
        "fuel_target": sig.get("fuel_target"),
        "forms_today": sig.get("forms_today"),
        "forms_expected": sig.get("forms_expected"),
        "state": str(state_path(STATE_NAME)),
        "note": ("İş seviyesi bekçi: kuyruk+yakıt+form+webchat. Servis 'active' "
                 "olsa bile iş durursa yakalar, restart eder, sahibe haber verir."),
    }
