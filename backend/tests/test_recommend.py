import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException, Request
from pydantic import ValidationError

from api.recommend import Recommendation, RecommendRequest, recommend_articles
from main import app
from recommender.config import EMBED_MODEL
from recommender.focus import parse_focus, tsquery_terms, word_patterns
from recommender.hosts import host_affinity, rank_hosts, score_hosts
from recommender.recommend import (Article, explore, Listener, NoProfileSignal, ensure_user_embedding, interest_facets,
                                   prefer_language,
                                   profile_text, recommend, score, select_events, siblings)

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def article(id, *, score_=None, embedding=(1.0, 0.0), coverage=0.0, focus_match=False, excluded=False,
            sim_focus=0.0, sim_listener=0.3, hours_old=0.0, language="en", translation_of=None,
            publication="pub1", sim_profile=0.6) -> Article:
    a = Article(id=id, publication_id=publication, language=language, translation_of=translation_of,
                published_at=NOW - timedelta(hours=hours_old), sim_listener=sim_listener, sim_negative=0.0,
                sim_host=0.0, sim_focus=sim_focus, topic_pref=0.0, focus_match=focus_match, excluded=excluded,
                embedding=list(embedding), coverage=coverage, sim_profile=sim_profile)
    if score_ is not None:
        a.score = score_
    return a


class FakeConn:
    """Answers selects with `row` / `rows` and records every other statement."""

    def __init__(self, row=None, rows=()):
        self.row, self.rows, self.writes, self.commits = row, list(rows), [], 0

    def execute(self, sql, params=None):
        if not sql.lstrip().startswith("select"):
            self.writes.append((sql, params))
        return self

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def commit(self):
        self.commits += 1


def profile_row(*, interests="ECB decisions and the euro", goal="FX trading", country="NL", risk=4,
                embedding=None, model=None, history=None, stale=False, levels=None):
    return (interests, goal, None, country, risk, "en", ["nl"], 10,
            embedding, None, model, history, ["seen-1"], None, stale, [], levels or {})


class ListenerTest(unittest.TestCase):
    def test_profile_text_uses_the_filled_fields(self) -> None:
        text = profile_text({"interests_text": " Oil, gold ", "listener_goal": "Trading", "risk_appetite": 5})
        self.assertEqual(text, "Interests: Oil, gold\nGoal: Trading\nRisk appetite: speculative")

    def test_interests_split_into_facets(self) -> None:
        self.assertEqual(interest_facets("Currencies: the Fed, ECB; inflation and jobs data, ab"),
                         ["Currencies: the Fed", "ECB", "inflation and jobs data"])
        self.assertEqual(interest_facets(None), [])

    def test_missing_embedding_is_created_and_saved_with_the_current_model(self) -> None:
        conn = FakeConn(profile_row())
        vector, profile = ensure_user_embedding(conn, "u1", embed=lambda t: [3.0, 4.0])
        self.assertEqual(vector, [0.6, 0.8])
        self.assertEqual(profile["interest_embedding_source"], "onboarding")
        self.assertEqual(conn.writes[0][1][1:], ("onboarding", EMBED_MODEL, "u1"))

    def test_history_is_blended_into_a_hybrid(self) -> None:
        vector, profile = ensure_user_embedding(FakeConn(profile_row(history="[0, 1]")), "u1",
                                                embed=lambda t: [1.0, 0.0])
        self.assertEqual(profile["interest_embedding_source"], "hybrid")
        self.assertAlmostEqual(vector[0], vector[1])

    def test_current_embedding_is_reused_but_a_stale_one_is_rebuilt(self) -> None:
        conn = FakeConn(profile_row(embedding="[1, 0]", model=EMBED_MODEL))
        self.assertEqual(ensure_user_embedding(conn, "u1", embed=lambda t: self.fail("no embed"))[0], [1, 0])
        stale = FakeConn(profile_row(embedding="[1, 0]", model=EMBED_MODEL, stale=True))
        ensure_user_embedding(stale, "u1", embed=lambda t: [0.0, 1.0])
        self.assertEqual(len(stale.writes), 1)

    def test_radar_tilts_the_vector_toward_the_strongest_topics(self) -> None:
        topics = {"Currencies": [0.0, 1.0], "Technology": [0.0, -1.0]}
        conn = FakeConn(profile_row(levels={"Currencies": 3, "Technology": 1}))
        vector, _ = ensure_user_embedding(conn, "u1", embed=lambda t: [1.0, 0.0],
                                          embed_batch=lambda names: [topics[n] for n in names])
        self.assertGreater(vector[1], 0)  # 3 x Currencies outweighs 1 x Technology
        self.assertGreater(vector[0], vector[1])  # the profile text still leads
        radar_only = FakeConn(profile_row(interests=None, goal=None, country=None, risk=None, levels={"Technology": 2}))
        self.assertEqual(ensure_user_embedding(radar_only, "u1", embed_batch=lambda names: [[0.0, -1.0]])[0], [0.0, -1.0])

    def test_rebuild_ignores_a_current_vector(self) -> None:
        conn = FakeConn(profile_row(embedding="[1, 0]", model=EMBED_MODEL))
        self.assertEqual(ensure_user_embedding(conn, "u1", embed=lambda t: [0.0, 2.0], rebuild=True)[0], [0.0, 1.0])
        self.assertEqual(len(conn.writes), 1)

    def test_empty_profile_without_history_is_rejected(self) -> None:
        with self.assertRaises(NoProfileSignal):
            ensure_user_embedding(FakeConn(profile_row(interests=None, goal=None, country=None, risk=None)),
                                  "u1", embed=lambda t: [1.0])


