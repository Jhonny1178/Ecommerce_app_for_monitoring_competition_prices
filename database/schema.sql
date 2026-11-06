-- database/schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- 1. Klienci aktywni w systemie
--    Ten rekord powstaje dopiero po akceptacji przez admina.
-- ============================================================

CREATE TABLE IF NOT EXISTS clients (
    id                      SERIAL PRIMARY KEY,

    name                    TEXT NOT NULL,
    slug                    TEXT UNIQUE NOT NULL,
    store_prefix            TEXT UNIQUE NOT NULL,
    website_url             TEXT,

    is_active               BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMP DEFAULT NOW(),
    updated_at              TIMESTAMP DEFAULT NOW(),

    source_type             TEXT DEFAULT 'pending',
    source_path             TEXT,
    source_url              TEXT,
    file_format             TEXT,
    field_mapping           JSONB NOT NULL DEFAULT '{}'::jsonb,

    spiders_to_run          TEXT[] DEFAULT ARRAY[]::TEXT[],

    match_name_threshold    FLOAT DEFAULT 90.0,
    match_color_threshold   FLOAT DEFAULT 80.0,
    match_maker_threshold   FLOAT DEFAULT 80.0
);

-- ============================================================
-- 2. Użytkownicy systemu
--    users = konto i logowanie, nie pełne zgłoszenie biznesowe.
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id                  SERIAL PRIMARY KEY,

    username            TEXT UNIQUE NOT NULL,
    password_hash       TEXT NOT NULL,

    is_admin            BOOLEAN DEFAULT FALSE,
    client_id           INTEGER REFERENCES clients(id) ON DELETE SET NULL,

    status              TEXT DEFAULT 'pending_admin',
    subscription_plan   TEXT DEFAULT 'Podstawowy',

    first_name          TEXT,
    last_name           TEXT,
    email               TEXT,
    phone               TEXT,

    company_domain      TEXT,
    competitor_urls     JSONB DEFAULT '[]'::jsonb,

    rejection_reason    TEXT,

    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- 3. Zgłoszenia onboardingowe
--    To jest najważniejsza tabela przed utworzeniem klienta.
--    Admin powinien patrzeć głównie tutaj.
-- ============================================================

CREATE TABLE IF NOT EXISTS onboarding_requests (
    id                      SERIAL PRIMARY KEY,

    user_id                 INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id               INTEGER REFERENCES clients(id) ON DELETE SET NULL,

    requested_store_name    TEXT NOT NULL,
    requested_store_slug    TEXT,

    company_domain          TEXT,
    website_url             TEXT,
    competitor_urls         JSONB DEFAULT '[]'::jsonb,

    source_type             TEXT,
    source_path             TEXT,
    source_url              TEXT,
    file_format             TEXT,
    field_mapping           JSONB NOT NULL DEFAULT '{}'::jsonb,

    status                  TEXT NOT NULL DEFAULT 'pending_admin',

    approved_by             INTEGER REFERENCES users(id) ON DELETE SET NULL,
    approved_at             TIMESTAMP,
    rejection_reason        TEXT,

    notes                   TEXT,

    created_at              TIMESTAMP DEFAULT NOW(),
    updated_at              TIMESTAMP DEFAULT NOW(),

    UNIQUE(user_id, requested_store_name)
);

-- ============================================================
-- 4. Źródła danych klienta
--    Plik CSV/XLSX albo link/API podany w formularzu onboardingu.
-- ============================================================

CREATE TABLE IF NOT EXISTS onboarding_sources (
    id              SERIAL PRIMARY KEY,

    request_id      INTEGER NOT NULL REFERENCES onboarding_requests(id) ON DELETE CASCADE,

    source_kind     TEXT NOT NULL,
    source_path     TEXT,
    source_url      TEXT,
    file_format     TEXT,

    original_name    TEXT,
    mime_type        TEXT,

    uploaded_at      TIMESTAMP DEFAULT NOW(),

    CHECK (source_kind IN ('local', 'url'))
);

-- ============================================================
-- 5. Mapowanie pól klienta do Twojego modelu danych
--    Np. klient ma kolumnę "CENA", a u Ciebie to "price_normal".
-- ============================================================

CREATE TABLE IF NOT EXISTS onboarding_field_mappings (
    id                  SERIAL PRIMARY KEY,

    request_id          INTEGER NOT NULL REFERENCES onboarding_requests(id) ON DELETE CASCADE,

    external_field      TEXT NOT NULL,
    internal_field      TEXT NOT NULL,

    is_required         BOOLEAN DEFAULT FALSE,
    sample_value        TEXT,

    created_at          TIMESTAMP DEFAULT NOW(),

    UNIQUE(request_id, external_field)
);

