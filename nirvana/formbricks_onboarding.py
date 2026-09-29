"""Rapor 4.3 adim 4 — Formbricks onboarding anketi koprusu.

Sozlesme imzalandiktan sonra musteriye kisisellestirilmis karşılama anketi
gonderilir (logo, marka rehberi, reklam hesabi yetkileri, oncelikler).
Formbricks yapilandirilmamisken (FORMBRICKS_URL veya anket ID'si bos) tum
fonksiyonlar sessizce no-op olur — satis hatti durmaz (fail-open).
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

import config

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(
        str(getattr(config, "FORMBRICKS_URL", "") or "").strip()
        and str(getattr(config, "FORMBRICKS_SURVEY_ID", "") or "").strip()
    )


def survey_link(*, email: str = "", name: str = "", company: str = "") -> str:
    """Anket linki; on-doldurma parametreleriyle (saglayici destekliyorsa)."""
    if not configured():
        return ""
    base = str(getattr(config, "FORMBRICKS_URL", "") or "").rstrip("/")
    survey = str(getattr(config, "FORMBRICKS_SURVEY_ID", "") or "").strip()
    link = f"{base}/s/{survey}"
    params = {}
    if email:
        params["email"] = email
    if name:
        params["name"] = name
    if company:
        params["company"] = company
    return f"{link}?{urlencode(params)}" if params else link


def welcome_block(*, email: str = "", name: str = "", company: str = "", turkish: bool = True) -> str:
    """Karşılama metnine eklenecek blok; yapilandirilmamissa '' doner."""
    link = survey_link(email=email, name=name, company=company)
    if not link:
        return ""
    if turkish:
        return (
            "\n\nOnboarding anketi (2 dakika): marka materyalleri, erisim yetkileri ve "
            f"onceliklerinizi topluyor: {link}"
        )
    return f"\n\nOnboarding survey (2 minutes): branding assets, access and priorities: {link}"


def run_batch(*, email: str = "", name: str = "", company: str = "") -> dict[str, object]:
    return {"configured": configured(), "survey_url": survey_link(email=email, name=name, company=company)}
