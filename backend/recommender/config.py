"""Settings for the research package, read from the .env file at the repository root."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Used for embeddings only (no chat/LLM calls): the listener, focus and exclusion vectors.
OPENAI_EMBEDDINGS_API_KEY = os.environ.get("OPENAI_EMBEDDINGS_API_KEY", "").strip()

# Must match data_parser/tio_data/config.py: articles, users and live queries share one embedding model.
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-large")
EMBED_DIM = 1536  # vector(1536) in supabase/migrations
