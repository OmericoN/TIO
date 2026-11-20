# TIO — The Liquid Briefing

## Project brief

Build a personalized audio news briefing for the Sourcera track at MAIN AI Hackathon 2026 in Maastricht (20–21 November). The listener should hear a short, natural English briefing shaped around what they need from the day's news and be able to check every spoken claim as they listen.

### Official challenge

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

### Team concept

Start from a headline, give a short host introduction, and play the story in presenter or two-host debate mode. These are presentation formats available independently of listener profiles. During playback, show the active segment's source article and a supporting quote in the fact-check view. The team also sketched a general news feed, story charts or timelines, and a saved-conversation loop. Their scope and some whiteboard details are still open.

### Recommended build

1. **Select.** Ingest the full dataset, cluster coverage by event, merge duplicates, and extract key facts. Rank stories by listener needs, keeping the day's essential stories across profiles. Use lower-cost processing at this stage.
2. **Script.** Write short English segments for the listener's needs, depth, and tone. Carry article references and supporting quotes with each segment. Use spoken attribution, short sentences, and clear transitions. Debate can differ on interpretation while sharing the same checked facts.
3. **Verify.** Check each claim against the original-language article before rendering, especially names and figures. Use public releases to cross-check reporting, flag disagreement, and correct or drop unsupported claims.
4. **Render.** Read scripts aloud, audition voices, then generate audio from the verified scripts. Listen through both demo briefings. Pre-rendering both gives the demo a fallback while the same pipeline handles a fresh profile.
5. **Play.** Sync the active segment with its source and supporting quote. Include a short methods note and disclose synthetic narration.

The handoff recommends a script-to-audio pipeline and suggests OpenAI for text stages and ElevenLabs for voice. Provider selection, model IDs, account access, budget, and voice casting remain open. Confirm the dataset schema, language mix, and supplied profiles at handover. The organizer PDF governs challenge requirements where the handoff differs.

The proposed [live discussion architecture](architecture.mmd) lets the listener and several hosts take turns. FastAPI selects the next speaker, each host's model generates its own turn, and ElevenLabs renders the verified text in that host's voice.

Source context supplied for this project includes the organizer's *Sourcera Track Description* and the team's *Sourcera audio briefing: build handoff*.

## Local development

The backend uses Python 3.12, uv, and FastAPI. The frontend uses Bun, React, TypeScript, Vite, and Tailwind CSS. Install uv, Bun, and Node.js 22.12 or newer in the 22.x line (or a newer supported LTS release).

Run the backend in one terminal from the repository root.

```sh
cd backend
uv sync --locked
uv run --locked uvicorn main:app --reload
```

Run the frontend in a second terminal from the repository root.

```sh
cd frontend
bun install --frozen-lockfile
bun run dev
```

Vite prints the frontend URL, typically `http://localhost:5173`. It forwards `/api` requests to the backend at `http://127.0.0.1:8000`. Try `/api/health` on either server for a health check, or open `http://127.0.0.1:8000/docs` for the interactive API docs.

Run `uv run --locked python -m unittest` in `backend/` for backend tests and `bun run build` in `frontend/` to type-check and build the frontend.

### Backend layout

- [`backend/main.py`](backend/main.py) creates the FastAPI app and mounts the routers under `/api`.
- [`backend/api/`](backend/api/) holds endpoint definitions. Add new endpoints to the matching feature router and register new routers in `main.py`.
- [`backend/voice/`](backend/voice/) holds the ElevenLabs client. Add ElevenLabs synthesis calls to the client when implementing verified audio rendering.
- [`backend/editorial/`](backend/editorial/framework.md) holds the written editorial framework, host persona Markdown, and listener profiles. These files are authoring references.
- [`backend/tests/`](backend/tests/) holds backend tests. Add retrieval, script generation, and verification code in a `backend/research/` package when implementing that pipeline.

| Endpoint | Route file |
| --- | --- |
| `GET /api/health` | [`backend/api/health.py`](backend/api/health.py) |
| `POST /api/voice/session` | [`backend/api/voice.py`](backend/api/voice.py) |
| `POST /api/discussion/next-speaker` | [`backend/api/discussion.py`](backend/api/discussion.py) |

### Match hosts to articles

Set `DATABASE_URL` and `OPENAI_API_KEY` in the root `.env` or the backend environment. The root `.env.example` has blank placeholders. The Postgres driver uses the system PostgreSQL client library (`libpq`).

Call the matcher from the Python pipeline after the recommender has produced a shortlist. Create it once per worker to reuse article classifications across listeners.

```python
from research.matcher import create_matcher

matcher = create_matcher()
result = matcher.match_personas(article_ids, candidate_agent_ids, n=2)
```

`result["articles"]` preserves the input sequence and pairs each `article_id` with its `desired_tone` for scripting. `result["agent_ids"]` lists suitable hosts in preference order. Check its length before starting a two-host debate. Unknown IDs raise `ValueError`. A failed OpenAI request or unusable decision raises `MatchProcessingError`.

Look up an article's delivery guidance with `config["article_tones"][desired_tone]`. Use `config["persona_tones"]` for the database values in a host's `tone` and `secondary_tones`. Both mappings live in the root [tones.json](tones.json).

Enable INFO logging for `research.openai_client` to collect token usage by stage and model. For a cost estimate, sum input, cached input, and output at their respective rates, subtracting cached tokens from total input before pricing uncached input. Scale classification by distinct uncached articles and ranking by conversations for the 10× projection.

