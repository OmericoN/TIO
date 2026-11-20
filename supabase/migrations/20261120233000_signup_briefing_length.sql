create or replace function public.save_signup_briefing_length()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  duration text := new.raw_user_meta_data #>> '{tio_onboarding,briefing_length_minutes}';
begin
  if duration ~ '^([1-9]|[1-5][0-9]|60)$' then
    update public.profiles
    set briefing_length_minutes = duration::smallint
    where id = new.id;
  end if;
  return new;
end;
$$;

-- Same-event triggers run in name order. The profile is created first.
create or replace trigger on_auth_user_duration_preference
  after insert on auth.users
  for each row execute function public.save_signup_briefing_length();
