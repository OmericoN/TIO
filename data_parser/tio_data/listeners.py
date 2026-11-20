"""Create or remove one listener account in a single command, e.g. the judges' live extra profile.

    tio_data listener add --email judge@example.com --name "Judge" --interests "..."
                          --personas rates-and-fx,everyday-economy,quick-morning-desk
                          [--goal ...] [--occupation ...] [--languages en,nl] [--minutes 8] [--risk 3] [--country NL]
    tio_data listener remove --email judge@example.com

`add` creates the Supabase Auth account (the on_auth_user_created trigger makes the profile row), fills the profile
with the three personas picked at sign-up (by slug) and prints the user_id to send to POST /api/recommend. The
interest embedding is made on the first recommendation.
"""

import secrets

from tio_data.db import connect
from tio_data.seed_db import auth_user_id


SIGN_UP_PICKS = 3


def add(email: str, name: str, interests: str, personas: list[str], goal: str | None = None,
        occupation: str | None = None, languages: list[str] = ("en",), minutes: int = 10, risk: int | None = None,
        country: str | None = None) -> str:
    languages = [l.strip() for l in languages if l.strip()] or ["en"]
    slugs = list(dict.fromkeys(p.strip() for p in personas if p.strip()))
    if len(slugs) != SIGN_UP_PICKS:
        raise SystemExit(f"Pick exactly {SIGN_UP_PICKS} different personas at sign-up (got {len(slugs)}).")
    with connect() as conn:
        found = dict(conn.execute("select slug, id from public.agent_personas where slug = any(%s)", (slugs,)).fetchall())
        unknown = [s for s in slugs if s not in found]
        if unknown:
            raise SystemExit(f"Unknown personas: {', '.join(unknown)}. See: select slug from agent_personas;")
        uid = auth_user_id(conn, email, "Tio-" + secrets.token_urlsafe(12) + "!", {"display_name": name})
        conn.execute(
            """update public.profiles set display_name = %s, interests_text = %s, listener_goal = %s, occupation = %s,
                   preferred_language = %s, secondary_languages = %s::public.lang_code[], briefing_length_minutes = %s,
                   risk_appetite = %s, country = %s, onboarding_completed = true, chosen_persona_ids = %s,
                   interest_embedding = null, interest_embedding_model = null
               where id = %s""",
            (name, interests, goal, occupation, languages[0], languages[1:], minutes, risk, country,
             [found[s] for s in slugs], uid))
        conn.commit()
    return str(uid)


def remove(email: str) -> bool:
    """Deletes the account; the profile and its interactions go with it (on delete cascade)."""
    with connect() as conn:
        deleted = conn.execute("delete from auth.users where email = %s returning id", (email,)).fetchone()
        conn.commit()
    return deleted is not None
