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
begin
  if jsonb_typeof(picks) is distinct from 'array' then
    return new;
  end if;

  update public.profiles
  set preferred_persona_ids = array(
    select persona.id
    from jsonb_array_elements_text(picks) with ordinality as pick(id, position)
    join public.agent_personas as persona on persona.id::text = pick.id
    group by persona.id
    order by min(pick.position)
  )
  where id = new.id;

  return new;
end;
$$;

-- PostgreSQL fires same-event triggers alphabetically. Create the profile first.
create or replace trigger on_auth_user_voice_preferences
  after insert on auth.users
  for each row execute function public.save_signup_voice_preferences();
