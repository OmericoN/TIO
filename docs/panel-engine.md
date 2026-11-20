# Run a moderated briefing

Start FastAPI and Vite using the repository README. Use one FastAPI worker on loopback. Sessions and the recent shared evidence reviews live in that worker's memory. A restart ends active briefings.

## Demo setup

1. Set `OPENAI_API_KEY` and `ELEVENLABS_API_KEY` in the root environment file. The voice key needs text-to-speech and realtime transcription access. Give each persona a voice ID. The starter catalog can use `PANEL_VOICE_MAIN_HOST`, `PANEL_VOICE_MARKETS`, and `PANEL_VOICE_ECONOMY`. A persona's model and voice override the environment defaults.
2. Include one or both fictional stories in the news feed. Under Briefing settings, select generated playback, enter an organizer profile and choose the format. Tap the detached host orb to start. Repeat with the second profile and paste the live wildcard into the same field. The player sends the selected story IDs in lineup order. The minutes setting guides discussion length. The host closes at a segment boundary.
3. Click **Enable microphone** and ask a guest which source reported a figure. The first partial transcript pauses playback and invalidates pending work. A committed transcript triggers the answer. The text field uses the same routing. Test microphone permissions, echo cancellation and interruption latency on the demo machine. [Scribe client streaming](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/client-side-streaming)
4. To switch to event coverage, load the articles and guest personas into the configured database using the data parser. From `backend/`, run `uv run --locked python -m discussion.stories ../data_parser/data_files`. This sends report titles and leads to the configured model and writes the local event index under `.briefing/`. To include public releases, pass `--releases PATH` for a JSONL containing the Article fields in `discussion/stories.py`. Give each release a distinct ID. The event index groups the releases with related reporting.
5. After the hackathon remove the licensed input, derived index, database records, copies, exports and backups according to the data terms. Earlier Git commits containing the original articles require separate history cleanup.

## Engine handoff

`POST /api/panel` accepts `profile`, `minutes`, `mode` (panel or presenter), and an optional `ranked_guests` map keyed by story ID. This map carries the teammate recommender's final choices. With the database index and no map, the existing article matcher ranks database personas for each selected story. Resolve the moderator independently by slug `main-host`.

FastAPI returns a session ID. Post to `/{id}/events` under the same prefix to stream newline-delimited status and result events for one turn. Start at generation zero. Each result carries the speaker, story, transcript, claim-to-quote source cards and MP3 audio. When playback ends, request the next turn with the previous segment ID in `heard` and its duration in `seconds`. Full segments enter conversation history only on acknowledgement.

Post a higher generation to `/{id}/interrupt` as soon as listener speech is detected. Include the active segment ID and playback offset. Send the committed question once on the next turn request. Use `/{id}/microphone` for a single-use transcription token. Delete the session when the listener stops. Each source card stays attached to its audio segment during seeking and replay.

## Evidence and limits

The event review extracts reported facts, shared claims, different measurement bases and unresolved disagreements. Each finding keeps exact original-language quotes. Each speaker generates one segment. A second model call checks every factual phrase against its mapped quotes, including claims inside questions. The engine checks article identity, quote membership and numeric tokens, allows one repair, then substitutes an uncertainty statement if support is insufficient. Unresolved discrepancies prompt a host follow-up while the turn budget permits.

The index builder links exact text copies to their original and considers the 160 most recently updated events when matching each batch. A turn retrieves passages from up to 16 reports, prioritizing releases and distinct outlets. Spot-check story grouping and three spoken claims against original articles and the organizer answer key.

## Cost measurement

Capture INFO logs from `research.openai_client` and `api.panel` during the index build and representative profile sessions. For example, from `backend/` in PowerShell, capture the indexing command's combined output with `2>&1 | Tee-Object ../.briefing/index.log` after creating the ignored directory. Start the backend with the following command to include its usage logs.

```powershell
uv run --locked python -c "import logging,uvicorn; logging.basicConfig(level=logging.INFO); uvicorn.run('main:app',host='127.0.0.1')" 2>&1 | Tee-Object ../.briefing/panel.log
```

Combine the two logs into a UTF-8 file. Supply account rates in JSON as `{"models":{"MODEL_ID":[INPUT,CACHED_INPUT,OUTPUT]},"tts_per_1000_chars":PRICE,"stt_per_hour":PRICE}`, with model prices in USD per million tokens. From `backend/` run:

```sh
uv run --locked python -m discussion.costs ../.briefing/usage.log ../.briefing/rates.json --microphone-seconds 120
```

Replace 120 with measured billable microphone seconds. For a representative subset, set `--corpus-factor` to full article count divided by sampled article count and `--listener-factor` to target sessions divided by sampled sessions. The output estimates shared processing, per-listener work, and 10× volume with fixed or proportionally increased listeners. Estimates assume comparable article length, cache coverage, listener duration, and rates. Use provider billing for retries that failed before returning usage.
