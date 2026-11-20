-- Remove profile fields the team decided not to use: followed_tickers, muted_topics, investor_experience.
-- Safe to re-run.

-- recommend_articles read muted_topics; redefine it without the muted-topic filter first.
create or replace function public.recommend_articles(
  p_user_id        uuid,
  match_count      integer default 20,
  min_published_at timestamptz default null
)
returns table (id text, headline text, permalink text, published_at timestamptz, similarity double precision)
language sql
stable
set search_path = public, extensions
as $$
  with u as (
    select p.interest_embedding
    from public.profiles p
    where p.id = p_user_id
      and p.interest_embedding is not null
  )
  select a.id,
         a.headline,
         a.permalink,
         a.published_at,
         1 - (a.summary_embedding <=> u.interest_embedding) as similarity
  from public.articles a
  cross join u
  where a.summary_embedding is not null
    and (min_published_at is null or a.published_at >= min_published_at)
    and not exists (
      select 1
      from public.user_article_interactions i
      where i.user_id = p_user_id
        and i.article_id = a.id
        and i.type in ('played', 'completed')
    )
  order by a.summary_embedding <=> u.interest_embedding
  limit match_count;
$$;

-- profiles_with_age selects p.*, so it pins every column; drop it, drop the columns, then recreate it.
drop view if exists public.profiles_with_age;

alter table public.profiles
  drop column if exists followed_tickers,
  drop column if exists muted_topics,
  drop column if exists investor_experience;

drop type if exists public.experience_level;

create view public.profiles_with_age
with (security_invoker = true)
as
select p.*,
       extract(year from age(p.date_of_birth))::integer as age
from public.profiles p;
