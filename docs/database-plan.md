# Supabase schema for the AI news podcast (ERD + SQL migration)

## Context
TIO is an AI podcast built from news. It sends each user a personalised audio briefing, voiced by an agent persona, and links every claim back to its source article. The repo has a FastAPI backend stub, a React frontend and untracked Newstex data in `data_parser/data_files/` (6 JSONL files, 1,740 articles). There is no database yet. This plan defines the Supabase schema with three core areas, as requested:
1. **Users**: registered profiles with personal data and recommender preferences.
2. **Agent personas**: id, personality markdown and voice/LLM settings.
3. **News**: one column for every data point in the JSONL records.

Two small connecting areas, **episodes** and **interactions**, tie the three together. Without them the recommender has no signal and the podcast has no record of what it cited.

Decisions confirmed with the user:
- **Auth:** Supabase Auth (`auth.users`) handles email and hashed passwords. No password column in our tables.
- **Deliverable:** an ERD doc plus a runnable SQL migration. No loader script yet.

## Files to create
- `docs/database-plan.md`: this document.
- `supabase/migrations/20261120000000_init_schema.sql`: enums, tables, indexes, triggers and RLS. It can be pasted into the Supabase SQL editor or run with `supabase db push`.
- `docs/erd.md`: a Mermaid `erDiagram`, a short description of each table, and a JSONL → column mapping table.

## Enums
- `lang_code`: `en`, `de`, `nl`
- `episode_status`: `queued`, `scripting`, `synthesizing`, `ready`, `failed`
- `interaction_type`: `impression`, `played`, `completed`, `skipped`, `liked`, `disliked`, `source_opened`, `shared`
- `preference_source`: `explicit` (onboarding) and `implicit` (learned)

## Nullability rule (MVP)
**Every column is nullable unless it is listed below.** These are the only `NOT NULL` columns: what the MVP needs to sign up, load the news, generate an episode and cite sources. Everything else (profile details, persona extras, embeddings, summaries, audio, stats, Newstex bookkeeping) can be filled in later. Where a column has a default (`now()`, `'{}'`, `false`), the default still applies, but the column stays nullable unless listed.

| Table | NOT NULL columns |
|---|---|
| `profiles` | `id`, `email`, `preferred_language` (default `'en'`), `briefing_length_minutes` (default 10), `onboarding_completed` (default false), `created_at`, `updated_at` |
| `user_topic_preferences` | `user_id`, `category_code`, `weight` (default 0), `source` (default `'explicit'`) |
| `agent_personas` | `id`, `slug`, `name`, `personality_md` |
| `publishers` | `id`, `name` |
| `publications` | `id`, `publisher_id`, `name` |
| `articles` | `id`, `publication_id`, `headline`, `excerpt`, `text_content`, `published_at`, `language`, `permalink`, `source_file`, `ingested_at` (all present on all 1,740 rows) |
| `newstex_categories` | `code`, `is_sentiment` (default false) |
| `article_category_scores` | `article_id`, `category_code`, `score` |
| `episodes` | `id`, `user_id`, `persona_id`, `language`, `status` (default `'queued'`), `created_at` |
| `episode_sources` | `id`, `episode_id`, `article_id`, `position` |
| `user_article_interactions` | `id`, `user_id`, `article_id`, `type`, `created_at` |

So for example `date_of_birth`, `country`, `default_persona_id`, `interest_embedding`, persona voice/LLM settings, `html_content`, `stats.*`, `summary`, `summary_embedding`, `audio_path` (default `'-1'`), `cost_usd` and `claim_text` are all nullable.

## 1. Users
**`profiles`**: 1:1 with `auth.users`. The row is created by an `on_auth_user_created` trigger.
- `id uuid PK → auth.users.id on delete cascade`, `email text unique` (copied from auth)
- `display_name`, `first_name`, `last_name`, `date_of_birth date` (age is derived in a view, so it is never stale), `gender text null`
- `country text` (ISO-2), `city text`, `timezone text default 'Europe/Amsterdam'`
- `preferred_language lang_code default 'en'`, `secondary_languages lang_code[]`
- `occupation text null`
- `risk_appetite smallint null check (risk_appetite between 1 and 5)`: 1 = very cautious (savings, bonds), 5 = speculative (crypto, small caps, FX). It steers story choice, for example putting crypto and trading-ideas stories higher for a 5.
- `listener_goal text` (for example "FX trading", "general awareness")
- Briefing preferences: `briefing_length_minutes smallint default 10`, `preferred_listen_time time`, `delivery_days smallint[]` (ISO weekdays), `default_persona_id uuid → agent_personas`
- `followed_publication_ids text[]`
- `onboarding_completed bool`, `marketing_opt_in bool default false` (GDPR consent to receive promotional emails; separate from the briefing itself), `last_active_at`, `created_at`, `updated_at`

