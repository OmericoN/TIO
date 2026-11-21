"""Experimental realtime panel: a main model writes a short brief for each turn, and separate speech-to-speech
agents (OpenAI Realtime, one per persona) improvise the spoken line from it, taking turns.

While one agent speaks, the API already prepares the next brief (see api/panel.py). Facts reach the agents only
through the brief's fact cards, and every spoken line is audited afterwards against the cards."""

import hashlib
import json
import os
from typing import Literal
from urllib.request import Request, urlopen

from pydantic import Field

from discussion.panel import TURN_GUIDE, Turn, model
from research.matcher import StructuredOutput

REALTIME_MODEL = os.getenv("PANEL_REALTIME_MODEL", "gpt-realtime")
HOST_VOICE = "marin"
GUEST_VOICES = ["cedar", "coral", "ash", "sage", "verse", "ballad", "shimmer", "echo", "alloy"]


class TurnBrief(StructuredOutput):
    goal: str = Field(description="What this turn must achieve, in one sentence.")
    points: list[str] = Field(description="One to three points to make, as plain notes, not a script.")
    card_ids: list[str] = Field(description="Ids of the fact cards these points rely on.")
    handoff: str | None = Field(description="Exact name of the guest to hand over to at the end, or null.")
    action: Literal["respond", "clarify", "close"]
    max_seconds: int = Field(description="How long the spoken line should run, 8 to 35 seconds.")


def voices(panel):
    """A distinct OpenAI voice per speaker: the host always gets the same one, guests a stable pick by persona."""
    chosen = {panel.host: HOST_VOICE}
    guests = sorted({g for roster in panel.guests.values() for g in roster} - {panel.host}, key=str)
    for guest in guests:
        start = int(hashlib.sha256(str(guest).encode()).hexdigest(), 16) % len(GUEST_VOICES)
        chosen[guest] = next(v for k in range(len(GUEST_VOICES))
                             if (v := GUEST_VOICES[(start + k) % len(GUEST_VOICES)]) not in chosen.values())
    return chosen


def agent_instructions(panel, persona_id):
    persona = panel.personas[persona_id]
    host = persona_id == panel.host
    habits = "; ".join(persona.catchphrases or [])
    stories = "\n".join(f"- {s.headline}" for s in panel.stories)
    listener = (f"""
# When the listener speaks
You'll get a LISTENER QUESTION message with what they said. Answer from your latest CONTEXT UPDATE: answer a
question in your first sentence, re-explain simply if they're lost, offer to move on if they're bored. Two or three
sentences, then stop; the panel resumes on its own. If the context doesn't cover it, say so plainly.

# The only stories in this briefing
{stories}
Never talk about any other story.""" if host else "")
    return f"""You are {persona.name}, {"the host" if host else "a guest expert"} on TIO, a live audio news panel for
one listener. You are speaking out loud.

# Your persona
{(persona.system_prompt or persona.personality_md or "")[:1500]}
Verbal habits: {habits or "none"}

# How you speak
- You speak only when you receive a TURN BRIEF (or, as the host, a LISTENER QUESTION). Never start on your own.
- The panel is only the people named in your briefs. Never invent other guests or stories.
- Deliver the brief in your own words as natural spoken English with contractions: a real person talking, not a
  script. Keep within its time. Never read out the brief's labels and never mention the brief.
- React to what the previous speaker said, by name, when the brief says so.
- Don't introduce yourself or say your own name.
- State only facts from the brief's fact cards: no figures, names or dates from anywhere else. Name the outlet when
  giving a figure and round honestly. No investment advice.
- If the brief names someone to hand over to, end by putting a question to them by name.
{listener}"""


