"""Supabase (Postgres) connection."""

import psycopg

from research.config import DATABASE_URL


def connect() -> psycopg.Connection:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Fill it in in the .env file at the repository root.")
    return psycopg.connect(DATABASE_URL)