User embedding (also on `profiles`, all nullable):
- `interests_text text null`: the user's own description of what they want to hear, written in onboarding (for example "Dutch tech stocks, ECB decisions, no celebrity news").
- `interest_embedding vector(1536) null`: the user's taste vector. It uses the same model and size as `articles.summary_embedding`, so users and articles can be compared directly.
- `interest_embedding_source text null check in ('onboarding','behavior','hybrid')`: `onboarding` = embedding of `interests_text`; `behavior` = weighted average of the embeddings of articles the user played, completed or liked (skips and dislikes count negatively); `hybrid` = a blend of the two. It starts as `onboarding` and moves to `hybrid` once there are interactions.
- `interest_embedding_model text null`, `interest_embedded_at timestamptz null`.
- `past_article_ids text[]`: articles the user played, completed, liked, shared or opened the source of, most recent first. Impressions, skips and dislikes are left out.
- `past_articles_embedding vector(1536) null`: the mean of those articles' embeddings, a plain "what they have listened to" vector, separate from the onboarding-weighted `interest_embedding`. `past_articles_updated_at timestamptz null`.
- Both are kept current by a trigger on `user_article_interactions` that calls `refresh_profile_past_articles(user_id)` (migration `20261120190000_profile_past_articles.sql`); `tio_data embed users` refreshes them too, after articles are re-embedded.
- An HNSW index on `interest_embedding (vector_cosine_ops)` for "listeners like you".
- An RPC `recommend_articles(p_user_id uuid, match_count int, min_published_at timestamptz default null)` ranks articles by cosine similarity to the user's `interest_embedding`. It excludes articles the user has already played.

**`user_topic_preferences`**: the recommender's interest vector.
- `user_id → profiles`, `category_code → newstex_categories`, `weight real` (−1…1), `source preference_source`, `updated_at`. PK `(user_id, category_code)`.

**`profiles_with_age`**: a view that adds `age` computed from `date_of_birth`.

