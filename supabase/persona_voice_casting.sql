-- TIO ElevenLabs casting for the 70-persona roster.
-- Published IDs: https://elevenlabs.io/docs/help-center/product/voices/my-voices/what-are-default-voices
-- Fallbacks prioritize delivery when the requested regional accent is unverified.
-- Running this script replaces the existing assignment for every matching slug.
WITH casting (slug, voice_name, voice_id) AS (
  VALUES
    ('main-host', 'Darian (fallback)', 'gOupLcAkjEnguROwi4oS'),
    ('earnings-and-equities', 'Elara', 'WQP7cQUF5aAS6Axh5yaa'),
    ('rates-and-fx', 'Alicia', 'BFd5oBc2DDna33pSi4Gf'),
    ('everyday-economy', 'Maisie', 'QtY3JBOUKEB5xzrRfOKc'),
    ('funds-and-etfs', 'Caleb', 'AaOhDHYJ1XLZk74lXhdE'),
    ('ai-and-chip-industry', 'Elowen', 'dvbL7qkNGZY1IqPGZAjM'),
    ('digital-assets', 'Warren', '7QN34D2r3hCNwbOYIeK0'),
    ('energy-and-commodities', 'Eldrin', '6WwXjDDEMyNmFG95zycZ'),
    ('policy-and-business', 'Jade', 'g7LVvkPWALzPxOQbF6OE'),
    ('glasgow-scoreboard', 'Lawrence (fallback)', 'ktkP7Nsj67dw2zcplQYt'),
    ('brooklyn-copy-desk', 'Warren (fallback)', '7QN34D2r3hCNwbOYIeK0'),
    ('australian-field-desk', 'Baxter (fallback)', 'jSuBIjxMKhqIfb0wCK1F'),
    ('dublin-quiz-desk', 'Jade (fallback)', 'g7LVvkPWALzPxOQbF6OE'),
    ('southern-porch-radio', 'Wyatt (fallback)', 'FrS6cKLB1wg4WYgPa9GW'),
    ('yorkshire-reality-check', 'Florence (fallback)', '22N9cF8z0o7y23njdyaY'),
    ('midnight-museum', 'Sawyer', '8dEUmyPMdDdK91vboYih'),
    ('switchboard-operator', 'Jade', 'g7LVvkPWALzPxOQbF6OE'),
    ('weather-for-headlines', 'Lawrence', 'ktkP7Nsj67dw2zcplQYt'),
    ('quick-morning-desk', 'Elowen', 'dvbL7qkNGZY1IqPGZAjM'),
    ('curious-explainer', 'Eddie', 'l7kNoIfnJKPg7779LI2t'),
    ('after-work-anchor', 'Florence', '22N9cF8z0o7y23njdyaY'),
    ('workplace-brief', 'Darian', 'gOupLcAkjEnguROwi4oS'),
    ('on-the-record', 'Talia', 'OZ0L6eISlOejga3XjDFt'),
    ('numbers-desk', 'Wyatt', 'FrS6cKLB1wg4WYgPa9GW'),
    ('rolling-news-desk', 'Alicia', 'BFd5oBc2DDna33pSi4Gf'),
    ('corner-news-desk', 'Warren', '7QN34D2r3hCNwbOYIeK0'),
    ('stand-up-news-desk', 'Callum', 'fs2OqxduwXgp9foh2xjK'),
    ('asmr-quiet-brief', 'Talia', 'OZ0L6eISlOejga3XjDFt'),
    ('socratic-news-desk', 'Caleb', 'AaOhDHYJ1XLZk74lXhdE'),
    ('kingston-public-radio', 'Eddie (fallback)', 'l7kNoIfnJKPg7779LI2t'),
    ('osaka-city-map', 'Elara (fallback)', 'WQP7cQUF5aAS6Axh5yaa'),
    ('mexico-city-driving-radio', 'Lawrence (fallback)', 'ktkP7Nsj67dw2zcplQYt'),
    ('sao-paulo-greenroom', 'Elowen (fallback)', 'dvbL7qkNGZY1IqPGZAjM'),
    ('debug-console', 'Lawrence', 'ktkP7Nsj67dw2zcplQYt'),
    ('corner-news-desk--nia', 'Jade', 'g7LVvkPWALzPxOQbF6OE'),
    ('corner-news-desk--tasha', 'Elara', 'WQP7cQUF5aAS6Axh5yaa'),
    ('corner-news-desk--jay', 'Lawrence', 'ktkP7Nsj67dw2zcplQYt'),
    ('corner-news-desk--malik', 'Darian', 'gOupLcAkjEnguROwi4oS'),
    ('stand-up-news-desk--june', 'Maisie', 'QtY3JBOUKEB5xzrRfOKc'),
    ('stand-up-news-desk--harriet', 'Florence', '22N9cF8z0o7y23njdyaY'),
    ('stand-up-news-desk--owen', 'Warren', '7QN34D2r3hCNwbOYIeK0'),
    ('stand-up-news-desk--rafi', 'Callum', 'fs2OqxduwXgp9foh2xjK'),
    ('asmr-quiet-brief--selene', 'Florence', '22N9cF8z0o7y23njdyaY'),
    ('asmr-quiet-brief--mira', 'Talia', 'OZ0L6eISlOejga3XjDFt'),
    ('asmr-quiet-brief--eli', 'Sawyer', '8dEUmyPMdDdK91vboYih'),
    ('asmr-quiet-brief--theo', 'Darian', 'gOupLcAkjEnguROwi4oS'),
    ('socratic-news-desk--bea', 'Elara', 'WQP7cQUF5aAS6Axh5yaa'),
    ('socratic-news-desk--anika', 'Alicia', 'BFd5oBc2DDna33pSi4Gf'),
    ('socratic-news-desk--martin', 'Finley', 'fnYMz3F5gMEDGMWcH1ex'),
    ('socratic-news-desk--dario', 'Caleb', 'AaOhDHYJ1XLZk74lXhdE'),
    ('kingston-public-radio--leonie', 'Alicia (fallback)', 'BFd5oBc2DDna33pSi4Gf'),
    ('kingston-public-radio--talia', 'Maisie (fallback)', 'QtY3JBOUKEB5xzrRfOKc'),
    ('kingston-public-radio--desmond', 'Finley (fallback)', 'fnYMz3F5gMEDGMWcH1ex'),
    ('kingston-public-radio--micah', 'Lawrence (fallback)', 'ktkP7Nsj67dw2zcplQYt'),
    ('osaka-city-map--emi', 'Talia (fallback)', 'OZ0L6eISlOejga3XjDFt'),
    ('osaka-city-map--rina', 'Alicia (fallback)', 'BFd5oBc2DDna33pSi4Gf'),
    ('osaka-city-map--ren', 'Eddie (fallback)', 'l7kNoIfnJKPg7779LI2t'),
    ('osaka-city-map--naoki', 'Finley (fallback)', 'fnYMz3F5gMEDGMWcH1ex'),
    ('mexico-city-driving-radio--lucia', 'Elowen (fallback)', 'dvbL7qkNGZY1IqPGZAjM'),
    ('mexico-city-driving-radio--elena', 'Elara (fallback)', 'WQP7cQUF5aAS6Axh5yaa'),
    ('mexico-city-driving-radio--tomas', 'Finley (fallback)', 'fnYMz3F5gMEDGMWcH1ex'),
    ('mexico-city-driving-radio--gael', 'Warren (fallback)', '7QN34D2r3hCNwbOYIeK0'),
    ('sao-paulo-greenroom--marina', 'Alicia (fallback)', 'BFd5oBc2DDna33pSi4Gf'),
    ('sao-paulo-greenroom--bia', 'Jade (fallback)', 'g7LVvkPWALzPxOQbF6OE'),
    ('sao-paulo-greenroom--rafael', 'Darian (fallback)', 'gOupLcAkjEnguROwi4oS'),
    ('sao-paulo-greenroom--caio', 'Lawrence (fallback)', 'ktkP7Nsj67dw2zcplQYt'),
    ('debug-console--vera', 'Elara', 'WQP7cQUF5aAS6Axh5yaa'),
    ('debug-console--mina', 'Elowen', 'dvbL7qkNGZY1IqPGZAjM'),
    ('debug-console--felix', 'Eddie', 'l7kNoIfnJKPg7779LI2t'),
    ('debug-console--dev', 'Wyatt', 'FrS6cKLB1wg4WYgPa9GW')
),
updated AS (
  UPDATE public.agent_personas AS persona
  SET voice_provider = 'elevenlabs',
      voice_id = casting.voice_id
  FROM casting
  WHERE persona.slug = casting.slug
    AND (persona.voice_provider, persona.voice_id) IS DISTINCT FROM ('elevenlabs', casting.voice_id)
  RETURNING persona.slug
)
SELECT casting.slug,
       casting.voice_name,
       CASE WHEN persona.slug IS NOT NULL
            THEN casting.voice_id
       END AS voice_id,
       CASE
         WHEN persona.slug IS NULL THEN 'persona missing'
         WHEN updated.slug IS NOT NULL THEN 'updated'
         ELSE 'already assigned'
       END AS status
FROM casting
LEFT JOIN public.agent_personas AS persona USING (slug)
LEFT JOIN updated USING (slug)
ORDER BY casting.slug;
