-- What each article is about, extracted by the local LLM (`tio_data extract articles`), plus full-text search.
-- Every extracted field is in English, whatever the article's language, so DE/NL coverage matches EN searches.
-- Safe to re-run.

alter table public.articles
  add column if not exists main_subject     text,                -- 'Anthropic IPO pricing'
  add column if not exists event            text,                -- one sentence: what happened
  add column if not exists event_type       text,                -- 'earnings', 'ipo', 'central_bank', ...
  add column if not exists topics           text[] default '{}', -- lowercase themes: 'artificial intelligence'
  add column if not exists people           text[] default '{}', -- full names: 'Elon Musk'
  add column if not exists entities         text[] default '{}', -- companies, institutions, places, products
  add column if not exists extraction_model text,
  add column if not exists extracted_at     timestamptz,
  add column if not exists search_vector    tsvector;

create index if not exists articles_event_type_idx    on public.articles (event_type);
create index if not exists articles_topics_idx        on public.articles using gin (topics);
create index if not exists articles_people_idx        on public.articles using gin (people);
create index if not exists articles_entities_idx      on public.articles using gin (entities);
create index if not exists articles_search_vector_idx on public.articles using gin (search_vector);

-- search_vector weights: A = headline, people, entities; B = subject, event, topics; C = excerpt; D = body.
-- 'simple' config: no stemming, so names and tickers match exactly in all three languages.
create or replace function public.articles_search_vector_update()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.search_vector :=
       setweight(to_tsvector('simple', coalesce(new.headline, '')), 'A')
    || setweight(to_tsvector('simple', array_to_string(coalesce(new.people, '{}'), ' ')), 'A')
    || setweight(to_tsvector('simple', array_to_string(coalesce(new.entities, '{}'), ' ')), 'A')
    || setweight(to_tsvector('simple', coalesce(new.main_subject, '') || ' ' || coalesce(new.event, '')), 'B')
    || setweight(to_tsvector('simple', array_to_string(coalesce(new.topics, '{}'), ' ')), 'B')
    || setweight(to_tsvector('simple', coalesce(new.excerpt, '')), 'C')
    || setweight(to_tsvector('simple', coalesce(new.text_content, '')), 'D');
  return new;
end;
$$;

drop trigger if exists articles_search_vector_trg on public.articles;
create trigger articles_search_vector_trg
  before insert or update of headline, excerpt, text_content, main_subject, event, topics, people, entities
  on public.articles
  for each row execute function public.articles_search_vector_update();

-- Fill search_vector for rows that already exist (fires the trigger).
update public.articles set headline = headline where search_vector is null;

-- Keyword search: names, tickers, words. websearch syntax: "exact phrase", OR, -exclude.
create or replace function public.search_articles(
  query            text,
  match_count      integer default 20,
  min_published_at timestamptz default null
)
returns table (id text, headline text, permalink text, published_at timestamptz, main_subject text,
               event_type text, people text[], entities text[], rank real)
language sql
stable
set search_path = public
as $$
  select a.id, a.headline, a.permalink, a.published_at, a.main_subject, a.event_type, a.people, a.entities,
         ts_rank(a.search_vector, q) as rank
  from public.articles a, websearch_to_tsquery('simple', query) q
  where a.search_vector @@ q
    and (min_published_at is null or a.published_at >= min_published_at)
  order by rank desc, a.published_at desc
  limit match_count;
$$;
