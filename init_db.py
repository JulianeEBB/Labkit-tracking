"""Database initialization for the lab kit tracking system."""

from db_config import get_connection


def initialize_database() -> None:
    """Create all required tables if they do not already exist."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS site (
                id SERIAL PRIMARY KEY,
                site_code TEXT UNIQUE NOT NULL,
                site_name TEXT NOT NULL,
                address_line1 TEXT,
                address_line2 TEXT,
                city TEXT,
                state TEXT,
                postal_code TEXT,
                country TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """
        )
        # Ensure new address columns exist for older databases
        cur.execute(
            """
            ALTER TABLE site
            ADD COLUMN IF NOT EXISTS address_line1 TEXT,
            ADD COLUMN IF NOT EXISTS address_line2 TEXT,
            ADD COLUMN IF NOT EXISTS city TEXT,
            ADD COLUMN IF NOT EXISTS state TEXT,
            ADD COLUMN IF NOT EXISTS postal_code TEXT,
            ADD COLUMN IF NOT EXISTS country TEXT;
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS labkit_type (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                prefix TEXT,
                description TEXT,
                default_expiry_days INTEGER,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """
        )
        # Ensure new column exists for older databases
        cur.execute(
            """
            ALTER TABLE labkit_type
            ADD COLUMN IF NOT EXISTS prefix TEXT,
            ADD COLUMN IF NOT EXISTS default_expiry_days INTEGER;
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shipment (
                id SERIAL PRIMARY KEY,
                site_id INTEGER REFERENCES site(id),
                shipped_at TIMESTAMP,
                expected_arrival DATE,
                carrier TEXT,
                tracking_number TEXT,
                status TEXT
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS labkit (
                id SERIAL PRIMARY KEY,
                kit_barcode TEXT UNIQUE NOT NULL,
                labkit_type_id INTEGER REFERENCES labkit_type(id),
                site_id INTEGER REFERENCES site(id),
                shipment_id INTEGER REFERENCES shipment(id),
                lot_number TEXT,
                expiry_date DATE,
                status TEXT DEFAULT 'planned',
                created_at TIMESTAMP DEFAULT NOW(),
                barcode_value TEXT UNIQUE,
                updated_at TIMESTAMP DEFAULT NOW()
            );
            """
        )
        # Ensure new barcode column exists for older databases
        cur.execute(
            """
            ALTER TABLE labkit
            ADD COLUMN IF NOT EXISTS barcode_value TEXT;
            """
        )
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'labkit_barcode_value_key'
                ) THEN
                    ALTER TABLE labkit ADD CONSTRAINT labkit_barcode_value_key UNIQUE (barcode_value);
                END IF;
            END$$;
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS labkit_status_event (
                id SERIAL PRIMARY KEY,
                labkit_id INTEGER REFERENCES labkit(id),
                old_status TEXT,
                new_status TEXT,
                event_time TIMESTAMP DEFAULT NOW()
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS labkit_event (
                id SERIAL PRIMARY KEY,
                labkit_id INTEGER REFERENCES labkit(id),
                event_type TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                created_by TEXT
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shipment (
                id SERIAL PRIMARY KEY,
                site_id INTEGER REFERENCES site(id),
                shipped_at TIMESTAMP,
                expected_arrival DATE,
                carrier TEXT,
                tracking_number TEXT,
                status TEXT
            );
            """
        )

        # Ensure new columns for existing databases
        cur.execute(
            """
            ALTER TABLE labkit
            ADD COLUMN IF NOT EXISTS shipment_id INTEGER REFERENCES shipment(id);
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS site_contact (
                id SERIAL PRIMARY KEY,
                site_id INTEGER REFERENCES site(id),
                name TEXT NOT NULL,
                role TEXT,
                email TEXT,
                phone TEXT,
                is_primary BOOLEAN DEFAULT FALSE
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_user (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                "user" TEXT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                description TEXT
            );
            """
        )

        conn.commit()
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    initialize_database()
    print("Database initialized (tables created if they did not exist).")