class SelectionTest(unittest.TestCase):
    def test_related_coverage_cannot_displace_a_pinned_article(self) -> None:
        first = article("first")
        second = article("second", embedding=(0, 1))
        sibling = article("other-outlet", publication="pub2")
        with (patch("recommender.recommend.fetch_articles", return_value=[first, second, sibling]),
              patch("recommender.recommend.fetch_articles_by_id", return_value=[first, second])):
            result = recommend(FakeConn(), Listener([1, 0], ["en"]), pinned_article_ids=["first", "second"], limit=2)
        self.assertEqual(result["article_ids"], ["first", "second"])

    def test_score_rewards_interest_coverage_and_recency(self) -> None:
        close, old, base, covered = (article("close", sim_listener=0.6), article("old", sim_listener=0.6, hours_old=120),
                                     article("base"), article("cov", coverage=1.0))
        score([close, old, base, covered])
        self.assertGreater(close.score, old.score)
        self.assertGreater(covered.score, base.score)

    def test_exploration_varies_close_picks_but_keeps_clear_winners(self) -> None:
        import numpy as np

        def draw(seed: int, scale: float) -> list[str]:
            pool = [article("best", score_=0.9)] + [article(f"near{i}", score_=0.5 + i / 1000) for i in range(5)]
            explore(pool, np.random.default_rng(seed), scale)
            return [a.id for a in sorted(pool, key=lambda a: a.score, reverse=True)]

        self.assertEqual(draw(1, 0.0), ["best", "near4", "near3", "near2", "near1", "near0"])
        orders = [draw(seed, 0.03) for seed in range(20)]
        self.assertTrue(all(order[0] == "best" for order in orders))
        self.assertGreater(len({tuple(order) for order in orders}), 1)
        self.assertEqual(draw(7, 0.03), draw(7, 0.03))

    def test_pins_come_first_and_extend_the_budget(self) -> None:
        chosen = select_events([article("a", score_=0.9)], [article("p1"), article("p2", embedding=(0, 1))],
                               count=2, has_focus=False, primary_language="en")
        self.assertEqual([a.id for a in chosen], ["p1", "p2"])

    def test_without_focus_the_most_covered_event_gets_a_slot(self) -> None:
        chosen = select_events([article("liked", score_=0.9, sim_listener=0.6, embedding=(1, 0)),
                                article("liked2", score_=0.85, sim_listener=0.6, embedding=(0, 1)),
                                article("big", score_=0.2, coverage=1.0, embedding=(0.6, 0.8)),
                                article("one-other-outlet", score_=0.3, coverage=1 / 3, embedding=(0.8, 0.6))],
                               [], count=2, has_focus=False, primary_language="en")
        self.assertEqual([a.id for a in chosen], ["liked", "big"])  # own top story first, then the most covered

    def test_vague_profile_leads_with_the_most_covered(self) -> None:
        chosen = select_events([article("weak", score_=0.9, sim_profile=0.2, embedding=(1, 0)),
                                article("big", score_=0.2, sim_profile=0.1, coverage=1.0, embedding=(0, 1))],
                               [], count=2, has_focus=False, primary_language="en")
        self.assertEqual([a.id for a in chosen], ["big", "weak"])

    def test_focus_restricts_then_fills_with_related(self) -> None:
        chosen = select_events([article("off-topic", score_=0.99, sim_focus=0.1),
                                article("match", score_=0.3, focus_match=True, embedding=(1, 0)),
                                article("near", score_=0.2, sim_focus=0.5, embedding=(0, 1))],
                               [], count=3, has_focus=True, primary_language="en")
        self.assertEqual([a.id for a in chosen], ["match", "near"])

    def test_exclusions_never_appear_but_a_pin_wins(self) -> None:
        chosen = select_events([article("crypto", score_=0.99, excluded=True), article("ok", score_=0.1)], [],
                               count=2, has_focus=False, primary_language="en")
        self.assertNotIn("crypto", [a.id for a in chosen])
        chosen = select_events([], [article("crypto", excluded=True)], count=1, has_focus=False,
                               primary_language="en")
        self.assertEqual(chosen[0].id, "crypto")

    def test_one_version_per_translation_in_the_listener_language(self) -> None:
        kept = prefer_language([article("nl", score_=0.9, language="nl", translation_of="en", focus_match=True),
                                article("en", score_=0.4), article("solo-nl", score_=0.3, language="nl")], "en")
        self.assertEqual([(a.id, a.score, a.focus_match) for a in kept],
                         [("en", 0.9, True), ("solo-nl", 0.3, False)])

    def test_one_article_per_event(self) -> None:
        chosen = select_events([article("en", score_=0.9),
                                article("copy", score_=0.85, publication="pub2"),  # same embedding: same event
                                article("other", score_=0.5, embedding=(0, 1))],
                               [], count=2, has_focus=False, primary_language="en")
        self.assertEqual([a.id for a in chosen], ["en", "other"])

    def test_siblings_are_the_same_event_from_other_outlets_and_languages(self) -> None:
        event = article("en", score_=0.9)
        pool = [event, article("de", score_=0.5, language="de", translation_of="en", embedding=(0, 1)),
                article("other-outlet", score_=0.7, publication="pub2"),
                article("days-later", score_=0.8, hours_old=100),
                article("different", score_=0.9, embedding=(0, 1))]
        self.assertEqual([a.id for a in siblings(event, pool, {"en"})], ["days-later", "other-outlet", "de"])
        # with a time range from the focus ("last 2 days"), the article 100 hours later is another event
        self.assertEqual([a.id for a in siblings(event, pool, {"en"}, event_hours=48)], ["other-outlet", "de"])


