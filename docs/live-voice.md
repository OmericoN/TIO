# Live voice and discussion

The [panel engine guide](panel-engine.md) describes the implemented moderated discussion. These notes cover the underlying speaker utilities and the [standalone ElevenAgents preview](#test-the-existing-elevenagents-preview).

In the proposed discussion flow, each host has its own persona Markdown, model settings, and ElevenLabs voice ID. FastAPI will call the selected host's model with the current headline, listener preferences, relevant excerpts, and the recent conversation. The same underlying model can power multiple hosts through separate calls with distinct instructions.

## Starter personas

The [starter catalog](../backend/editorial/personas/starters.json) includes Tio as moderator, plus the Markets Specialist and Economy Specialist. Edit their Markdown files in the same directory to change how each speaker sounds. Add a catalog entry and a matching `<slug>.md` file to add a host.

`discussion.personas.load_starter_personas()` reads the local fixtures into `AgentPersona` records. The same model accepts existing `agent_personas` rows. The fixture UUIDs are for local development. Once the database is connected, use the returned row IDs from the recommender. Fill in `llm_model`, `voice_id`, and Newstex codes for `expertise_topics` when the team chooses them.

`discussion.agents.prepare_agent_turn()` takes the recommender's ordered host IDs and the current turn context. It fills the [turn template](../backend/editorial/discussion_turn.md) with that speaker's name, persona instructions, and delivery settings. Pass the returned `instructions` and `input` separately to the model. The input carries the headline, optional article ID, listener preferences, question, recent turns, and source excerpts. Keep the persona's voice settings for rendering the verified reply.

## Set up ElevenLabs for the discussion

1. In the **Voice Library**, choose and preview a distinct English voice for each starter host. Start with two hosts, such as Markets Host and Explainer. Copy each **voice ID**. [ElevenLabs Voice Library](https://elevenlabs.io/app/voice-library)
2. In **Developers → API Keys**, create a restricted key with **Speech to Text** and **Text to Speech** access. Store it on the backend as `ELEVENLABS_API_KEY`. [API key setup](https://elevenlabs.io/docs/help-center/technical/how-do-i-authorize-myself-using-an-api-key)
3. Write each host's instructions using the [persona template](../backend/editorial/personas/template.md). In the app's host catalog, use one `agent_personas` row per host with its stable slug, expertise topics, personality Markdown, model settings, and ElevenLabs voice ID. [Current database model](erd.md)
4. Use **Scribe v3 Live** for microphone input and **Text to Speech** with each host's voice ID for spoken output. The browser opens Scribe with a single-use token issued by FastAPI. The app receives committed transcripts, then plays one verified speaker turn at a time. [Scribe browser setup](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/client-side-streaming) · [Text to Speech](https://elevenlabs.io/docs/overview/capabilities/text-to-speech)

## Choose who speaks next

The recommender ranks eligible personas for the active headline using listener preferences and embeddings. The discussion scheduler takes that ordered list and chooses the next speaker. A question addressed to an eligible host goes to that host. For a host-to-host reply, the scheduler picks the highest-ranked other host. Each host generates its own response.

Give listener speech priority over queued host turns. Pause playback on interruption, cancel superseded work where possible, and discard stale responses. Start with two hosts per headline and one optional reply to a direct question. Grow the available roster by adding persona rows and voice IDs. Bound how many hosts speak on each headline to control latency and cost.

`POST /api/discussion/next-speaker` accepts `ranked_host_ids`, a list of unique `agent_personas.id` values in best-first order. Send `addressed_host_id` for a direct question or `reply_to_host_id` for a host reply. Include an explicitly addressed host in the list after confirming they are eligible. It returns `roster` and `next_host_id`. An empty list yields an empty roster and a null next host. Backend code can also call `discussion.speakers.plan_speaker` directly with the recommender output or fixture IDs.

## Test the existing ElevenAgents preview

Create a private ElevenAgents preview agent and grant the backend key ElevenAgents access. On the agent's **Security** tab, allow the **System prompt** override and publish the settings. The app loads the [preview prompt](../backend/editorial/personas/live_host_preview.md) from the repo and sends it when opening each session. [ElevenAgents quickstart](https://elevenlabs.io/docs/eleven-agents/quickstart/) · [Prompt overrides](https://elevenlabs.io/docs/eleven-agents/customization/personalization/overrides)

Run these PowerShell commands from the repository root.

```powershell
$env:ELEVENLABS_AGENT_ID = Read-Host "ElevenLabs agent ID"
$env:ELEVENLABS_API_KEY = Read-Host "ElevenLabs API key" -MaskInput
cd backend
uv run --locked uvicorn main:app --reload
```

The workspace opens agent discussions from the host orb in the news feed. To check the standalone backend preview route, run the following in a second terminal. It prints whether the token, conversation ID, and prompt are present.

```powershell
$session = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/voice/session
[bool]($session.token -and $session.conversation_id -and $session.system_prompt)
```

`True` confirms session setup for the [preview route](../backend/api/voice.py). The briefing player uses its own session and transcription routes.

Keep the development servers bound to localhost. Add application authentication and a session rate limit before exposing token issuance through a public server or proxy.
