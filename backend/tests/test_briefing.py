import random
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import psycopg

from pydantic import ValidationError

from api.briefing import GenerateRequest
from briefing.generate import generate, group_stories, load_tones, pick_hosts
from briefing.timing import (MAX_S, MIN_S, OVERHEAD_S, TRANSITION_S, Story, fit, story_seconds, total_seconds)
from main import app
from recommender.recommend import Article, Listener

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def art(id, embedding=(1.0, 0.0), pub="pub1", translation_of=None, hours=0.0, words=800):
    a = Article(id, pub, "en", translation_of, NOW - timedelta(hours=hours), 0, 0, 0, 0, 0, False, False,
                embedding=list(embedding))
    return id, (a, words)


class TimingTest(unittest.TestCase):
    def test_story_length_grows_with_outlets_and_is_capped_by_material(self) -> None:
        one = Story(["a"], {"p1"}, 800)
        three = Story(["a", "b", "c"], {"p1", "p2", "p3"}, 2400)
        self.assertEqual(story_seconds(one), 72)
        self.assertEqual(story_seconds(three), 132)
        self.assertEqual(story_seconds(Story(["a"], {"p1"}, 100)), MIN_S * 1.2)          # thin source: short segment
        self.assertEqual(story_seconds(Story(list("abcdefghij"), set("abcdefghij"), 99999)), MAX_S * 1.2)

    def test_total_adds_intro_outro_and_transitions(self) -> None:
        stories = [Story(["a"], {"p1"}, 800), Story(["b"], {"p1"}, 800)]
        self.assertEqual(total_seconds(stories), OVERHEAD_S + 2 * (72 + TRANSITION_S))
        self.assertEqual(total_seconds([]), 0)

    def test_fit_keeps_selected_first_then_ranked_order_within_time(self) -> None:
        stories = [Story([f"s{i}"], {"p"}, 800) for i in range(10)]
        stories[7].selected = True
        kept, dropped = fit(stories, minutes=3)            # 180 s: 30 + 2 × 77 = 184, within the overshoot allowance
        self.assertEqual([s.article_ids[0] for s in kept], ["s7", "s0"])
        self.assertEqual(len(dropped), 8)

    def test_selected_stories_are_kept_even_over_time(self) -> None:
        stories = [Story([f"s{i}"], {"p"}, 800, selected=True) for i in range(5)]
        kept, _ = fit(stories, minutes=1)
        self.assertEqual(len(kept), 5)

    def test_longer_podcast_keeps_more(self) -> None:
        stories = [Story([f"s{i}"], {"p"}, 800) for i in range(60)]
        counts = [len(fit(stories, m)[0]) for m in (3, 15, 30, 60)]
        self.assertEqual(counts, sorted(counts))
        self.assertGreater(counts[-1], 40)

    def test_short_debate_still_has_a_story(self) -> None:
        story = Story(["a"], {"p"}, 800)
        self.assertEqual(fit([story], minutes=1), ([story], []))


class GroupingTest(unittest.TestCase):
    def test_articles_on_one_event_form_one_story(self) -> None:
        articles = dict([art("ev1"), art("ev1-other-outlet", pub="pub2"), art("ev1-nl", (0, 1), translation_of="ev1"),
                         art("ev2", (0, 1)), art("ev2-copy", (0, 1), pub="pub3")])
        ids = ["ev1", "ev1-other-outlet", "ev1-nl", "ev2", "ev2-copy"]
        stories = group_stories(ids, articles, selected={"ev2-copy"})
        self.assertEqual([s.article_ids for s in stories], [["ev1", "ev1-other-outlet", "ev1-nl"], ["ev2", "ev2-copy"]])
        self.assertEqual(stories[0].outlets, {"pub1", "pub2"})
        self.assertEqual(stories[0].words, 1600)            # the translation adds no new material
        self.assertEqual([s.selected for s in stories], [False, True])


