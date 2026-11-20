-- Record which host personas a user heard for each interaction.
-- An array, because a two-host debate has two personas; a solo briefing has one.
-- Postgres can't put a foreign key on array elements, so a trigger checks each id exists in agent_personas.
-- Safe to re-run.

alter table public.user_article_interactions
  add column if not exists persona_ids uuid[] not null default '{}';

create index if not exists interactions_persona_ids_idx
  on public.user_article_interactions using gin (persona_ids);

create or replace function public.user_article_interactions_check_personas()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  missing uuid;
begin
  select p into missing
  from unnest(new.persona_ids) as p
  where not exists (select 1 from public.agent_personas a where a.id = p)
  limit 1;
  if missing is not null then
    raise exception 'persona_ids contains % which is not in agent_personas', missing
      using errcode = 'foreign_key_violation';
  end if;
  return new;
end;
$$;

drop trigger if exists user_article_interactions_check_personas_trg on public.user_article_interactions;
create trigger user_article_interactions_check_personas_trg
  before insert or update of persona_ids on public.user_article_interactions
  for each row execute function public.user_article_interactions_check_personas();
