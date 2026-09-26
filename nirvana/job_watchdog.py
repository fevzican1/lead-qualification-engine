"""Lane AO — job_watchdog [Oracle VM, light]: İŞ seviyesi bekçi (faso fiso değil).

Kök neden: eski bekçi yalnızca ``systemctl is-active`` bakıyordu. Servis "active"
görünürken kuyruk 0 / yakıt 0 / form 0 kalabiliyordu (canlı arıza: günlerce 0).
Bu lane SERVİSİN DEĞİL İŞİN sağlığına bakar:

- ``queue``  : üretim kuyruğu derinliği (domain_store.queue_depth)
- ``fuel``   : hot_fuel rezervuarındaki hazır yakıt (hedefin altındaysa kırmızı)
- ``forms``  : bugünkü form sayısı (gün ilerlemesine göre beklenen tabanın altı)
- ``webchat``: müşteri kapısı ``/health`` + bugünkü webchat oturum sayısı
  ("müşteri bize ulaşıyor mu?" sorusunun ölçülmüş cevabı)

Otonom onarım döngüsü (1 dk'da bir tarama; MAKSİMUM 5 dakika; kök neden odaklı):

- Chromium kilidi : 5 dk boyunca form yoksa ``pkill -9 -f chromium`` + servis restart.
- Port kilidi     : WebChat ``/health`` yanıtsızsa ``fuser -k -9 <port>/tcp`` +
  ``systemctl reset-failed`` + ``systemctl restart``.
- SQLite WAL kilidi: kilitli veritabanında checkpoint + ``-wal``/``-shm`` sıfırlama.
- Asılı süreçler  : yaş tavanını aşan pipeline / boru sarmalayıcı süreçler süpürülür.
- SERT TAVAN      : 5 dakika içinde çözülmezse izin/bildirim OLMADAN ``sudo reboot``.

Bildirim SADECE iş otonom çözüldüğünde, tek satır:
``Kök Neden: [X] -> 5 dk içinde Otonom Onarıldı`` (spam yok; operatör beklenmez).
``dry_run=True`` hiçbir restart/bildirim yapmaz (kurulum doğrulaması).
Hiçbir hata ana hattı düşürmez: tüm ölçümler try/except ile korunur.
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
WEBCHAT_PORT = int(os.getenv("WEBCHAT_PORT", "8765") or 8765)
FORMS_GRACE_HOUR = int(os.getenv("JOB_WATCHDOG_FORMS_GRACE_HOUR", "3") or 3)
# Asılı süreç tavanı: legit pipeline turu PIPELINE_RUN_TIMEOUT_SECONDS (2400s) ile
# sınırlıdır; bu yaştan eski pipeline süreci kaçak/asılıdır. Canlı arıza
# 2026-09-25: cgroup dışında başlatılan `pipeline.py --targets ... | tail -n 50`
# süreci 9 saat asılı kaldı; `systemctl restart` onu öldüremedi.
STUCK_PROC_MAX_AGE_S = float(os.getenv("JOB_WATCHDOG_STUCK_MAX_AGE_S", "2700") or 2700)
# Otonom müdahale döngüsü (MAKSİMUM 5 dk sert tavan): timer 1 dk'da bir tarar;
# tavan dolduğunda ve iş hâlâ kırmızıysa `sudo reboot` (izin/bildirim yok).
HARD_CAP_S = float(os.getenv("JOB_WATCHDOG_HARD_CAP_S", "300") or 300)
VERIFY_WAIT_S = float(os.getenv("JOB_WATCHDOG_VERIFY_WAIT_S", "20") or 20)
FORM_IDLE_S = float(os.getenv("JOB_WATCHDOG_FORM_IDLE_S", "300") or 300)
MAX_ROUNDS = int(os.getenv("JOB_WATCHDOG_MAX_ROUNDS", "12") or 12)

# Kök neden etiketleri: tek satır başarı bildiriminde kullanılır.
RC_DB = "SQLite WAL Kilidi"
RC_CHROMIUM = "Chromium Kilitlenmesi (5 dk form yok)"
RC_WORK = "İş Hattı Durması (kuyruk/yakıt/form)"

UNIT_FOR_SIGNAL = {
    "queue": "nirvana-pipeline.service",
    "fuel": "nirvana-pipeline.service",
    "forms": "nirvana-pipeline.service",
    "webchat": "nirvana-webchat.service",
}
# Otonom müdahale sırasında hedeflenen systemd birimleri (kök neden eşlemesi).
PIPELINE_UNIT = "nirvana-pipeline.service"
WEBCHAT_UNIT = "nirvana-webchat.service"


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


def _kill_pid(pid: int) -> bool:
    """PID'i zorla öldür (SIGKILL); hata halinde False (asla raise etmez)."""
    try:
        subprocess.run(
            ["kill", "-9", str(pid)],
            capture_output=True, timeout=10, check=False,
        )
        return True
    except Exception:  # noqa: BLE001 — öldürme hatası bekçiyi düşürmez
        return False


