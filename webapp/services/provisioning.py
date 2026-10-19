import json
import psycopg2.extras
from psycopg2 import sql

from utils import generate_unique_store_prefix, table_identifier


def create_client_tables(cur, store_prefix: str):
    products_table = table_identifier(store_prefix, "products")
    competitors_table = table_identifier(store_prefix, "competitors")
    matches_table = table_identifier(store_prefix, "matches")

    cur.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS {} (
            id SERIAL PRIMARY KEY,

            sku TEXT,
            name TEXT,
            size VARCHAR(50),
            color VARCHAR(50),
            manufacturer VARCHAR(50),
            category VARCHAR(100),

            price_normal FLOAT,
            price_special FLOAT,

            store VARCHAR(50),
            availability VARCHAR(50),
            date_of_download TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            url TEXT,
            image TEXT,
            description TEXT,

            raw_data JSONB DEFAULT '{{}}'::jsonb,

            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),

            UNIQUE (sku, store)
        )
    """).format(products_table))

    cur.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS {} (
            id SERIAL PRIMARY KEY,

            competitor_name TEXT,
            competitor_url TEXT,

            sku TEXT,
            name TEXT,
            size VARCHAR(50),
            color VARCHAR(50),
            manufacturer VARCHAR(50),
            category VARCHAR(100),

            price_normal FLOAT,
            price_special FLOAT,

            store VARCHAR(50),
            availability VARCHAR(50),
            date_of_download TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            url TEXT,
            image TEXT,
            description TEXT,

            raw_data JSONB DEFAULT '{{}}'::jsonb,

            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),

            UNIQUE (sku, store)
        )
    """).format(competitors_table))

    cur.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS {} (
            id SERIAL PRIMARY KEY,

            product_id INTEGER,
            competitor_product_id INTEGER,

            product_name TEXT,
            competitor_product_name TEXT,

            similarity_score NUMERIC,
            price_difference NUMERIC,

            match_status TEXT DEFAULT 'new',

            raw_data JSONB DEFAULT '{{}}'::jsonb,

            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """).format(matches_table))


def provision_client_from_request(
    conn,
    user_id: int,
    request_id: int,
    store_name: str,
    requested_store_slug=None,
    company_domain=None,
    website_url=None,
    source_type: str = "pending",
    source_path=None,
    source_url=None,
    file_format=None,
    field_mapping=None
):
    if field_mapping is None:
        field_mapping = {}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        store_prefix = generate_unique_store_prefix(conn, requested_store_slug or store_name)

        cur.execute("""
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
                %s,
                %s,
                %s,
                %s,
                FALSE,
                %s,
                %s,
                %s,
                %s,
                %s::jsonb,
                ARRAY[]::TEXT[]
            )
            RETURNING id, slug, store_prefix
        """, (
            store_name,
            store_prefix,
            store_prefix,
            website_url,
            source_type or "pending",
            source_path,
            source_url,
            file_format,
            json.dumps(field_mapping)
        ))

        client_row = cur.fetchone()

        if not client_row:
            raise Exception("Client insert failed. No row returned from INSERT INTO clients.")

        client_id = client_row["id"]
        slug = client_row["slug"]
        created_store_prefix = client_row["store_prefix"]

        create_client_tables(cur, created_store_prefix)

        cur.execute("""
            UPDATE users
            SET client_id = %s,
                company_domain = COALESCE(%s, company_domain),
                status = 'onboarding_required',
                updated_at = NOW()
            WHERE id = %s
        """, (
            client_id,
            company_domain,
            user_id
        ))

        cur.execute("""
            UPDATE onboarding_requests
            SET client_id = %s,
                requested_store_slug = %s,
                status = 'onboarding_required',
                approved_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
        """, (
            client_id,
            created_store_prefix,
            request_id
        ))

        return {
            "client_id": client_id,
            "slug": slug,
            "store_prefix": created_store_prefix
        }