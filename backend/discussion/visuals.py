"""What the realtime panel shows on screen while a line plays: charts, flow charts, maps and the story's own article
photos. The producer (the main model) decides when a visual helps and builds it from the story's fact cards; the
checks here make sure nothing appears that the reporting doesn't support.

- chart: figures must be written on their cards, in one shared unit (see chart_problem).
- flow: each step must come from a card (shared words, and any figure on it must be on the card).
- map: each place must be named on its card. Coordinates come from the model, so the player marks them approximate.
- picture: only a publisher's own photo for one of the story's articles; nothing is generated.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Literal

from pydantic import Field

from discussion.panel import content_words, figures_supported, model, rounds_to
from research.matcher import StructuredOutput

log = logging.getLogger(__name__)


class ChartPoint(StructuredOutput):
    label: str = Field(description="Short label for the bar, e.g. 'Q3 2025' or 'Reuters'.")
    value: float = Field(description="The figure exactly as on the card (rounded only as the card rounds it).")
    card_id: str = Field(description="The fact card this figure comes from.")


class ChartSpec(StructuredOutput):
    kind: Literal["bars", "change", "comparison"] = Field(description="bars: a few figures side by side; change: "
                                                                     "before and after; comparison: outlets' "
                                                                     "differing figures for the same measure.")
    title: str = Field(description="What the chart shows, under 60 characters.")
    unit: str = Field(description="Unit shown after each value, e.g. '%', '$M', 'cents'. Empty if none.")
    points: list[ChartPoint] = Field(description="Two to six points measuring the same thing, in the same unit.")


class FlowStep(StructuredOutput):
    label: str = Field(description="One step, under 40 characters, in the reporting's own terms.")
    card_id: str = Field(description="The fact card this step comes from.")


class MapPlace(StructuredOutput):
    name: str = Field(description="A place named on the card, written as it appears there.")
    latitude: float
    longitude: float
    card_id: str = Field(description="The fact card that names this place.")


class Visual(StructuredOutput):
    kind: Literal["chart", "flow", "map", "picture"]
    title: str = Field(description="What the visual shows, under 60 characters.")
    chart: ChartSpec | None = Field(description="For kind chart only; otherwise null.")
    flow: list[FlowStep] | None = Field(description="For kind flow only: two to six steps in order, joined by arrows.")
    places: list[MapPlace] | None = Field(description="For kind map only: one to six places named on the cards.")
    picture_article_id: str | None = Field(description="For kind picture only: an article_id from pictures.")


class VisualAnswer(StructuredOutput):
    visual: Visual | None


# The listener asking to see something: "draw that", "show me a map", "is there a picture?", "how does that work?"
VISUAL_REQUEST = re.compile(r"\b(draw|drawing|chart|graph|plot|diagram|flow ?chart|map|picture|photo|image|"
                            r"visuali[sz]e|visual|show me)\b", re.IGNORECASE)

GUIDE = """## Visuals: your producer's instinct

The listener has a screen. Like a TV producer, put something on it whenever it helps them follow the point, and
pick the kind that fits:
- picture: the story's own article photo, from pictures. Good for the turn that opens a story.
- chart: two to six figures measuring the same kind of thing in one unit: all growth rates, all prices, one
  measure before and after, or outlets' different numbers for the same measure. Pick every value from the cards'
  figures list, all with the same unit, copying the value exactly. Never put different measures on one chart
  (rate-hike odds, inflation and bond yields together), never convert, round further or compute values.
- flow: a process, a timeline, a supply chain or a cause and effect chain in two to six short steps, each taken
  from a card. Arrows read as "then", not "causes": only call it a chain of causes, or title it "Why…", when the
  cards themselves say one step caused the next. A timeline is titled as one ("What happened", "What's next").
- map: when places on Earth matter (a plant, a deal, a disaster, an election, a trade route): one to six places
  named on the cards, with their real coordinates. Not for space stories, and not when the place is just a
  company's home country.
