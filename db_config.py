import os
import psycopg2

# Allow overriding DB connection details with environment variables so the app can
# run locally (e.g., against localhost) without editing code each time.
DB_HOST = os.getenv("DB_HOST", "192.168.99.41")
DB_NAME = os.getenv("DB_NAME", "labkit_db")
DB_USER = os.getenv("DB_USER", "labkit_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "labkitdb123")
DB_PORT = int(os.getenv("DB_PORT", "5432"))


def get_connection():
    """Return a new psycopg2 connection using configured credentials."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