-- ============================================================
-- 6. Dodatkowe pola, które klient chce scrapować
--    Np. "materiał", "czas dostawy", "liczba opinii".
-- ============================================================

CREATE TABLE IF NOT EXISTS onboarding_scrape_fields (
    id                  SERIAL PRIMARY KEY,

    request_id          INTEGER NOT NULL REFERENCES onboarding_requests(id) ON DELETE CASCADE,

    field_name          TEXT NOT NULL,
    field_description   TEXT,
    is_required         BOOLEAN DEFAULT FALSE,

    created_at          TIMESTAMP DEFAULT NOW(),

    UNIQUE(request_id, field_name)
);

-- ============================================================
-- 7. Logi uruchomień całego pipeline dla klienta
--    Jeden rekord = jedno uruchomienie pipeline klienta.
-- ============================================================

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              SERIAL PRIMARY KEY,

    client_id       INTEGER REFERENCES clients(id) ON DELETE CASCADE,

    started_at      TIMESTAMP DEFAULT NOW(),
    finished_at     TIMESTAMP,

    status          TEXT DEFAULT 'running',
    error_msg       TEXT
);

-- ============================================================
-- 8. Logi pojedynczych tasków Airflow
--    Np. ingest, scraper_x, matching, report.
-- ============================================================