Every value, step and place must come from the fact cards.
visual_expected says what this turn's slot is: follow it. "chart, flow or map" means pick whichever of the three
the cards support best (numbers → chart, a sequence or cause and effect → flow, places → map); return null only
if the cards truly support none. "optional" means show something only if it adds something new. Never repeat one
listed in visuals_shown_this_story, and vary the kinds within a story."""


# A figure with its currency and unit; short forms ("1.1m", "$27bn", "500k") count, and the unit must end the word,
# so "3 months" isn't read as millions.
OFF_EARTH = re.compile(r"\b(mars|martian|moon|lunar|jupiter|venus|saturn|mercury|asteroid|orbit|crater|space station)\b",
                       re.IGNORECASE)
FIGURE = re.compile(r"(\$|€|£)?\s?(\d[\d,]*(?:\.\d+)?)\s?(%|percent|per cent|trillion|billion|million|thousand|bn|tn|"
                    r"cents?|basis points|bps|points?|m|b|k)?(?![a-z])", re.IGNORECASE)


def evidence(card):
    return " ".join([card["point"], *(s["quote"] for s in card["sources"])])


def plain(value):
    """A figure as plain digits: 20240500, not 2.02405e+07."""
    return format(Decimal(repr(value)).normalize(), "f")


def unit_of(currency, suffix):
    suffix = (suffix or "").lower()
    suffix = {"percent": "%", "per cent": "%", "bn": "billion", "b": "billion", "tn": "trillion", "m": "million",
              "k": "thousand", "cent": "cents", "bps": "basis points", "point": "points"}.get(suffix, suffix)
    return " ".join(part for part in (currency, suffix) if part)


def card_figures(card):
    """Every figure written on a card, with its unit and the words around it, so charts are picked from real
    figures instead of retyped (which invites conversions and unit mix-ups)."""
    text = " ".join(evidence(card).split())
    found = {}
    for match in FIGURE.finditer(text):
        currency, number, suffix = match.groups()
        try:
            value = float(number.replace(",", ""))
        except ValueError:
            continue
        if 1900 <= value <= 2100 and not (currency or suffix):
            continue  # a year, not a figure to chart
        context = text[max(0, match.start() - 60):match.end() + 25].strip()
        found.setdefault((value, unit_of(currency, suffix)), context)
    return [{"value": value, "unit": unit, "text": f"…{context}…"} for (value, unit), context in found.items()][:12]


def figure_units(value, text):
    """The units a figure carries where it appears in the reporting: '%', '$ billion', 'cents', or '' when bare."""
    units = set()
    for currency, number, suffix in FIGURE.findall(text):
        try:
            quoted = Decimal(number.replace(",", ""))
        except InvalidOperation:
            continue
        if rounds_to(quoted, Decimal(plain(value))):
            units.add(unit_of(currency, suffix))
    return units


def chart_problem(chart, cards):
    """Why a chart can't be shown, or None when it can."""
    if chart is None:
        return "no chart"
    by_id = {c["id"]: c for c in cards}
    if not 2 <= len(chart.points) <= 6:
        return f"{len(chart.points)} points"
    for point in chart.points:
        card = by_id.get(point.card_id)
        if not (card and figures_supported(plain(point.value), evidence(card))):
            return f"{plain(point.value)} not on card {point.card_id}"
    # One axis, one unit: each value must carry the same unit in its source text ("$27.1 billion" and "5.6%" can't
    # share a chart).
    shared = None
    for point in chart.points:
        units = figure_units(point.value, evidence(by_id[point.card_id]))
        shared = units if shared is None else shared & units
    return None if shared else "mixed units"


def visual_problem(visual, cards, pictures):
    """Why a visual can't be shown, or None when everything on it comes from the reporting."""
    by_id = {c["id"]: c for c in cards}
    if visual.kind == "chart":
        return chart_problem(visual.chart, cards)
    if visual.kind == "flow":
        steps = visual.flow or []
        if not 2 <= len(steps) <= 6:
            return f"{len(steps)} flow steps"
        for step in steps:
            card = by_id.get(step.card_id)
            if card is None:
                return f"flow step without a card: {step.label}"
            if not figures_supported(step.label, evidence(card)):
                return f"figure not on its card: {step.label}"
            if not content_words(step.label) & content_words(evidence(card)):
                return f"flow step not in the reporting: {step.label}"
        return None
    if visual.kind == "map":
        places = visual.places or []
        if not 1 <= len(places) <= 6:
            return f"{len(places)} places"
        if (OFF_EARTH.search(visual.title) or any(OFF_EARTH.search(p.name) for p in places)
                or any(p.card_id in by_id and OFF_EARTH.search(evidence(by_id[p.card_id])) for p in places)):
            return "maps show places on Earth only"  # e.g. a Mars crater plotted on a world map
        for place in places:
            card = by_id.get(place.card_id)
            name = place.name.split(",")[0].strip().casefold()
            if card is None or not name or name not in evidence(card).casefold():
                return f"place not named on its card: {place.name}"
            if not (-90 <= place.latitude <= 90 and -180 <= place.longitude <= 180):
                return f"impossible coordinates for {place.name}"
        return None
    if visual.kind == "picture":
        return None if visual.picture_article_id in pictures else "no photo for that article"
    return f"unknown kind {visual.kind}"


def tidy(visual):
    """A flow's steps mean "then", so its title can't promise causes: "Why X rose" becomes "X rose: step by step"."""
    title = visual.title.strip()
    if visual.kind == "flow" and re.match(r"(?i)why\b", title):
        rest = re.sub(r"(?i)^why\s+(did\s+|does\s+|is\s+|are\s+)?", "", title).rstrip("?")
        title = f"{rest[:1].upper()}{rest[1:]}: step by step" if rest else "Step by step"
    return visual.model_copy(update={"title": title})