def _ps_rows() -> list[tuple[int, int, str]]:
    """(pid, yaş_sn, args) listesi — posix; hata halinde boş (fail-open)."""
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,etimes=,args="],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except Exception:  # noqa: BLE001
        return []
    rows: list[tuple[int, int, str]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, age_s, args = parts
        if not pid_s.isdigit() or not age_s.isdigit():
            continue
        rows.append((int(pid_s), int(age_s), args))
    return rows


def sweep_stuck_processes(
    *,
    max_age_s: float | None = None,
    rows: list[tuple[int, int, str]] | None = None,
    kill_fn: Any = None,
) -> list[str]:
    """Kaçak/asılı form hattı süreçlerini yaş tavanıyla temizle (her turda).

    Canlı arıza 2026-09-25: cgroup dışında başlatılan `pipeline.py --targets ...
    --submit | tail -n 50` süreci 9 saat asılı kaldı; `systemctl restart` onu
    öldüremediği için form hattı 0'da dondu. Bekçi artık systemd'ye bağımlı
    değil: yaş tavanını aşan pipeline süreçlerini ve boru sarmalayıcısı
    `tail` süreçlerini kendisi öldürür.

    Kurallar (yanlış pozitif koruması):
      * `pipeline.py` (--targets/--submit) > max_age_s (varsayılan 2700s:
        PIPELINE_RUN_TIMEOUT_SECONDS=2400 + 5 dk marj) → kaçak, öldür.
      * `tail -n 50` boru sarmalayıcısı > 600s → asılı, öldür.
      * Çalışan uzun ömürlü `auto_runner.py` servis süreci ÖLDÜRÜLMEZ (zaten
        systemd restart mekanizması ona bağlı); yalnızca `bash -c` sarmalayıcısı
        içindeki farklı kabuk kopyaları yaş tavanında öldürülür.
    """
    if rows is None:
        if os.name != "posix":
            return []
        rows = _ps_rows()
    limit = float(max_age_s if max_age_s is not None else STUCK_PROC_MAX_AGE_S)
    kill = kill_fn or _kill_pid
    my_pid = os.getpid()
    killed: list[str] = []
    for pid, age, args in rows:
        if pid == my_pid:
            continue
        match = ""
        if "pipeline.py" in args and ("--submit" in args or "--targets" in args):
            if age >= limit:
                match = "pipeline"
        # KURALLAR BAĞIMSIZ (elif DEĞİL): birleşik `bash -c ... pipeline.py
        # --targets ... | tail -n 50` satırında yaş tavanı henüz dolmamış olsa
        # bile boru sarmalayıcısı 10 dk eşiğinde yakalanmalı. Canlı arıza
        # 2026-09-25'teki asılı süreç tam bu birleşik biçimdeydi ve elif
        # zinciri yüzünden tail kuralı hiç değerlendirilmiyordu.
        if not match and "tail -n 50" in args and age >= 600:
            # Boru sarmalayıcısı hiçbir meşru akışta 10 dk yaşamaz.
            match = "tail"
        if (not match and "auto_runner.py" in args and "bash -c" in args
                and age >= limit):
            # Farklı kabukta başlatılmış kopya (systemd cgroup'u dışında) —
            # servis süreci değil.
            match = "auto_runner_stray"
        if not match:
            continue
        if kill(pid):
            killed.append(f"{match}:{pid}")
            logger.warning("Kaçak/asılı süreç temizlendi: %s (yaş %ss)", args[:120], age)
    return killed


def restart_unit(unit: str) -> dict[str, Any]:
    """İlgili birimi yeniden başlat; hata bilgisi raporlanır (asla raise etmez)."""
    # ÖNCE: systemd dışında asılı kalmış kaçak süreçleri temizle. Canlı arıza
    # 2026-09-25: farklı bir kabukta başlatılan `pipeline.py --submit` süreci
    # `| tail -n 50` borusuna takılı kalmış; systemd "running" görüp restart
    # etmemiş, Chromium kilidi kaçak süreçte kalmış, 9 saat form 0. Restart
    # ÖNCESİ kill şart.
    killed: list[str] = []
    if unit == "nirvana-pipeline.service":
        # Servisin KENDİ sürecini (MainPID) -9 ile öldürme: `systemctl restart`
        # onu zaten düzgün kapatır. Kaçak = cgroup DIŞINDAKİ farklı kabuk
        # kopyaları; MainPID korunur, gerisi temizlenir.
        main_pid = ""
        try:
            show = subprocess.run(
                ["systemctl", "show", unit, "-p", "MainPID", "--value"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            main_pid = (show.stdout or "").strip()
        except Exception:  # noqa: BLE001 — PID çözülemezse yalnız pattern koruması
            main_pid = ""
        for pat in ("pipeline.py --targets", "pipeline.py --submit",
                    "auto_runner.py"):
            try:
                ps = subprocess.run(
                    ["pgrep", "-f", pat],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                for pid in (ps.stdout or "").split():
                    pid = pid.strip()
                    if not pid.isdigit():
                        continue
                    if main_pid and pid == main_pid:
                        continue
                    try:
                        subprocess.run(
                            ["kill", "-9", pid],
                            capture_output=True, timeout=10, check=False,
                        )
                        killed.append(f"{pat.split()[0]}:{pid}")
                    except Exception:
                        pass
            except Exception:
                pass
        # Boru sarmalayıcısı da ölsün (bash -c ... | tail -n 50 asılı kalıyor).
        try:
            ps = subprocess.run(
                ["pgrep", "-f", "tail -n 50"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            for pid in (ps.stdout or "").split():
                pid = pid.strip()
                if pid.isdigit():
                    try:
                        subprocess.run(
                            ["kill", "-9", pid],
                            capture_output=True, timeout=10, check=False,
                        )
                        killed.append(f"tail:{pid}")
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        proc = subprocess.run(
            ["systemctl", "restart", unit],
            capture_output=True, text=True, timeout=60, check=False,
        )
        out = {"unit": unit, "ok": proc.returncode == 0,
                "code": int(proc.returncode),
                "err": (proc.stderr or "").strip()[:160]}
        if killed:
            out["killed"] = killed
        return out
    except Exception as exc:  # noqa: BLE001 — onarım hatası bekçiyi düşürmez
        out2 = {"unit": unit, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:140]}
        if killed:
            out2["killed"] = killed
        return out2


# --- otonom müdahale araçları (1 dk tarama / 5 dk sert tavan) -----------------

def _run(cmd: list[str], *, timeout: float = 30, cmd_fn: Any = None) -> dict[str, Any]:
    """Harici komut; hata halinde ``ok: False`` döner, asla raise etmez.

    ``cmd_fn`` testlerde komutları yakalamak için enjekte edilir (canlıda yok).
    """
    if cmd_fn is not None:
        try:
            out = cmd_fn(list(cmd))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:120]}
        return out if isinstance(out, dict) else {"ok": bool(out)}
    if os.name != "posix":
        return {"ok": False, "skipped": "posix-disi ortam"}
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True,
                              timeout=timeout, check=False)
        return {
            "ok": proc.returncode == 0,
            "code": int(proc.returncode),
            "out": (proc.stdout or "").strip()[:200],
            "err": (proc.stderr or "").strip()[:200],
        }
    except Exception as exc:  # noqa: BLE001 — müdahale hatası bekçiyi düşürmez
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:160]}


