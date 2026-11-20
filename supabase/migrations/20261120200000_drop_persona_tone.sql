-- Remove tone from agent_personas. secondary_tones only made sense next to tone, so it goes too,
-- and with it the persona_tone enum. Personality stays in personality_md, formality and humor_level.
-- Safe to re-run.

alter table public.agent_personas
  drop column if exists tone,
  drop column if exists secondary_tones;

drop type if exists public.persona_tone;
