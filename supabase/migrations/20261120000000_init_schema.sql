-- TIO: initial schema for the AI news podcast.
-- See docs/database-plan.md and docs/erd.md.
--
-- Rule: every column is nullable unless the MVP needs it (see "Nullability rule" in the plan).
-- Embeddings: 768 dimensions from nomic-embed-text-v2-moe, computed locally with Ollama.

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
create extension if not exists vector with schema extensions;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
create type public.lang_code as enum ('en', 'de', 'nl');
create type public.experience_level as enum ('none', 'beginner', 'intermediate', 'professional');
create type public.persona_tone as enum ('formal', 'conversational', 'energetic', 'calm', 'witty', 'analytical', 'authoritative');
create type public.episode_status as enum ('queued', 'scripting', 'synthesizing', 'ready', 'failed');
create type public.interaction_type as enum ('impression', 'played', 'completed', 'skipped', 'liked', 'disliked', 'source_opened', 'shared');
create type public.preference_source as enum ('explicit', 'implicit');

-- ---------------------------------------------------------------------------
-- Shared trigger: keep updated_at current
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- 2. Agent personas (created before profiles, which references it)
-- ---------------------------------------------------------------------------
create table public.agent_personas (
  id                  uuid primary key default gen_random_uuid(),
  slug                text not null unique,
  name                text not null,
  tagline             text,            -- one-line pitch shown on the persona picker
  description         text,
  personality_md      text not null,   -- full persona markdown
  system_prompt       text,
  tone                public.persona_tone not null,
  secondary_tones     public.persona_tone[] default '{}',
  target_audience     text,
  expertise_topics    text[] default '{}',   -- Newstex category codes
  formality           smallint check (formality between 1 and 5),
  humor_level         smallint check (humor_level between 1 and 5),
  pace_wpm            smallint check (pace_wpm > 0),
  catchphrases        text[] default '{}',
  do_rules            text[] default '{}',
  dont_rules          text[] default '{}',
  intro_template      text,
  outro_template      text,
  voice_provider      text,
  voice_id            text,
  voice_settings      jsonb,
  llm_model           text,
  temperature         real check (temperature between 0 and 2),
  max_episode_minutes smallint check (max_episode_minutes > 0)
);

-- ---------------------------------------------------------------------------
-- 1. Users
-- ---------------------------------------------------------------------------
create table public.profiles (
  id                        uuid primary key references auth.users (id) on delete cascade,
  email                     text not null unique,
  display_name              text,
  first_name                text,
  last_name                 text,
  date_of_birth             date,
  gender                    text,
  country                   text check (country is null or char_length(country) = 2),  -- ISO-2
  city                      text,
  timezone                  text default 'Europe/Amsterdam',
  preferred_language        public.lang_code not null default 'en',
  secondary_languages       public.lang_code[] default '{}',
  occupation                text,
  investor_experience       public.experience_level,
  risk_appetite             smallint check (risk_appetite between 1 and 5),  -- 1 cautious .. 5 speculative
  listener_goal             text,
  briefing_length_minutes   smallint not null default 10 check (briefing_length_minutes > 0),
  preferred_listen_time     time,
  delivery_days             smallint[] default '{1,2,3,4,5}'
                              check (delivery_days <@ array[1,2,3,4,5,6,7]::smallint[]),  -- ISO weekdays
  default_persona_id        uuid references public.agent_personas (id) on delete set null,
  followed_tickers          text[] default '{}',
  followed_publication_ids  text[] default '{}',
  muted_topics              text[] default '{}',   -- category codes or publisher section names
  -- user embedding
  interests_text            text,
  interest_embedding        extensions.vector(768),
  interest_embedding_source text check (interest_embedding_source in ('onboarding', 'behavior', 'hybrid')),
  interest_embedding_model  text,
  interest_embedded_at      timestamptz,
  -- account state
  onboarding_completed      boolean not null default false,
  marketing_opt_in          boolean default false,  -- GDPR consent for promotional email
  last_active_at            timestamptz,
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now()
);

create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- 3. News
-- ---------------------------------------------------------------------------
create table public.newstex_categories (
  code         text primary key,
  label        text,
  is_sentiment boolean not null default false
);

create table public.publishers (
  id         text primary key,   -- publisher.$id
  type       text,               -- publisher.$type
  name       text not null,
  newstex_id text,
  url        text,
  status     text,
  country    text
);