class FocusTest(unittest.TestCase):
    def test_parse_without_an_llm(self) -> None:
        f = parse_focus("oil and gold news from the last 2 days")
        self.assertEqual((f.terms, f.exclude, f.since_days), (["oil", "gold"], [], 2))
        f = parse_focus("markets but no crypto or bitcoin")
        self.assertEqual((f.terms, f.exclude), (["markets"], ["crypto", "bitcoin"]))
        f = parse_focus("only ECB and Fed rate decisions this week, nothing about Trump")
        self.assertEqual((f.terms, f.exclude, f.since_days), (["ECB", "Fed rate decisions"], ["Trump"], 7))

    def test_terms_become_prefix_and_phrase_queries(self) -> None:
        self.assertEqual(tsquery_terms(["crypto", "Elon Musk", "gold"]),
                         "crypto:*AB | (elon:AB <-> musk:AB) | gold:AB")
        self.assertIsNone(tsquery_terms([]))
        self.assertEqual(word_patterns(["Musk", "S&P 500"]), [r"\mMusk\M", r"\mS\W+P\W+500\M"])


class HostsTest(unittest.TestCase):
    def test_affinity_follows_reactions_and_shrinks_with_few(self) -> None:
        conn = FakeConn(rows=[("h1", "liked", 4), ("h1", "completed", 2), ("h2", "disliked", 3)])
        affinity = host_affinity(conn, "u1")
        self.assertGreater(affinity["h1"], 0.5)
        self.assertLess(affinity["h2"], 0)
        self.assertEqual(host_affinity(conn, None), {})

    def test_every_persona_is_ranked_even_without_embeddings(self) -> None:
        conn = FakeConn(rows=[("a", None), ("b", None), ("c", None)])
        self.assertEqual(rank_hosts(conn, None, {"c": 0.5}, "b"), ["c", "b", "a"])

    def test_new_listener_falls_back_to_profile_fit(self) -> None:
        conn = FakeConn(rows=[("a", "[0.6, 0.8]"), ("b", "[0, 1]"), ("c", None)])
        self.assertEqual(rank_hosts(conn, [0.0, 1.0], {}, None), ["b", "a", "c"])

    def test_past_preferences_outweigh_profile_fit(self) -> None:
        rows = [("perfect-fit", 0.9), ("liked", 0.1), ("default", 0.1), ("disliked", 0.8)]
        self.assertEqual(score_hosts(rows, {"liked": 0.6, "disliked": -0.6}, "default"),
                         ["liked", "default", "perfect-fit", "disliked"])

    def test_new_listener_gets_their_three_sign_up_picks_first(self) -> None:
        rows = [("best-fit", 0.9), ("pick-1", 0.2), ("pick-2", 0.1), ("pick-3", 0.3), ("other", 0.5)]
        self.assertEqual(score_hosts(rows, {}, None, ["pick-1", "pick-2", "pick-3"])[:3], ["pick-3", "pick-1", "pick-2"])

    def test_history_takes_over_from_the_sign_up_picks(self) -> None:
        rows = [("pick", 0.2), ("loved", 0.2)]
        self.assertEqual(score_hosts(rows, {"loved": 0.5}, None, ["pick"]), ["loved", "pick"])


class EndpointTest(unittest.TestCase):
    def test_route_output_and_validation(self) -> None:
        self.assertIn("post", app.openapi()["paths"]["/api/recommend"])
        self.assertEqual(set(Recommendation.model_fields), {"article_ids", "persona_ids"})
        uid = "00000000-0000-0000-0000-000000000000"
        for body in ({}, {"profile_text": "FX news"}, {"user_id": "not-a-uuid"},  # a signed-up user is required
                     {"user_id": uid, "hosts": 2},  # every persona is always ranked; there is no count
                     {"user_id": uid, "minutes": 10}):  # the length is chosen at Generate, not here
            with self.assertRaises(ValidationError):
                RecommendRequest(**body)

    def test_remote_clients_are_refused(self) -> None:
        remote = Request({"type": "http", "client": ("192.0.2.1", 50000), "headers": []})
        with self.assertRaises(HTTPException) as caught:
            recommend_articles(RecommendRequest(user_id="00000000-0000-0000-0000-000000000000"), remote)
        self.assertEqual(caught.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
