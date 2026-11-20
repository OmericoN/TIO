# Scrum Task Distribution — The Liquid Briefing

## Sprint goal

Deliver a working, demo-ready personalized audio briefing: a listener can ask or select a story, see research progress, receive a current answer grounded in cited sources, and hear a short clip with its source card. Keep the live conversation responsive while research runs in the background.

This is a short hackathon sprint, so treat the work below as one prioritized sprint backlog rather than trying to build every future product feature. The judging-critical path is accurate, cited, profile-aware briefings and reliable playback. The conversational research flow is the demo wrapper around that path.

## Team and ownership

Roles are task ownership, not silos. Everyone joins the daily Scrum and helps unblock integration. The Scrum Master role rotates at each check-in; that person tracks blockers and keeps the sprint goal visible.

| Owner | Sprint responsibility | Owns these graph components |
|---|---|---|
| **Person 1 — Conversation and client** | Keep the listener experience usable and responsive | React player; pause/resume at saved position; microphone/story context and interrupt behavior; show the active source card; display non-interrupting status updates. |
| **Person 2 — Research orchestration** | Run background research and report job state | FastAPI enqueue/current-question handling; job status and errors over SSE; async webhook or equivalent completion path; stable request/result contract. |
| **Person 3 — Evidence and verification** *(not doing the all-nighter)* | Produce traceable, freshness-checked evidence for answers | Retrieve relevant original passages; batch deduplicate/group/index evidence; draft claims with article IDs and quotes; check quotes, figures, and claim meaning; flag source conflicts; mark results current, superseded, or insufficient. |
| **Person 4 — Story selection and audio** | Turn approved, listener-relevant content into audio | Rank/select stories using listener profile and format; cache verified briefing clips; reuse cached audio or generate TTS from approved text; connect audio playback to citations/source cards. |

Person 3’s scope is deliberately bounded to a daytime handoff. If the team needs additional late-night engineering, it belongs with Persons 1, 2, and 4 after the evidence contract and handoff are complete. Person 3 can still review the final demo during their agreed hours.

## Prioritized sprint backlog

Estimate each item together during planning using relative points (1, 2, 3, 5). Keep work small enough to demonstrate independently. These are starting tickets; the team should adjust estimates after checking the current code and available APIs.

| Priority | Story / task | Owner | Acceptance criteria |
|---|---|---|---|
| **P0** | Define the shared research result and status contract | Persons 1–4, led by Person 2 | Agree on request/story IDs, status values, claims, citations, quote fields, audio reference, and error shape. Commit a small example payload that all workstreams can build against. |
| **P0** | Retrieve and package supporting evidence | Person 3 | Given a question/story, return a small set of relevant original passages with article IDs, language, source, and quote/text. Do not invent a citation when evidence is missing. |
| **P0** | Draft and verify answer claims | Person 3 | Each proposed claim and number has a supporting quote and article ID; conflicts are flagged; the result is marked `current`, `superseded`, or `insufficient_evidence`. Unsupported claims are dropped. |
| **P0** | Enqueue research and expose progress | Person 2 | A request returns a job ID and active story/question ID. The client can receive progress and terminal success/error status. A stale or superseded result is identifiable. |
| **P0** | Show a cited result and play its approved clip | Persons 1 and 4 | The listener can play/pause/resume; the active segment shows its supporting source card; only approved, current text is eligible for audio. Cached audio is preferred when available. |
| **P1** | Keep conversation available during research | Persons 1 and 2 | The listener can interrupt or clarify; progress updates do not interrupt speech; an insufficient-evidence state is communicated clearly. Use the existing voice provider if credentials and time allow. |
| **P1** | Select stories for a listener profile | Person 4 | Selection uses the profile and requested format; at least two supplied demo profiles can produce meaningfully different briefings. Preserve essential stories where practical. |
| **P1** | Deduplicate and group multilingual coverage | Persons 3 and 4 | Related articles are grouped into a story and duplicate coverage is not presented as separate events. Preserve links back to source articles. |
| **P2** | Improve research coverage and operational resilience | Persons 2 and 4 | Add relevant official releases and multilingual articles; handle retries/timeouts within available time. Keep a pre-rendered briefing as a demo fallback. |
| **P2** | Wildcard profile and polish | Persons 1 and 4 | Accept a new listener profile and format without hardcoding the supplied profiles; refine pacing and transitions if the P0 path is stable. |

## Scrum cadence for the hackathon

Use short, timeboxed check-ins rather than long status meetings:

1. **Sprint planning (20–30 min):** agree on the sprint goal, inspect the repo/data/API access, estimate the P0 backlog, and assign one owner per ticket. Identify the demo path and fallback.
2. **Daily Scrum (10 min at start of each work block):** each person says what changed, what they will finish next, and what is blocked. Update the board immediately; take technical problem-solving offline with only the needed people.
3. **Mid-sprint integration (15 min):** run the end-to-end path with the sample contract. Resolve interface mismatches before adding P1 work.
4. **Sprint review (15–20 min):** demo the listener flow using real or approved sample data; compare against acceptance criteria and the judging requirements in `README.md`.
5. **Retrospective (10 min):** identify one process fix for the remaining work block (for example, smaller PRs or earlier API integration).

Suggested board columns: **Backlog → Ready → In progress → Review/integration → Done**. Keep no more than one major task per person in progress at once.

## Handoff for Person 3 (no all-nighter)

Before their agreed stop time, Person 3 should leave a reviewable handoff in the shared repo or team notes containing:

- the evidence/result contract and one example result;
- the evidence retrieval and verification code or a clear runnable procedure;
- a concise list of verified claims, citations, source conflicts, and unresolved gaps for the demo stories;
- the meaning of each freshness/evidence status and what downstream owners should do with it;
- current blockers, commands or steps to reproduce, and the next recommended task.

Person 2 confirms the result is consumable by the research endpoint; Person 4 confirms approved text can be rendered or served; Person 1 confirms citations can be shown in the player. After that handoff, Person 3 is off duty. Do not leave an undocumented integration dependency on them overnight.

## Definition of Done

A backlog item is Done when its acceptance criteria are met, the code or artifact is in the shared project, another teammate can run or inspect it, and any contract change is communicated to affected owners. A demo-critical item is not Done if it only works on the author's machine or has no usable fallback.

The sprint is demo-ready when the team can show this path end to end:

1. Select or ask about a story for a listener profile.
2. Keep the conversation/player responsive while background research reports progress.
3. Return a current answer whose claims and numbers link to supporting source articles and quotes.
4. Reject a stale result or clearly report insufficient evidence.
5. Play or pause/resume an approved briefing clip while showing the active source card.
6. Demonstrate different story selection or presentation for at least two supplied profiles, with a pre-rendered fallback if live services fail.

## Scope and risk rules

- If the live voice integration threatens the demo, keep the research job/status API and deliver the cited briefing through the React player; voice can be a scripted or pre-rendered wrapper.
- If time is short, prioritize accurate source-linked claims, profile-aware selection, and stable playback over advanced conversational behavior.
- Keep licensed Newstex event data out of Git and delete it after the hackathon, as required by the project brief.
- Track model/provider cost across the full dataset and the 10× estimate for the presentation; prefer lower-cost processing where it meets the quality bar.
