-- The interest radar (sign-up and Profile): how strongly a listener follows each listed topic, from 1 (somewhat)
-- to 3 (very interested). Topics left at 0 are omitted, so '{}' means none picked.
-- interests_text stays the recommender's input; the app writes it with the strongest topics first.
-- Safe to re-run.

create or replace function public.valid_interest_levels(levels jsonb)
returns boolean
language sql
immutable
set search_path = ''
as $$
  -- case, not and: jsonb_each raises on an array, and a malformed sign-up must not fail the whole sign-up
  select case
    when levels is null or jsonb_typeof(levels) <> 'object' then false
    else (select count(*) from jsonb_object_keys(levels)) <= 40
     and not exists (
       select 1
       from jsonb_each(levels) as topic(name, level)
       where char_length(topic.name) not between 1 and 80
          or topic.name ~ '[,;\n]'  -- interests_text is a comma-separated list
          or topic.level not in ('1'::jsonb, '2'::jsonb, '3'::jsonb)
     )
  end
$$;

alter table public.profiles
  add column if not exists interest_levels jsonb not null default '{}'::jsonb;

alter table public.profiles drop constraint if exists profiles_interest_levels_valid;
alter table public.profiles
  add constraint profiles_interest_levels_valid check (public.valid_interest_levels(interest_levels));

-- Sign-up sends the radar as tio_onboarding.interest_levels. Invalid or missing levels are skipped, not fatal:
-- interests_text, which the profile trigger requires, still carries the picked topics.
create or replace function public.save_signup_interest_levels()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  levels jsonb := new.raw_user_meta_data #> '{tio_onboarding,interest_levels}';
begin
  if levels is null or not public.valid_interest_levels(levels) then
    return new;
  end if;

  update public.profiles
  set interest_levels = levels
  where id = new.id;

  return new;
end;
$$;

-- Same-event triggers run in name order. The profile is created first (on_auth_user_created).
create or replace trigger on_auth_user_interest_levels
  after insert on auth.users
  for each row execute function public.save_signup_interest_levels();