create table public.publications (
  id           text primary key,   -- publication.$id
  type         text,               -- publication.$type
  publisher_id text not null references public.publishers (id),
  name         text not null,
  newstex_id   text,
  url          text,
  code         text unique,
  status       text,
  country      text,
  content_type text,
  lc_class     text[]
);

create table public.articles (
  id                   text primary key,                -- $id
  type                 text,                            -- $type
  external_id          text,
  publication_id       text not null references public.publications (id),
  publisher_id         text references public.publishers (id),
  headline             text not null,
  excerpt              text not null,
  text_content         text not null,
  html_content         text,
  published_at         timestamptz not null,            -- date
  received_at          timestamptz,
  language             public.lang_code not null,
  author               text,                            -- source (the byline, not the outlet)
  permalink            text not null unique,
  path                 text,
  feed                 text,                            -- base64 RSS URL
  pub_type             text,
  status               text,
  version              integer,
  thumb_url            text,
  categories           text[] default '{}',
  provider_categories  text[] default '{}',
  company_tickers      text[] default '{}',
  provider_tickers     text[] default '{}',
  category_scores      jsonb,
  category_score_audit jsonb,                           -- Zacks only
  character_count      integer,                         -- stats.*
  word_count           integer,
  line_count           integer,
  sentiment_score      integer,
  content_lock         text,
  content_commit_token text,
  -- extras
  source_file          text not null,                   -- which .jsonl the row came from
  translation_of       text references public.articles (id) on delete set null,
  ingested_at          timestamptz not null default now(),
  summary              text,
  summary_model        text,
  summary_embedding    extensions.vector(768),
  embedding_source     text check (embedding_source in ('excerpt', 'summary')),
  embedding_model      text,                            -- e.g. 'nomic-embed-text-v2-moe'
  embedded_at          timestamptz
);

create table public.article_category_scores (
  article_id    text not null references public.articles (id) on delete cascade,
  category_code text not null references public.newstex_categories (code),
  score         integer not null,
  primary key (article_id, category_code)
);

create table public.user_topic_preferences (
  user_id       uuid not null references public.profiles (id) on delete cascade,
  category_code text not null references public.newstex_categories (code),
  weight        real not null default 0 check (weight between -1 and 1),
  source        public.preference_source not null default 'explicit',
  updated_at    timestamptz default now(),
  primary key (user_id, category_code)
);

create trigger user_topic_preferences_set_updated_at
  before update on public.user_topic_preferences
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- 4. Connecting tables
-- ---------------------------------------------------------------------------
create table public.episodes (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null references public.profiles (id) on delete cascade,
  persona_id            uuid not null references public.agent_personas (id),
  language              public.lang_code not null,
  status                public.episode_status not null default 'queued',
  title                 text,
  script_md             text,
  audio_path            text default '-1',   -- Storage path in bucket episode-audio; '-1' = no audio
  audio_mime            text,
  audio_size_bytes      bigint,
  tts_provider          text,
  tts_voice_id          text,
  duration_seconds      integer,
  target_length_minutes smallint,
  llm_model             text,
  input_tokens          integer,
  output_tokens         integer,
  cost_usd              numeric(10, 4),
  error                 text,
  generated_at          timestamptz,
  created_at            timestamptz not null default now()
);

create table public.episode_sources (
  id              bigint generated always as identity primary key,
  episode_id      uuid not null references public.episodes (id) on delete cascade,
  article_id      text not null references public.articles (id),
  position        smallint not null,
  claim_text      text,   -- our paraphrase, never article text
  audio_start_sec real,
  audio_end_sec   real,
  unique (episode_id, position)
);