CREATE TABLE IF NOT EXISTS pipeline_task_runs (
    id                  SERIAL PRIMARY KEY,

    pipeline_run_id     INTEGER REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    client_id           INTEGER REFERENCES clients(id) ON DELETE CASCADE,

    dag_id              TEXT,
    run_id              TEXT,
    task_id             TEXT NOT NULL,

    status              TEXT NOT NULL,

    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,

    log_excerpt         TEXT,
    error_msg           TEXT,

    created_at          TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- 9. Rejestr scraperów
--    To łączy autonomiczny generator z Airflow.
--    Airflow powinien odpalać tylko scrapery ze statusem approved.
-- ============================================================

CREATE TABLE IF NOT EXISTS scraper_registry (
    id              SERIAL PRIMARY KEY,

    client_id       INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    request_id      INTEGER REFERENCES onboarding_requests(id) ON DELETE SET NULL,

    store_slug      TEXT NOT NULL,

    competitor_url  TEXT,
    competitor_name TEXT,

    spider_name     TEXT NOT NULL,
    spider_module   TEXT,
    spider_path     TEXT,

    output_table    TEXT ,

    status          TEXT NOT NULL DEFAULT 'generated',

    last_error      TEXT,

    generated_at    TIMESTAMP DEFAULT NOW(),
    approved_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    approved_at     TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_scraper_registry_client_spider
ON scraper_registry(client_id, spider_name)
WHERE client_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_scraper_registry_request_spider
ON scraper_registry(request_id, spider_name)
WHERE client_id IS NULL;

-- ============================================================
-- 10. Logi procesu generowania scraperów przez AI generator
--     Tu zapisujesz kroki: sitemap, sample HTML, selector generation itd.
-- ============================================================

CREATE TABLE IF NOT EXISTS scraper_logs (
    id              SERIAL PRIMARY KEY,

    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    client_id       INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    request_id      INTEGER REFERENCES onboarding_requests(id) ON DELETE CASCADE,
    scraper_id      INTEGER REFERENCES scraper_registry(id) ON DELETE SET NULL,

    url             TEXT,
    step            TEXT NOT NULL,
    status          TEXT NOT NULL,
    message         TEXT,

    created_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- 11. Ogólne błędy aplikacji
--     Panel admina może pokazywać błędy aplikacji, scraperów i Airflow.
-- ============================================================

CREATE TABLE IF NOT EXISTS error_logs (
    id              SERIAL PRIMARY KEY,

    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    client_id       INTEGER REFERENCES clients(id) ON DELETE CASCADE,

    category        VARCHAR(50) NOT NULL,
    error_code      VARCHAR(100),
    message         TEXT,
    error_type      VARCHAR(50) NOT NULL,

    is_reviewed     BOOLEAN DEFAULT FALSE,

    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP
);

-- ============================================================
-- 12. Indeksy pomocnicze
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_users_client_id
ON users(client_id);

CREATE INDEX IF NOT EXISTS idx_users_status
ON users(status);

CREATE INDEX IF NOT EXISTS idx_onboarding_requests_user_id
ON onboarding_requests(user_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_requests_client_id
ON onboarding_requests(client_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_requests_status
ON onboarding_requests(status);

CREATE INDEX IF NOT EXISTS idx_onboarding_sources_request_id
ON onboarding_sources(request_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_field_mappings_request_id
ON onboarding_field_mappings(request_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_scrape_fields_request_id
ON onboarding_scrape_fields(request_id);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_client_id
ON pipeline_runs(client_id);

CREATE INDEX IF NOT EXISTS idx_pipeline_task_runs_client_id
ON pipeline_task_runs(client_id);

CREATE INDEX IF NOT EXISTS idx_pipeline_task_runs_pipeline_run_id
ON pipeline_task_runs(pipeline_run_id);

CREATE INDEX IF NOT EXISTS idx_scraper_registry_client_id
ON scraper_registry(client_id);

CREATE INDEX IF NOT EXISTS idx_scraper_registry_request_id
ON scraper_registry(request_id);

CREATE INDEX IF NOT EXISTS idx_scraper_registry_status
ON scraper_registry(status);

CREATE INDEX IF NOT EXISTS idx_scraper_logs_user_id
ON scraper_logs(user_id);

CREATE INDEX IF NOT EXISTS idx_scraper_logs_client_id
ON scraper_logs(client_id);

CREATE INDEX IF NOT EXISTS idx_scraper_logs_request_id
ON scraper_logs(request_id);

CREATE INDEX IF NOT EXISTS idx_error_logs_user_id
ON error_logs(user_id);

CREATE INDEX IF NOT EXISTS idx_error_logs_client_id
ON error_logs(client_id);

CREATE INDEX IF NOT EXISTS idx_error_logs_category
ON error_logs(category);

-- ============================================================
-- 13. Trigger aktualizujący updated_at
-- ============================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_clients_updated_at ON clients;
CREATE TRIGGER trg_clients_updated_at
BEFORE UPDATE ON clients
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_onboarding_requests_updated_at ON onboarding_requests;
CREATE TRIGGER trg_onboarding_requests_updated_at
BEFORE UPDATE ON onboarding_requests
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


-- ============================================================
-- 14. DEV SEED
--     Seed lokalny: tylko jeden aktywny klient bez tworzenia userów.
-- ============================================================
INSERT INTO users (
    username,
    password_hash,
    is_admin,
    client_id,
    status,
    first_name,
    last_name,
    email
)
VALUES (
    'admin',
    '!',
    TRUE,
    NULL,
    'active',
    'Admin',
    'System',
    'admin@example.com'
)
ON CONFLICT (username) DO UPDATE SET
    is_admin = EXCLUDED.is_admin,
    client_id = NULL,
    status = EXCLUDED.status,
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    email = EXCLUDED.email,
    updated_at = NOW();
INSERT INTO clients (
    name,
    slug,
    store_prefix,
    website_url,
    is_active,
    source_type,
    source_path,
    source_url,
    file_format,
    field_mapping,
    spiders_to_run
)
VALUES (
    'Nasz Klient',
    'nasz_klient',
    'nasz_klient',
    'https://lambfield.com',
    TRUE,
    'url',
    'https://lambfield.com/data/export/feed10000_10e8959b59a79f3367f2f493.xml',
    'https://lambfield.com/data/export/feed10000_10e8959b59a79f3367f2f493.xml',
    'xml',
    '{
        "title": "name",
        "price": "price_normal",
        "link": "url",
        "id": "sku",
        "description": "description",
        "color": "color",
        "brand": "manufacturer",
        "size": "size",
        "product_type": "category",
        "availability": "availability",
        "image_link": "image",
        "sale_price": "price_special"
    }'::jsonb,
    ARRAY[
        'Calavado',
        'jmbdesing',
        'pod_pierzyna'
    ]::TEXT[]
)
ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    store_prefix = EXCLUDED.store_prefix,
    website_url = EXCLUDED.website_url,
    is_active = EXCLUDED.is_active,
    source_type = EXCLUDED.source_type,
    source_path = EXCLUDED.source_path,
    source_url = EXCLUDED.source_url,
    file_format = EXCLUDED.file_format,
    field_mapping = EXCLUDED.field_mapping,
    spiders_to_run = EXCLUDED.spiders_to_run,
    updated_at = NOW();


INSERT INTO users (
    username,
    password_hash,
    is_admin,
    client_id,
    status,
    first_name,
    last_name
)
VALUES (
    'admin',
    '!',
    TRUE,
    NULL,
    'active',
    'Admin',
    'System'
)
ON CONFLICT (username) DO NOTHING;


INSERT INTO users (
    username,
    password_hash,
    is_admin,
    client_id,
    status,
    first_name,
    last_name,
    email,
    company_domain,
    competitor_urls
)
VALUES (
    'nasz_klient',
    '!',
    FALSE,
    (SELECT id FROM clients WHERE slug = 'nasz_klient'),
    'active',
    'Nasz',
    'Klient',
    'nasz_klient@example.com',
    'lambfield.com',
    '[
        "https://www.calvado.com/",
        "https://jmbdesign.pl/",
        "https://podpierzyna.com/"
    ]'::jsonb
)
ON CONFLICT (username) DO UPDATE SET
    client_id = EXCLUDED.client_id,
    status = EXCLUDED.status,
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    email = EXCLUDED.email,
    company_domain = EXCLUDED.company_domain,
    competitor_urls = EXCLUDED.competitor_urls,
    updated_at = NOW();

INSERT INTO scraper_registry (
    client_id,
    request_id,
    store_slug,
    competitor_url,
    competitor_name,
    spider_name,
    spider_module,
    spider_path,
    output_table,
    status,
    approved_by,
    approved_at
)
VALUES
(
    (SELECT id FROM clients WHERE slug = 'nasz_klient'),
    NULL,
    'nasz_klient',
    'https://lambfield.com/',
    'Nasz Klient',
    'nasz_klient',
    'spiders.nasz_klient',
    'spiders/nasz_klient.py',
    'nasz_klient_products',
    'approved',
    NULL,
    NOW()
),
(
    (SELECT id FROM clients WHERE slug = 'nasz_klient'),
    NULL,
    'nasz_klient',
    'https://www.calvado.com/',
    'Calavado',
    'Calavado',
    'spiders.Calavado',
    'spiders/Calavado.py',
    'nasz_klient_competitors',
    'approved',
    NULL,
    NOW()
),
(
    (SELECT id FROM clients WHERE slug = 'nasz_klient'),
    NULL,
    'nasz_klient',
    'https://jmbdesign.pl/',
    'JMB Design',
    'jmbdesing',
    'spiders.jmbdesing',
    'spiders/jmbdesing.py',
    'nasz_klient_competitors',
    'approved',
    NULL,
    NOW()
),
(
    (SELECT id FROM clients WHERE slug = 'nasz_klient'),
    NULL,
    'nasz_klient',
    'https://podpierzyna.com/',
    'Pod Pierzyną',
    'pod_pierzyna',
    'spiders.pod_pierzyna',
    'spiders/pod_pierzyna.py',
    'nasz_klient_competitors',
    'approved',
    NULL,
    NOW()
)
ON CONFLICT (client_id, spider_name)
WHERE client_id IS NOT NULL
DO UPDATE SET
    store_slug = EXCLUDED.store_slug,
    competitor_url = EXCLUDED.competitor_url,
    competitor_name = EXCLUDED.competitor_name,
    spider_module = EXCLUDED.spider_module,
    spider_path = EXCLUDED.spider_path,
    output_table = EXCLUDED.output_table,
    status = EXCLUDED.status,
    last_error = NULL,
    approved_at = NOW();

UPDATE clients c
SET spiders_to_run = sub.spiders,
    updated_at = NOW()
FROM (
    SELECT
        client_id,
        array_agg(spider_name ORDER BY spider_name) AS spiders
    FROM scraper_registry
    WHERE status = 'approved'
      AND client_id IS NOT NULL
    GROUP BY client_id
) sub
WHERE c.id = sub.client_id
  AND c.slug = 'nasz_klient';

-- ============================================================
-- 15. Resetowanie hasła
-- ============================================================

CREATE TABLE IF NOT EXISTS password_resets (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reset_token_hash TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_resets_user_id
ON password_resets(user_id);

-- ============================================================
-- 16. Zarządzanie kluczami API (GROQ)
-- ============================================================

CREATE TABLE IF NOT EXISTS api_keys_usage (
    id SERIAL PRIMARY KEY,
    key_name VARCHAR(50) UNIQUE NOT NULL,
    total_tokens INTEGER DEFAULT 0,
    is_exhausted BOOLEAN DEFAULT FALSE,
    last_used TIMESTAMP,
    exhausted_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO api_keys_usage (key_name)
VALUES
    ('GROQ_API_KEY_1'),
    ('GROQ_API_KEY_2'),
    ('GROQ_API_KEY_3')
ON CONFLICT (key_name) DO NOTHING;