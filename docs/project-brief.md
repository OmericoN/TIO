# Project brief

Build a personalized audio news briefing for the Sourcera track at MAIN AI Hackathon 2026 in Maastricht (20–21 November). The listener should hear a short, natural English briefing shaped around what they need from the day's news and be able to check every spoken claim as they listen.

## Official challenge

- **Input.** One week of business and finance reporting licensed for the event by Newstex, from Zacks, Invezz (English, German, Dutch), IBTimes UK, and ActionForex. The handoff estimates roughly 2,000 articles. The organizers also provide public releases from the same week for cross-checking, four listener profiles, and an answer key for stories and facts.
- **Deliverables.** A working prototype that generates audio for at least two supplied profiles, with every spoken claim and number linked to its source article. The judges test listener fit live with an additional wildcard profile. The presentation covers the problem, who pays, cost for the full dataset, and estimated cost at 10× volume.
- **Data terms.** Use the licensed Newstex articles only during the hackathon and delete them afterward. Keep the licensed dataset out of Git.

| Points | Criterion | What to demonstrate |
| --- | --- | --- |
| 25 | Trust and accuracy | The jury checks three spoken claims against their articles. Preserve names and numbers, flag conflicting sources, and keep the audio informative without giving investment advice. |
| 20 | Listener fit | Give listeners meaningfully different briefings through story selection, order, depth, length, or language. Support the live wildcard. |
| 20 | Making sense of the news | Merge duplicate coverage, group articles by event, extract correct facts, and put the most important stories first. The jury checks against the answer key. |
| 15 | Scalability and cost | Report full-dataset cost and the 10× estimate. Use rules, embeddings, or smaller models where they suffice and reserve a larger model for work that needs it. |
| 10 | Listening experience | Write for the ear, with clear structure, good pacing, and a sensible length. |
| 10 | Path to product | Explain who would pay, how it fits into a newsroom's daily work, and how listeners learn the voice is synthetic. |

## Team concept

Start from a headline, give a short host introduction, and play the story in presenter or two-host debate mode. These are presentation formats available independently of listener profiles. During playback, show the active segment's source article and a supporting quote in the fact-check view. The team also sketched a general news feed, story charts or timelines, and a saved-conversation loop. Their scope and some whiteboard details are still open.

## Recommended build

1. **Select.** Ingest the full dataset, cluster coverage by event, merge duplicates, and extract key facts. Rank stories by listener needs, keeping the day's essential stories across profiles. Use lower-cost processing at this stage.
2. **Script.** Write short English segments for the listener's needs, depth, and tone. Carry article references and supporting quotes with each segment. Use spoken attribution, short sentences, and clear transitions. Debate can differ on interpretation while sharing the same checked facts.
3. **Verify.** Check each claim against the original-language article before rendering, especially names and figures. Use public releases to cross-check reporting, flag disagreement, and correct or drop unsupported claims.
4. **Render.** Read scripts aloud, audition voices, then generate audio from the verified scripts. Listen through both demo briefings. Pre-rendering both gives the demo a fallback while the same pipeline handles a fresh profile.
5. **Play.** Sync the active segment with its source and supporting quote. Include a short methods note and disclose synthetic narration.

The handoff recommends a script-to-audio pipeline and suggests OpenAI for text stages and ElevenLabs for voice. Provider selection, model IDs, account access, budget, and voice casting remain open. Confirm the dataset schema, language mix, and supplied profiles at handover. The organizer PDF governs challenge requirements where the handoff differs.

The [live discussion architecture](../architecture.mmd) gives the main host and recommended guest experts separate roles. FastAPI manages the floor, verifies claim-level citations and hands approved segments to ElevenLabs. The [engine guide](panel-engine.md) covers local setup and the judging run.

Source context supplied for this project includes the organizer's *Sourcera Track Description* and the team's *Sourcera audio briefing: build handoff*.
