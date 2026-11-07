"""Celery işçilerinde kiracı bağlama (tenant-schemas-celery benzeri).

Rapor gereksinimi:
  * Mesaj kuyruğuna (AMQP) atılan her iş paketinin üst verisine (headers)
    ilgili kiracının şema ve kimlik bilgisi eklenir.
  * İşçi görevi devraldığında ilk iş olarak veritabanı bağlantısını o
    kiracının izolasyon şemasına bağlar.
  * Böylece X firması için yürütülen bir zarar tespiti veya teslimat paketinin
    Y firmasına iletilme ihtimali sistemsel olarak engellenir.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Optional

from nirvana.tenant_isolation import (
    clear_tenant_id,
    get_tenant_id,
    require_tenant_id,
    set_tenant_id,
    tenant_schema,
)


def inject_tenant_headers(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Gönderilecek iş paketine tenant şema/kimlik bilgilerini ekler.

    Celery `apply_async` veya `send_task` çağrılarından önce kullanılmalıdır.
    """
    tid = get_tenant_id()
    if tid:
        headers = dict(kwargs.get("headers") or {})
        headers["tenant_id"] = tid
        headers["tenant_schema"] = tenant_schema(tid)
        kwargs["headers"] = headers
    return kwargs


def bind_tenant_from_headers(headers: Optional[dict[str, Any]] = None) -> str:
    """İş paketinden tenant kimliğini çıkar ve bağlamı ayarlar.

    İşçi görevi devraldığında ilk iş olarak çağrılmalıdır. Döndürülen
    şema adı, işçinin DB bağlantısını o kiracının izolasyon şemasına
    bağlamak için kullanılabilir.
    """
    headers = headers or {}
    tid = headers.get("tenant_id") or get_tenant_id()
    if not tid:
        raise RuntimeError(
            "Celery iş paketinde tenant_id yok — izolasyon bağlamı kurulmadı."
        )
    set_tenant_id(str(tid))
    return tenant_schema(tid)


def tenant_task(func: Callable[..., Any]) -> Callable[..., Any]:
    """Celery görev dekoratörü: çalışmadan önce tenant bağlamını kurar.

    İş bittiğinde bağlamı temizler (sızdırmaz).
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        headers = kwargs.pop("_tenant_headers", None) or {}
        schema = bind_tenant_from_headers(headers)
        try:
            # Çağrılana şema bilgisini de aktar (işçi DB bağlantısı için).
            kwargs["_tenant_schema"] = schema
            return func(*args, **kwargs)
        finally:
            clear_tenant_id()

    return wrapper


def tenant_apply_async(
    task: Any,
    args: Optional[list[Any]] = None,
    kwargs: Optional[dict[str, Any]] = None,
    *,
    tenant_id: Optional[str] = None,
    **options: Any,
) -> Any:
    """Celery apply_async'ın tenant-biliary sarmalayıcısı.

    `tenant_id` verilirse o kiracı bağlamında iş paketi oluşturulur.
    """
    args = args or []
    kwargs = kwargs or {}
    tid = tenant_id or get_tenant_id()
    if tid:
        kwargs = inject_tenant_headers(kwargs)
    return task.apply_async(args=args, kwargs=kwargs, **options)


__all__ = [
    "inject_tenant_headers",
    "bind_tenant_from_headers",
    "tenant_task",
    "tenant_apply_async",
]
