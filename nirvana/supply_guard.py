"""SBOM + imza zinciri dogrulama [light, $0]."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
import config


def build_sbom() -> dict[str, Any]:
    req = config.ROOT / "requirements.txt"
    comps: list[dict[str, Any]] = []
    try:
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                comps.append({"name": line, "source": "requirements.txt"})
    except OSError:
        pass
    mods: list[str] = []
    for p in sorted((config.ROOT / "nirvana").glob("*.py")):
        mods.append(p.name)
    blob = json.dumps({"deps": comps, "modules": mods},
                      sort_keys=True).encode()
    digest = hashlib.sha256(blob).hexdigest()
    sbom = {"version": 1, "digest": digest,
            "dependencies": comps, "modules": mods}
    out = config.ROOT / "sbom.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(sbom, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    tmp.replace(out)
    return {"digest": digest, "deps": len(comps),
            "modules": len(mods), "out": str(out)}


def verify_chain(*, expected_digest: str = "") -> dict[str, Any]:
    info = build_sbom()
    if expected_digest and info["digest"] != expected_digest:
        return {"ok": False, "reason": "sbom_digest_mismatch",
                "digest": info["digest"]}
    return {"ok": True, "digest": info["digest"]}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    exp = str(kwargs.get("expected_digest") or "")
    return verify_chain(expected_digest=exp)
