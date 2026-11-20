"""Ollama client for nomic-embed-text-v2-moe (768 dimensions), plus small vector helpers.

nomic models expect a task prefix, so always use the matching method:
- embed_documents(): articles   -> "search_document: <text>"
- embed_queries():   user interests and searches -> "search_query: <text>"
"""

import json
import math
import urllib.error
import urllib.request

from tio_data.config import EMBED_DIM, EMBED_MODEL, OLLAMA_URL

DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class OllamaEmbedder:
    def __init__(self, base_url: str = OLLAMA_URL, model: str = EMBED_MODEL, dim: int = EMBED_DIM,
                 timeout: float = 300):
        self.base_url = base_url
        self.model = model
        self.dim = dim
        self.timeout = timeout

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([DOC_PREFIX + t for t in texts])

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed([QUERY_PREFIX + t for t in texts])

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        body = json.dumps({"model": self.model, "input": texts, "truncate": True}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/embed", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                vectors = json.load(response).get("embeddings") or []
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"Ollama returned {err.code}: {detail}. Is the model pulled? Run: ollama pull {self.model}"
            ) from err
        except urllib.error.URLError as err:
            raise RuntimeError(
                f"Cannot reach Ollama at {self.base_url} ({err.reason}). Is `ollama serve` running?"
            ) from err

        if len(vectors) != len(texts):
            raise RuntimeError(f"Ollama returned {len(vectors)} embeddings for {len(texts)} texts")
        if any(len(v) != self.dim for v in vectors):
            raise RuntimeError(f"{self.model} did not return {self.dim} dimensions, which the database expects")
        return vectors


def to_pgvector(vector: list[float]) -> str:
    """pgvector text format: [0.1,0.2,...]"""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def from_pgvector(text: str) -> list[float]:
    return json.loads(text)


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


def weighted_average(vectors: list[list[float]], weights: list[float]) -> list[float] | None:
    """Normalized weighted sum. Negative weights push away. None if there is nothing to average."""
    if not vectors:
        return None
    total = [0.0] * len(vectors[0])
    for vector, weight in zip(vectors, weights):
        for i, x in enumerate(vector):
            total[i] += weight * x
    return normalize(total) if any(total) else None