def kill_chromium(*, cmd_fn: Any = None) -> dict[str, Any]:
    """Chromium kilidi: 5 dk form yok → tüm chromium süreçlerini -9 ile indir."""
    return _run(["pkill", "-9", "-f", "chromium"], timeout=15, cmd_fn=cmd_fn)


def _port_clear_candidates(port: int) -> list[list[str]]:
    """Port kilidi temizliği: fuser → lsof → ss (ücretsiz araçlar, sırayla)."""
    p = int(port)
    return [
        ["fuser", "-k", "-9", f"{p}/tcp"],
        ["sh", "-c", f"lsof -ti tcp:{p} 2>/dev/null | xargs -r kill -9"],
        ["sh", "-c",
         f"ss -ltnp 2>/dev/null | grep ':{p} ' | grep -o 'pid=[0-9]*' "
         "| cut -d= -f2 | xargs -r kill -9"],
    ]


def clear_port(port: int, *, cmd_fn: Any = None) -> dict[str, Any]:
    """WebChat yanıtsız: portu ``fuser -k -9`` (gerekirse lsof/ss) ile boşalt."""
    last: dict[str, Any] = {"ok": False}
    for cmd in _port_clear_candidates(port):
        last = _run(cmd, timeout=15, cmd_fn=cmd_fn)
        if last.get("ok"):
            return last
    return last


