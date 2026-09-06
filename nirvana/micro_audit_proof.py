"""Lane L — micro_audit_proof_agent [GitHub Actions, heavy].

High-value targets' checkout/cart/main pages are probed with Playwright
headless on the GitHub runner ONLY (Oracle never runs Playwright). Observed
bottlenecks become an annotated Proof Card PNG + an honest, observed-data-only
dynamic hook. Fail-closed: every timeout/error falls back to the light default
hook with no image; temp images are deleted; one committed PNG per domain
(overwritten) keeps the repo small and the CDN (GitHub raw) URL stable.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

import config
from nirvana.registry import state_path

PROOF_DIR: Path = config.ROOT / "nirvana" / "proof-cards"
HOOKS_OUT = "proof_hooks.json"
MAX_SECONDS = 12.0
SLOW_MS = 1200
BAD_STATUS = 400
PER_RUN_LIMIT = 3
IMAGE_MAX_WIDTH = 720
_REPO = config._get("FEED_GITHUB_REPO", "fevzican1/lead-qualification-engine")
RAW_BASE = f"https://raw.githubusercontent.com/{_REPO}/master/nirvana/proof-cards"

_PROBE_JS = (
    "() => {"
    "const nav = performance.getEntriesByType('navigation')[0] || {};"
    "const res = performance.getEntriesByType('resource') || [];"
    "return {"
    " dom: nav.loadEventEnd ? Math.round(nav.loadEventEnd - nav.startTime) : 0,"
    " nav_status: nav.responseStatus || 0,"
    " slow_res: res.filter(function(r){ return r.duration > " + str(SLOW_MS) +
    " && (r.transferSize || 0) > 0; }).slice(0,5)"
    ".map(function(r){ return {url: String(r.name).slice(0,140), ms: Math.round(r.duration)}; }),"
    "};}")


def image_url(domain: str) -> str:
    return f"{RAW_BASE}/{domain}.png"


def _annotate(tmp_png: Path, out_png: Path, label: str) -> None:
    """Red header + magnifier on the screenshot (pure Pillow, no heavy AI)."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.open(tmp_png).convert("RGB")
    if img.width > IMAGE_MAX_WIDTH:
        ratio = IMAGE_MAX_WIDTH / img.width
        img = img.resize((IMAGE_MAX_WIDTH, int(img.height * ratio)))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, img.width, 44], fill=(198, 40, 40))
    draw.rectangle([0, 44, img.width, 50], fill=(255, 193, 7))
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    label = label[:90]
    draw.text((10, 12), label, font=font, fill=(255, 255, 255))
    cx, cy = img.width - 130, img.height - 130
    draw.ellipse([cx, cy, cx + 80, cy + 80], outline=(198, 40, 40), width=10)
    draw.line([cx + 62, cy + 62, cx + 105, cy + 105], fill=(198, 40, 40), width=12)
    img.save(out_png, optimize=True)


def _probe(url: str, tmp_dir: Path) -> dict[str, Any]:
    """One Playwright scan with a hard timeout. Never raises; returns failure dict."""
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return {"failed": True, "reason": "playwright_missing"}
    bad_reqs: list[dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})

            def on_response(resp):
                if resp.status >= BAD_STATUS:
                    bad_reqs.append({"url": resp.url[:140], "status": resp.status})
            page.on("response", on_response)

            page.goto(url, wait_until="load", timeout=int(MAX_SECONDS * 1000))
            data = page.evaluate(_PROBE_JS)
            if data.get("nav_status", 0) >= BAD_STATUS:
                bad_reqs.append({"url": url[:140], "status": data["nav_status"]})
            tmp_png = tmp_dir / f"{int(time.time() * 1000)}.png"
            page.screenshot(path=str(tmp_png))
            browser.close()
        return {"failed": False, "dom_ms": int(data.get("dom") or 0),
                "slow_res": data.get("slow_res") or [], "bad_reqs": bad_reqs[:6],
                "screenshot": tmp_png, "probed_url": url}
    except Exception as exc:  # timeout / target block / missing browser
        return {"failed": True, "reason": str(exc)[:120]}


def bottleneck_label(metrics: dict[str, Any]) -> str:
    slow = metrics.get("slow_res") or []
    bad = metrics.get("bad_reqs") or []
    if slow:
        top = slow[0]
        host = top["url"].split("/")[2] if top["url"][:4] == "http" else "API"
        return f"{host}: {top['ms']} ms slow fetch"
    if bad:
        return f"HTTP {bad[0]['status']} on {bad[0]['url'][:40]}"
    return f"DOM load {metrics.get('dom_ms', 0)} ms"


