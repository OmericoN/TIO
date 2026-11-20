"""Settings, read from the .env file at the repository root."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent  # data_parser/
REPO_ROOT = PROJECT_DIR.parent
DATA_DIR = PROJECT_DIR / "data_files"

load_dotenv(REPO_ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text-v2-moe")
EMBED_DIM = 768  # must match vector(768) in supabase/migrations
