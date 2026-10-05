"""Lane AL — keepalive_guard [GitHub Actions].

Public repolarda 60 gun boyunca commit/depo etkinligi olmazsa GitHub zamanlanmis
workflow'lari otomatik olarak pasife alir (disabled_inactivity). Feed/kuyruk
zinciri bu yuzden sessizce durur ve satis hatti kurur.

Bu lane bekci gorevi gorur:
- state dosyasindaki son etkinlikten gecen gunu olcer,
- pencere (varsayilan 20-40 gun) dolunca GitHub REST API contents API ile mikro
  commit (synthetic heartbeat) uretir; zamanlayici canli kalir,
- token yoksa dry-run doner (yerel test/CI bozulmaz). Maliyet: $0.

Tasarim: agir is yok; tek dosya, en fazla iki API cagrisi (GET sha + PUT icerik).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from nirvana.registry import state_path

STATE_NAME = "github_keepalive.json"
API_BASE = "https://api.github.com"
# GitHub 60 gun sonra pasife alir; rapor stratejisi her 20-40 gunde bir dokunus.
THRESHOLD_DAYS = 40
MIN_INTERVAL_DAYS = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_stamp(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path(STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def save_state(row: dict[str, Any]) -> dict[str, Any]:
    path = state_path(STATE_NAME)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return row


def days_since(stamp: Any, *, now: datetime | None = None) -> float | None:
    """Son etkinlikten gecen gun (None: tarih okunamadi)."""
    parsed = _parse_stamp(stamp)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    return round((current - parsed).total_seconds() / 86400.0, 3)


def should_commit(days: float | None, *, threshold_days: int = THRESHOLD_DAYS) -> bool:
    """Esik asildi mi? Tarih hic yoksa (ilk kurulum) evet — pencere kapanmadan yaz."""
    if days is None:
        return True
    return days >= float(threshold_days)


def window_settled(days: float | None, *, min_interval_days: int = MIN_INTERVAL_DAYS) -> bool:
    """Erken/yinelenen dokunusu engelle: son yazim uzerinden en az N gun gecti mi."""
    if days is None:
        return True
    return days >= float(min_interval_days)


def heartbeat_body(*, note: str = "synthetic-keepalive", run_count: int = 0) -> dict[str, Any]:
    """Repo'ya yazilacak mikro icerik — sir yok, yalnizca zaman damgasi."""
    return {"keepalive": True, "note": note, "run_count": int(run_count),
            "written_at": _now_iso(),
            "guard": "GitHub Actions 60-gun pasiflesme kapisi"}


def _github_request(method: str, url: str, token: str,
                    payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tek API cagrisi. Testler bu fonksiyonu monkeypatch eder (ag yok)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "nirvana-keepalive-guard",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8") or "{}"
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def synthetic_commit(*, repo: str, token: str, branch: str = "master",
                     path: str = "nirvana/state/github_keepalive.json",
                     body: dict[str, Any] | None = None) -> dict[str, Any]:
    """contents API ile mikro commit — depo etkinligi tazelenir.

    Once mevcut sha okunur (varsa guncelleme, yoksa olusturma), sonra tek PUT.
    """
    payload = body if body is not None else heartbeat_body()
    url = f"{API_BASE}/repos/{repo}/contents/{path}"
    sha = ""
    try:
        existing = _github_request("GET", f"{url}?ref={branch}", token)
        sha = str(existing.get("sha") or "")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    content = base64.b64encode(
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    ).decode("ascii")
    put_payload: dict[str, Any] = {
        "message": f"keepalive: synthetic activity {payload.get('written_at', '')}".strip(),
        "content": content,
        "branch": branch,
    }
    if sha:
        put_payload["sha"] = sha
    result = _github_request("PUT", url, token, put_payload)
    commit = result.get("commit") if isinstance(result.get("commit"), dict) else {}
    content_row = result.get("content") if isinstance(result.get("content"), dict) else {}
    return {"committed": True, "sha": commit.get("sha") or content_row.get("sha"),
            "path": path, "branch": branch}


def _token() -> str:
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "KEEPALIVE_TOKEN", "GITHUB_DISPATCH_TOKEN"):
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return ""


def _repo() -> str:
    return (os.getenv("GITHUB_REPOSITORY") or "fevzican1/lead-qualification-engine").strip()


def run_batch(*, threshold_days: int | None = None, force: bool = False,
              dry_run: bool = False) -> dict[str, Any]:
    """GitHub Actions / CLI giris noktasi.

    dry_run=True veya token yoksa hicbir sey yazilmaz; yalnizca karar raporlanir.
    """
    threshold = int(threshold_days or THRESHOLD_DAYS)
    state = load_state()
    since = days_since(state.get("last_activity"))
    due = bool(force) or should_commit(since, threshold_days=threshold)
    # Erken dokunus engeli: son yazim uzerinden 20 gun gecmediyse bekle.
    if due and not force and since is not None and not window_settled(since):
        due = False
    branch = (os.getenv("GITHUB_REF_NAME") or "").strip() or "master"
    repo = _repo()
    token = _token()
    result: dict[str, Any] = {
        "days_since_activity": since,
        "threshold_days": threshold,
        "due": due,
        "repo": repo,
        "branch": branch,
        "token_present": bool(token),
        "action": "skip",
        "at": _now_iso(),
        "out": str(state_path(STATE_NAME)),
    }
    if not due:
        return result
    if dry_run or not token:
        result["action"] = "dry_run" if dry_run else "no_token"
        result["note"] = ("token yok — keepalive commit atilamadi; depo etkinliginin "
                          "baska bir workflow'dan gelmesi gerekir") if not token else "dry_run"
        return result
    try:
        run_count = int(state.get("run_count") or 0) + 1
        outcome = synthetic_commit(repo=repo, token=token, branch=branch,
                                   body=heartbeat_body(run_count=run_count))
        result.update({"action": "commit", "run_count": run_count, **outcome})
        save_state({"last_activity": _now_iso(), "days_before": since,
                    "run_count": run_count, "repo": repo, "branch": branch,
                    "last_commit": outcome.get("sha") or ""})
    except Exception as exc:  # noqa: BLE001 — bekci hatasi satis hattini bozmaz
        result.update({"action": "error", "error": str(exc)[:200]})
    return result


if __name__ == "__main__":  # pragma: no cover - elle calistirma kolayligi
    print(json.dumps(run_batch(), ensure_ascii=False, indent=2, default=str))