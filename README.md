# TIO — The Liquid Briefing

Business and finance news often covers the same event across several outlets and languages. Listeners have to piece those stories together, decide what matters to them, and track down the original reporting when they hear a claim they want to check.

TIO is a prototype for short, personalized English audio briefings with sources visible during playback.

## Project brief

TIO is built for the Sourcera track at MAIN AI Hackathon 2026 in Maastricht. The challenge uses one week of Newstex reporting in English, German, and Dutch. The demo must generate briefings for at least two supplied listener profiles, handle a live wildcard profile, and account for processing costs across the full dataset and at 10× volume.

The [full project brief](docs/project-brief.md) records the judging criteria, demo requirements, team concept, and open decisions. Keep licensed Newstex data out of Git and delete it after the hackathon.

## How the briefing works

- **Group coverage by event.** Merge duplicate reporting across outlets and languages into a single story.
- **Tailor the briefing.** Choose the stories, order, and depth for each listener profile. Tio moderates an expert panel for every listener profile.
- **Show the evidence as you listen.** Link every spoken claim and number to its source article. Show the supporting article and excerpt for the active segment, and flag conflicts between sources.

In the local **News feed**, signed-in listeners see articles ranked for their profile. The demo workspace lets you choose a listener profile. Use the tab on the right edge of each article to add it to your briefing or remove it. The tab appears on hover and keyboard focus. On touchscreens, swipe the article left to expand the tab or tap it directly. Select up to six headlines, then tap the host orb beside the dock. In the room, drag the upper arc to choose 1–15 whole minutes and the lower arc to choose 2–4 experts, then press the host to begin. The dials also support arrow keys, Home and End. Generated briefings use these settings; the recorded preview retains its fixed audio and two experts. Each speaker’s audio appears alongside its supporting sources. **Briefings** keeps the conversation, citations and audio in this browser for replay or deletion. The fictional recorded edition works without provider credentials. Use **Profile** to set appearance, interests, and favorite voices. Topic and voice edits are saved per listener in this browser. Choose the duration and expert count when setting up a generated briefing. See the [panel engine guide](docs/panel-engine.md) for provider setup and cost measurement.

The panel API can start a discussion from a completed host match or exercise the matcher with up to 15 recent database articles. See the [matcher handoff](docs/panel-engine.md#engine-handoff).

## Run locally

Install [uv](https://docs.astral.sh/uv/), [Bun](https://bun.sh/), and Node.js 22.12+ in the 22.x line or a newer supported LTS release. From the repository root, start the FastAPI backend in one terminal.

```sh
cd backend
uv sync --locked
uv run --locked uvicorn main:app --reload
```

From the repository root, start the frontend in a second terminal.

```sh
cd frontend
bun install --frozen-lockfile
bun run dev
```

Open the URL Vite prints, usually [http://localhost:5173](http://localhost:5173). Check [backend health](http://127.0.0.1:8000/api/health) or open the [interactive API docs](http://127.0.0.1:8000/docs).

For a local demo, select **Open demo in a new tab** on the sign-in screen or open [the demo workspace](http://localhost:5173/demo) directly. Demo access is available on localhost with the Vite development server.

Both servers can start without provider credentials. For generated discussions, configure the model and speaker voices in the [panel engine guide](docs/panel-engine.md). Typed questions and optional microphone input are available during generated sessions. Remove any locally saved licensed briefings after the hackathon.

## Guides

- [Project brief](docs/project-brief.md) — judging, demo requirements, and proposed pipeline
- [Backend development](docs/backend-development.md) — layout, API routes, tests, and frontend build checks
- [Article-to-host matching](docs/host-matching.md) — configuration, pipeline integration, cost estimates, and live test
- [Live voice and discussion](docs/live-voice.md) — host personas, ElevenLabs setup, speaker selection, and preview checks
- [Database model](docs/erd.md) and [database plan](docs/database-plan.md)