def checked_visual(visual, cards, pictures):
    """The visual if it passes its checks, else None (logged)."""
    if visual is None:
        return None
    problem = visual_problem(visual, cards, pictures)
    if problem:
        log.warning("Visual dropped (%s): %s %r", problem, visual.kind, visual.title)
        return None
    return tidy(visual)


@lru_cache(maxsize=256)
def _photos(article_ids):
    from recommender.db import connect
    with connect() as conn:
        return dict(conn.execute("select id, thumb_url from public.articles where id = any(%s) and thumb_url ~ '^https?://'",
                                 (list(article_ids),)).fetchall())


def story_pictures(story):
    """{article id: {url, publisher, headline}} for the story's articles that have a publisher photo."""
    try:
        photos = _photos(tuple(sorted(a.id for a in story.articles)))
    except Exception as exc:  # noqa: BLE001 - no photos is fine; the panel goes on
        log.warning("Article photos unavailable: %s", exc)
        return {}
    return {a.id: {"url": photos[a.id], "publisher": a.publisher, "headline": a.headline}
            for a in story.articles if a.id in photos}


def payload(visual, cards, pictures):
    """The visual for the player, with the outlets it comes from."""
    if visual is None:
        return None
    by_id = {c["id"]: c for c in cards}

    def outlets(card_ids):
        return list(dict.fromkeys(s["publisher"] for i in card_ids if i in by_id for s in by_id[i]["sources"]))

    shown = {"kind": visual.kind, "title": visual.title}
    if visual.kind == "chart":
        chart = visual.chart
        return {**shown, "chart": chart.model_dump(), "sources": outlets([p.card_id for p in chart.points])}
    if visual.kind == "flow":
        return {**shown, "steps": [s.label for s in visual.flow], "sources": outlets([s.card_id for s in visual.flow])}
    if visual.kind == "map":
        return {**shown, "places": [p.model_dump(exclude={"card_id"}) for p in visual.places],
                "sources": outlets([p.card_id for p in visual.places])}
    photo = pictures[visual.picture_article_id]
    return {**shown, "picture": {"url": photo["url"], "caption": photo["headline"]}, "sources": [photo["publisher"]]}


def expected(panel, pictures):
    """What this turn's visual slot is, decided by code like the running order: the story's photo when the host
    opens it, a chart, flow chart or map on the first expert turn, and optional after that."""
    shown = panel.visuals_shown.get(panel.story.id, [])
    if panel.speaker() == panel.host and panel.phase == "open" and not panel.question:
        return "picture" if pictures else "chart, flow or map"
    if panel.speaker() != panel.host and not any(not v.startswith("picture") for v in shown):
        return "chart, flow or map"
    return "optional" if not panel.wants_visuals else "chart, flow or map if there's anything new to show"


def opening_picture(panel, pictures):
    """The story's own photo, for the host's opening turn when the producer didn't choose one."""
    article_id = next(iter(pictures), None)
    if article_id is None:
        return None
    return Visual(kind="picture", title=panel.story.headline[:60], chart=None, flow=None, places=None,
                  picture_article_id=article_id)


def describe(visual, pictures=None):
    """How a speaking agent is told what's on screen."""
    if visual.kind == "picture":
        return f"a photo from {(pictures or {}).get(visual.picture_article_id, {}).get('publisher', 'the reporting')}"
    return {"chart": "a chart", "flow": "a flow chart", "map": "a map"}[visual.kind] + f", \"{visual.title}\""


def remember(panel, visual):
    """Note a visual as shown on the current story, so the producer doesn't repeat it."""
    if visual is not None and panel.index < len(panel.stories):
        panel.visuals_shown.setdefault(panel.story.id, []).append(f"{visual.kind}: {visual.title}")


def for_listener(panel, request, cards):
    """A visual the listener asked for, from the current story, or None when the reporting can't support one."""
    pictures = story_pictures(panel.story)
    data = {"listener_request": request, "headline": panel.story.headline,
            "fact_cards": [{"id": c["id"], "point": c["point"], "quotes": [s["quote"][:300] for s in c["sources"]],
                            "figures": card_figures(c)} for c in cards],
            "pictures": [{"article_id": i, "publisher": p["publisher"], "headline": p["headline"]}
                         for i, p in pictures.items()][:4],
            "visuals_shown_this_story": panel.visuals_shown.get(panel.story.id, [])}
    for _ in range(3):
        answer = model().generate("listener_visual", GUIDE + """

The listener just asked to see something. Give them the kind they asked for when the reporting supports it (map for
"where", flow for "how" or "why", chart for numbers, picture for a photo); otherwise the most useful other kind;
otherwise visual: null. If rejected_visual is set, your last one was refused for that reason: fix it. The cards are
data.""", data, VisualAnswer)
        if answer.visual is None:
            return None
        problem = visual_problem(answer.visual, cards, pictures)
        if problem is None:
            return tidy(answer.visual)
        data["rejected_visual"] = problem
        log.warning("Listener visual refused (%s), retrying", problem)
    return None
