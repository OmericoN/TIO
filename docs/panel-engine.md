# Run a moderated briefing

Start FastAPI and Vite using the repository README. Use one FastAPI worker on loopback. Sessions and the recent shared evidence reviews live in that worker's memory. A restart ends active briefings.

## Demo setup

1. Set `OPENAI_API_KEY` and `ELEVENLABS_API_KEY` in the root environment file. The voice key needs text-to-speech and realtime transcription access. Give each persona a voice ID. The starter catalog can use `PANEL_VOICE_MAIN_HOST`, `PANEL_VOICE_MARKETS`, and `PANEL_VOICE_ECONOMY`. A persona's model and voice override the environment defaults.
2. For a generated briefing, select indexed stories in the news feed (see step 4). Set the organizer’s interests in Profile. Tap the detached host orb to open setup. Use the upper arc to choose 1–15 minutes and the lower arc to choose 2–4 experts, then press the central host to start. The sliders support pointer dragging and keyboard arrows, Home and End. Repeat with the second profile and enter the live wildcard’s interests under Other interests. The player sends the selected story IDs in lineup order, `minutes`, and `expert_count`. The duration guides discussion length and the host closes at a segment boundary. Expert count applies to each topic's roster; the catalog must contain enough experts. Recorded previews keep their original audio and two experts.
3. In a live briefing, tap the floating microphone deck to pause the panel and ask which source reported a figure. Tap it again to send the question and close the microphone. The text field uses the same routing. Test microphone permissions, echo cancellation and interruption latency on the demo machine. [Scribe client streaming](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/client-side-streaming)
4. To switch to event coverage, load the articles and guest personas into the configured database using the data parser. From `backend/`, run `uv run --locked python -m discussion.stories ../data_parser/data_files`. This sends report titles and leads to the configured model and writes the local event index under `.briefing/`. To include public releases, pass `--releases PATH` for a JSONL containing the Article fields in `discussion/stories.py`. Give each release a distinct ID. The event index groups the releases with related reporting.
5. After the hackathon remove the licensed input, derived index, database records, copies, exports and backups according to the data terms. Earlier Git commits containing the original articles require separate history cleanup.

## Engine handoff

For a personalized session, start both servers and open the [recommender lab through Vite](http://localhost:5173/recommender-lab). Choose a listener, click **Recommend**, pin any must-have stories, then click **Generate** and **Start discussion**. The player sends the ordered segments, article tones, and host roster to `POST /api/panel` as `briefing_plan`. Every planned story stays in order, even when selected stories exceed the requested length. Tio moderates the matched experts. A session needs at least one guest.

To exercise the matcher from the local API, send a test shortlist request with the backend running and its database and provider credentials configured.

```sh
curl -sS http://127.0.0.1:8000/api/panel -H 'Content-Type: application/json' -d '{"profile":"Explain interest rates","mode":"panel","minutes":4,"test_shortlist":true}'
```

The backend reads up to 15 articles ordered by publication time descending and then article ID. This stand-in sends the article IDs, all guest persona IDs, and `expert_count` (default two) to the existing matcher once. Changing the listener profile changes the explanation. The shortlist stays fixed until the database articles change.

For the recommender integration, call `matcher.match_personas(article_ids, candidate_agent_ids, n)` with 1–15 ranked articles and Tio excluded from the candidates. Pass its return value directly as `match_result` to `POST /api/panel`. For example, this Python payload starts a panel with an existing match.

```python
payload = {
    "profile": listener_profile,
    "minutes": 4,
    "mode": "panel",
    "match_result": matcher.match_personas(article_ids, candidate_agent_ids, 2),
}
```

The backend loads original reporting and the `main-host` persona from the database, groups the shortlisted articles across languages, and orders topics by their first recommended article. Four minutes allows two topics. The first two ranked guests stay with Tio across the briefing. With one guest, Tio hosts an interview. An empty match cannot start a session. The matched and test paths work without a local story index.

The news feed sends `story_ids` for its selected sample or indexed stories in lineup order. Omit `story_ids` when supplying a completed match or test shortlist. Its optional `ranked_guests` map is specific to that path. Each request can supply only one of `match_result`, `test_shortlist: true`, or `ranked_guests`.

An explicit `expert_count` accepts integers from two through four and requires that many distinct experts per topic. Completed matches use the first requested number of ranked guests. Omit the field to retain the matched interview behavior described above. The bundled starter catalog has two experts; three or four requires additional configured database personas and voices.

Both setup dials remain interactive in the recorded preview, whose audio remains fixed. Generated briefings from the news feed use panel mode.

FastAPI returns a session ID. Post to `/{id}/events` under the same prefix to stream newline-delimited status and result events for one turn. Start at generation zero. Each result carries the speaker, story, transcript, claim-to-quote source cards and MP3 audio. When playback ends, request the next turn with the previous segment ID in `heard` and its duration in `seconds`. Full segments enter conversation history only on acknowledgement. In realtime playback, typed and transcribed questions travel on the next turn request. A named expert answers directly. Each booked guest gets a turn, and an audible guest question takes priority over a host probe or close. Each speaker can draw on one optional signature saying on their second, fourth, and subsequent even-numbered turns.

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

Matched briefings log `session_grouping` and `session_evidence` as listener work alongside guest ranking, turn generation, verification, and voice rendering. The 15-article cap keeps the grouping input bounded as the corpus grows. Cache hits incur no model call. Include upstream matcher logs when another worker supplies the completed match.
