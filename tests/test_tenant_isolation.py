"""Multi-tenant ContextVar izolasyonu testleri — X/Y firması karışıklaşma yok."""

from __future__ import annotations

import asyncio
import threading

import pytest

from nirvana.tenant_isolation import (
    clear_tenant_id,
    get_tenant_id,
    require_tenant_id,
    set_tenant_id,
    tenant_schema,
    TenantContext,
)


def test_require_tenant_id_raises_without_context():
    """İzolasyon bağlamı yoksa RuntimeError fırlatılır (veritabanı erişimi kapatılır)."""
    clear_tenant_id()
    with pytest.raises(RuntimeError, match="tenant_id"):
        require_tenant_id()


def test_set_get_clear_tenant_id():
    """Kiracı kimliği bağlama / okuma / temizleme döngüsü."""
    clear_tenant_id()
    assert get_tenant_id() is None

    set_tenant_id("firma_x")
    assert get_tenant_id() == "firma_x"
    assert tenant_schema() == "tenant_firma_x"

    clear_tenant_id()
    assert get_tenant_id() is None


def test_tenant_schema_physical_separation():
    """X ve Y firmaları farklı şemalara mapped edilir (fiziksel ayrım)."""
    clear_tenant_id()
    set_tenant_id("firma_x")
    sx = tenant_schema()
    set_tenant_id("firma_y")
    sy = tenant_schema()
    clear_tenant_id()
    assert sx != sy
    assert sx == "tenant_firma_x"
    assert sy == "tenant_firma_y"


def test_tenant_context_manager_sets_and_clears():
    """TenantContext girdiğinde kiracı bağlanır, çıktığında temizlenir."""
    clear_tenant_id()
    with TenantContext("firma_x"):
        assert get_tenant_id() == "firma_x"
    assert get_tenant_id() is None


def test_tenant_isolation_is_thread_safe():
    """Farklı thread'lerdeki kiracı kimlikleri karışmaz (ContextVar garantisi)."""
    clear_tenant_id()
    results: dict[int, str] = {}

    def worker(tid: str):
        set_tenant_id(tid)
        results[threading.get_ident()] = get_tenant_id()
        clear_tenant_id()

    t1 = threading.Thread(target=worker, args=("firma_x",))
    t2 = threading.Thread(target=worker, args=("firma_y",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert set(results.values()) == {"firma_x", "firma_y"}
    assert get_tenant_id() is None


@pytest.mark.asyncio
async def test_tenant_isolation_is_async_safe():
    """Farklı async görevlerdeki kiracı kimlikleri karışmaz."""
    clear_tenant_id()

    async def worker(tid: str, results: dict[int, str]):
        set_tenant_id(tid)
        results[id(asyncio.current_task())] = get_tenant_id()
        clear_tenant_id()

    results: dict[int, str] = {}
    await asyncio.gather(worker("firma_x", results), worker("firma_y", results))
    assert set(results.values()) == {"firma_x", "firma_y"}
    assert get_tenant_id() is None
