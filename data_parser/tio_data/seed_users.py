"""Generate 10 demo users and their article interactions as CSV files for review.

Each user has interest categories; the articles they "read" are drawn from the articles table in those
categories, so their history lines up with their interests. A few off-interest articles are skipped or
disliked. Nothing is written to the database here.

    PYTHONPATH=. uv run --no-project --with "psycopg[binary]" --with python-dotenv \
        python -m tio_data.seed_users
"""

import csv
import random
import secrets
import string
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tio_data.config import PROJECT_DIR
from tio_data.db import connect

OUT_DIR = PROJECT_DIR / "seed"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
RNG = random.Random(20261120)
# Separate generator so adding personas leaves the articles and timestamps above unchanged.
PERSONA_RNG = random.Random(4242)

# Placeholder hosts until real persona Markdown lands in backend/editorial/personas/.
PERSONAS = {
    "nora-analyst": "Nora: calm, data-first markets analyst",
    "leo-explainer": "Leo: friendly explainer for non-experts",
    "ava-headlines": "Ava: fast, crisp headline briefings",
    "max-skeptic": "Max: contrarian who challenges the consensus (debate partner)",
}

USERS = [
    dict(default_persona="nora-analyst", first_name="Sanne", last_name="de Vries", gender="female", dob="1991-04-12", country="NL", city="Amsterdam",
         timezone="Europe/Amsterdam", preferred_language="en", secondary_languages=["nl"], occupation="Portfolio analyst",
         risk_appetite=3, listener_goal="Stay on top of markets before the open", briefing_length_minutes=10,
         preferred_listen_time="07:30", delivery_days=[1, 2, 3, 4, 5],
         interests=["MARKECON", "INTRSTRA", "ASSMNGMT", "MUTUALFD"],
         interests_text="Central banks, interest rates, inflation, fund flows, European markets"),
    dict(default_persona="ava-headlines", first_name="Lukas", last_name="Schneider", gender="male", dob="1987-09-03", country="DE", city="Munich",
         timezone="Europe/Berlin", preferred_language="en", secondary_languages=["de"], occupation="Software engineer",
         risk_appetite=4, listener_goal="Understand where AI and chip stocks are heading", briefing_length_minutes=15,
         preferred_listen_time="08:00", delivery_days=[1, 2, 3, 4, 5],
         interests=["AI", "TECHNOLO"],
         interests_text="Artificial intelligence, semiconductors, big tech, AI startups and IPOs"),
    dict(default_persona="ava-headlines", first_name="Priya", last_name="Raman", gender="female", dob="1995-01-27", country="GB", city="London",
         timezone="Europe/London", preferred_language="en", secondary_languages=[], occupation="Crypto trader",
         risk_appetite=5, listener_goal="Catch crypto moves and the macro news behind them", briefing_length_minutes=5,
         preferred_listen_time="06:45", delivery_days=[1, 2, 3, 4, 5, 6, 7],
         interests=["CRYPTOC", "MARKECON"],
         interests_text="Bitcoin, Ethereum, XRP, altcoins, crypto regulation, technical analysis"),
    dict(default_persona="nora-analyst", first_name="Daniel", last_name="Okafor", gender="male", dob="1979-06-18", country="US", city="Chicago",
         timezone="America/Chicago", preferred_language="en", secondary_languages=[], occupation="Healthcare consultant",
         risk_appetite=2, listener_goal="Follow pharma and healthcare companies I advise", briefing_length_minutes=10,
         preferred_listen_time="07:00", delivery_days=[1, 2, 3, 4, 5],
         interests=["HC", "MEDCN"],
         interests_text="Pharmaceuticals, biotech, drug approvals, hospital groups, medical devices"),
    dict(default_persona="leo-explainer", first_name="Emma", last_name="Bakker", gender="female", dob="1983-11-30", country="NL", city="Utrecht",
         timezone="Europe/Amsterdam", preferred_language="nl", secondary_languages=["en"], occupation="CFO, retail chain",
         risk_appetite=2, listener_goal="Know what is moving retail and consumer companies", briefing_length_minutes=10,
         preferred_listen_time="18:30", delivery_days=[1, 3, 5],
         interests=["RETAIL99", "EARNINGS"],
         interests_text="Retail earnings, consumer spending, supermarkets, e-commerce"),
    dict(default_persona="max-skeptic", first_name="Jonas", last_name="Weber", gender="male", dob="1990-02-14", country="DE", city="Hamburg",
         timezone="Europe/Berlin", preferred_language="de", secondary_languages=["en"], occupation="M&A associate",
         risk_appetite=3, listener_goal="Track deals, takeovers and earnings surprises", briefing_length_minutes=15,
         preferred_listen_time="07:15", delivery_days=[1, 2, 3, 4, 5],
         interests=["MERGERAC", "EARNINGS"],
         interests_text="Mergers and acquisitions, private equity, quarterly earnings, guidance changes"),
    dict(default_persona="leo-explainer", first_name="Maria", last_name="Lopez", gender="female", dob="1968-08-08", country="ES", city="Madrid",
         timezone="Europe/Madrid", preferred_language="en", secondary_languages=[], occupation="Retired teacher",
         risk_appetite=1, listener_goal="Keep my pension savings safe and understand the news", briefing_length_minutes=5,
         preferred_listen_time="09:30", delivery_days=[1, 4],
         interests=["MUTUALFD", "ASSMNGMT", "WORLDNEW"],
         interests_text="Index funds, dividends, pensions, world news that affects savings"),
    dict(default_persona="leo-explainer", first_name="Tom", last_name="Jansen", gender="male", dob="1998-12-05", country="BE", city="Antwerp",
         timezone="Europe/Brussels", preferred_language="en", secondary_languages=["nl"], occupation="Logistics planner",
         risk_appetite=3, listener_goal="Learn how industry and trade news hits the economy", briefing_length_minutes=10,
         preferred_listen_time="17:45", delivery_days=[1, 2, 3, 4, 5],
         interests=["INDUSGDS", "MARKCOMM", "NATIONAL"],
         interests_text="Manufacturing, commodities, oil, shipping, trade policy"),
    dict(default_persona="ava-headlines", first_name="Aisha", last_name="Haddad", gender="female", dob="1993-05-21", country="FR", city="Paris",
         timezone="Europe/Paris", preferred_language="en", secondary_languages=[], occupation="Journalist",
         risk_appetite=3, listener_goal="A broad daily overview of business and world news", briefing_length_minutes=15,
         preferred_listen_time="08:15", delivery_days=[1, 2, 3, 4, 5, 6],
         interests=["WORLDNEW", "BUSINESS", "AI"],
         interests_text="Geopolitics, big business stories, AI policy, global economy"),
    dict(default_persona="nora-analyst", first_name="Kevin", last_name="Müller", gender="male", dob="1985-03-09", country="CH", city="Zurich",
         timezone="Europe/Zurich", preferred_language="de", secondary_languages=["en"], occupation="Private banker",
         risk_appetite=4, listener_goal="Talking points for client calls", briefing_length_minutes=10,
         preferred_listen_time="07:00", delivery_days=[1, 2, 3, 4, 5],
         interests=["INVESTOP", "TJ", "MARKECON"],
         interests_text="Investment opportunities, stock picks, analyst ratings, macro outlook"),
]


