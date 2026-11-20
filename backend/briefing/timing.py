"""How long a story takes to tell, and how to fit a list of stories into the listener's chosen length.

A story (one event, with its articles from several outlets) gets 60 s plus 25 s per extra outlet, but never more
than its sources can support: about a quarter of their words, read at the hosts' pace. Clamped to 30-240 s.
A podcast adds 30 s of intro and outro and 5 s per transition; a two-host debate takes about 20 % longer.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

DEFAULT_WPM = 144          # mean pace_wpm of the personas
BASE_S, PER_SOURCE_S = 60, 25
MIN_S, MAX_S = 30, 240
MATERIAL_SHARE = 0.25      # share of the sources' words a segment can draw on
OVERHEAD_S, TRANSITION_S = 30, 5
DEBATE_FACTOR = 1.2
OVERSHOOT = 1.10           # the last story may run the podcast up to 10 % over the chosen length
FILLED = 0.90              # a plan below 90 % of the chosen length asks the recommender for more


@dataclass
class Story:
    article_ids: list[str]
    outlets: set[str] = field(default_factory=set)
    words: int = 0
    selected: bool = False


class PlannedStory(BaseModel):
    article_ids: list[str] = Field(min_length=1, max_length=340)
    estimated_seconds: int = Field(gt=0, le=300)


def story_seconds(story: Story, wpm: float = DEFAULT_WPM, mode: str = "presenter") -> float:
    seconds = BASE_S + PER_SOURCE_S * max(0, len(story.outlets) - 1)
    supported = MATERIAL_SHARE * story.words / max(wpm, 1) * 60
    seconds = max(MIN_S, min(MAX_S, min(seconds, supported)))
    return seconds * (DEBATE_FACTOR if mode == "debate" else 1.0)


def total_seconds(stories: list[Story], wpm: float = DEFAULT_WPM, mode: str = "presenter") -> float:
    if not stories:
        return 0.0
    return OVERHEAD_S + sum(story_seconds(s, wpm, mode) + TRANSITION_S for s in stories)


def fit(stories: list[Story], minutes: float, wpm: float = DEFAULT_WPM,
        mode: str = "presenter") -> tuple[list[Story], list[Story]]:
    """Selected stories first (always kept), then the rest in order while they fit. Returns (kept, dropped)."""
    budget = minutes * 60
    ordered = [s for s in stories if s.selected] + [s for s in stories if not s.selected]
    kept: list[Story] = []
    dropped: list[Story] = []
    total = OVERHEAD_S
    for story in ordered:
        cost = story_seconds(story, wpm, mode) + TRANSITION_S
        if story.selected or total + cost <= budget or (total < budget and total + cost <= budget * OVERSHOOT):
            kept.append(story)
            total += cost
        else:
            dropped.append(story)
    if not kept and dropped:
        kept.append(dropped.pop(0))
    return kept, dropped
