-- Listening history on the profile: which articles the user engaged with, and one vector for all of them.
-- Kept up to date by a trigger on user_article_interactions, so it is always current without a batch job.
-- `tio_data embed users` also refreshes it, because re-embedding articles changes the average.
-- Safe to re-run.

alter table public.profiles
  add column if not exists past_article_ids        text[] default '{}',          -- most recent first
  add column if not exists past_articles_embedding extensions.vector(768),       -- mean of their embeddings
  add column if not exists past_articles_updated_at timestamptz;

-- Interaction types that count as "the user consumed this article". Impressions, skips and dislikes don't.
create or replace function public.refresh_profile_past_articles(p_user_id uuid)
returns void
language sql
security definer
set search_path = public, extensions
as $$
  update public.profiles p
  set past_article_ids         = coalesce(h.ids, '{}'),
      past_articles_embedding  = h.embedding,
      past_articles_updated_at = now()
  from (
    select array_agg(x.article_id order by x.last_at desc) as ids,
           avg(a.summary_embedding)                        as embedding  -- pgvector mean, nulls ignored
    from (
      select i.article_id, max(i.created_at) as last_at
      from public.user_article_interactions i
      where i.user_id = p_user_id
        and i.type in ('played', 'completed', 'liked', 'shared', 'source_opened')
      group by i.article_id
    ) x
    join public.articles a on a.id = x.article_id
  ) h
  where p.id = p_user_id;
$$;

-- Only the trigger and the service role call this; signed-in users can't refresh other people's profiles.
revoke execute on function public.refresh_profile_past_articles(uuid) from public, anon, authenticated;

create or replace function public.user_article_interactions_refresh_profile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if tg_op in ('UPDATE', 'DELETE') then
    perform public.refresh_profile_past_articles(old.user_id);
  end if;
  if tg_op in ('INSERT', 'UPDATE') and (tg_op = 'INSERT' or new.user_id is distinct from old.user_id) then
    perform public.refresh_profile_past_articles(new.user_id);
  end if;
  return null;
end;
$$;

drop trigger if exists user_article_interactions_refresh_profile_trg on public.user_article_interactions;
create trigger user_article_interactions_refresh_profile_trg
  after insert or update or delete on public.user_article_interactions
  for each row execute function public.user_article_interactions_refresh_profile();

-- Backfill users who already have interactions.
select public.refresh_profile_past_articles(p.id) from public.profiles p;

-- profiles_with_age expands p.* when it is created; recreate it so it includes the new columns.
drop view if exists public.profiles_with_age;
create view public.profiles_with_age
with (security_invoker = true)
as
select p.*,
       extract(year from age(p.date_of_birth))::integer as age
from public.profiles p;
