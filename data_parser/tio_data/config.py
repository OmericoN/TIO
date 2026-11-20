"""Settings, read from the .env file at the repository root."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent  # data_parser/
REPO_ROOT = PROJECT_DIR.parent
DATA_DIR = PROJECT_DIR / "data_files"

load_dotenv(REPO_ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")  # local LLM: extract, summarize

# Embeddings: OpenAI for articles, users and live queries alike, so every vector is comparable.
# Used for embeddings only (no chat/LLM calls); the LLM steps (extract, summarize) run locally in Ollama.
OPENAI_EMBEDDINGS_API_KEY = os.environ.get("OPENAI_EMBEDDINGS_API_KEY", "").strip()
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-large")
EMBED_DIM = 1536  # must match vector(1536) in supabase/migrations; 3-large shortened via `dimensions`
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "qwen2.5:14b")