## 2. Agent personas
**`agent_personas`**
- `id uuid PK`, `slug text unique`, `name`, `tagline` (one-line pitch shown on the persona picker, for example "Your no-nonsense FX desk analyst"), `description`
- `personality_md text`: the full persona markdown
- `system_prompt text` (generated from or alongside the markdown)
- (No language fields: the episode language comes from the user's `preferred_language` and is stored on `episodes.language`. Any persona can speak any language.)
- `target_audience text`, `expertise_topics text[]` (category codes), `formality smallint 1–5`, `humor_level smallint 1–5`, `pace_wpm smallint` (speaking rate)
- `catchphrases text[]`, `do_rules text[]`, `dont_rules text[]`, `intro_template`, `outro_template`
- Voice/TTS: `voice_provider`, `voice_id`, `voice_settings jsonb`
- LLM: `llm_model text`, `temperature real`, `max_episode_minutes smallint`

## 3. News
Every data point in the JSONL files maps to a column. `publication` and `publisher` are normalised into their own tables because only 6 distinct values exist.

**`publishers`**: `id text PK` (`publisher.$id`), `type` (`$type`), `name`, `newstex_id`, `url`, `status`, `country null`.

**`publications`**: `id text PK` (`publication.$id`), `type`, `publisher_id → publishers`, `name`, `newstex_id`, `url`, `code unique`, `status`, `country`, `content_type`, `lc_class text[] null`.

**`articles`**
| Column | JSONL field |
|---|---|
| `id text PK` | `$id` |
| `type text` | `$type` |
| `external_id text` | `external_id` |
| `publication_id text → publications` | `publication.$id` |
| `publisher_id text → publishers` | `publisher.$id` |
| `headline text not null` | `headline` |
| `excerpt text` | `excerpt` |
| `text_content text` | `text_content` |
| `html_content text` | `html_content` |
| `published_at timestamptz` | `date` |
| `received_at timestamptz` | `received_at` |
| `language lang_code` | `language` |
| `author text` | `source` (it is the byline, not the outlet) |
| `permalink text not null unique` | `permalink` |
| `path text` | `path` |
| `feed text` | `feed` (base64 RSS URL) |
| `pub_type text` | `pub_type` |
| `status text` | `status` |
| `version int` | `version` |
| `thumb_url text null` | `thumb_url` |
| `categories text[]` | `categories` |
| `provider_categories text[]` | `provider_categories` |
| `company_tickers text[]` | `company_tickers` |
| `provider_tickers text[]` | `provider_tickers` |
| `category_scores jsonb` | `category_scores` (raw copy) |
| `category_score_audit jsonb null` | `category_score_audit` (Zacks only) |
| `character_count`, `word_count`, `line_count`, `sentiment_score` int null | `stats.*` |
| `content_lock text null`, `content_commit_token text null` | same names |
| `source_file text` | extra: which `.jsonl` it came from |
| `translation_of text null → articles.id` | extra: links Invezz DE/NL rows to their EN twin |
| `ingested_at timestamptz default now()` | extra |
| `summary text null` | extra: our own LLM-written summary (filled in later) |
| `summary_model text null` | extra: which LLM wrote the summary |
| `summary_embedding vector(1536) null` | extra: embedding (pgvector, OpenAI `text-embedding-3-large`) |
| `embedding_source text null check in ('excerpt','summary')` | extra: which text was embedded |
| `embedding_model text null` | extra: for example `'text-embedding-3-large'`, set when embedded |
| `embedded_at timestamptz null` | extra: when it was embedded |
| `main_subject text null` | extra (LLM): what the article is mainly about, 2–6 words, English |
| `event text null` | extra (LLM): one English sentence on what happened |
| `event_type text null` | extra (LLM): `earnings`, `ipo`, `central_bank`, `merger_acquisition`, … (list in `tio_data/extract.py`) |
| `topics text[]` | extra (LLM): 2–5 lowercase English themes, e.g. `interest rates` |
| `people text[]` | extra (LLM): full names of the people the article is about or quotes |
| `entities text[]` | extra (LLM): main companies, institutions, places, products; tickers in brackets |
| `extraction_model text null`, `extracted_at timestamptz null` | extra: which LLM extracted the fields, and when |
| `search_vector tsvector` | extra: full-text index, kept up to date by a trigger |

**Extraction and keyword search** (migration `20261120170000_article_extraction.sql`): `tio_data extract articles` fills the LLM columns above with a local Ollama model. `search_vector` combines headline, people and entities (weight A), subject, event and topics (B), excerpt (C) and body (D) with the `simple` config, so names and tickers match exactly in EN, DE and NL. RPC `search_articles(query text, match_count int, min_published_at timestamptz)` takes web-search syntax (`"exact phrase"`, `OR`, `-exclude`) and returns matches ranked by `ts_rank`. GIN indexes on `topics`, `people`, `entities` and `search_vector`, and a b-tree on `event_type`.

**Embeddings:** the migration starts with `create extension if not exists vector;`. The embedded text is the headline plus the extracted subject, event, topics, people and entities (when present), then the `summary` if there is one (`embedding_source = 'summary'`) or else the publisher's `excerpt` (`embedding_source = 'excerpt'`). `tio_data embed articles` re-embeds an article when its model changes, a summary appears or it is extracted again. An HNSW index `articles_summary_embedding_idx on articles using hnsw (summary_embedding vector_cosine_ops)` makes similarity search fast. An RPC function `match_articles(query_embedding vector(1536), match_count int, min_published_at timestamptz default null)` returns `id, headline, permalink, published_at, similarity`, ordered by cosine similarity. It covers "articles like this one", clustering the same story across outlets, and matching a user's interests to articles. All embedding columns are nullable, so articles load first and get embedded afterwards.

**Embedding model:** OpenAI `text-embedding-3-large`, shortened to 1536 dimensions with the API's `dimensions` parameter (under pgvector's 2,000-dimension HNSW limit). It is multilingual, so EN, DE and NL versions of the same Invezz story land close together, and it needs no task prefixes. Articles (`tio_data embed articles`), users (`tio_data embed users` and the recommender) and live wildcard queries all use this one model, so every vector can be compared with every other; the recommender ignores rows whose `embedding_model` differs. Migration `20261120210000_openai_embeddings.sql` moved the columns from `vector(768)` (local `nomic-embed-text-v2-moe`) and cleared the old vectors. Cost: $0.13 per 1M tokens, about $0.04 for the 1,740 articles and $0.40 at 10× volume. Embeddings are computed by the pipeline and backend and written to Supabase; Supabase only stores and searches them.

**`newstex_categories`**: `code text PK`, `label text null` (filled in by hand later), `is_sentiment bool`. Holds the 205 codes.

**`article_category_scores`**: `article_id → articles`, `category_code → newstex_categories`, `score int`. PK `(article_id, category_code)`. This is a normalised copy of `category_scores` (about 100k rows), which makes "articles where FINANCIL ≥ 10" a fast indexed query for the recommender.

Indexes: `articles(published_at desc)`, `(publication_id, published_at)`, `(language)`, GIN on `categories`, `provider_categories` and `company_tickers`, `article_category_scores(category_code, score desc)`.

