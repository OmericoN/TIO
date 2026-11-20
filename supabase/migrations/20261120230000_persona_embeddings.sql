-- Embedding for each host persona, so personas can be matched to articles and listeners by similarity.
-- 1536 dimensions for OpenAI text-embedding-3-large, the same size as articles and profiles after
-- 20261120210000_openai_embeddings. Vectors are only comparable when made by the same model.
-- Safe to re-run.

alter table public.agent_personas
  add column if not exists embedding        extensions.vector(1536),
  add column if not exists embedding_source text,         -- which text was embedded, e.g. 'personality_md'
  add column if not exists embedding_model  text,
  add column if not exists embedded_at      timestamptz;
