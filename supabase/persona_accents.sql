-- Strong, specific accents for the persona roster. The live voices treat agent_personas.accent as authoritative and
-- are told to keep it strong in every sentence, so each speaker is recognisable with your eyes closed. Accents are
-- written without a language ("strong Parisian French accent") because the voices speak the listener's language.
--
-- Running this script sets accent for every matching slug, rewrites the accent part of the first line of
-- personality_md ("Name — Gender · <accent>."), and updates the audition tagline where it named a different accent.
-- A changed accent or persona text recasts that persona's live voice on its next briefing.
WITH accents (slug, accent, header, tagline) AS (
  VALUES
    ('main-host', 'strong Mexico City Mexican accent', 'Strong Mexico City Mexican accent.', NULL),
    ('after-dark-desk', 'strong, smoky East London (Cockney) accent', 'Strong, smoky East London (Cockney) accent.', NULL),
    ('ai-and-chip-industry', 'strong Californian (Bay Area) accent', 'Energetic explainer with a strong Californian (Bay Area) accent.', NULL),
    ('asmr-quiet-brief', 'soft but strong Scottish Highland accent', 'Soft, soothing voice with a strong Scottish Highland accent.', NULL),
    ('big-picture-radio', 'strong Nigerian (Lagos) accent', 'Strong Nigerian (Lagos) accent.', NULL),
    ('bronx-two-bars', 'thick Bronx, New York accent', 'Thick Bronx, New York accent.', NULL),
    ('company-correspondent', 'strong Italian (Milanese) accent', 'Strong Italian (Milanese) accent.', NULL),
    ('council-notes', 'broad Yorkshire accent', 'Broad Yorkshire accent; natural stutter.', NULL),
    ('curious-explainer', 'strong Mandarin Chinese (Beijing) accent', 'Conversational explainer with a strong Mandarin Chinese (Beijing) accent.', NULL),
    ('dry-run-desk', 'very strong Russian (Moscow) accent', 'Very strong Russian (Moscow) accent.', NULL),
    ('earnings-and-equities', 'crisp, strong BBC Received Pronunciation accent', 'Newsreader with a crisp, strong BBC Received Pronunciation accent.', 'Woman - Newsreader, BBC English accent'),
    ('everyday-economy', 'warm, broad Geordie (Newcastle) accent', 'Warm explainer with a broad Geordie (Newcastle) accent.', NULL),
    ('funds-and-etfs', 'strong, formal Edinburgh Scottish accent', 'Measured explainer with a strong, formal Edinburgh Scottish accent.', 'Man - Formal Edinburgh accent'),
    ('hot-mic-consumer-desk', 'strong Romanian (Bucharest) accent', 'Raspy, loud, angry voice with a strong Romanian (Bucharest) accent.', NULL),
    ('industry-correspondent', 'strong German (Berlin) accent', 'Strong German (Berlin) accent.', 'Woman - Factual, German accent'),
    ('kingston-public-radio', 'strong Jamaican (Kingston) accent', 'Strong Jamaican (Kingston) accent.', NULL),
    ('labour-correspondent', 'very strong Castilian Spanish (Madrid) accent', 'Very strong Castilian Spanish (Madrid) accent.', NULL),
    ('mexico-city-driving-radio', 'strong Northern Mexican (Monterrey) accent', 'Strong Northern Mexican (Monterrey) accent.', 'Man - Northern Mexican accent'),
    ('numbers-desk', 'strong South African (Johannesburg) accent', 'Newsreader with a strong South African (Johannesburg) accent.', 'Man - Newsreader, South African accent'),
    ('policy-correspondent', 'strong Parisian French accent', 'Strong Parisian French accent.', NULL),
    ('rolling-news-desk', 'strong Australian (Sydney) accent', 'Newsreader with a strong Australian (Sydney) accent.', 'Woman - Newsreader, Australian accent'),
    ('systems-desk', 'strong Indian (Bangalore) accent', 'Strong Indian (Bangalore) accent.', NULL),
    ('trade-correspondent', 'strong Dutch (Rotterdam) accent', 'Strong Dutch (Rotterdam) accent.', NULL),
    ('weekend-copy-desk', 'strong South Wales Welsh accent', 'Strong South Wales Welsh accent.', NULL),
    ('workplace-brief', 'broad Texan drawl', 'Newsreader with a broad Texan drawl.', 'Man - Newsreader, Texan drawl')
)
UPDATE public.agent_personas AS persona
SET accent = accents.accent,
    personality_md = replace(
      regexp_replace(persona.personality_md, '^([^\n·]*·)[^\n]*', '\1 ' || accents.header),
      'you speak english with a mexican accent', 'You speak with a strong Mexican accent in every sentence.'),
    tagline = coalesce(accents.tagline, persona.tagline)
FROM accents
WHERE persona.slug = accents.slug;
