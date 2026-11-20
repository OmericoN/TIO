"""Load the seed users and their interactions from data_parser/seed/ into Supabase.

Creates each account through the Supabase Auth admin API (the on_auth_user_created trigger makes the profile row),
fills the profile, adds explicit topic preferences, and inserts the interactions with the hosts that voiced them.
The trigger on user_article_interactions then fills past_article_ids and past_articles_embedding.

Safe to re-run: existing accounts are reused, and a user's seeded interactions and preferences are replaced.

    PYTHONPATH=. uv run --no-project --with "psycopg[binary]" --with python-dotenv python -m tio_data.seed_db
"""

import csv
import json
import os
import urllib.error
import urllib.request

from tio_data.config import PROJECT_DIR
from tio_data.db import connect

SEED_DIR = PROJECT_DIR / "seed"

# Host casting per listener, chosen from agent_personas. `default` voices most briefings, `debate` is the
# second voice in two-host mode (a contrasting view), and `alternates` cover topics the default is weaker on.
CASTING = {
    # Portfolio analyst: check the figures and add the policy outlook.
    "sanne.devries@example.com": dict(default="numbers-desk", debate="rates-and-fx",
                                      alternates=["funds-and-etfs", "rates-and-fx"]),
    # Software engineer: pair the chip industry reporter with the numbers desk.
    "lukas.schneider@example.com": dict(default="ai-and-chip-industry", debate="numbers-desk",
                                        alternates=["curious-explainer", "earnings-and-equities"]),
    # Crypto trader: pair digital asset coverage with the rates and currency context.
    "priya.raman@example.com": dict(default="digital-assets", debate="rates-and-fx",
                                    alternates=["glasgow-scoreboard", "rates-and-fx"]),
    # Healthcare consultant, cautious: drug approvals and regulation need careful qualification.
    "daniel.okafor@example.com": dict(default="on-the-record", debate="earnings-and-equities",
                                      alternates=["earnings-and-equities", "rolling-news-desk"]),
    # Retail CFO: cover margins and add the view from the workplace.
    "emma.bakker@example.com": dict(default="earnings-and-equities", debate="workplace-brief",
                                    alternates=["after-work-anchor", "quick-morning-desk"]),
    # M&A associate: distinguish company results from the legal status of deals.
    "jonas.weber@example.com": dict(default="earnings-and-equities", debate="on-the-record",
                                    alternates=["numbers-desk", "on-the-record"]),
    # Retired teacher, risk 1, pensions: plain household language and a patient lecturer.
    "maria.lopez@example.com": dict(default="everyday-economy", debate="curious-explainer",
                                    alternates=["curious-explainer", "funds-and-etfs"]),
    # Logistics planner, industry, commodities, trade: trade strategy plus resource and worker angles.
    "tom.jansen@example.com": dict(default="policy-and-business", debate="energy-and-commodities",
                                   alternates=["energy-and-commodities", "workplace-brief"]),
    # Journalist, world news and AI: strategic overview, an evidence-checking sceptic, a tech voice.
    "aisha.haddad@example.com": dict(default="policy-and-business", debate="yorkshire-reality-check",
                                     alternates=["ai-and-chip-industry", "curious-explainer"]),
    # Private banker, risk 4, client talking points: formal banker against market energy; punchy hooks.
    "kevin.muller@example.com": dict(default="funds-and-etfs", debate="glasgow-scoreboard",
                                     alternates=["glasgow-scoreboard", "brooklyn-copy-desk"]),
}

# When a briefing uses an alternate host, prefer the one whose beat matches the article's topic.
TOPIC_HOST = {
    "CRYPTOC": "digital-assets", "AI": "ai-and-chip-industry", "TECHNOLO": "ai-and-chip-industry",
    "MERGERAC": "on-the-record", "HC": "on-the-record", "MEDCN": "on-the-record",
    "RETAIL99": "workplace-brief", "MARKCOMM": "energy-and-commodities", "INDUSGDS": "energy-and-commodities",
    "WORLDNEW": "policy-and-business", "NATIONAL": "policy-and-business",
    "MARKECON": "rates-and-fx", "INTRSTRA": "rates-and-fx",
    "INVESTOP": "glasgow-scoreboard", "TJ": "glasgow-scoreboard", "EARNINGS": "earnings-and-equities",
    "MUTUALFD": "funds-and-etfs", "ASSMNGMT": "funds-and-etfs",
}


