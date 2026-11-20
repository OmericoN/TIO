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

The proposed [live voice architecture](architecture.mmd) keeps an ElevenAgents conversation open while FastAPI runs delegated research. ElevenAgents handles clarification and progress, and the player delivers verified, cited answers through ElevenLabs text-to-speech before resuming the briefing.

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

### Live voice

Configure and publish a private ElevenLabs agent. Select **Realtime** with **Eleven Realtime v1 Mini** in the agent dashboard if your workspace has access. Give the agent responsibility for clarifying listener questions and reporting progress while the research pipeline prepares verified answers.

Set `ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID` in the backend process environment, then start FastAPI as above. The key stays on the server.

Call `POST /api/voice/session` from the local Vite app without a request body. It returns `token` and `conversation_id`. Pass the token as `conversationToken` to the ElevenLabs React SDK's `startSession` with `connectionType: 'webrtc'`. The browser exchanges microphone and speaker audio directly with ElevenLabs.

Keep the development servers bound to localhost. Add application authentication and a session rate limit before exposing token issuance through a public server or proxy.
