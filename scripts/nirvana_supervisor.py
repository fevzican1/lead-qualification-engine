#!/usr/bin/env python3
"""Dis Nobetci (Watchdog Supervisor) — uygulamadan BAGIMSIZ otonom onarim.

Neden disarida: systemd WatchdogSec + ic job_watchdog ayni surecte yasiyor;
surec donarsa bekci de donar (canli ariza: saatlerce takilma, 2 gun 0 form).
Bu betik systemd timer ile HER 60 SNDE bir ayri surecte calisir:

  1) Webchat WS saglik: /health 5 sn icinde 200 vermezse
     -> fuser -k + reset-failed + restart nirvana-webchat.
  2) Pipeline kalp atisi: heartbeat_auto_runner.json 180 sn'den eskiyse
     -> pipeline agacina kill -9, Chromium zombileri pkill, restart pipeline.
  3) Zombie Chromium: 10 dk+ yasli yetim chrome (ppid=1) -> kill -9.
  4) RAM/OOM: %90 uzeri -> drop_caches + en obur asili sureci oldur + Telegram'a
     TEK SATIR dogrulanmis rapor ('Hat canli' iddiasi YASAK).

1 DAKIKA KURALI: betik <=60 snde biter; her ariza <=60 snde onarima girer.
REBOOT YOK. Fail-open (asla patlamaz, cikis 0). SADECE stdlib.
Windows'ta no-op (gelistirme).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path("/opt/devsolve")
if not APP_DIR.exists():
    APP_DIR = Path(__file__).resolve().parents[1]

STATE_DIR = APP_DIR / "nirvana" / "state"
HEARTBEAT_AUTO_RUNNER = STATE_DIR / "heartbeat_auto_runner.json"
SUP_STATE = STATE_DIR / "supervisor_state.json"

WEBCHAT_PORT = int(os.getenv("WEBCHAT_PORT", "8765") or 8765)
HEALTH_TIMEOUT_S = float(os.getenv("SUPERVISOR_HEALTH_TIMEOUT_S", "5") or 5)
PIPELINE_HEARTBEAT_MAX_AGE_S = float(os.getenv("SUPERVISOR_PIPELINE_MAX_AGE_S", "180") or 180)
ZOMBIE_AGE_S = float(os.getenv("SUPERVISOR_ZOMBIE_AGE_S", "600") or 600)
RAM_PCT_LIMIT = float(os.getenv("SUPERVISOR_RAM_PCT_LIMIT", "90") or 90)
HARD_CAP_S = float(os.getenv("SUPERVISOR_HARD_CAP_S", "55") or 55)
DRY_RUN = os.getenv("SUPERVISOR_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}

logger = logging.getLogger("supervisor")
_MACTIONS: list[str] = []
_T0 = time.monotonic()


def _over() -> bool:
    return (time.monotonic() - _T0) >= HARD_CAP_S


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return int(p.returncode), str(p.stdout or "") + str(p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 127, str(exc)[:200]


def _systemctl(*args: str, timeout: int = 25) -> tuple[int, str]:
    if DRY_RUN:
        _MACTIONS.append("dry-run systemctl %s" % " ".join(args))
        return 0, "dry-run"
    return _run(["systemctl", *args], timeout=timeout)


def _svc_active(unit: str) -> bool:
    code, out = _run(["systemctl", "is-active", unit], timeout=10)
    return code == 0 and (out or "").strip() == "active"


def health_ok(port: int = WEBCHAT_PORT, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    import urllib.request

    url = "http://127.0.0.1:%d/health" % int(port)
    try:
        with urllib.request.urlopen(url, timeout=max(1.0, float(timeout))) as resp:
            if int(getattr(resp, "status", 200) or 200) != 200:
                return False
            body = resp.read(4096).decode("utf-8", "ignore")
            return '"ok":true' in body.replace(" ", "")
    except Exception:
        return False


def _hb_age(path: Path) -> float | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return max(0.0, time.time() - float(data.get("ts") or 0))
    except Exception:
        return None


def _ps_rows() -> list:
    code, out = _run(["ps", "-eo", "pid=,ppid=,etimes=,args="], timeout=10)
    if code != 0 or not out:
        return []
    rows: list = []
    for line in out.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid_s, ppid_s, age_s, args = parts
        if not (pid_s.isdigit() and ppid_s.isdigit() and age_s.isdigit()):
            continue
        rows.append((int(pid_s), int(ppid_s), int(age_s), args))
    return rows


def _is_chrome(args: str) -> bool:
    low = args.lower()
    return ("chromium" in low or "chrome" in low) and "supervisor" not in low


def _is_pipeline(args: str) -> bool:
    low = args.lower()
    return ("pipeline.py" in low and ("--submit" in low or "--targets" in low)) or (
        "auto_runner.py" in low and "supervisor" not in low
    )


def _kill(pid: int) -> bool:
    if DRY_RUN:
        _MACTIONS.append("dry-run kill -9 %d" % pid)
        return True
    code, _ = _run(["kill", "-9", str(pid)], timeout=10)
    return code == 0


def fix_webchat() -> str:
    """KAPI 1: /health 5 sn cevap vermezse portu bosalt + restart."""
    if _over():
        return "webchat: zaman asimi (atlaniyor)"
    if health_ok():
        return "webchat: /health 200 OK"
    _MACTIONS.append("webchat /health yanitsiz")
    _run(["fuser", "-k", "-9", "%d/tcp" % WEBCHAT_PORT], timeout=10)
    _systemctl("reset-failed", "nirvana-webchat.service")
    _systemctl("restart", "nirvana-webchat.service")
    time.sleep(6)
    if health_ok():
        _MACTIONS.append("webchat restart -> /health 200")
        return "webchat: OLDU -> restart, /health 200 (60sn icinde)"
    _MACTIONS.append("webchat restart sonrasi hala yanitsiz")
    return "webchat: restart edildi AMA hala yanitsiz — operator bakmali"

def fix_pipeline(hb_age) -> str:
    """KAPI 2: nabiz 180 sn eskiyse agaci oldur + zombileri supur + restart."""
    if _over():
        return "pipeline: zaman asimi (atlaniyor)"
    if hb_age is not None and hb_age <= PIPELINE_HEARTBEAT_MAX_AGE_S:
        return "pipeline: nabiz taze (%.0fs)" % hb_age
    _MACTIONS.append(
        "pipeline nabiz %s" % ("yok" if hb_age is None else "eski (%.0fs)" % hb_age)
    )
    rows = _ps_rows()
    killed = 0
    for pid, ppid, age, args in rows:
        if _over():
            break
        if _is_pipeline(args):
            if _kill(pid):
                killed += 1
                _MACTIONS.append("oldurulen pipeline pid=%d" % pid)
        elif _is_chrome(args) and (ppid == 1 and age >= ZOMBIE_AGE_S):
            if _kill(pid):
                killed += 1
                _MACTIONS.append("oldurulen zombi chrome pid=%d yas=%ds" % (pid, age))
    _systemctl("reset-failed", "nirvana-pipeline.service")
    _systemctl("restart", "nirvana-pipeline.service")
    _MACTIONS.append("nirvana-pipeline restart")
    return "pipeline: OLDU -> %d surec olduruldu, taze tur ateslendi (60sn)" % killed


def sweep_zombies() -> str:
    """KAPI 3: 10 dk+ yetim chrome zombileri (her turda, ucuz supurge)."""
    if _over():
        return "zombi: zaman asimi (atlaniyor)"
    rows = _ps_rows()
    killed = 0
    for pid, ppid, age, args in rows:
        if _over():
            break
        if ppid == 1 and age >= ZOMBIE_AGE_S and _is_chrome(args):
            if _kill(pid):
                killed += 1
    if killed:
        _MACTIONS.append("%d zombi chrome temizlendi" % killed)
        return "zombi: %d yetim chrome olduruldu" % killed
    return "zombi: temiz (0)"


def guard_ram() -> str:
    """KAPI 4: RAM %90+ -> cache bosalt + en obur asili sureci oldur."""
    if _over():
        return "ram: zaman asimi (atlaniyor)"
    code, out = _run(
        ["sh", "-c", "free | awk '/^Mem:/{print $3/$2*100}'"], timeout=10
    )
    try:
        pct = float((out or "").strip().split()[0])
    except Exception:
        return "ram: olculemedi (atlaniyor)"
    if pct < RAM_PCT_LIMIT:
        return "ram: %%%.0f normal" % pct
    _MACTIONS.append("RAM %%%.0f kritik" % pct)
    _run(["sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], timeout=10)
    victim = 0
    for pid, _ppid, age, args in _ps_rows():
        if _is_pipeline(args) or (_is_chrome(args) and age >= ZOMBIE_AGE_S):
            if _kill(pid):
                victim = pid
                _MACTIONS.append("RAM icin oldurulen pid=%d" % pid)
                break
    return "ram: %%%.0f KRITIK -> cache bosaltildi%s" % (
        pct, (" + pid=%d olduruldu" % victim) if victim else " (kurban yok)",
    )


def _load_sup() -> dict:
    try:
        if SUP_STATE.exists():
            data = json.loads(SUP_STATE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_sup(state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SUP_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(SUP_STATE)
    except Exception:
        pass


def notify(report: str, actions: list) -> None:
    """SADECE gercek mudahalede TEK SATIR Telegram (spam yok, yalan yok)."""
    if not actions or DRY_RUN:
        return
    try:
        sys.path.insert(0, str(APP_DIR))
        import owner_notify as _on  # type: ignore

        _on.send(
            "NOBETCI (60s): %s | aksiyon: %s" % (report, "; ".join(actions[:4])),
            high_priority=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("supervisor notify atlandi: %s", str(exc)[:120])


def main() -> int:
    if os.name != "posix":
        print("supervisor: posix disi (gelistirme) — no-op")
        return 0
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    hb = _hb_age(HEARTBEAT_AUTO_RUNNER)
    parts = [fix_webchat(), fix_pipeline(hb), sweep_zombies(), guard_ram()]
    report = " | ".join(parts)
    state = _load_sup()
    state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state["last_report"] = report
    state["last_actions"] = list(_MACTIONS)
    state["runs"] = int(state.get("runs") or 0) + 1
    if _MACTIONS:
        state["repairs"] = int(state.get("repairs") or 0) + 1
    _save_sup(state)
    print("supervisor: %s" % report)
    notify(report, list(_MACTIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
