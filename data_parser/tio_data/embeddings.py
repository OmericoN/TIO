"""OpenAI embedding client (text-embedding-3-large at 1536 dimensions), plus small vector helpers.

Articles, users and live queries all go through the same model, so every vector in the database is comparable.
OpenAI embeddings need no task prefix, so embed_documents() and embed_queries() send the text as is; both exist so
callers say what they embed.
"""

import json
import math
import time
import urllib.error
import urllib.request

from tio_data.config import EMBED_DIM, EMBED_MODEL, OPENAI_API_KEY

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
MAX_BATCH = 256          # inputs per request; OpenAI allows 2,048 but smaller batches retry cheaply
MAX_INPUT_CHARS = 24_000  # ~6k tokens, under the model's 8,191-token input limit
RETRIES = 5


class OpenAIEmbedder:
    def __init__(self, api_key: str = OPENAI_API_KEY, model: str = EMBED_MODEL, dim: int = EMBED_DIM,
                 timeout: float = 120):
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self.base_url = OPENAI_EMBEDDINGS_URL

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH):
            vectors.extend(self._request([t[:MAX_INPUT_CHARS] or " " for t in texts[start:start + MAX_BATCH]]))
        return vectors

    def _request(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key.startswith("sk-"):
            raise RuntimeError("OPENAI_API_KEY in .env is not an OpenAI API key (it should start with 'sk-'). "
                               "Create one at https://platform.openai.com/api-keys.")
        body = json.dumps({"model": self.model, "input": texts, "dimensions": self.dim}).encode("utf-8")
        for attempt in range(RETRIES):
            request = urllib.request.Request(self.base_url, data=body, headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = sorted(json.load(response)["data"], key=lambda d: d["index"])
                break
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", "replace")[:300]
                if err.code in (429, 500, 502, 503, 504) and attempt < RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"OpenAI returned {err.code}: {detail}") from err
            except urllib.error.URLError as err:
                if attempt < RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Cannot reach OpenAI ({err.reason}).") from err

        vectors = [d["embedding"] for d in data]
        if len(vectors) != len(texts):
            raise RuntimeError(f"OpenAI returned {len(vectors)} embeddings for {len(texts)} texts")
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
