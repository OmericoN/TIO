-- The recommender ranks a new listener's hosts from profiles.chosen_persona_ids (at most three), but the sign-up
-- trigger only filled preferred_persona_ids. Fill both: the full ordered list, and the first three picks.
-- Safe to re-run.

alter table public.profiles
  add column if not exists preferred_persona_ids uuid[] not null default '{}';

create or replace function public.save_signup_voice_preferences()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  picks jsonb := new.raw_user_meta_data #> '{tio_onboarding,preferred_persona_ids}';
  ordered uuid[];
begin
  if jsonb_typeof(picks) is distinct from 'array' then
    return new;
  end if;

  ordered := array(
    select persona.id
    from jsonb_array_elements_text(picks) with ordinality as pick(id, position)
    join public.agent_personas as persona on persona.id::text = pick.id
    group by persona.id
    order by min(pick.position)
  );

  update public.profiles
  set preferred_persona_ids = ordered,
      chosen_persona_ids = coalesce(ordered[1:3], '{}')
  where id = new.id;

  return new;
end;
$$;
