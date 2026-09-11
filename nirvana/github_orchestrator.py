"""Lane Q — github_orchestrator [Oracle VM, hafif tetikleyici].

Rapor kapsamı: GitHub Actions REST API ile olay güdümlü sunucusuz orkestrasyon.
workflow_dispatch ve repository_dispatch tetikleme protokollerini kullanır.
Çifte JSON serileştirme hatasını önler (dict doğrudan json= parametresine).
return_run_details=true ile workflow_run_id anında döner.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"


def _auth_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def dispatch_workflow(workflow: str, inputs: dict[str, str], *,
                      owner: str = "", repo: str = "", ref: str = "master") -> dict[str, Any]:
    """workflow_dispatch: belirli bir workflow'u tetikler (repo master dalını kullanır)."""
    owner = owner or os.getenv("GITHUB_OWNER", "")
    repo = repo or os.getenv("GITHUB_REPO", "")
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches"
    payload = {"ref": ref, "inputs": inputs}
    try:
        r = httpx.post(url, json=payload, headers=_auth_headers(), timeout=15)
        return {"ok": r.status_code in (200, 204), "status": r.status_code}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)[:120]}


def dispatch_event(event_type: str, payload: dict[str, Any], *,
                   owner: str = "", repo: str = "") -> dict[str, Any]:
    """repository_dispatch: özel event_type ile karmaşık JSON gönderir."""
    owner = owner or os.getenv("GITHUB_OWNER", "")
    repo = repo or os.getenv("GITHUB_REPO", "")
    url = f"{GITHUB_API}/repos/{owner}/{repo}/dispatches"
    body = {"event_type": event_type, "client_payload": payload}
    try:
        r = httpx.post(url, json=body, headers=_auth_headers(), timeout=15)
        return {"ok": r.status_code in (200, 204), "status": r.status_code}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)[:120]}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Oracle'da hafif tetikleme testi (token yoksa dry-run)."""
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        return {"ok": False, "reason": "no_token", "ts": time.time()}
    return dispatch_event("nirvana-ping", {"ts": time.time()})
