"""Settings for the research package, read from the .env file at the repository root."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or os.environ.get("MARCOS_API_OPENAI") or "").strip()

# Must match data_parser/tio_data/config.py: articles, users and live queries share one embedding model.
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-large")
EMBED_DIM = 1536  # vector(1536) in supabase/migrations
