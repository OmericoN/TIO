"""OpenAI embeddings for listener profiles and live queries, using the same model as the article embeddings."""

import json
import math
from urllib.request import Request, urlopen

from research.config import EMBED_DIM, EMBED_MODEL, OPENAI_API_KEY

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


class EmbeddingError(RuntimeError):
    pass


def embed_query(text: str) -> list[float]:
    if not OPENAI_API_KEY.startswith("sk-"):
        raise EmbeddingError("OPENAI_API_KEY is missing or is not an OpenAI API key.")
    body = json.dumps({"model": EMBED_MODEL, "input": [text[:24_000]], "dimensions": EMBED_DIM}).encode("utf-8")
    request = Request(OPENAI_EMBEDDINGS_URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"})
    with urlopen(request, timeout=30) as response:
        vector = json.load(response)["data"][0]["embedding"]
    if len(vector) != EMBED_DIM:
        raise EmbeddingError(f"{EMBED_MODEL} returned {len(vector)} dimensions, expected {EMBED_DIM}.")
    return vector


def to_pgvector(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def from_pgvector(text: str) -> list[float]:
    return json.loads(text)


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
