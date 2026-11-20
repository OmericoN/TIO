# Match hosts to articles

Set `DATABASE_URL` and `OPENAI_API_KEY` in the root `.env` or the backend environment. The root [`.env.example`](../.env.example) has blank placeholders. The Postgres driver uses the system PostgreSQL client library (`libpq`).

Call the matcher from the Python pipeline after the recommender has produced a shortlist. Create it once per worker to reuse article classifications across listeners.

```python
from research.matcher import create_matcher

matcher = create_matcher()
result = matcher.match_personas(article_ids, candidate_agent_ids, n=2)
```

`result["articles"]` preserves the input sequence and pairs each `article_id` with its `desired_tone` for scripting. `result["agent_ids"]` lists suitable hosts in preference order. Check its length before starting a two-host debate. Unknown IDs raise `ValueError`. A failed OpenAI request or unusable decision raises `MatchProcessingError`.

Look up an article's delivery guidance with `config["article_tones"][desired_tone]`. Use `config["persona_tones"]` for the database values in a host's `tone` and `secondary_tones`. Both mappings live in the root [tones.json](../tones.json).

Enable INFO logging for `research.openai_client` to collect token usage by stage and model. For a cost estimate, sum input, cached input, and output at their respective rates, subtracting cached tokens from total input before pricing uncached input. Scale classification by distinct uncached articles and ranking by conversations for the 10× projection.

To check live editorial accuracy, supply the actual demo UUIDs for an always-cheerful host followed by a sober host. Run this from `backend/` with the database and OpenAI key configured. It retrieves those personas, classifies three synthetic English, German, and Dutch articles, and checks the selected host and article tones.

```sh
TIO_LIVE_MATCHER_HOSTS="cheerful-host-uuid,sober-host-uuid" uv run --locked python -m unittest tests.test_matcher.LiveMatcherTest
```
