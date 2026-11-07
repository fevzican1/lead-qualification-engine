"""Multi-tenant veri izolasyonu — ContextVar ve şema bazlı kilitli erişim.

Rapor gereksinimi:
  * Eşzamanlı işlemlerde veri sızıntısı önlenir: thread-safe / async-safe
    Python ``ContextVar`` ile kiracı kimliği (tenant ID) çalışma bağlamına
    mühürlenir.
  * Veritabanı katmanında, aktif kiracı kimliği olmadan sorgu çalıştırılamaz;
    izolasyon bağlamı olmadan erişim ``RuntimeError`` ile reddedilir.
  * X ve Y firmalarının verileri fiziksel olarak ayrılır; kârışıklaşma
    sistemi seviyesinde engellenir.
"""

from __future__ import annotations

import contextvars
from typing import Optional

# Çalışma-anında kiracı kimliğini taşıyan ContextVar (async + thread güvenli).
_tenant_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "tenant_id", default=None
)


def set_tenant_id(tenant_id: str) -> None:
    """İstek / arka plan görevi başladığında kiracı kimliğini bağlamına mühürler."""
    _tenant_id_ctx.set(str(tenant_id).strip())


def get_tenant_id() -> Optional[str]:
    """Geçerli bağlamdaki kiracı kimliğini döner; yoksa None."""
    return _tenant_id_ctx.get()


def clear_tenant_id() -> None:
    """Kiracı bağlamını temizler (iş bitişinde çağrılır)."""
    _tenant_id_ctx.set(None)


def require_tenant_id() -> str:
    """Aktif kiracı kimliğini döner; yoksa çalışma zamanı hatası fırlatır.

    Veritabanı erişimi, mesaj kuyruğu publishing vb. bu fonksiyonla kezene
    alınır — izolasyon bağlamı yoksa işlem durdurulur.
    """
    tid = get_tenant_id()
    if not tid:
        raise RuntimeError(
            "Multi-tenant izolasyon bağlamı yok: aktif tenant_id tanımlı değil. "
            "Veritabanına / kuyruğa erişim izolasyon bağlamı olmadan yasaktır."
        )
    return tid


def tenant_schema(tenant_id: Optional[str] = None) -> str:
    """Kiracıya özel veritabanı şema adını döner (fiziksel ayrım).

    Şema ismi ``tenant_<tenant_id>`` biçiminde olup X ve Y firmaları için
    ayrı şemalar üretilir.
    """
    tid = tenant_id or require_tenant_id()
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in tid)
    return f"tenant_{safe.lower()}"


class TenantContext:
    """Kiracı bağlamını yöneten context manager.

    Kullanım:

        with TenantContext("firma_x"):
            # bu blokta tüm DB/kuyruk işlemleri firma_x şemasına ait
            ...
    """

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = str(tenant_id).strip()
        self._token: Optional[contextvars.Token[Optional[str]]] = None

    def __enter__(self) -> "TenantContext":
        self._token = _tenant_id_ctx.set(self.tenant_id)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _tenant_id_ctx.reset(self._token)
            self._token = None


__all__ = [
    "set_tenant_id",
    "get_tenant_id",
    "clear_tenant_id",
    "require_tenant_id",
    "tenant_schema",
    "TenantContext",
]