class GenerateTest(unittest.TestCase):
    def test_plan_uses_preset_tones_and_refill_preserves_filters(self) -> None:
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = ("moderator",)
        conn.execute.return_value.fetchall.return_value = [(144,)]
        listener = Listener([1, 0], ["en"], preferences="Explain rates")
        with (patch("briefing.generate.load_listener", return_value=listener),
              patch("briefing.generate.rank_hosts", return_value=["moderator", "joker", "calm"]),
              patch("briefing.generate.host_affinity", return_value={}),
              patch("briefing.generate.load_articles", return_value=dict([art("keep")])) as load,
              patch("briefing.generate.load_tones", return_value={"keep": "serious"}),
              patch("briefing.generate.pick_hosts", return_value=["calm"]) as pick,
              patch("briefing.generate.recommend", return_value={"article_ids": []}) as refill):
            plan = generate(conn, "u1", ["keep", "dismissed"], [], 5, days=2,
                            excluded_article_ids=["dismissed"])
        load.assert_called_once_with(conn, ["keep"])
        refill.assert_called_once_with(conn, listener, None, excluded_article_ids=["dismissed", "keep"],
                                       days=2, limit=30)
        pick.assert_called_once_with(conn, ["joker", "calm"], {"keep": "serious"}, 2)  # Tio moderates a debate
        self.assertEqual((plan["article_ids"], plan["tones"], plan["hosts"], plan["untoned_article_ids"]),
                         (["keep"], {"keep": "serious"}, ["calm"], []))


class HostTest(unittest.TestCase):
    def conn(self, humor):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = list(humor.items())
        return conn

    def test_sensitive_tone_skips_jokey_hosts_in_listener_order(self) -> None:
        conn = self.conn({"joker": 5, "wry": 3, "calm": 1, "dry": 2})
        ranked = ["joker", "wry", "calm", "dry"]
        top = dict(top_k=2)  # a pool the size of the panel is deterministic
        self.assertEqual(pick_hosts(conn, ranked, {"a": "upbeat"}, 2, **top), ["joker", "wry"])
        self.assertEqual(pick_hosts(conn, ranked, {"a": "upbeat", "b": "serious"}, 2, **top), ["wry", "calm"])
        self.assertEqual(pick_hosts(conn, ranked, {"a": "serious", "b": "somber"}, 2, **top), ["calm", "dry"])
        self.assertEqual(pick_hosts(conn, ranked, {}, 1, top_k=1), ["joker"])

    def test_hosts_vary_across_briefings_but_favour_the_top(self) -> None:
        ranked = [f"p{i}" for i in range(10)]
        conn = self.conn({i: 1 for i in ranked})
        rng = random.Random(7)
        panels = [tuple(pick_hosts(conn, ranked, {}, 2, rng=rng)) for _ in range(300)]
        seen = {p for panel in panels for p in panel}
        self.assertEqual(seen, set(ranked[:6]))                      # only the top six, and all of them come up
        counts = {p: sum(p in panel for panel in panels) for p in seen}
        self.assertGreater(counts["p0"], counts["p5"])
        self.assertTrue(all(list(panel) == sorted(panel, key=ranked.index) for panel in panels))

    def test_tones_come_from_the_article_column(self) -> None:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [("a", "somber")]
        self.assertEqual(load_tones(conn, ["a", "b"]), {"a": "somber"})
        self.assertEqual(load_tones(conn, []), {})

    def test_missing_tone_column_plans_without_tones(self) -> None:
        conn = MagicMock()
        conn.execute.side_effect = psycopg.errors.UndefinedColumn("column tone does not exist")
        with self.assertLogs("briefing.generate", level="WARNING"):
            self.assertEqual(load_tones(conn, ["a"]), {})


class EndpointTest(unittest.TestCase):
    def test_route_and_validation(self) -> None:
        self.assertIn("post", app.openapi()["paths"]["/api/briefing/generate"])
        uid = "00000000-0000-0000-0000-000000000000"
        GenerateRequest(user_id=uid, article_ids=["a"], minutes=3)
        for body in ({"user_id": uid, "article_ids": ["a"], "minutes": 10, "mode": "presenter"},
                     {"user_id": uid, "article_ids": [], "minutes": 10},
                     {"user_id": uid, "article_ids": ["a"], "minutes": 0},
                     {"user_id": uid, "article_ids": ["a"], "minutes": 10, "hosts": 2}):
            with self.assertRaises(ValidationError):
                GenerateRequest(**body)


if __name__ == "__main__":
    unittest.main()