create table public.user_article_interactions (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references public.profiles (id) on delete cascade,
  article_id text not null references public.articles (id) on delete cascade,
  episode_id uuid references public.episodes (id) on delete set null,
  type       public.interaction_type not null,
  value      real,   -- e.g. fraction listened
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
create index articles_published_at_idx        on public.articles (published_at desc);
create index articles_publication_date_idx    on public.articles (publication_id, published_at desc);
create index articles_language_idx            on public.articles (language);
create index articles_translation_of_idx      on public.articles (translation_of);
create index articles_categories_idx          on public.articles using gin (categories);
create index articles_provider_categories_idx on public.articles using gin (provider_categories);
create index articles_company_tickers_idx     on public.articles using gin (company_tickers);
create index articles_summary_embedding_idx   on public.articles
  using hnsw (summary_embedding extensions.vector_cosine_ops);

create index article_category_scores_code_score_idx on public.article_category_scores (category_code, score desc);

create index profiles_interest_embedding_idx on public.profiles
  using hnsw (interest_embedding extensions.vector_cosine_ops);

create index episodes_user_created_idx        on public.episodes (user_id, created_at desc);
create index episode_sources_article_idx      on public.episode_sources (article_id);
create index interactions_user_created_idx    on public.user_article_interactions (user_id, created_at desc);
create index interactions_article_idx         on public.user_article_interactions (article_id);

-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------
create view public.profiles_with_age
with (security_invoker = true)
as
select p.*,
       extract(year from age(p.date_of_birth))::integer as age
from public.profiles p;

-- ---------------------------------------------------------------------------
-- Similarity search (RPC)
-- ---------------------------------------------------------------------------
create or replace function public.match_articles(
  query_embedding  extensions.vector(768),
  match_count      integer default 10,
  min_published_at timestamptz default null
)
returns table (id text, headline text, permalink text, published_at timestamptz, similarity double precision)
language sql
stable
set search_path = public, extensions
as $$
  select a.id,
         a.headline,
         a.permalink,
         a.published_at,
         1 - (a.summary_embedding <=> query_embedding) as similarity
  from public.articles a
  where a.summary_embedding is not null
    and (min_published_at is null or a.published_at >= min_published_at)
  order by a.summary_embedding <=> query_embedding
  limit match_count;
$$;

-- Runs with the caller's rights: RLS on profiles means a user only gets their own recommendations.
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
    select p.interest_embedding,
           coalesce(p.muted_topics, '{}') as muted
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
    and not (coalesce(a.categories, '{}') && u.muted)
    and not (coalesce(a.provider_categories, '{}') && u.muted)
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

-- ---------------------------------------------------------------------------
-- New auth user -> profile row
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email);
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- Row level security
-- ---------------------------------------------------------------------------
alter table public.agent_personas            enable row level security;
alter table public.profiles                  enable row level security;
alter table public.newstex_categories        enable row level security;
alter table public.publishers                enable row level security;
alter table public.publications              enable row level security;
alter table public.articles                  enable row level security;
alter table public.article_category_scores   enable row level security;
alter table public.user_topic_preferences    enable row level security;
alter table public.episodes                  enable row level security;
alter table public.episode_sources           enable row level security;
alter table public.user_article_interactions enable row level security;

-- Shared read-only data: any signed-in user can read; writes only via service_role (bypasses RLS).
create policy "Signed-in users read personas"     on public.agent_personas          for select to authenticated using (true);
create policy "Signed-in users read categories"   on public.newstex_categories      for select to authenticated using (true);
create policy "Signed-in users read publishers"   on public.publishers              for select to authenticated using (true);
create policy "Signed-in users read publications" on public.publications            for select to authenticated using (true);
create policy "Signed-in users read articles"     on public.articles                for select to authenticated using (true);
create policy "Signed-in users read scores"       on public.article_category_scores for select to authenticated using (true);

-- Own profile
create policy "Users read own profile"   on public.profiles for select to authenticated
  using ((select auth.uid()) = id);
create policy "Users insert own profile" on public.profiles for insert to authenticated
  with check ((select auth.uid()) = id);
create policy "Users update own profile" on public.profiles for update to authenticated
  using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

-- Own topic preferences
create policy "Users read own preferences"   on public.user_topic_preferences for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Users insert own preferences" on public.user_topic_preferences for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Users update own preferences" on public.user_topic_preferences for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Users delete own preferences" on public.user_topic_preferences for delete to authenticated
  using ((select auth.uid()) = user_id);

-- Own episodes
create policy "Users read own episodes"   on public.episodes for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Users insert own episodes" on public.episodes for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Users update own episodes" on public.episodes for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

-- Episode sources follow the parent episode's owner
create policy "Users read sources of own episodes" on public.episode_sources for select to authenticated
  using (exists (
    select 1 from public.episodes e
    where e.id = episode_id and e.user_id = (select auth.uid())
  ));

-- Own interactions
create policy "Users read own interactions"   on public.user_article_interactions for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Users insert own interactions" on public.user_article_interactions for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Users update own interactions" on public.user_article_interactions for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

-- ---------------------------------------------------------------------------
-- Audio storage: private bucket, one folder per user ({user_id}/{episode_id}.mp3)
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('episode-audio', 'episode-audio', false, 52428800, array['audio/mpeg'])
on conflict (id) do nothing;

create policy "Users read own episode audio" on storage.objects for select to authenticated
  using (
    bucket_id = 'episode-audio'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
-- Uploads and deletes: service_role only (no policy needed, it bypasses RLS).