def reset_failed(unit: str, *, cmd_fn: Any = None) -> dict[str, Any]:
    """``systemctl reset-failed``: restart öncesi failed sayacını sıfırla."""
    return _run(["systemctl", "reset-failed", unit], timeout=20, cmd_fn=cmd_fn)


def sqlite_dbs() -> list[Path]:
    """Kilit denetimi yapılacak SQLite dosyaları (WAL kullanan hatlar)."""
    paths: list[Path] = []
    try:
        from nirvana import hot_fuel

        paths.append(Path(hot_fuel.DB_PATH))
    except Exception:  # noqa: BLE001 — modül yoksa atlanır
        pass
    try:
        import config

        paths.append(Path(config.ROOT) / "task_queue.db")
        paths.append(Path(config.ROOT) / "reply_cache.db")
    except Exception:  # noqa: BLE001
        pass
    out: list[Path] = []
    for path in paths:
        if path not in out:
            out.append(path)
    return out


def db_locked(path: Path) -> bool:
    """SQLite yazma kilidi var mı? ``BEGIN IMMEDIATE`` denemesiyle ölçülür."""
    import sqlite3

    target = Path(path)
    if not target.exists():
        return False
    try:
        conn = sqlite3.connect(str(target), timeout=1.0)
        try:
            conn.execute("PRAGMA busy_timeout=800")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
        finally:
            conn.close()
        return False
    except sqlite3.OperationalError as exc:
        low = str(exc).lower()
        return "locked" in low or "busy" in low
    except Exception:  # noqa: BLE001 — ölçülemezse kilit varsayma
        return False


