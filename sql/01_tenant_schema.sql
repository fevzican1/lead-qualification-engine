-- PostgreSQL Multi-Tenant Şema İzolasyonu
-- Rapor gereksinimi: X ve Y firmalarının veri tablolarının fiziksel olarak ayrılması.
-- Aktif kiracı kimliği olmadan sorgu çalıştırılamaz (kilitli ORM yöneticisi).

-- Kiracı şeması şablonu: tenant_<tenant_id>
-- Her müşteri için ayrı şema oluşturulur: CALL create_tenant_schema('firma_x');

CREATE OR REPLACE FUNCTION create_tenant_schema(tenant_name TEXT)
RETURNS VOID AS $$
BEGIN
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', tenant_name);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO nirvana_app', tenant_name);
    EXECUTE format('GRANT CREATE ON SCHEMA %I TO nirvana_app', tenant_name);
END;
$$ LANGUAGE plpgsql;

-- Örnek tenant şemaları (canlıda create_tenant_schema ile dinamik oluşturulur)
CREATE SCHEMA IF NOT EXISTS tenant_firma_x;
CREATE SCHEMA IF NOT EXISTS tenant_firma_y;

GRANT USAGE ON SCHEMA tenant_firma_x TO nirvana_app;
GRANT USAGE ON SCHEMA tenant_firma_y TO nirvana_app;

-- Kiracıya özel tablo örneği (her şemada aynı tablo yapısı, fiziksel ayrım)
CREATE TABLE IF NOT EXISTS tenant_firma_x.leads (
    id SERIAL PRIMARY KEY,
    company_name TEXT NOT NULL,
    form_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_firma_y.leads (
    id SERIAL PRIMARY KEY,
    company_name TEXT NOT NULL,
    form_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

GRANT ALL ON ALL TABLES IN SCHEMA tenant_firma_x TO nirvana_app;
GRANT ALL ON ALL TABLES IN SCHEMA tenant_firma_y TO nirvana_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA tenant_firma_x TO nirvana_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA tenant_firma_y TO nirvana_app;

-- İzolasyon kuralı: nirvana_app rolü aktif tenant şeması dışında erişemez.
-- Bu kural uygulama katmanında (tenant_db.py SET search_path) zorunlu kılınır.
