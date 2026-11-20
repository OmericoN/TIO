"""Article summaries with a local Ollama chat model (default qwen2.5:14b).

Writes public.articles.summary and summary_model. Every summary is in English, whatever the article's
language, because the briefings are English. Run `embed articles` afterwards: it re-embeds every article
that gained a summary, from "headline + summary".
"""

import re

from tio_data.config import OLLAMA_URL, SUMMARY_MODEL
from tio_data.llm import article_prompt, ollama_chat, run_over_articles

SYSTEM_PROMPT = """You summarise financial and business news articles for an English audio news briefing.

Rules:
- Write in English, even when the article is in German, Dutch or another language.
- 3 to 4 sentences, at most 90 words, as one plain paragraph.
- Lead with the main news: who did what, and why it matters.
- Keep every key number exactly as the article states it (prices, percentages, dates, amounts, ratings) and keep
  company names with their tickers.
- Use only facts stated in the article. No opinions, predictions or advice of your own.
- No preamble such as "This article" or "Summary:", no bullet points, no markdown."""

_PREAMBLE = re.compile(r"^\s*(\*\*)?\s*(summary|samenvatting|zusammenfassung)\s*:?\s*(\*\*)?\s*", re.IGNORECASE)


class OllamaSummarizer:
    def __init__(self, base_url: str = OLLAMA_URL, model: str = SUMMARY_MODEL):
        self.base_url = base_url
        self.model = model

    def summarize(self, headline: str, text: str, language: str) -> str:
        content = ollama_chat(self.base_url, self.model, SYSTEM_PROMPT, article_prompt(headline, text, language),
                              num_predict=300)
        return " ".join(_PREAMBLE.sub("", content).split())


def check(summarizer: OllamaSummarizer) -> None:
    summary = summarizer.summarize(
        "ECB holds rates at 2%", "The European Central Bank kept its deposit rate at 2% on Thursday, "
        "citing inflation close to its 2% target, and said future moves depend on incoming data.", "en",
    )
    print(f"Ollama OK at {summarizer.base_url}: {summarizer.model}\n  {summary}")


def summarize_articles(summarizer: OllamaSummarizer, force: bool = False, limit: int | None = None,
                       language: str | None = None, workers: int = 4) -> None:
    run_over_articles(
        label="summary", model=summarizer.model, pending_sql="nullif(trim(summary), '') is null",
        force=force, limit=limit, language=language, workers=workers, process=summarizer.summarize,
        save_sql="update public.articles set summary = %s, summary_model = %s where id = %s",
        save_params=lambda summary: (summary, summarizer.model),
    )
    print("Now run `embed articles` to re-embed the summarised articles.")
