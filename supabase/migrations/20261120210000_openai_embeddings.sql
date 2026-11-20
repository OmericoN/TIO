-- Move every embedding to OpenAI text-embedding-3-large at 1536 dimensions (was local nomic-embed-text-v2-moe,
-- 768). Vectors from different models can't be compared, so the old ones are cleared; `tio_data embed articles`
-- refills articles and the recommender refills users. 1536 stays under pgvector's 2,000-dim HNSW limit.
-- Safe to re-run.

-- profiles_with_age selects p.*, which pins the column types; drop it now, recreate it at the end.
drop view if exists public.profiles_with_age;

drop index if exists public.articles_summary_embedding_idx;
drop index if exists public.profiles_interest_embedding_idx;
drop index if exists public.profiles_past_articles_embedding_idx;

-- Only convert (and clear) while the columns are still 768-dim, so a re-run never wipes the new vectors.
do $$
begin
  if (select atttypmod from pg_attribute
      where attrelid = 'public.articles'::regclass and attname = 'summary_embedding') <> 1536 then
    alter table public.articles alter column summary_embedding type extensions.vector(1536) using null;
    update public.articles set embedding_source = null, embedding_model = null, embedded_at = null;

    alter table public.profiles alter column interest_embedding type extensions.vector(1536) using null;
    alter table public.profiles alter column past_articles_embedding type extensions.vector(1536) using null;
    update public.profiles set interest_embedding_source = null, interest_embedding_model = null,
                               interest_embedded_at = null;
  end if;
end;
$$;

create index if not exists articles_summary_embedding_idx on public.articles
  using hnsw (summary_embedding extensions.vector_cosine_ops);
create index if not exists profiles_interest_embedding_idx on public.profiles
  using hnsw (interest_embedding extensions.vector_cosine_ops);
create index if not exists profiles_past_articles_embedding_idx on public.profiles
  using hnsw (past_articles_embedding extensions.vector_cosine_ops);

-- The query vector must have the same size as the column.
drop function if exists public.match_articles(extensions.vector, integer, timestamptz);
create function public.match_articles(
  query_embedding  extensions.vector(1536),
  match_count      integer default 10,
  min_published_at timestamptz default null
)
returns table (id text, headline text, permalink text, published_at timestamptz, similarity double precision)
language sql
stable
set search_path = public, extensions
as $$
  select a.id,
         a.headline,
         a.permalink,
         a.published_at,
         1 - (a.summary_embedding <=> query_embedding) as similarity
  from public.articles a
  where a.summary_embedding is not null
    and (min_published_at is null or a.published_at >= min_published_at)
  order by a.summary_embedding <=> query_embedding
  limit match_count;
$$;

create view public.profiles_with_age
with (security_invoker = true)
as
select p.*,
       extract(year from age(p.date_of_birth))::integer as age
from public.profiles p;