def create_agent_secret(panel, persona_id, voice):
    """An ephemeral client secret for one browser-side realtime agent session."""
    audio = {"output": {"voice": voice}}
    if persona_id == panel.host:  # only the host listens to the microphone
        audio["input"] = {"transcription": {"model": "gpt-4o-mini-transcribe"},
                          # Never reply or stop on its own: background noise or the speakers' own echo would set it
                          # off with no context. The player decides, once a real question has been transcribed.
                          "turn_detection": {"type": "semantic_vad", "eagerness": "low",
                                             "create_response": False, "interrupt_response": False}}
    else:
        audio["input"] = {"turn_detection": None}
    body = {"session": {"type": "realtime", "model": REALTIME_MODEL,
                        "instructions": agent_instructions(panel, persona_id), "audio": audio}}
    request = Request("https://api.openai.com/v1/realtime/client_secrets", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
    request.add_unredirected_header("Authorization", "Bearer " + os.getenv("OPENAI_API_KEY", ""))
    with urlopen(request, timeout=20) as response:
        return json.load(response)["value"]


def story_cards(panel):
    number = panel.index + 1
    return [c for c in panel.fact_cards() if c["story"] == number]


def make_brief(panel):
    """The orchestrator: the main model plans the next turn as a brief, from the same context the scripted panel
    uses, plus the current story's fact cards."""
    speaker = panel.speaker()
    persona = panel.personas[speaker]
    cards = story_cards(panel)
    data = {
        "speaker": persona.name, "role": "host" if speaker == panel.host else "guest",
        "phase": "answer" if panel.question else panel.phase, "headline": panel.story.headline,
        "first_story": panel.index == 0, "stories_left": [s.headline for s in panel.stories[panel.index + 1:]],
        "listener_preferences": panel.profile, "listener_style_requests": panel.listener_style,
        "recent_turns": [line.model_dump() for line in panel.history[-12:]],
        "roster": [panel.personas[i].name for i in panel.roster], "speaker_angle": panel.angle(speaker),
        "guests_yet_to_speak": [panel.personas[i].name for i in panel.roster
                                if i not in panel.guest_turns and i != speaker],
        "story_tone": panel.story_tone(), "fact_cards": [{k: c[k] for k in ("id", "point", "status")} for c in cards],
        "mode": "panel" if panel.roster else "presenter",
    }
    instructions = TURN_GUIDE + """

## Your job here

You are the show's producer, not the speaker. Plan the next turn for `speaker` as a brief that a voice agent will
improvise from: a goal, one to three points as notes (not a script) and the fact card ids they rely on. Follow
every rule above for what the turn should do. Only plan facts that are on the fact cards. Put the exact guest
name in handoff when the speaker should hand over. Pick max_seconds to fit the point: 8 to 15 for a reaction,
up to 35 for a fuller point."""
    brief = model().generate("realtime_brief", instructions, data, TurnBrief)
    known = {c["id"] for c in cards}
    return brief.model_copy(update={"card_ids": [i for i in brief.card_ids if i in known],
                                    "max_seconds": max(8, min(35, brief.max_seconds))})


def render_brief(panel, brief):
    """The brief as the speaking agent receives it, with the cited cards spelled out."""
    cards = {c["id"]: c for c in story_cards(panel)}
    facts = "\n".join(f"- [{i}] {cards[i]['point']} (" + "; ".join(
        f"{s['publisher']}: \"{s['quote'][:300]}\"" for s in cards[i]["sources"]) + ")"
        for i in brief.card_ids if i in cards) or "- none: this turn is conversation only"
    points = "\n".join(f"- {p}" for p in brief.points)
    handoff = f"\nEnd by putting a question to {brief.handoff} by name." if brief.handoff else ""
    return f"""TURN BRIEF
Story: {panel.story.headline}
Goal: {brief.goal}
Points to make, in your own words:
{points}
Fact cards you may use:
{facts}{handoff}
Speak for at most {brief.max_seconds} seconds."""


def brief_turn(brief, text=None):
    """A Turn for the panel's history: the agent's actual words when known, otherwise the brief's points (used to
    plan the next brief ahead while the line is still being spoken)."""
    return Turn(text=text or " ".join(brief.points), citations=[], handoff=brief.handoff, action=brief.action)


def context_update(panel, speaker_name=None, text=None):
    """A note for the host agent before and after each line, so it can answer the listener at any moment."""
    cards = "\n".join(f"- [{c['id']}] {c['point']}" for c in story_cards(panel)) if panel.index < len(panel.stories) \
        else "(the briefing has finished)"
    story = panel.story.headline if panel.index < len(panel.stories) else "none"
    said = f" {speaker_name} just said: \"{text}\"." if speaker_name and text else ""
    return (f"CONTEXT UPDATE (not the listener). Current story: {story}.{said}\n"
            f"Facts for this story:\n{cards}\nListener style: {'; '.join(panel.listener_style) or 'none yet'}")
