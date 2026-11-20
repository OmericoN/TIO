"""Shared Ollama chat call and article batch runner for the local-LLM steps (summarize, extract)."""

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from tio_data.db import connect

# Ollama reserves NUM_CTX tokens of KV cache for each of its OLLAMA_NUM_PARALLEL slots (12 on this machine).
# 4096 x 12 keeps qwen2.5:14b fully on a 24 GB GPU; 8192 x 12 pushed 60% of it onto the CPU (~4x slower).
# 10k chars is ~3k tokens even for German, leaving room for the prompt and the answer.
NUM_CTX = 4096
MAX_INPUT_CHARS = 10_000  # median article is ~4.5k chars; the facts are almost always near the top


def article_prompt(headline: str, text: str, language: str) -> str:
    body = (text or "").strip()
    if len(body) > MAX_INPUT_CHARS:
        body = body[:MAX_INPUT_CHARS].rsplit(" ", 1)[0] + " [...]"
    return f"Article language: {language}\nHeadline: {headline}\n\nArticle:\n{body}"


def ollama_chat(base_url: str, model: str, system: str, user: str, *, num_predict: int,
                format: dict | None = None, timeout: float = 600) -> str:
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"temperature": 0.2, "num_ctx": NUM_CTX, "num_predict": num_predict},
    }
    if format:
        payload["format"] = format  # JSON schema: Ollama constrains the output to it
    request = urllib.request.Request(
        f"{base_url}/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)["message"]["content"]
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        raise RuntimeError(f"Ollama returned {err.code}: {detail}. Is the model pulled? Run: ollama pull {model}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"Cannot reach Ollama at {base_url} ({err.reason}). Is `ollama serve` running?") from err


def run_over_articles(*, label: str, model: str, pending_sql: str, force: bool, limit: int | None,
                      language: str | None, workers: int,
                      process: Callable[[str, str, str], object | None],
                      save_sql: str, save_params: Callable[[object], tuple]) -> None:
    """Run `process(headline, text, language)` on every pending article, `workers` at a time, and write each
    result with `save_sql` % save_params(result) + (article_id,). Commits per article, so a stopped run resumes."""
    conditions = ["true" if force else pending_sql]
    params: list = []
    if language:
        conditions.append("language::text = %s")
        params.append(language)
    sql = (f"select id, headline, text_content, excerpt, language::text from public.articles "
           f"where {' and '.join(conditions)} order by published_at desc")
    if limit:
        sql += f" limit {int(limit)}"

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            print(f"Nothing to do: every article already has a {label}.")
            return
        print(f"{label.capitalize()}: {len(rows)} articles with {model}, {workers} at a time...")

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process, headline, text or excerpt, lang): (article_id, headline, lang)
                       for article_id, headline, text, excerpt, lang in rows}
            for done, future in enumerate(as_completed(futures), 1):
                article_id, headline, lang = futures[future]
                result = future.result()
                if not result:
                    print(f"  {done}/{len(rows)}  {article_id}: empty response, skipped")
                    continue
                conn.execute(save_sql, (*save_params(result), article_id))
                conn.commit()
                elapsed = time.monotonic() - started
                eta_min = (len(rows) - done) * elapsed / done / 60
                print(f"  {done}/{len(rows)}  {elapsed / done:.1f}s/article, ~{eta_min:.0f} min left  "
                      f"[{lang}] {headline[:60]}")
    print("Done.")
