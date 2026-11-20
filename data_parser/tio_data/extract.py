"""What each article is about, extracted by a local Ollama model (default qwen2.5:14b).

Fills these public.articles columns (migration 20261120170000_article_extraction.sql):
  main_subject, event, event_type, topics[], people[], entities[], extraction_model, extracted_at
Everything is in English so DE/NL articles match English searches. A database trigger folds the new fields into
search_vector, which public.search_articles(query) uses for keyword search.
"""

import json

from tio_data.config import OLLAMA_URL, SUMMARY_MODEL
from tio_data.llm import article_prompt, ollama_chat, run_over_articles

EVENT_TYPES = [
    "earnings", "guidance", "merger_acquisition", "ipo", "analyst_rating", "stock_move", "market_update",
    "central_bank", "economic_data", "policy_regulation", "legal", "product_launch", "management_change",
    "commodity", "currency", "crypto", "fund_etf", "other",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "main_subject": {"type": "string"},
        "event": {"type": "string"},
        "event_type": {"type": "string", "enum": EVENT_TYPES},
        "topics": {"type": "array", "items": {"type": "string"}},
        "people": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["main_subject", "event", "event_type", "topics", "people", "entities"],
}

SYSTEM_PROMPT = f"""You index financial and business news articles for search. Read the article and return JSON.

Write every field in English, even when the article is in German, Dutch or another language.

- main_subject: what the article is mainly about, 2 to 6 words. Example: "Anthropic IPO pricing".
- event: one sentence saying what happened, with the key number if there is one. Use only facts in the article.
- event_type: the single best fit from: {", ".join(EVENT_TYPES)}.
- topics: 2 to 5 broad themes, lowercase. Examples: "artificial intelligence", "interest rates", "oil prices".
- people: full names of real people the article is about or quotes, as written in the article. Not the author.
  Empty list if none.
- entities: the main companies, institutions, countries, indices, currencies and products, at most 8, using
  their usual English name. Add the ticker in brackets for listed companies when the article gives it,
  for example "Meta Platforms (META)". Leave out news outlets and data providers that are only cited as a
  source (Reuters, Bloomberg, Yahoo Finance). Empty list if none."""

_LIMITS = {"topics": 5, "people": 10, "entities": 8}


def _clean_list(values, limit: int, lower: bool = False) -> list[str]:
    seen, out = set(), []
    for value in values or []:
        value = " ".join(str(value).split()).strip(" .,;")
        if lower:
            value = value.lower()
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    return out[:limit]


class OllamaExtractor:
    def __init__(self, base_url: str = OLLAMA_URL, model: str = SUMMARY_MODEL):
        self.base_url = base_url
        self.model = model

    def extract(self, headline: str, text: str, language: str) -> dict | None:
        content = ollama_chat(self.base_url, self.model, SYSTEM_PROMPT, article_prompt(headline, text, language),
                              num_predict=500, format=SCHEMA)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return None
        event_type = data.get("event_type")
        return {
            "main_subject": " ".join(str(data.get("main_subject") or "").split()) or None,
            "event": " ".join(str(data.get("event") or "").split()) or None,
            "event_type": event_type if event_type in EVENT_TYPES else "other",
            "topics": _clean_list(data.get("topics"), _LIMITS["topics"], lower=True),
            "people": _clean_list(data.get("people"), _LIMITS["people"]),
            "entities": _clean_list(data.get("entities"), _LIMITS["entities"]),
        }


def check(extractor: OllamaExtractor) -> None:
    result = extractor.extract(
        "EZB belässt Leitzins bei 2 Prozent",
        "Die Europäische Zentralbank hat am Donnerstag ihren Einlagensatz bei 2 Prozent belassen. "
        "EZB-Präsidentin Christine Lagarde sagte, die Inflation liege nahe am Ziel von 2 Prozent.", "de",
    )
    print(f"Ollama OK at {extractor.base_url}: {extractor.model}\n{json.dumps(result, indent=2, ensure_ascii=False)}")


def extract_articles(extractor: OllamaExtractor, force: bool = False, limit: int | None = None,
                     language: str | None = None, workers: int = 4) -> None:
    run_over_articles(
        label="extraction", model=extractor.model, pending_sql="extracted_at is null",
        force=force, limit=limit, language=language, workers=workers, process=extractor.extract,
        save_sql="update public.articles set main_subject = %s, event = %s, event_type = %s, topics = %s, "
                 "people = %s, entities = %s, extraction_model = %s, extracted_at = now() where id = %s",
        save_params=lambda r: (r["main_subject"], r["event"], r["event_type"], r["topics"], r["people"],
                               r["entities"], extractor.model),
    )