def read_csv(name):
    with (SEED_DIR / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def auth_user_id(conn, email, password, meta):
    row = conn.execute("select id from auth.users where email = %s", (email,)).fetchone()
    if row:
        return row[0]
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/admin/users"
    key = os.environ["SUPABASE_SECRET_KEY"]
    body = json.dumps({"email": email, "password": password, "email_confirm": True, "user_metadata": meta}).encode()
    headers = {"apikey": key, "Content-Type": "application/json"}
    if not key.startswith("sb_secret_"):  # legacy service_role JWTs also go in Authorization
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)["id"]
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise SystemExit(f"Creating {email} failed: {e.code} {e.read().decode()[:300]}")
    # The API key was refused: create the account in SQL the way Supabase seed files do. The bcrypt hash and
    # email identity let the user sign in with email and password as normal.
    uid = conn.execute(
        """insert into auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
                                   raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
                                   confirmation_token, recovery_token, email_change_token_new, email_change)
           values ('00000000-0000-0000-0000-000000000000', gen_random_uuid(), 'authenticated', 'authenticated',
                   %s, extensions.crypt(%s, extensions.gen_salt('bf')), now(),
                   '{"provider": "email", "providers": ["email"]}', %s, now(), now(), '', '', '', '')
           returning id""",
        (email, password, json.dumps(meta))).fetchone()[0]
    conn.execute(
        """insert into auth.identities (id, user_id, provider_id, provider, identity_data, created_at, updated_at)
           values (gen_random_uuid(), %s, %s, 'email', %s, now(), now())""",
        (uid, str(uid), json.dumps({"sub": str(uid), "email": email, "email_verified": True})))
    return uid


def session_kind(csv_personas, csv_default):
    hosts = csv_personas.split(";")
    if len(hosts) > 1:
        return "debate"
    return "default" if hosts[0] == csv_default else "alternate"


def main():
    users = read_csv("users.csv")
    interactions = read_csv("user_interactions.csv")

    with connect() as conn:
        persona_id = dict(conn.execute("select slug, id from agent_personas").fetchall())
        missing = {s for c in CASTING.values() for s in [c["default"], c["debate"], *c["alternates"]]} - persona_id.keys()
        if missing:
            raise SystemExit(f"Personas not in agent_personas: {sorted(missing)}")
        article_ids = list({r["article_id"] for r in interactions})
        categories = dict(conn.execute(
            "select id, categories from articles where id = any(%s)", (article_ids,)).fetchall())
        if len(categories) != len(article_ids):
            raise SystemExit("Some seeded article ids are no longer in the articles table.")

        for u in users:
            email, cast = u["email"], CASTING[u["email"]]
            uid = auth_user_id(conn, email, u["password"], {"display_name": u["display_name"]})
            conn.execute(
                """update profiles set
                     display_name = %s, first_name = %s, last_name = %s, date_of_birth = %s, gender = %s,
                     country = %s, city = %s, timezone = %s, preferred_language = %s,
                     secondary_languages = %s::lang_code[], occupation = %s, risk_appetite = %s,
                     listener_goal = %s, briefing_length_minutes = %s, preferred_listen_time = %s,
                     delivery_days = %s, default_persona_id = %s, interests_text = %s,
                     onboarding_completed = %s, marketing_opt_in = %s, last_active_at = %s
                   where id = %s""",
                (u["display_name"], u["first_name"], u["last_name"], u["date_of_birth"], u["gender"],
                 u["country"], u["city"], u["timezone"], u["preferred_language"],
                 [x for x in u["secondary_languages"].split(";") if x], u["occupation"], int(u["risk_appetite"]),
                 u["listener_goal"], int(u["briefing_length_minutes"]), u["preferred_listen_time"],
                 [int(x) for x in u["delivery_days"].split(";")], persona_id[cast["default"]], u["interests_text"],
                 u["onboarding_completed"] == "True", u["marketing_opt_in"] == "True", u["last_active_at"], uid))

            conn.execute("delete from user_topic_preferences where user_id = %s", (uid,))
            for code in u["interest_categories"].split(";"):
                conn.execute("insert into user_topic_preferences (user_id, category_code, weight, source) "
                             "values (%s, %s, 1, 'explicit')", (uid, code))

            conn.execute("delete from user_article_interactions where user_id = %s", (uid,))
            rows, alt_turn, hosts_for = [], 0, {}
            for r in (x for x in interactions if x["email"] == email):
                aid = r["article_id"]
                if aid not in hosts_for:
                    kind = session_kind(r["personas"], u["default_persona"])
                    if kind == "default":
                        hosts = [cast["default"]]
                    elif kind == "debate":
                        hosts = [cast["default"], cast["debate"]]
                    else:
                        topical = [TOPIC_HOST.get(c) for c in categories[aid]]
                        match = next((h for h in topical if h in cast["alternates"]), None)
                        if match is None:
                            match = cast["alternates"][alt_turn % len(cast["alternates"])]
                            alt_turn += 1
                        hosts = [match]
                    hosts_for[aid] = [persona_id[h] for h in hosts]
                rows.append((uid, aid, r["type"], float(r["value"]) if r["value"] else None,
                             hosts_for[aid], r["created_at"]))
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into user_article_interactions (user_id, article_id, type, value, persona_ids, created_at) "
                    "values (%s, %s, %s, %s, %s, %s)", rows)
            print(f"{email}: {len(rows)} interactions, default host {cast['default']}")


if __name__ == "__main__":
    main()