def build_hook(domain: str, metrics: dict[str, Any], image: str | None) -> str:
    """Honest, observed-only hook. No fabricated abandonment percentages."""
    slow = metrics.get("slow_res") or []
    bad = metrics.get("bad_reqs") or []
    if not image:
        return ""  # caller keeps the standard hook (fail-safe fallback)
    if slow:
        top = slow[0]
        host = top["url"].split("/")[2] if top["url"][:4] == "http" else "istek"
        line = (f"Taramanızda {host} isteği {top['ms']} ms'de yanıtladı; bu gecikme "
                "seviyesinde sepet adımındaki kayıp riski belirgin biçimde artar. "
                f"Teknik kanıt kartı: {image}")
    elif bad:
        line = (f"Taramanızda HTTP {bad[0]['status']} yanıtı veren istekler gördük; "
                "bu, sepet/ödeme adımlarında dönüşümü kesen klasik sinyaldir. "
                f"Teknik kanıt kartı: {image}")
    else:
        line = (f"Taramanızda checkout akışını {metrics.get('dom_ms', 0)} ms DOM "
                "yüklemesiyle ölçtük; hâlihazırda sınıra yakınsınız. "
                f"Kanıt kartı: {image}")
    return (f"Sitenizin checkout/sepet akışında {line} "
            "İlk 7 gün performans yamalarını ücretsiz uyguluyoruz.")


def load_targets(in_name: str, *, limit: int) -> list[dict[str, Any]]:
    in_path = state_path(in_name)
    try:
        rows = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    out: list[dict[str, Any]] = []
    for row in rows:
        if len(out) >= limit:
            break
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        domain = str(row.get("domain") or "").strip()
        if url.startswith("https://") and domain:
            out.append({"domain": domain, "url": url, "fallback_hook": row.get("hook", "")})
    return out


def run_proofs(items: list[dict[str, Any]], *, out_name: str = HOOKS_OUT) -> dict[str, Any]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="nirvana-proof-"))
    proofs: list[dict[str, Any]] = []
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for it in items:
            data = _probe(it["url"], tmp_dir)
            entry: dict[str, Any] = {"domain": it["domain"], "probed_url": it["url"]}
            if data.get("failed"):
                entry.update({"mode": "fallback", "hook": it.get("fallback_hook", "")})
                proofs.append(entry)
                continue
            try:
                png = PROOF_DIR / f"{it['domain']}.png"
                _annotate(data["screenshot"], png, bottleneck_label(data))
                img_url = image_url(it["domain"])
            except Exception:
                entry.update({"mode": "fallback", "hook": it.get("fallback_hook", "")})
                proofs.append(entry)
                continue
            hook = build_hook(it["domain"], data, img_url)
            entry.update({"mode": "proof", "hook": hook, "image_url": img_url,
                          "metrics": {"dom_ms": data.get("dom_ms"), "slow_res": data.get("slow_res"),
                                      "bad_reqs": data.get("bad_reqs")}})
            proofs.append(entry)
    finally:
        for f in tmp_dir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        tmp_dir.rmdir()

    out_path = state_path(out_name)
    payload = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "proofs": proofs}
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"probed": len(items),
            "proofs": sum(1 for p in proofs if p.get("mode") == "proof"),
            "fallbacks": sum(1 for p in proofs if p.get("mode") == "fallback"),
            "out": str(out_path)}


def self_test(*, out_name: str = HOOKS_OUT) -> dict[str, Any]:
    """Validates the whole chain against a local fixture (no external network)."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="nirvana-proof-"))
    fixture = tmp_dir / "fixture.html"
    slow_ref = config.ROOT / "README.md"
    fixture.write_text(
        "<html><body><h1>Checkout</h1><img src='file://"
        + str(slow_ref).replace("\\", "/")
        + "'></body></html>", encoding="utf-8")
    try:
        result = run_proofs([{"domain": "fixture.local", "url": fixture.as_uri(), "fallback_hook": ""}],
                            out_name=out_name)
        return {"self_test": "proof_ok" if result["proofs"] else "fallback_ok", **result}
    finally:
        for f in tmp_dir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        tmp_dir.rmdir()


def run_batch(*, in_name: str = "verified_queue.json", out_name: str = HOOKS_OUT,
              limit: int = PER_RUN_LIMIT, run_self_test: bool = False) -> dict[str, Any]:
    if run_self_test:
        return self_test(out_name=out_name)
    items = load_targets(in_name, limit=limit)
    if not items:
        return {"probed": 0, "proofs": 0, "fallbacks": 0,
                "note": f"no https targets in {in_name}; try self-test",
                "out": str(state_path(out_name))}
    return run_proofs(items, out_name=out_name)