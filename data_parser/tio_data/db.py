"""Supabase (Postgres) connection."""

import psycopg

from tio_data.config import DATABASE_URL


def connect() -> psycopg.Connection:
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set. Fill it in in the .env file at the repository root.")
    return psycopg.connect(DATABASE_URL)
