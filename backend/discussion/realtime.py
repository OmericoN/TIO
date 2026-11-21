"""Experimental realtime panel: a main model writes a short brief for each turn, and separate speech-to-speech
agents (OpenAI Realtime, one per persona) improvise the spoken line from it, taking turns.

While one agent speaks, the API already prepares the next brief (see api/panel.py). Facts reach the agents only
through the brief's fact cards, and every spoken line is audited afterwards against the cards."""

import hashlib
import random
import re
import json
import os
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.request import Request, urlopen

from pydantic import Field

import logging

from discussion import visuals
from discussion.panel import TURN_GUIDE, Turn, content_words, figures_supported, model, values

log = logging.getLogger(__name__)
from research.matcher import REPO_ROOT, StructuredOutput

REALTIME_MODEL = os.getenv("PANEL_REALTIME_MODEL", "gpt-realtime")
# How strongly the live voices play their characters: "extreme" (default) or "natural".
PERSONA_STYLE = os.getenv("PANEL_PERSONA_STYLE", "extreme").strip().lower()

# Appended to every live agent's instructions in "extreme" mode: a shared direction to play each persona as large
# as it will go, so the panel sounds like distinct, unmistakable characters.
EXTREME_PERFORMANCE = """# Performance: turn it all the way up
This is a show, and you are a vivid character, not a neutral newsreader. Play your persona at full intensity:
- Accent: make your accent strong and unmistakable in every sentence: its vowels, rhythm, intonation and stress.
  Keep it authentic and respectful. Never use broken English, fake grammar mistakes, slurs or cultural jokes.
- Personality: push every trait in your persona to the extreme. Warm becomes beaming; dry becomes bone-dry;
  curious becomes delighted; measured becomes slow and deliberate. Let it colour every line.
- Speech patterns: lean into them. Use your habits often and audibly, with your own pauses, fillers and emphasis.
- Delivery: big dynamic range. Change pace, pitch and volume for emphasis; let reactions be audible.
- Contrast: sound nothing like the other speakers. Listeners should know who is talking with their eyes closed.
- Limits that never change: the facts stay exactly as the brief and cards give them, you keep to your time, and on
  somber or serious stories you dial the energy down while keeping your character and accent."""
CUT_IN_CHANCE = 0.7  # how often the next guest cuts in on the first guest's answer in a story
HOST_VOICE = "marin"
GUEST_VOICES = ["cedar", "coral", "ash", "sage", "verse", "ballad", "shimmer", "echo", "alloy"]


class TurnBrief(StructuredOutput):
    goal: str = Field(description="What this turn must achieve, in one sentence.")
    take: str = Field(description="The speaker's own opinion for this turn, in one punchy sentence they'll argue "
                                  "for (a guest's verdict, or the host's provocation). Opinion, not new facts.")
    points: list[str] = Field(description="One to three points to make, as plain notes, not a script. No figures, "
                                          "names or dates that aren't in facts.")
    facts: list[str] = Field(description="Zero to three factual sentences this turn states, each restating one fact "
                                         "card's point with its exact figures, names and dates. Empty for pure "
                                         "conversation.")
    card_ids: list[str] = Field(description="Ids of the fact cards these points rely on.")
    handoff: str | None = Field(description="Exact name of the guest to hand over to at the end, or null.")
    action: Literal["respond", "clarify", "close"]
    max_seconds: int = Field(description="How long the spoken line should run, 8 to 35 seconds.")
    visual: visuals.Visual | None = Field(description="What to show on screen while this line plays, or null.")
    cut_off: bool = Field(description="True when the next speaker should cut in on this one mid-sentence: a strong "
                                      "disagreement coming, or this speaker going on too long. About one turn in "
                                      "three; never the opening or a closing.")


# OpenAI's built-in realtime voices, roughly characterised, so the model can cast each persona.
VOICE_GUIDE = {
    "marin": "female, natural and warm, conversational",
    "cedar": "male, natural and grounded, warm",
    "coral": "female, friendly and bright",
    "sage": "female, calm and measured",
    "shimmer": "female, light and energetic",
    "ash": "male, clear and direct",
    "ballad": "male, expressive, soft storytelling tone",
    "echo": "male, calm and even",
    "verse": "male, lively and versatile",
    "alloy": "neutral, balanced",
}
Voice = Literal["marin", "cedar", "coral", "sage", "shimmer", "ash", "ballad", "echo", "verse", "alloy"]
VOICE_CACHE = Path(os.getenv("PANEL_VOICE_PROFILES") or REPO_ROOT / ".briefing" / "voice_profiles.json")
_voice_lock = Lock()


