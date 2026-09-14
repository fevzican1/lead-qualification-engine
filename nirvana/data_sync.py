"""Dagitik senkronizasyon: CDC + Saga telafi [light, $0]."""
from __future__ import annotations
import hashlib
import json
import time
from typing import Any
from nirvana.registry import state_path


def event_id(row: dict[str, Any]) -> str:
    blob = json.dumps(row, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def cdc_events(rows: list[dict[str, Any]], *, op: str = "upsert") -> list[dict[str, Any]]:
    """Islem gunlugunden akis olayi uret (DB'ye ek sorgu yok)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({"event_id": event_id(r), "op": op,
                    "domain": str(r.get("domain") or r.get("host") or ""),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "row": r})
    return out


def publish_cdc(events: list[dict[str, Any]], *,
                name: str = "cdc_stream.json") -> dict[str, Any]:
    path = state_path(name)
    try:
        cur = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cur = []
    if not isinstance(cur, list):
        cur = []
    seen = {str(e.get("event_id")) for e in cur if isinstance(e, dict)}
    for e in events:
        if e.get("event_id") not in seen:
            cur.append(e)
            seen.add(str(e.get("event_id")))
    cur = cur[-2000:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return {"published": len(events), "stream": str(path), "depth": len(cur)}


def saga_run(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Saga: her adim yerel islem; hata olursa telafi sirasiyla geri alinir."""
    done: list[str] = []
    for s in steps:
        fn = s.get("run")
        name = str(s.get("name") or "step")
        try:
            if callable(fn):
                fn()
            done.append(name)
        except Exception as exc:  # noqa: BLE001
            comp: list[str] = []
            for prev in reversed(done):
                cfn = next((x.get("compensate") for x in steps
                            if str(x.get("name")) == prev), None)
                try:
                    if callable(cfn):
                        cfn()
                    comp.append(prev)
                except Exception:
                    comp.append(prev + ":compensate_failed")
            return {"ok": False, "failed_at": name,
                    "error": str(exc)[:120], "compensated": comp}
    return {"ok": True, "steps": done}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    return {"mode": "BASE/eventual-consistency",
            "cdc": "append-only json stream",
            "saga": "telafi zinciri hazir"}
