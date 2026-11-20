-- The three host personas a listener picks at sign-up. The recommender ranks them first for a new listener; as
-- listening history builds up, past reactions take over.
-- Postgres can't put a foreign key on array elements, so a trigger checks each id exists in agent_personas.
-- Safe to re-run.

alter table public.profiles
  add column if not exists chosen_persona_ids uuid[] not null default '{}';

alter table public.profiles drop constraint if exists profiles_chosen_persona_ids_check;
alter table public.profiles add constraint profiles_chosen_persona_ids_check
  check (cardinality(chosen_persona_ids) <= 3);

create or replace function public.profiles_check_chosen_personas()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  missing uuid;
begin
  if cardinality(new.chosen_persona_ids) <> cardinality(array(select distinct unnest(new.chosen_persona_ids))) then
    raise exception 'chosen_persona_ids contains the same persona twice' using errcode = 'check_violation';
  end if;
  select p into missing
  from unnest(new.chosen_persona_ids) as p
  where not exists (select 1 from public.agent_personas a where a.id = p)
  limit 1;
  if missing is not null then
    raise exception 'chosen_persona_ids contains % which is not in agent_personas', missing
      using errcode = 'foreign_key_violation';
  end if;
  return new;
end;
$$;

drop trigger if exists profiles_check_chosen_personas_trg on public.profiles;
create trigger profiles_check_chosen_personas_trg
  before insert or update of chosen_persona_ids on public.profiles
  for each row execute function public.profiles_check_chosen_personas();

-- profiles_with_age selects p.*, which is expanded when the view is created; recreate it to include the column.
drop view if exists public.profiles_with_age;
create view public.profiles_with_age
with (security_invoker = true)
as
select p.*,
       extract(year from age(p.date_of_birth))::integer as age
from public.profiles p;
