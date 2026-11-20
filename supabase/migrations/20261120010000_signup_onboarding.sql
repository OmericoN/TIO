create table public.signup_terms_acceptances (
  user_id uuid primary key references auth.users (id) on delete cascade,
  terms_url text not null check (terms_url like 'https://%'),
  accepted_at timestamptz not null default now()
);

alter table public.signup_terms_acceptances enable row level security;

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  survey jsonb := new.raw_user_meta_data -> 'tio_onboarding';
  birth_date date;
  primary_language public.lang_code;
  second_language public.lang_code;
begin
  if survey is null then
    insert into public.profiles (id, email) values (new.id, new.email);
    return new;
  end if;

  birth_date := (survey ->> 'date_of_birth')::date;
  primary_language := (survey ->> 'preferred_language')::public.lang_code;
  second_language := (survey ->> 'secondary_language')::public.lang_code;

  if survey -> 'accepted_terms' is distinct from 'true'::jsonb
      or birth_date > current_date
      or primary_language is null
      or second_language = primary_language
      or nullif(btrim(survey ->> 'display_name'), '') is null
      or nullif(btrim(survey ->> 'first_name'), '') is null
      or nullif(btrim(survey ->> 'last_name'), '') is null
      or nullif(btrim(survey ->> 'listener_goal'), '') is null
      or nullif(btrim(survey ->> 'interests_text'), '') is null then
    raise exception 'Invalid signup survey';
  end if;

  insert into public.profiles (
    id, email, display_name, first_name, last_name, date_of_birth, gender,
    preferred_language, secondary_languages, listener_goal, preferred_listen_time,
    interests_text, onboarding_completed
  ) values (
    new.id, new.email, survey ->> 'display_name', survey ->> 'first_name',
    survey ->> 'last_name', birth_date, survey ->> 'gender', primary_language,
    case when second_language is null then '{}'::public.lang_code[] else array[second_language] end,
    survey ->> 'listener_goal', (survey ->> 'preferred_listen_time')::time,
    survey ->> 'interests_text', true
  );

  insert into public.signup_terms_acceptances (user_id, terms_url)
  values (new.id, survey ->> 'terms_url');

  return new;
end;
$$;