To check live editorial accuracy, supply the actual demo UUIDs for an always-cheerful host followed by a sober host. Run this from `backend/` with the database and OpenAI key configured. It retrieves those personas, classifies three synthetic English, German, and Dutch articles, and checks the selected host and article tones.

```sh
TIO_LIVE_MATCHER_HOSTS="cheerful-host-uuid,sober-host-uuid" uv run --locked python -m unittest tests.test_matcher.LiveMatcherTest
```

### Live voice

The discussion agents live in the app. Each has its own persona Markdown, model settings, and ElevenLabs voice ID. FastAPI calls the selected host's model directly with the current headline, listener preferences, relevant excerpts, and the recent conversation. The same underlying model can power multiple hosts through separate calls with distinct instructions.

#### Starter personas

The [starter catalog](backend/editorial/personas/starters.json) contains editable placeholders for Main Host, Markets Specialist, and Economy Specialist. Edit their Markdown files in the same directory to change how each speaker sounds. Add a catalog entry and a matching `<slug>.md` file to add a host.

`discussion.personas.load_starter_personas()` reads the local fixtures into `AgentPersona` records. The same model accepts existing `agent_personas` rows. The fixture UUIDs are for local development. Once the database is connected, use the returned row IDs from the recommender. Fill in `llm_model`, `voice_id`, and Newstex codes for `expertise_topics` when the team chooses them.

`discussion.agents.prepare_agent_turn()` takes the recommender's ordered host IDs and the current turn context. It fills the [turn template](backend/editorial/discussion_turn.md) with that speaker's name, persona instructions, and delivery settings. Pass the returned `instructions` and `input` separately to the model. The input carries the headline, optional article ID, listener preferences, question, recent turns, and source excerpts. Keep the persona's voice settings for rendering the verified reply.

#### Set up ElevenLabs for the discussion

1. In the **Voice Library**, choose and preview a distinct English voice for each starter host. Start with two hosts, such as Markets Host and Explainer. Copy each **voice ID**. [ElevenLabs Voice Library](https://elevenlabs.io/app/voice-library)
2. In **Developers → API Keys**, create a restricted key with **Speech to Text** and **Text to Speech** access. Store it on the backend as `ELEVENLABS_API_KEY`. [API key setup](https://elevenlabs.io/docs/help-center/technical/how-do-i-authorize-myself-using-an-api-key)
3. Write each host's instructions using the [persona template](backend/editorial/personas/template.md). In the app's host catalog, use one `agent_personas` row per host with its stable slug, expertise topics, personality Markdown, model settings, and ElevenLabs voice ID. [Current database model](docs/erd.md)
4. Use **Scribe v3 Live** for microphone input and **Text to Speech** with each host's voice ID for spoken output. The browser opens Scribe with a single-use token issued by FastAPI. The app receives committed transcripts, then plays one verified speaker turn at a time. [Scribe browser setup](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/client-side-streaming) · [Text to Speech](https://elevenlabs.io/docs/overview/capabilities/text-to-speech)

#### Choose who speaks next

The recommender ranks eligible personas for the active headline using listener preferences and embeddings. The discussion scheduler takes that ordered list and chooses the next speaker. A question addressed to an eligible host goes to that host. For a host-to-host reply, the scheduler picks the highest-ranked other host. Each host generates its own response.

Give listener speech priority over queued host turns. Pause playback on interruption, cancel superseded work where possible, and discard stale responses. Start with two hosts per headline and one optional reply to a direct question. Grow the available roster by adding persona rows and voice IDs. Bound how many hosts speak on each headline to control latency and cost.

`POST /api/discussion/next-speaker` accepts `ranked_host_ids`, a list of unique `agent_personas.id` values in best-first order. Send `addressed_host_id` for a direct question or `reply_to_host_id` for a host reply. Include an explicitly addressed host in the list after confirming they are eligible. It returns `roster` and `next_host_id`. An empty list yields an empty roster and a null next host. Backend code can also call `discussion.speakers.plan_speaker` directly with the recommender output or fixture IDs.

#### Test the existing ElevenAgents preview

Create a private ElevenAgents preview agent and grant the backend key ElevenAgents access. On the agent's **Security** tab, allow the **System prompt** override and publish the settings. The app loads the [preview prompt](backend/editorial/personas/live_host_preview.md) from the repo and sends it when opening each session. [ElevenAgents quickstart](https://elevenlabs.io/docs/eleven-agents/quickstart/) · [Prompt overrides](https://elevenlabs.io/docs/eleven-agents/customization/personalization/overrides)

From the repository root, run this in PowerShell:

```powershell
$env:ELEVENLABS_AGENT_ID = Read-Host "ElevenLabs agent ID"
$env:ELEVENLABS_API_KEY = Read-Host "ElevenLabs API key" -MaskInput
cd backend
uv run --locked uvicorn main:app --reload
```

Start Vite as described above, then click **Start voice preview** in the local app and allow microphone access. To check the backend alone, run the following in a second terminal. It prints whether the token, conversation ID, and prompt are present.

```powershell
$session = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/voice/session
[bool]($session.token -and $session.conversation_id -and $session.system_prompt)
```

`True` confirms session setup for the [FastAPI route](backend/api/voice.py). The full discussion still needs Scribe token issuance, live turn scheduling, model execution, and verified TTS playback.

Keep the development servers bound to localhost. Add application authentication and a session rate limit before exposing token issuance through a public server or proxy.