## 4. Connecting tables
**`episodes`**: one generated briefing.
- `id uuid PK`, `user_id → profiles`, `persona_id → agent_personas`, `language`, `status episode_status`, `title`, `script_md text`, `audio_path text null default '-1'` (object path in Storage, not a URL; `'-1'` = no audio yet, and episodes without audio are allowed), `audio_mime text null`, `audio_size_bytes bigint null`, `tts_provider text null`, `tts_voice_id text null`, `duration_seconds`, `target_length_minutes`, `llm_model`, `input_tokens`, `output_tokens`, `cost_usd numeric(10,4)`, `error text`, `generated_at`, `created_at`

**`episode_sources`**: citations, so every claim maps to an article.
- `id`, `episode_id → episodes`, `article_id → articles`, `position smallint`, `claim_text text` (our paraphrase, not article text), `audio_start_sec real`, `audio_end_sec real`

**`user_article_interactions`**: implicit feedback for the recommender.
- `id bigint identity`, `user_id → profiles`, `article_id → articles`, `episode_id null → episodes`, `persona_ids uuid[] not null default '{}'` (hosts the user heard: one for a solo briefing, two for a debate; a trigger rejects ids missing from `agent_personas`, migration `20261120220000_interaction_personas.sql`), `type interaction_type`, `value real null` (for example the fraction listened), `created_at`

## ERD (goes in docs/erd.md)
```
auth.users 1─1 profiles
profiles 1─* user_topic_preferences *─1 newstex_categories
profiles *─1 agent_personas (default_persona_id)
profiles 1─* episodes *─1 agent_personas
episodes 1─* episode_sources *─1 articles
profiles 1─* user_article_interactions *─1 articles
publishers 1─* publications 1─* articles
articles 1─* article_category_scores *─1 newstex_categories
articles 0..1─* articles (translation_of)
```

## Audio storage (Supabase Storage)
- The migration creates a **private** bucket `episode-audio` (`insert into storage.buckets (id, name, public) values ('episode-audio','episode-audio', false)`), with a 50 MB file limit and only `audio/mpeg` allowed.
- Object path: `{user_id}/{episode_id}.mp3`. The database stores only this path in `episodes.audio_path`.
- Generation flow (FastAPI backend, using the service-role key): pick articles → LLM writes `script_md` → TTS returns MP3 bytes → upload to `episode-audio/{user_id}/{episode_id}.mp3` → set `audio_path`, `audio_size_bytes`, `duration_seconds` and `status = 'ready'`.
- No audio: `audio_path` stays `'-1'` (the default) until a file is uploaded. The frontend and backend treat `'-1'` (or NULL) as "no audio" and show the script only, without requesting a signed URL.
- Playback: the frontend calls `supabase.storage.from('episode-audio').createSignedUrl(audio_path, 3600)` and plays the temporary URL in an `<audio>` element. The URL expires after one hour, so links can't be shared around.
- Storage RLS on `storage.objects`: an authenticated user may `select` objects in this bucket only where `(storage.foldername(name))[1] = auth.uid()::text`. Only the service role may insert or delete.
- Sizing: a 10-minute MP3 is about 4.8 MB at 64 kbps mono (enough for speech), or about 9.6 MB at 128 kbps. The free tier (1 GB) holds about 200 episodes at 64 kbps; delete old episodes' audio after N days if needed.

## RLS and triggers
- Enable RLS on every table.
- `profiles`, `user_topic_preferences`, `episodes` and `user_article_interactions`: users can select, insert and update only rows where `user_id` (or `id`) = `auth.uid()`. `episode_sources` follows the parent episode's owner.
- `articles`, `publications`, `publishers`, `newstex_categories`, `article_category_scores` and `agent_personas`: `select` for `authenticated`. Writes only via `service_role`, used by the backend and the loader.
- Triggers: `handle_new_user()` (security definer) inserts a `profiles` row from `auth.users`, and a shared `set_updated_at()` trigger runs on tables with `updated_at`.

## Licence note
Full article text lives only in this private Supabase project. Do not expose `text_content` or `html_content` to the frontend; RLS can allow selecting them, but the API should not return them. Drop the tables after the event.

## Verification
1. Run the migration in a fresh Supabase project (SQL editor or `supabase db reset` locally). It must apply with no errors.
2. Sign up a test user in Supabase Auth and confirm a `profiles` row appears.
3. Insert one article built by hand from a real JSONL line (for example the first line of `invezz_en.jsonl`). Confirm every JSONL key has a destination column, using the mapping table.
4. RLS check: as user A, select user B's profile, episodes and interactions → 0 rows. Select articles → allowed. Insert an article as an authenticated user → denied.
5. Render the Mermaid ERD in `docs/erd.md` (VS Code preview or GitHub) and check that it matches the SQL.