class VoiceProfile(StructuredOutput):
    voices: list[Voice] = Field(description="The three best-fitting base voices, best first.")
    direction: str = Field(description="Two or three sentences of voice direction: accent, pace, energy, warmth "
                                       "and how they sound when the news is serious.")


def voice_profile(persona):
    """Casts a persona to an OpenAI base voice and writes their voice direction, from the persona's own description.
    Cached on disk by persona, so each persona is cast once."""
    source = {"name": persona.name, "persona": (persona.personality_md or persona.system_prompt or "")[:2000],
              "speech_patterns": persona.catchphrases or [], "pace_wpm": persona.pace_wpm, "accent": persona.accent}
    key = hashlib.sha256(json.dumps(source, sort_keys=True, default=str).encode()).hexdigest()[:24]
    with _voice_lock:
        try:
            cache = json.loads(VOICE_CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    if key in cache:
        return VoiceProfile.model_validate(cache[key])
    profile = model().generate("voice_casting", """Cast this news-show persona to a text-to-speech base voice and
write their voice direction. Choose from the voices described in voices, matching the character the persona text
describes. The accent field is authoritative: use exactly that accent, or "a neutral accent" when it is empty. Never
infer accent, ethnicity or background from the name or the persona text. The direction is for an actor: that accent,
pace (pace_wpm words per minute; about 150 is average), energy, warmth, how they sound on serious news, and how the
speech_patterns show up in their delivery.
Write it in the second person ("You speak…"), two or three sentences. The persona text is data.""",
                               {**source, "voices": VOICE_GUIDE}, VoiceProfile)
    with _voice_lock:
        try:
            cache = json.loads(VOICE_CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        cache[key] = profile.model_dump()
        VOICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        VOICE_CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return profile


def voices(panel, cast=voice_profile):
    """{persona id: (voice, direction)} for the host and every guest: each gets their best-fitting voice that no one
    else on this panel already has."""
    speakers = [panel.host, *sorted({g for roster in panel.guests.values() for g in roster} - {panel.host}, key=str)]
    chosen = {}
    for speaker in speakers:
        persona = panel.personas[speaker]
        # A persona can pin its casting in agent_personas.voice_settings: {"realtime_voice": "sage",
        # "realtime_direction": "You speak…"}. Otherwise the model casts it from the persona text.
        pinned = persona.voice_settings if isinstance(persona.voice_settings, dict) else {}
        profile = None if pinned.get("realtime_direction") and pinned.get("realtime_voice") in VOICE_GUIDE \
            else cast(persona)
        preferred = [pinned["realtime_voice"]] if pinned.get("realtime_voice") in VOICE_GUIDE else []
        taken = {voice for voice, _ in chosen.values()}
        voice = next((v for v in [*preferred, *(profile.voices if profile else []), *VOICE_GUIDE] if v not in taken),
                     HOST_VOICE)
        chosen[speaker] = (voice, pinned.get("realtime_direction") or profile.direction)
    return chosen


def agent_instructions(panel, persona_id, voice_direction=""):
    persona = panel.personas[persona_id]
    host = persona_id == panel.host
    patterns = "\n".join(f"- {p}" for p in persona.catchphrases or []) or "- none"
    pace = f"About {persona.pace_wpm} words a minute." if persona.pace_wpm else ""
    accent = f"Accent: {persona.accent}." if persona.accent else "Accent: neutral." 
    stories = "\n".join(f"- {s.headline}" for s in panel.stories)
    listener = (f"""
# When the listener speaks
You'll get a LISTENER QUESTION message with what they said. Answer from your latest CONTEXT UPDATE: answer a
question in your first sentence, re-explain simply if they're lost, offer to move on if they're bored. Two or three
sentences, then stop; the panel resumes on its own. If the context doesn't cover it, say so plainly.

# The only stories in this briefing
{stories}
Never talk about any other story.""" if host else "")
    host_name = panel.personas[panel.host].name
    role = (f"""# Your role: HOST
You are the host and moderator. You open the show, introduce the guests and each story, ask the guests questions,
probe disagreements, close each story and sign off.""" if host else f"""# Your role: GUEST EXPERT
{host_name} is the host. You are a guest: you answer the questions you're asked, give your expert view and react to
the other guests. You never welcome the listener, introduce yourself, the guests or a story, move the show to the
next story, wrap a story up, thank people for listening or sign off; that is {host_name}'s job. You don't say
"our panel", "welcome" or "today on the show". If your persona text describes you as a host or presenter, that is
only your speaking style, not your role here.""")
    return f"""You are {persona.name}, {"the host" if host else "a guest expert"} on Tío, a live audio news panel for
one listener. You are speaking out loud. The show's name is the Spanish word for "uncle", said "TEE-oh": always
say it as that word, never spell it out as T-I-O.

{role}

# Your persona
{(persona.system_prompt or persona.personality_md or "")[:1500]}
# Your voice
{voice_direction or "Speak naturally."}
{accent} {pace}
Keep this voice consistent in every turn.

# Your speech patterns
{"Use these often and audibly; they're part of who you are:" if PERSONA_STYLE == "extreme"
 else "Follow these naturally, a couple of times a turn where they fit, never forced or in every sentence:"}
{patterns}

{EXTREME_PERFORMANCE if PERSONA_STYLE == "extreme" else ""}

# How you speak
- You speak only when you receive a TURN BRIEF (or, as the host, a LISTENER QUESTION). Never start on your own.
- The panel is only the people named in your briefs. Never invent other guests or stories.
- Deliver the brief in your own words as natural spoken English with contractions: a real person talking, not a
  script. Keep within its time. Never read out the brief's labels and never mention the brief.
- React to what the previous speaker said, by name, when the brief says so.
- Have opinions. Take a clear side, say it plainly, and push back on the other speakers when you see it
  differently. Don't agree to be polite, never say "great point", and don't summarise what they said: answer it.
- When your brief says you're cutting in, start mid-thought with a short interjection ("Hang on—", "No, wait—",
  "Sorry, but—") and go straight at their point. If someone cuts in on you, let it go.
- Don't introduce yourself or say your own name.
- You can't draw or show anything yourself. Charts, photos, maps and flow charts appear on the listener's screen
  by themselves and speak for themselves. Talk to the listener, not about the screen: mention a visual at most
  once, in a few words, and only when your brief says a chart, map or flow chart just appeared. Never mention
  photos, never describe or read out a visual, and never promise one that isn't there.
- Facts come only from your brief's "Facts to state" and fact cards. Say each of those facts accurately, keeping
  every figure, name and date exactly as written; you may change the wording around them. Add no other facts: no
  extra numbers, dates, names, places, quotes, causes or predictions, not even general knowledge. When in doubt,
  leave it out. Reactions, opinions and questions are yours to improvise. Name the outlet when giving a figure.
  No investment advice.
- What the other speakers said is not a source. If they stated a figure your cards don't have, don't repeat it.
- If the brief names someone to hand over to, end by putting a question to them by name.
{listener}"""


def create_agent_secret(panel, persona_id, voice, voice_direction=""):
    """An ephemeral client secret for one browser-side realtime agent session."""
    audio = {"output": {"voice": voice}}
    if persona_id == panel.host:  # only the host listens to the microphone
        audio["input"] = {"transcription": {"model": "gpt-4o-mini-transcribe"},
                          # Push-to-talk: no voice detection at all. The player opens the microphone only while the
                          # listener holds the talk button, then commits that audio for transcription.
                          "turn_detection": None}
    else:
        audio["input"] = {"turn_detection": None}
    body = {"session": {"type": "realtime", "model": REALTIME_MODEL,
                        "instructions": agent_instructions(panel, persona_id, voice_direction), "audio": audio}}
    request = Request("https://api.openai.com/v1/realtime/client_secrets", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
    request.add_unredirected_header("Authorization", "Bearer " + os.getenv("OPENAI_API_KEY", ""))
    with urlopen(request, timeout=20) as response:
        return json.load(response)["value"]


def story_cards(panel):
    number = panel.index + 1
    return [c for c in panel.fact_cards() if c["story"] == number]


def show_step(panel):
    """The fixed shape of the show, decided by code rather than the model: who this turn addresses next and what the
    turn is for. Tio opens and introduces the panel, each guest answers in turn, Tio probes and closes each story,
    and Tio signs off after the last one."""
    speaker = panel.speaker()
    names = [panel.personas[i].name for i in panel.roster]
    unheard = [panel.personas[i].name for i in panel.roster if i not in panel.guest_turns and i != speaker]
    others = [n for n in names if n != panel.personas[speaker].name]
    last_story = panel.index == len(panel.stories) - 1
    if panel.question:
        return None, "Answer the listener's question directly, then hand back to the discussion."
    if speaker == panel.host and panel.phase == "open":
        target = unheard[0] if unheard else (names[0] if names else None)
        if panel.index == 0:
            guests = " and ".join(names) or "no guests today"
            return target, (f"You're opening the show. In one sentence welcome the listener to Tío and say today's "
                            f"guests are {guests}. Then introduce the first story and why it matters to them, and "
                            f"ask {target} one focused question." if target else
                            "You're opening the show: welcome the listener to Tío, then introduce the first story.")
        return target, (f"Move to the next story with a one-sentence transition, say why it matters to the listener, "
                        f"then ask {target} one focused question." if target else
                        "Move to the next story with a one-sentence transition and present it.")
    if speaker == panel.host and panel.phase == "clarify":
        target = others[0] if others else None
        return target, f"Probe a specific claim or disagreement you just heard, and put it to {target}."
    if speaker == panel.host and panel.phase == "resume":
        target = unheard[0] if unheard else (names[0] if names else None)
        return target, f"Link back to the story in a few words and bring in {target}."
    if speaker == panel.host:
        sign_off = (" This is the last story: after the takeaway, thank the guests and sign off warmly in one sentence."
                    if last_story else "")
        return None, ("Close this story in two sentences: the common ground and what's still unresolved, and one "
                      "takeaway for the listener." + sign_off)
    target = unheard[0] if unheard else None
    if target:
        return target, (f"Answer the question you were just asked from your angle, react to what was said, and end "
                        f"by asking {target} what they make of it.")
    return None, "Answer the question you were just asked from your angle and react to what was said. Don't hand over."


def make_brief(panel):
    """The orchestrator: the main model plans the next turn as a brief, from the same context the scripted panel
    uses, plus the current story's fact cards."""
    speaker = panel.speaker()
    persona = panel.personas[speaker]
    cards = story_cards(panel)
    pictures = visuals.story_pictures(panel.story)
    data = {
        "speaker": persona.name, "role": "host" if speaker == panel.host else "guest",
        "phase": "answer" if panel.question else panel.phase, "headline": panel.story.headline,
        "first_story": panel.index == 0, "stories_left": [s.headline for s in panel.stories[panel.index + 1:]],
        "listener_preferences": panel.profile, "listener_style_requests": panel.listener_style,
        "recent_turns": [line.model_dump() for line in panel.history[-12:]],
        "roster": [panel.personas[i].name for i in panel.roster], "speaker_angle": panel.angle(speaker),
        "guests_yet_to_speak": [panel.personas[i].name for i in panel.roster
                                if i not in panel.guest_turns and i != speaker],
        "story_tone": panel.story_tone(),
        "fact_cards": [{**{k: c[k] for k in ("id", "point", "status")},
                        "quotes": [s["quote"][:300] for s in c["sources"]],
                        "figures": visuals.card_figures(c)} for c in cards],
        "mode": "panel" if panel.roster else "presenter",
        "pictures": [{"article_id": i, "publisher": p["publisher"], "headline": p["headline"]}
                     for i, p in pictures.items()][:4],
        "visuals_shown_this_story": panel.visuals_shown.get(panel.story.id, []),
        "listener_wants_visuals": panel.wants_visuals,
        "visual_expected": visuals.expected(panel, pictures),
        "correction": panel.correction or None,
        "you_are_cutting_in": panel.cut_in,
    }
    instructions = TURN_GUIDE + """

## The show's structure (fixed)

data.this_turn says exactly what this turn is for in the show's running order. Plan the brief to do that and only
that, with handoff to the person it names. When role is "guest", the brief is only the guest's expert answer and
reaction: never plan a welcome, introductions, a move to another story, a wrap-up or a sign-off for a guest.

## Your job here

You are the show's producer, not the speaker. Plan the next turn for `speaker` as a brief that a voice agent will
improvise from: a goal, one to three points as notes (not a script) and the fact card ids they rely on. Follow
every rule above for what the turn should do. Only plan facts that are on the fact cards. Put the exact guest
name in handoff when the speaker should hand over. Pick max_seconds to fit the point: 8 to 15 for a reaction,
up to 35 for a fuller point.""" + "\n\n" + visuals.GUIDE
    target, direction = show_step(panel)
    data["this_turn"] = direction
    instructions += """

## Staying accurate

take is the speaker's own opinion for this turn, one punchy sentence: a guest's verdict on what the facts mean
("Google's pop is noise; Broadcom is the real winner"), or the host's provocation ("Isn't this just hype?"). When the
last speaker's take differs from this speaker's angle, the take disagrees with it. Never a fact the cards don't
have, never bland ("it's complicated").
facts are the factual sentences the speaker will state: each one restates a single fact card's point, keeping its
figures, names and dates exactly. Use at most two, and only facts not yet said in recent_turns: a turn that reacts
to the last speaker can have none. Points are conversational notes and carry no figures, names or dates of their own.
If you_are_cutting_in is true, the speaker is interrupting the last one mid-sentence: plan a short, punchy
turn that opens by cutting in on their point (8 to 15 seconds). Set cut_off for the speaker you're planning when
the next speaker in the running order would naturally jump in: a strong disagreement, a claim worth challenging,
or this speaker going on too long. Roughly one turn in three; never on the show's opening, a story's opening
question or a closing, and never twice in a row.
If correction is set, the last speaker got a figure wrong: this turn opens by putting it right in one short, friendly
sentence with the card's figure ("Quick correction: it was 5.6%, not 14%"), then carries on."""
    brief = model().generate("realtime_brief", instructions, data, TurnBrief)
    known = {c["id"] for c in cards}
    # Anything in the brief that the cards don't support is cut before an agent ever sees it.
    reporting = " ".join(visuals.evidence(c) for c in cards)
    # ...and so is any fact already said on this story, so the panel moves forward instead of repeating itself.
    # A correction may quote the wrong figure it puts right ("5.28 million barrels, not 23") and restate the fact.
    slipped = {str(v) for v in values(panel.correction)} if panel.correction else set()
    corrects = lambda f: bool(slipped & {str(v) for v in values(f)})
    facts = [f for f in brief.facts if (grounded(f, reporting + " " + panel.correction, 2) and corrects(f))
             or (grounded(f, reporting, 2) and not panel.repeated_line(f))]
    points = [p for p in brief.points if grounded(p, reporting, 0)]
    if len(facts) < len(brief.facts) or len(points) < len(brief.points):
        log.warning("Brief trimmed: dropped %s", [x for x in [*brief.facts, *brief.points] if x not in facts + points])
    visual = visuals.checked_visual(brief.visual, cards, pictures) if data["visual_expected"] != "none" else None
    if visual is None and brief.visual is not None and data["visual_expected"] == "chart, flow or map":
        # The expected visual was refused (an invented value, mixed units…): ask once more, with the reason.
        visual = visuals.for_listener(panel, f"Show a chart, flow chart or map for this point: {brief.goal}", cards)
    if visual is None and data["visual_expected"] == "picture":
        visual = visuals.opening_picture(panel, pictures)  # every story opens on its own photo when it has one
    # Openings and closings run to the end; the host's closing isn't cut, and no two cut-ins in a row.
    # A cut-in slot, like the visuals: in each story the next guest usually jumps in on the first guest's answer,
    # and the producer may add others.
    first_guest_answer = (panel.speaker() != panel.host and panel.phase == "guest" and target is not None
                          and target != panel.personas[panel.host].name and not any(
                              line.text.endswith("—") for line in panel.history))
    cut_off = ((brief.cut_off or (first_guest_answer and random.random() < CUT_IN_CHANCE))
               and panel.phase not in {"open", "close"} and not panel.cut_in and not panel.question)
    return brief.model_copy(update={"card_ids": [i for i in brief.card_ids if i in known], "handoff": target,
                                    "cut_off": cut_off,
                                    "facts": facts, "points": points or [brief.goal],
                                    "max_seconds": max(8, min(35, brief.max_seconds)),
                                    "visual": visual})


def grounded(text, reporting, shared_words):
    """Whether a brief line stays within the reporting: its figures are there, and (for facts) it shares at least
    shared_words content words with it."""
    if not figures_supported(text, reporting):
        return False
    return len(content_words(text) & content_words(reporting)) >= shared_words


def figure_slips(panel, text):
    """Figures a speaker said that the current story's cards don't support, for an on-air correction."""
    if panel.index >= len(panel.stories) or not values(text):
        return []
    reporting = " ".join(visuals.evidence(c) for c in story_cards(panel))
    return [str(v) for v in values(text) if not figures_supported(str(v), reporting)]


def render_brief(panel, brief):
    """The brief as the speaking agent receives it, with the cited cards spelled out."""
    cards = {c["id"]: c for c in story_cards(panel)}
    facts = "\n".join(f"- [{i}] {cards[i]['point']} (" + "; ".join(
        f"{s['publisher']}: \"{s['quote'][:300]}\"" for s in cards[i]["sources"]) + ")"
        for i in brief.card_ids if i in cards) or "- none: this turn is conversation only"
    points = "\n".join(f"- {p}" for p in brief.points) or "- (conversation only)"
    stated = "\n".join(f"- {f}" for f in brief.facts) or "- none: this turn states no facts"
    handoff = (f"\nEnd by putting a question to {brief.handoff} by name." if brief.handoff
               else "\nDon't hand over to anyone; just finish your point.")
    _, direction = show_step(panel)
    last = next((line for line in reversed(panel.history) if line.speaker != panel.personas[panel.speaker()].name), None)
    heard = f'\nJust said by {last.speaker}: "{last.text}"' if last else ""
    speaker = panel.speaker()
    role = ("You are the HOST." if speaker == panel.host
            else f"You are a GUEST, not the host: {panel.personas[panel.host].name} hosts. Don't welcome, introduce, "
                 "change the story or wrap up.")
    # Photos appear silently; a chart, map or flow chart gets at most a passing nod.
    on_screen = (f"\nJust appeared on the listener's screen: {visuals.describe(brief.visual)}. You may nod to it once "
                 f"in a few words (\"it's on your screen\"); don't describe it. Your point matters more."
                 if brief.visual and brief.visual.kind != "picture" else "")
    cutting = (f"\nYou're cutting in on {last.speaker} mid-sentence: open with a short interjection and go straight "
               f"at their point." if panel.cut_in and last else "")
    return f"""TURN BRIEF
{role}{cutting}{on_screen}
Story: {panel.story.headline}{heard}
Your part in the show right now: {direction}
Goal: {brief.goal}
Your take (argue for it; it's an opinion, so add no new facts): {brief.take}
Points to make, in your own words:
{points}
Facts to state (keep every figure, name and date exactly as written; add no other facts):
{stated}
Fact cards you may use:
{facts}{handoff}
Speak for at most {brief.max_seconds} seconds."""


def brief_turn(brief, text=None):
    """A Turn for the panel's history: the agent's actual words when known, otherwise the brief's points (used to
    plan the next brief ahead while the line is still being spoken)."""
    return Turn(text=text or " ".join([*brief.facts, *brief.points]), citations=[], handoff=brief.handoff,
                action=brief.action)


def context_update(panel, speaker_name=None, text=None):
    """A note for the host agent before and after each line, so it can answer the listener at any moment."""
    cards = "\n".join(f"- [{c['id']}] {c['point']}" for c in story_cards(panel)) if panel.index < len(panel.stories) \
        else "(the briefing has finished)"
    story = panel.story.headline if panel.index < len(panel.stories) else "none"
    said = f" {speaker_name} just said: \"{text}\"." if speaker_name and text else ""
    return (f"CONTEXT UPDATE (not the listener). Current story: {story}.{said}\n"
            f"Facts for this story:\n{cards}\nListener style: {'; '.join(panel.listener_style) or 'none yet'}")
