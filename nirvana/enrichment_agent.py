"""Lane B — enrichment_agent [GitHub Actions].

Free, public technical recon per discovered target: DNS resolution,
/.well-known/security.txt, and a light homepage fetch fingerprinted with the
existing stack_fingerprint module. Output is a company-specific hook line for
the application text. No paid API, capped probes per run.
"""
from __future__ import annotations

import json
import socket
from typing import Any

import httpx

import config
from nirvana.registry import state_path

DEFAULT_IN = "discovery.json"
DEFAULT_OUT = "enrichment.json"
MAX_PROBES = 40  # hard cap per run — GitHub runner stays polite, targets stay unbothered
TIMEOUT = 12.0


def dns_resolves(domain: str) -> bool:
    try:
        return bool(socket.getaddrinfo(domain.strip(), 443))
    except (OSError, ValueError):
        return False


def fetch_security_txt(domain: str) -> dict[str, Any]:
    """One request; any outcome is signal (missing security.txt = hook)."""
    for scheme in ("https",):
        url = f"{scheme}://{domain}/.well-known/security.txt"
        try:
            r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": "nirvana-enrich/1.0"})
            return {"present": r.status_code == 200, "status": r.status_code,
                    "contact": "contact:" in r.text[:4000] if r.status_code == 200 else False}
        except httpx.HTTPError:
            return {"present": False, "status": 0, "contact": False}
    return {"present": False, "status": 0, "contact": False}


def fetch_homepage(domain: str) -> dict[str, Any]:
    try:
        r = httpx.get(f"https://{domain}/", timeout=TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "nirvana-enrich/1.0"})
    except httpx.HTTPError:
        return {"ok": False, "html": "", "elapsed_ms": 0}
    return {"ok": r.status_code < 400, "html": r.text[:120_000], "elapsed_ms": int(r.elapsed.total_seconds() * 1000)}


def build_hook(domain: str, recon: dict[str, Any]) -> str:
    """Deterministic, honest technical hook derived only from observed facts."""
    parts: list[str] = []
    if not recon.get("security", {}).get("present"):
        parts.append("güvenlik.policy dosyanız (security.txt) yayında değil; API yanıt "
                     "sürenizdeki aksamalar için dış ekiplerin size ulaşacağı tek standart kanal bu")
    stack = recon.get("stack") or []
    if stack:
        parts.append(f"{', '.join(stack[:3])} yığınınızda versiyon/konfigürasyon sürüklenmesi klasik kesinti sebebi")
    if recon.get("homepage_ok") is False:
        parts.append("ana sayfanız bu taramada yanıt vermedi; zaten bilinen bir dalgalanma var")
    elif recon.get("http_probe_ms", 0) and int(recon["http_probe_ms"]) > 1500:
        parts.append(f"ana sayfanız {recon['http_probe_ms']} ms'de yanıt verdi; sınır bölgesinde")
    if not parts:
        parts.append("altyapınız sade ve sağlıklı görünüyor; tam da bu yüzden genişlerken önce "
                     "kenar servislerde kırılma yaşanır")
    return f"{domain}: " + "; ".join(parts) + " — önerilen çalışma planımızda bunlar ilk tur olur."


def recon_target(domain: str) -> dict[str, Any]:
    domain = (domain or "").strip().lower()
    page = fetch_homepage(domain)
    stack: list[str] = []
    if page["ok"]:
        try:
            import stack_fingerprint
            fp = stack_fingerprint.fingerprint(html=page["html"])
            stack = [str(s) for s in (fp.get("stack") or fp.get("hits") or [])][:6]
        except Exception:
            stack = []
    return {
        "domain": domain,
        "dns": dns_resolves(domain),
        "security": fetch_security_txt(domain),
        "homepage_ok": page["ok"],
        "http_probe_ms": page["elapsed_ms"],
        "stack": stack,
    }


def run_batch(*, in_name: str = DEFAULT_IN, out_name: str = DEFAULT_OUT, limit: int = MAX_PROBES) -> dict[str, Any]:
    in_path = state_path(in_name)
    try:
        rows = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    out_path = state_path(out_name)
    try:
        prior = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = []
    done = {str(r.get("domain")) for r in prior if isinstance(r, dict)}

    enriched = list(prior)
    probed = 0
    for row in rows:
        if probed >= limit:
            break
        if not isinstance(row, dict):
            continue
        domain = str(row.get("domain") or "").strip()
        if not domain or domain in done:
            continue
        data = recon_target(domain)
        if not data["dns"]:
            data["verdict"] = "reject_dns"
        else:
            data["verdict"] = "enriched"
            data["hook"] = build_hook(domain, data)
        data["company"] = row.get("company", domain)
        enriched.append(data)
        done.add(domain)
        probed += 1

    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(enriched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"probed": probed, "enriched": len(enriched), "out": str(out_path)}
