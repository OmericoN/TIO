alter table public.signup_terms_acceptances
  drop constraint signup_terms_acceptances_terms_url_check,
  add constraint signup_terms_acceptances_terms_url_check
    check (terms_url like 'https://%' or terms_url = '/terms-v1.html');
