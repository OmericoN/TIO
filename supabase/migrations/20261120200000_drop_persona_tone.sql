alter table public.agent_personas
  drop column if exists tone,
  drop column if exists secondary_tones;

drop type if exists public.persona_tone;
