"""PostgreSQL multi-tenant şema izolasyonu ve kilitli ORM yöneticisi.

Rapor gereksinimi:
  * Veritabanı katmanında, varsayılan veri yöneticileri (ORM Managers) aktif
    kiracı kimliği olmadan sorgu çalıştırılmasına izin vermemektedir.
  * İzolasyon bağlamı olmadan veri tabanına erişilmeye çalışıldığında sistem
    otomatik olarak bir çalışma zamanı hatası (RuntimeError) fırlatır.
  * X ve Y firmalarının veri tablolarının fiziksel olarak ayrılması
    (schema-per-tenant).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from nirvana.tenant_isolation import require_tenant_id, tenant_schema


class TenantIsolationError(RuntimeError):
    """Aktif tenant izolasyon bağlamı yoksa veritabanı erişimini engeller."""


class TenantBoundQueryManager:
    """Kilitli ORM yöneticisi: aktif tenant şeması olmadan sorgu çalıştırmaz.

    Kullanım:

        mgr = TenantBoundQueryManager(session)
        rows = mgr.query("SELECT * FROM leads")  # tenant şemasına yönlendirilir

    İzolasyon bağlamı yoksa ``TenantIsolationError`` fırlatır.
    """

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory
        self._local = threading.local()

    def _require_schema(self) -> str:
        """Aktif tenant şemasını döner; yoksa hata fırlatır."""
        return tenant_schema(require_tenant_id())

    def session(self) -> Any:
        """Aktif tenant şemasıyla yapılandırılmış bir DB oturumu döner."""
        schema = self._require_schema()
        sess = self._session_factory()
        # Şemayı OTURUM BAŞINDA kilitle: bu oturumdaki tüm sorgular
        # yalnızca bu tenant şemasını görür → X/Y karışıklaşma yok.
        try:
            sess.execute(f'SET search_path TO "{schema}"')
        except Exception:
            # Şema yoksa oluştur (idempotent)
            try:
                sess.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                sess.execute(f'SET search_path TO "{schema}"')
            except Exception as exc:
                sess.close()
                raise TenantIsolationError(
                    f"Tenant şeması '{schema}' ayarlanamadı: {exc}"
                ) from exc
        return sess

    def query(self, sql: str, params: Optional[dict[str, Any]] = None) -> Any:
        """Tek bir sorgu çalıştırır ve sonucu döner."""
        sess = self.session()
        try:
            result = sess.execute(sql, params or {})
            sess.commit()
            return result
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    def execute(self, sql: str, params: Optional[dict[str, Any]] = None) -> Any:
        """Yazma/ddl işlemi çalıştırır (aşırı kayıt korumalı)."""
        return self.query(sql, params)


# Global instance: uygulama başlatıldığında set_query_manager ile bağlanır.
_QUERY_MANAGER: Optional[TenantBoundQueryManager] = None
_lock = threading.Lock()


def set_query_manager(manager: TenantBoundQueryManager) -> None:
    """Uygulama genelinde kilitli ORM yöneticisini kaydeder."""
    global _QUERY_MANAGER
    with _lock:
        _QUERY_MANAGER = manager


def query_manager() -> TenantBoundQueryManager:
    """Kayıtlı ORM yöneticisini döner; yoksa hata fırlatır."""
    if _QUERY_MANAGER is None:
        raise TenantIsolationError(
            "Veritabanı izolasyon yöneticisi kaydedilmemiş. "
            "Uygulama başlatıldığında set_query_manager() çağrılmalı."
        )
    return _QUERY_MANAGER


def require_tenant_for_db() -> str:
    """DB erişimi öncesi tenant izolasyonunu zorunlu kılar ve şemayı döner."""
    return tenant_schema(require_tenant_id())


__all__ = [
    "TenantIsolationError",
    "TenantBoundQueryManager",
    "set_query_manager",
    "query_manager",
    "require_tenant_for_db",
]
