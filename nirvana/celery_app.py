"""Celery uygulaması — tenant-schemas-celery benzeri kiracı bağlama ile.

Rapor gereksinimi:
  * Mesaj kuyruğuna (AMQP) atılan her iş paketinin üst bilgisinde (headers)
    kiracı şeması ve kimliği bulunur.
  * İşçi görevi devraldığında ilk iş olarak veritabanı bağlantısı o
    kiracının izolasyon şemasına bağlanır.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import before_task_publish, task_prerun

from nirvana.celery_tenant import (
    inject_tenant_headers,
    bind_tenant_from_headers,
    clear_tenant_id,
    get_tenant_id,
    set_tenant_id,
)

celery_app = Celery("nirvana")

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="nirvana",
    task_ignore_result=False,
)


@before_task_publish.connect
def _add_tenant_headers(sender=None, headers=None, **kwargs):
    """Çalışmadan önce iş paketine tenant bilgilerini enjekte eder."""
    headers = headers or {}
    tid = get_tenant_id()
    if tid:
        headers = inject_tenant_headers({"headers": headers}).get("headers", headers)
    return {"headers": headers}


@task_prerun.connect
def _bind_tenant_on_task_start(sender=None, task=None, headers=None, **kwargs):
    """İşçi görevi devraldığında tenant bağlamını kurar (ilk işlem)."""
    headers = headers or {}
    try:
        bind_tenant_from_headers(headers)
    except Exception:
        # İzolasyon yoksa işlem başlatılmaz — fail-closed.
        raise


@celery_app.task(bind=True, name="nirvana.tasks.delivery_task")
def delivery_task(self, payload: dict[str, Any]) -> dict[str, Any]:
    """Örnek teslimat görevi — tenant şemasıyla izole çalışır."""
    from nirvana.tenant_isolation import require_tenant_id, tenant_schema

    require_tenant_id()
    schema = tenant_schema()
    return {
        "ok": True,
        "tenant_schema": schema,
        "task": "delivery_task",
        "payload": payload,
    }


@celery_app.task(bind=True, name="nirvana.tasks.tenant_aware_task")
def tenant_aware_task(self, tenant_id: str, work: str) -> dict[str, Any]:
    """Belirli bir kiracı bağlamında çalışan görev."""
    set_tenant_id(tenant_id)
    try:
        from nirvana.tenant_isolation import require_tenant_id, tenant_schema

        require_tenant_id()
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "tenant_schema": tenant_schema(),
            "work": work,
        }
    finally:
        clear_tenant_id()


__all__ = ["celery_app", "delivery_task", "tenant_aware_task"]
