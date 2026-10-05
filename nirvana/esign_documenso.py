"""Rapor 2.2 / 4.3 — Documenso v2 (envelope API) ile otomatik e-imza.

Yasam dongusu (uclar resmi surumleme/migration rehberinden dogrulandi):
    POST {DOCUMENSO_URL}/api/v2/envelope/create     type=DOCUMENT|TEMPLATE
    POST {DOCUMENSO_URL}/api/v2/envelope/distribute DRAFT -> PENDING
    Authorization: api_xxxxxxxx   (v1'deki "Bearer <key>" DEGIL)

Saglayici yapilandirilmamisken (DOCUMENSO_API_KEY bos) modul sessizce no-op
olur; satis hatti bu yuzden ASLA durmaz (fail-open, mevcut kod felsefesi).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(getattr(config, "DOCUMENSO_URL", "") and getattr(config, "DOCUMENSO_API_KEY", ""))


def _endpoint(path_key: str, default: str) -> str:
    base = str(getattr(config, "DOCUMENSO_URL", "") or "").rstrip("/")
    path = str(getattr(config, path_key, "") or default)
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}/api/v2{path}"


def _headers() -> dict[str, str]:
    key = str(getattr(config, "DOCUMENSO_API_KEY", "") or "").strip()
    # v2: ham anahtar (api_...) dogrudan Authorization basligina yazilir.
    return {"Authorization": key, "Content-Type": "application/json"}


def create_envelope(
    *,
    title: str,
    recipients: list[dict[str, Any]],
    template_id: str = "",
    external_id: str = "",
    form_values: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Envelope olustur (sablon varsa ondan). Hata halinde None doner."""
    if not configured():
        return None
    payload: dict[str, Any] = {
        "type": "DOCUMENT",
        "title": title[:180],
        "recipients": recipients,
    }
    if template_id:
        payload["templateId"] = template_id
    if external_id:
        payload["externalId"] = external_id
    if form_values:
        payload["formValues"] = form_values
    try:
        response = httpx.post(
            _endpoint("DOCUMENSO_CREATE_PATH", "/envelope/create"),
            headers=_headers(),
            json=payload,
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.info("Documenso envelope olusturulamadi: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def distribute_envelope(data: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any] | None:
    """DRAFT -> PENDING: imza davetleri gonderilir."""
    envelope = data.get("envelope") if isinstance(data.get("envelope"), dict) else data
    envelope_id = str(envelope.get("id") or envelope.get("envelopeId") or "")
    if not envelope_id:
        return None
    try:
        response = httpx.post(
            _endpoint("DOCUMENSO_DISTRIBUTE_PATH", "/envelope/distribute"),
            headers=_headers(),
            json={"envelopeId": envelope_id},
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.info("Documenso distribute atlandi: %s", exc)
        return None


def extract_signing_url(*payloads: dict[str, Any] | None) -> str:
    """Imza linkini olasi tum alan adlarindan topla (surum bagimsiz)."""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("signingUrl", "signUrl", "url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        envelope = payload.get("envelope") if isinstance(payload.get("envelope"), dict) else payload
        recipients = envelope.get("recipients") or payload.get("recipients") or []
        if isinstance(recipients, list):
            for row in recipients:
                if not isinstance(row, dict):
                    continue
                for key in ("signingUrl", "signUrl", "url"):
                    value = row.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value
                token = row.get("token") or row.get("signingToken")
                if isinstance(token, str) and token:
                    base = str(getattr(config, "DOCUMENSO_URL", "") or "").rstrip("/")
                    return f"{base}/sign/{token}"
    return ""


def signing_link(
    *,
    company: str,
    email: str,
    name: str = "",
    external_id: str = "",
) -> str:
    """Tek cagri: envelope olustur + distribute et + imza linkini dondur.

    Link yoksa "" doner; cagiran taraf (contract_pack / satis botu) linki
    yalnizca doluysa mesaja ekler.
    """
    if not configured() or not email:
        return ""
    data = create_envelope(
        title=f"B2B Retainer Sozlesmesi - {company or email}",
        recipients=[{"name": name or company or "Yetkili", "email": email, "role": "SIGNER", "order": 1}],
        template_id=str(getattr(config, "DOCUMENSO_TEMPLATE_ID", "") or ""),
        external_id=external_id or f"retainer-{email}",
    )
    if not data:
        return ""
    distributed = distribute_envelope(data)
    return extract_signing_url(distributed, data)


def run_batch(*, company: str = "", email: str = "", name: str = "") -> dict[str, Any]:
    """CLI/dry-run yuzeyi (nirvana.runner ile de cagrilabilir)."""
    link = signing_link(company=company, email=email, name=name)
    return {"configured": configured(), "signing_url": link}
