"""A listener's focus ("Elon Musk and Tesla, no crypto, last 2 days") turned into filters, without an LLM.

- Exclusions: whatever follows "no", "not", "without", "except", "excluding", "nothing about", ...
- Time range: "today", "yesterday", "last/past N days", "this/last/past week", "last 24 hours".
- Terms: the rest, split on commas and "and"/"or"/"plus", with filler words ("news", "only", "about") trimmed.

An article matches a term when its people/entities name it (whole words), when its headline, people, entities,
subject, event or topics contain it (search_vector weights A and B, not a passing mention in the body), or when
its embedding is close to the whole focus text (embedding match, in recommend.py).
"""

import re
from dataclasses import dataclass, field

_EXCLUDE = re.compile(
    r"\b(?:but\s+)?(?:no|not|without|except|excluding|exclude|minus|skip|nothing\s+(?:about|on))\s+([^,;.]+)",
    re.IGNORECASE)
_TIME = [
    (re.compile(r"\b(?:in\s+the\s+|from\s+the\s+|over\s+the\s+)?(?:last|past)\s+24\s+hours\b", re.I), lambda m: 1),
    (re.compile(r"\b(?:in\s+the\s+|from\s+the\s+|over\s+the\s+)?(?:last|past)\s+(\d{1,3})\s+days?\b", re.I),
     lambda m: int(m.group(1))),
    (re.compile(r"\b(?:from\s+|of\s+)?(?:this|last|the\s+past|past)\s+week\b", re.I), lambda m: 7),
    (re.compile(r"\b(?:from\s+|of\s+)?yesterday\b", re.I), lambda m: 2),
    (re.compile(r"\b(?:from\s+|of\s+)?today\b", re.I), lambda m: 1),
]
_SPLIT = re.compile(r"\s*(?:,|;|/|&|\band\b|\bor\b|\bplus\b)\s*", re.IGNORECASE)
_FILLER = {"news", "about", "only", "just", "the", "a", "an", "on", "of", "in", "for", "from", "stories", "story",
           "latest", "updates", "update", "what", "whats", "happening", "with", "me", "i", "want", "to", "please",
           "tell", "more", "focus", "anything", "everything", "all", "some", "any", "but", "also", "too", "really",
           "everything", "articles", "article", "headlines", "give", "show", "around", "regarding", "related"}


@dataclass
class Focus:
    text: str
    terms: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    since_days: int | None = None

    @property
    def positive_text(self) -> str:
        """What the listener asked for, for embedding (exclusions and time words left out)."""
        return ", ".join(self.terms)


def _phrases(text: str) -> list[str]:
    out = []
    for part in _SPLIT.split(text):
        words = re.findall(r"[\w'&.-]+", part)
        while words and words[0].lower() in _FILLER:
            words.pop(0)
        while words and words[-1].lower() in _FILLER:
            words.pop()
        phrase = " ".join(words).strip(" .'-")
        if len(phrase) >= 2 and phrase.lower() not in {p.lower() for p in out}:
            out.append(phrase)
    return out[:10]


def parse_focus(text: str) -> Focus:
    text = " ".join(text.split())
    rest = text
    since_days = None
    for pattern, days in _TIME:
        match = pattern.search(rest)
        if match:
            since_days = since_days or days(match)
            rest = pattern.sub(" ", rest)
    exclude = []
    for match in _EXCLUDE.finditer(rest):
        exclude += _phrases(match.group(1))
    rest = _EXCLUDE.sub(" ", rest)
    return Focus(text=text, terms=_phrases(rest), exclude=exclude, since_days=since_days)


def tsquery_terms(terms: list[str], weights: str = "AB") -> str | None:
    """A `to_tsquery('simple', ...)` string that matches any of the terms in the article's headline, people,
    entities, subject, event or topics (search_vector weights A and B), not a passing mention in the body.
    Single words of 5+ letters also match as prefixes: "crypto" finds "cryptocurrency", "gold" not "goldman"."""
    parts = []
    for term in terms:
        words = [w for w in "".join(c if c.isalnum() else " " for c in term.lower()).split() if w]
        if len(words) == 1:
            parts.append(f"{words[0]}:*{weights}" if len(words[0]) >= 5 else f"{words[0]}:{weights}")
        elif words:
            parts.append("(" + " <-> ".join(f"{w}:{weights}" for w in words) + ")")
    return " | ".join(parts) or None


def word_patterns(terms: list[str]) -> list[str]:
    """Postgres regexes matching each term as whole words, case-insensitively with `~*`: "Musk" finds
    "Elon Musk", "gold" doesn't find "Goldman Sachs"."""
    out = []
    for term in terms:
        words = [w for w in "".join(c if c.isalnum() else " " for c in term).split() if w]
        if words:
            out.append(r"\m" + r"\W+".join(words) + r"\M")
    return out
