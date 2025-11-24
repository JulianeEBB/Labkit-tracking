import psycopg2


def get_connection():
    """Return a new psycopg2 connection using fixed credentials."""
    return psycopg2.connect(
        host="192.168.99.41",
        dbname="labkit_db",
        user="labkit_app",
        password="labkitdb123",
    )