def make_password() -> str:
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"Tio-{core}!"


def session_personas(default: str) -> str:
    """Hosts heard for one article: mostly the user's default, sometimes a two-host debate or another host."""
    roll = PERSONA_RNG.random()
    if roll < 0.65:
        return default
    if roll < 0.85:
        partner = "nora-analyst" if default == "max-skeptic" else "max-skeptic"
        return f"{default};{partner}"
    return PERSONA_RNG.choice([p for p in PERSONAS if p != default])


def existing_passwords() -> dict:
    path = OUT_DIR / "users.csv"
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["email"]: r["password"] for r in csv.DictReader(f)}


def load_articles():
    with connect() as conn:
        rows = conn.execute(
            "select id, language, categories, published_at, headline from articles order by id"
        ).fetchall()
    return [dict(id=r[0], language=r[1], categories=set(r[2] or []), published_at=r[3], headline=r[4]) for r in rows]


def seen_at(article) -> datetime:
    """A time after the article was published, within 3 days and before NOW."""
    start = max(article["published_at"], NOW - timedelta(days=45))
    end = min(start + timedelta(days=3), NOW)
    return start + (end - start) * RNG.random()


def main():
    articles = load_articles()
    OUT_DIR.mkdir(exist_ok=True)
    user_rows, interaction_rows = [], []
    used_ids = set()
    passwords = existing_passwords()

    for n, u in enumerate(USERS, start=1):
        langs = {u["preferred_language"], *u["secondary_languages"], "en"}
        on = [a for a in articles if a["language"] in langs and a["categories"] & set(u["interests"])
              and a["id"] not in used_ids]
        # Prefer recent coverage so the history looks current.
        on.sort(key=lambda a: a["published_at"], reverse=True)
        read = RNG.sample(on[:150], RNG.randint(8, 14))
        off_pool = [a for a in articles if a["language"] in langs and not a["categories"] & set(u["interests"])]
        rejected = RNG.sample(off_pool, RNG.randint(3, 5))
        used_ids.update(a["id"] for a in read)

        local = f"{u['first_name']}.{u['last_name']}".lower().replace(" ", "")
        email = unicodedata.normalize("NFKD", local).encode("ascii", "ignore").decode() + "@example.com"
        events = []
        for a in read:
            t = seen_at(a)
            frac = round(RNG.choice([1.0, 1.0, 1.0, RNG.uniform(0.4, 0.95)]), 2)
            events.append((t, a, "impression", None))
            events.append((t + timedelta(seconds=RNG.randint(3, 40)), a, "played", frac))
            t2 = t + timedelta(seconds=RNG.randint(60, 240))
            if frac >= 1.0:
                events.append((t2, a, "completed", 1.0))
            if RNG.random() < 0.35:
                events.append((t2 + timedelta(seconds=5), a, "liked", None))
            if RNG.random() < 0.25:
                events.append((t2 + timedelta(seconds=20), a, "source_opened", None))
            if RNG.random() < 0.10:
                events.append((t2 + timedelta(seconds=60), a, "shared", None))
        for a in rejected:
            t = seen_at(a)
            events.append((t, a, "impression", None))
            if RNG.random() < 0.6:
                events.append((t + timedelta(seconds=RNG.randint(2, 15)), a, "skipped",
                               round(RNG.uniform(0.02, 0.15), 2)))
            else:
                events.append((t + timedelta(seconds=RNG.randint(10, 60)), a, "disliked", None))

        hosts = {a["id"]: session_personas(u["default_persona"]) for a in read + rejected}
        events.sort(key=lambda e: e[0])
        # Mirrors refresh_profile_past_articles: consumed articles, most recent interaction first.
        consumed_types = {"played", "completed", "liked", "shared", "source_opened"}
        last = {}
        for t, a, typ, _ in events:
            if typ in consumed_types:
                last[a["id"]] = t
        past_ids = [aid for aid, _ in sorted(last.items(), key=lambda kv: kv[1], reverse=True)]

        user_rows.append(dict(
            user_no=n, email=email, password=passwords.get(email) or make_password(),
            display_name=f"{u['first_name']} {u['last_name'][0]}.", first_name=u["first_name"], last_name=u["last_name"],
            date_of_birth=u["dob"], gender=u["gender"], country=u["country"], city=u["city"], timezone=u["timezone"],
            preferred_language=u["preferred_language"], secondary_languages=";".join(u["secondary_languages"]),
            occupation=u["occupation"], risk_appetite=u["risk_appetite"], listener_goal=u["listener_goal"],
            briefing_length_minutes=u["briefing_length_minutes"], preferred_listen_time=u["preferred_listen_time"],
            delivery_days=";".join(map(str, u["delivery_days"])), interest_categories=";".join(u["interests"]),
            interests_text=u["interests_text"], onboarding_completed=True, marketing_opt_in=RNG.random() < 0.5,
            default_persona=u["default_persona"],
            personas_heard=";".join(sorted(set(";".join(hosts.values()).split(";")))),
            last_active_at=events[-1][0].isoformat(), read_article_count=len(past_ids),
            past_article_ids=";".join(past_ids),
        ))
        for t, a, typ, val in events:
            interaction_rows.append(dict(
                email=email, article_id=a["id"], article_language=a["language"],
                article_headline=a["headline"], type=typ, value="" if val is None else val, personas=hosts[a["id"]],
                created_at=t.isoformat(),
            ))

    for name, rows in (("users.csv", user_rows), ("user_interactions.csv", interaction_rows)):
        path = OUT_DIR / name
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows to {path}")


if __name__ == "__main__":
    main()