def reset_wal(path: Path) -> dict[str, Any]:
    """SQLite WAL kilidini sıfırla: TRUNCATE checkpoint + ``-wal``/``-shm`` temizliği."""
    import sqlite3

    target = Path(path)
    out: dict[str, Any] = {"db": str(target), "ok": False, "removed": []}
    if not target.exists():
        out["error"] = "db dosyası yok"
        return out
    try:
        conn = sqlite3.connect(str(target), timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        out["checkpoint"] = "ok"
    except Exception as exc:  # noqa: BLE001 — kilit sürüyorsa yan dosyalar sıfırlanır
        out["checkpoint"] = f"{type(exc).__name__}: {exc}"[:120]
    for suffix in ("-wal", "-shm"):
        side = Path(str(target) + suffix)
        try:
            if side.exists():
                side.unlink()
                out["removed"].append(str(side))
        except OSError:
            pass
    out["ok"] = True
    return out


def reboot_enabled() -> bool:
    """Sert tavan reboot anahtarı: varsayılan AÇIK; bakım için env ile kapatılır."""
    if os.name != "posix":
        return False
    raw = str(os.getenv("JOB_WATCHDOG_REBOOT", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def hard_reboot(*, cmd_fn: Any = None) -> dict[str, Any]:
    """5 dk sert tavan doldu: izin/BİLDİRİM YOK — sunucu baştan başlatılır."""
    if cmd_fn is None and not reboot_enabled():
        return {"ok": False, "skipped": "JOB_WATCHDOG_REBOOT=0"}
    last: dict[str, Any] = {"ok": False}
    for cmd in (["sudo", "reboot"], ["systemctl", "reboot"]):
        last = _run(cmd, timeout=15, cmd_fn=cmd_fn)
        if last.get("ok"):
            logger.warning("5 dk sert tavan — sunucu yeniden başlatılıyor: %s",
                           " ".join(cmd))
            return {**last, "cmd": " ".join(cmd)}
    return {**last, "cmd": "sudo reboot",
            "error": last.get("error") or "reboot başarısız"}


def form_idle_update(
    state: dict[str, Any], sig: dict[str, Any], *, now: float | None = None,
) -> float | None:
    """Form durgunluğu (sn): onaylı form sayımı artmıyorsa süre sayılır.

    İlk turda tohumlanır (0 döner); sayım artışında damga tazelenir. ``None`` =
    form sayısı ölçülemedi (fail-open; Chromium müdahalesi tetiklenmez).
    """
    stamp = _now() if now is None else float(now)
    raw = sig.get("forms_today")
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return None
    last_count = state.get("forms_last_count")
    last_ts = float(state.get("forms_last_ts") or 0)
    if last_count is None or int(last_count) != count or last_ts <= 0:
        state["forms_last_count"] = count
        state["forms_last_ts"] = stamp
        return 0.0
    return max(0.0, stamp - last_ts)


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


def root_cause_label(
    red: set[str],
    *,
    db_locked_now: bool = False,
    forms_idle_s: float | None = None,
) -> str:
    """Tek satır bildirim için kök neden etiketi (DB > port > Chromium > iş)."""
    if db_locked_now:
        return RC_DB
    if "webchat" in red:
        return f"Port Kilidi (WebChat:{WEBCHAT_PORT})"
    if forms_idle_s is not None and float(forms_idle_s) >= FORM_IDLE_S:
        return RC_CHROMIUM
    return RC_WORK


def _locked_dbs(db_fn: Any = None) -> list[Path]:
    """Kilitli SQLite dosyaları (testler ``db_fn`` enjekte edebilir)."""
    checker = db_fn or db_locked
    out: list[Path] = []
    for path in sqlite_dbs():
        try:
            if checker(path):
                out.append(path)
        except Exception:  # noqa: BLE001 — DB denetimi müdahaleyi düşürmez
            continue
    return out


def _idle_from_state(state: dict[str, Any]) -> float | None:
    raw = state.get("forms_idle_s")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def autonomous_recover(
    sig: dict[str, Any],
    verdict: dict[str, Any],
    *,
    state: dict[str, Any],
    **kw: Any,
) -> dict[str, Any]:
    """Kök nedene göre otonom müdahale; MAKSİMUM 5 dk sonra çözülmezse sert reboot.

    Sıra (kök neden odaklı):
      1. SQLite WAL kilidi varsa checkpoint + ``-wal``/``-shm`` sıfırla.
      2. WebChat yanıtsızsa ``fuser -k -9 <port>/tcp`` + reset-failed + restart.
      3. 5 dk form yoksa Chromium'u ``-9`` ile indir + pipeline restart + süpürge.
      4. Kırmızı süren diğer iş sinyallerinde kaçak süpürge + pipeline restart.
    Tur sonunda ölçüm yinelenir; iş yeşile dönerse TEK SATIR bildirim gider.
    Süre dolduğunda hâlâ kırmızıysa izin/bildirim olmadan ``sudo reboot``.
    """
    now = kw.get("now_fn") or _now
    sleep = kw.get("sleep_fn") or time.sleep
    budget = float(kw.get("budget_s") if kw.get("budget_s") is not None else HARD_CAP_S)
    wait = float(kw.get("wait_s") if kw.get("wait_s") is not None else VERIFY_WAIT_S)
    cmd_fn = kw.get("cmd_fn")
    started = float(now())
    deadline = started + budget
    applied: list[str] = []
    notified: list[str] = []
    resolved = False

    red_first = set(verdict.get("red") or [])
    idle = _idle_from_state(state)
    label = root_cause_label(red_first,
                             db_locked_now=bool(_locked_dbs(kw.get("db_fn"))),
                             forms_idle_s=idle)
    base = {"root_cause": label, "applied": applied, "cap_s": budget,
            "notified": notified}
    if not red_first:
        return {**base, "resolved": True, "rebooted": False, "waited_s": 0.0}

    def probe() -> None:
        fn = kw.get("probe_fn")
        if fn is not None:
            s2, v2 = fn()
        else:
            s2 = measure()
            v2 = evaluate(s2)
        sig.clear()
        sig.update(s2)
        verdict.clear()
        verdict.update(v2)
        state["forms_idle_s"] = form_idle_update(state, sig, now=now())

    done: dict[str, int] = {}

    def once(key: str, limit: int = 2) -> bool:
        """Aynı spesifik müdahale bir turda en fazla ``limit`` kez uygulanır."""
        if done.get(key, 0) >= limit:
            return False
        done[key] = done.get(key, 0) + 1
        return True

    rounds = 0
    while rounds < MAX_ROUNDS:
        rounds += 1
        red = set(verdict.get("red") or [])
        if not red:
            resolved = True
            break
        if applied and float(now()) - started >= budget * 0.98:
            break  # tavan doldu: yeni müdahale yok, karar aşağıda verilir
        db_paths = _locked_dbs(kw.get("db_fn"))
        if db_paths and once("wal"):
            wal = kw.get("wal_fn") or reset_wal
            for path in db_paths:
                try:
                    out = wal(path)
                except Exception:  # noqa: BLE001 — sıfırlama hatası döngüyü düşürmez
                    out = {"ok": False}
                ok = isinstance(out, dict) and bool(out.get("ok"))
                applied.append(
                    f"wal_reset:{Path(path).name}:{'ok' if ok else 'denendi'}")
        if "webchat" in red:
            if once("port"):
                clear_port(int(WEBCHAT_PORT), cmd_fn=cmd_fn)
                applied.append(f"port_clear:{WEBCHAT_PORT}")
                reset_failed(WEBCHAT_UNIT, cmd_fn=cmd_fn)
                applied.append("reset-failed:webchat")
            if once("webchat"):
                (kw.get("restart_fn") or restart_unit)(WEBCHAT_UNIT)
                applied.append("restart:webchat")
        if red & {"queue", "fuel", "forms"}:
            idle_now = _idle_from_state(state)
            if idle_now is not None and idle_now >= FORM_IDLE_S and once("chromium"):
                # 5 dk form yok → Chromium kilidi: tüm chromium süreçleri indirilir.
                (kw.get("kill_fn") or kill_chromium)(cmd_fn=cmd_fn)
                applied.append(f"chromium_kill(idle={int(idle_now)}s)")
            if once("pipeline"):
                try:
                    swept = (kw.get("sweep_fn") or sweep_stuck_processes)(
                        max_age_s=kw.get("sweep_max_age_s"))
                except Exception:  # noqa: BLE001 — süpürge hatası döngüyü düşürmez
                    swept = []
                if swept:
                    applied.append(f"stray_sweep:{len(swept)}")
                (kw.get("restart_fn") or restart_unit)(PIPELINE_UNIT)
                applied.append("restart:pipeline")
        remaining = deadline - float(now())
        if remaining <= 0:
            break
        sleep(min(wait, remaining))
        probe()
        if not verdict.get("red"):
            resolved = True
            break
        if float(now()) >= deadline:
            break

    waited = round(float(now()) - started, 1)
    if not resolved and not verdict.get("red"):
        resolved = True  # son ölçüm temiz: iş kurtuldu
    if resolved:
        msg = f"[DevSolve Ops] Kök Neden: {label} -> 5 dk içinde Otonom Onarıldı"
        sent = bool((kw.get("notify_fn") or _notify)(msg))
        if sent:
            notified.append(label)
        return {**base, "resolved": True, "rebooted": False, "message": msg,
                "waited_s": waited}
    reboot = (kw.get("reboot_fn") or hard_reboot)(cmd_fn=cmd_fn)
    rebooted = bool(isinstance(reboot, dict) and reboot.get("ok"))
    logger.warning("5 dk sert tavan doldu (%s) — OTONOM REBOOT, bildirim YOK", label)
    return {**base, "resolved": False, "rebooted": rebooted, "reboot": reboot,
            "waited_s": waited}
def _acquire_singleton() -> Any:
    """Canlı turlar için tek örnek kilidi (timer 1 dk; döngü 5 dk sürebilir).

    Posix'te ``flock`` (süreç ölürse kilit düşer). Windows/test ortamında kilit
    gerekmez → ``None``. Başka bir tur hâlâ çalışıyorsa ``"busy"`` döner.
    """
    if os.name != "posix":
        return None
    try:
        import fcntl

        handle = open(state_path("job_watchdog.lock"), "w")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        return "busy"
    except Exception:  # noqa: BLE001 — kilit altyapısı yoksa engelleme yapma
        return None


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
    lock: Any = None
    if not args.no_notify:
        lock = _acquire_singleton()
        if lock == "busy":
            # Kırmızı iş 5 dk sürebilen bir onarım turunu tetikler; önceki tur
            # bitmeden yeni tur başlamaz (systemd birimi zaten seridir; çifte
            # emniyet katmanı).
            print(json.dumps({"lane": "job_watchdog", "skipped": "already_running"},
                             ensure_ascii=False))
            return 0
    result = run_batch(notify=args.notify and not args.no_notify,
                       dry_run=args.no_notify)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Ölç → kırmızı say → (streak dolunca) restart → 5 dk otonom onarım döngüsü.

    Bildirim YALNIZCA döngü işi otonom çözdüğünde tek satır gider; sorun 5 dk
    içinde çözülmezse sert tavan devreye girer: izin/bildirim olmadan reboot.
    """
    notify = bool(kwargs.get("notify", True))
    dry_run = bool(kwargs.get("dry_run", False))
    allow_restart = bool(kwargs.get("restart", True)) and not dry_run
    sig: dict[str, Any] = kwargs.get("checks") or measure()
    verdict = evaluate(sig)
    # DB KİLİDİ kırmızı bir sinyaldir: diğer dört sinyal yeşil olsa bile
    # kilitli SQLite, 5 dk otonom onarım döngüsünü başlatır (aksi halde WAL
    # sıfırlama hiç tetiklenmez ve iş süresiz durur).
    try:
        locked_now = _locked_dbs(kwargs.get("db_fn"))
    except Exception:  # noqa: BLE001 — kilit denetimi bekçiyi düşürmez
        locked_now = []
    if locked_now:
        verdict["checks"]["db"] = {
            "ok": False,
            "detail": "SQLite kilidi: " + ", ".join(Path(p).name for p in locked_now),
        }
        verdict["red"] = [name for name in verdict["red"] if name != "db"] + ["db"]
        verdict["ok"] = False

    state = _load_state()
    signals = state.get("signals") if isinstance(state.get("signals"), dict) else {}
    now = _now()
    restarts: list[dict[str, Any]] = []
    notified: list[str] = []
    notes: list[str] = []
    test_mode = kwargs.get("restart_fn") is not None and not systemd_available()
    # OTONOM TEMİZLİK (her turda, systemd'ye bağımsız): systemd cgroup'u dışında
    # asılı kalmış pipeline/tail kaçaklarını yaş tavanıyla öldür. 2026-09-25
    # arızasında `systemctl restart` kaçağı öldüremediği için form hattı 9 saat
    # 0'da donmuştu; bu süpürge aynı arızayı dakikalar içinde kendi kendine çözer.
    swept: list[str] = []
    if allow_restart:
        try:
            sweep = kwargs.get("sweep_fn") or sweep_stuck_processes
            swept = sweep(max_age_s=kwargs.get("sweep_max_age_s"))
        except Exception:  # noqa: BLE001 — süpürge hatası bekçiyi düşürmez
            logger.warning("Kaçak süreç süpürgesi atlandı", exc_info=True)
        if swept:
            notes.append(f"kaçak/asılı süreç temizlendi: {', '.join(swept)}")
    # Form durgunluğu: Chromium kilidi kök neden girdisi (5 dk form yok → müdahale).
    state["forms_idle_s"] = form_idle_update(state, sig, now=now)

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
        signals[name] = row

    # 5 DAKİKALIK OTONOM MÜDAHALE DÖNGÜSÜ (kök neden odaklı; sert tavanlı).
    # Canlı turlarda (bildirim açık) varsayılan olarak çalışır; testler
    # auto_loop/probe_fn/sleep_fn enjeksiyonlarıyla döngüyü deterministik kılar.
    auto_loop = kwargs.get("auto_loop")
    if auto_loop is None:
        auto_loop = bool(notify) and allow_restart
    recovery: dict[str, Any] | None = None
    if auto_loop and allow_restart and verdict["red"]:
        injected = {key: kwargs[key] for key in (
            "now_fn", "sleep_fn", "probe_fn", "cmd_fn", "restart_fn", "sweep_fn",
            "kill_fn", "wal_fn", "db_fn", "notify_fn", "reboot_fn",
            "budget_s", "wait_s", "sweep_max_age_s",
        ) if key in kwargs}
        recovery = autonomous_recover(sig, verdict, state=state, **injected)
        notified = list(recovery.get("notified") or [])
        if recovery.get("root_cause"):
            notes.append(f"kök neden: {recovery['root_cause']}")
        for action in recovery.get("applied") or []:
            notes.append(f"otonom müdahale: {action}")
        if recovery.get("rebooted"):
            notes.append("5 dk sert tavan: OTONOM REBOOT (bildirim yok)")

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
        "notes": notes,
        "swept": swept,
        "streaks": {name: int((signals.get(name) or {}).get("streak") or 0)
                    for name in signals},
        "webchat": sig.get("webchat") or {},
        "queue_depth": sig.get("queue_depth"),
        "fuel_ready": sig.get("fuel_ready"),
        "fuel_target": sig.get("fuel_target"),
        "forms_today": sig.get("forms_today"),
        "forms_expected": sig.get("forms_expected"),
        "forms_idle_s": state.get("forms_idle_s"),
        "recovery": recovery,
        "hard_cap_s": HARD_CAP_S,
        "state": str(state_path(STATE_NAME)),
        "note": ("İş seviyesi bekçi: kuyruk+yakıt+form+webchat. 1 dk tarama; "
                 "kırmızıda kök neden müdahalesi (chromium/port/WAL); 5 dk "
                 "içinde çözülmezse otonom reboot."),
    }
