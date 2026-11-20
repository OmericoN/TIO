import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from pydantic import ValidationError

from api.briefing import GenerateRequest
from briefing.generate import generate, group_stories, run_matcher
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
        self.assertEqual(story_seconds(one), 60)
        self.assertEqual(story_seconds(three), 110)
        self.assertEqual(story_seconds(Story(["a"], {"p1"}, 100)), MIN_S)          # thin source: short segment
        self.assertEqual(story_seconds(Story(list("abcdefghij"), set("abcdefghij"), 99999)), MAX_S)
        self.assertAlmostEqual(story_seconds(one, mode="debate"), 72)

    def test_total_adds_intro_outro_and_transitions(self) -> None:
        stories = [Story(["a"], {"p1"}, 800), Story(["b"], {"p1"}, 800)]
        self.assertEqual(total_seconds(stories), OVERHEAD_S + 2 * (60 + TRANSITION_S))
        self.assertEqual(total_seconds([]), 0)

    def test_fit_keeps_selected_first_then_ranked_order_within_time(self) -> None:
        stories = [Story([f"s{i}"], {"p"}, 800) for i in range(10)]
        stories[7].selected = True
        kept, dropped = fit(stories, minutes=3)            # 180 s: 30 + 2 × 65 = 160, a third would be 225
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
        self.assertEqual(fit([story], minutes=1, mode="debate"), ([story], []))


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
    def test_vetoed_guests_are_not_used_and_refill_preserves_filters(self) -> None:
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = ("moderator",)
        conn.execute.return_value.fetchall.return_value = [(144,)]
        listener = Listener([1, 0], ["en"], preferences="Explain rates")
        matcher = Mock()
        matcher.match_personas.return_value = {
            "articles": [{"article_id": "keep", "desired_tone": "serious"}], "agent_ids": [],
        }
        with (patch("briefing.generate.load_listener", return_value=listener),
              patch("briefing.generate.rank_hosts", return_value=["moderator", "guest"]),
              patch("briefing.generate.host_affinity", return_value={}),
              patch("briefing.generate.load_articles", return_value=dict([art("keep")])) as load,
              patch("briefing.generate.recommend", return_value={"article_ids": []}) as refill):
            plan = generate(conn, "u1", ["keep", "dismissed"], [], 5, "debate", matcher=matcher,
                            days=2, excluded_article_ids=["dismissed"])
        load.assert_called_once_with(conn, ["keep"])
        refill.assert_called_once_with(conn, listener, None, excluded_article_ids=["dismissed", "keep"],
                                       days=2, limit=30)
        matcher.match_personas.assert_called_once_with(["keep"], ["guest"], 1)
        self.assertEqual((plan["article_ids"], plan["tones"], plan["hosts"], plan["hosts_from_matcher"]),
                         (["keep"], {"keep": "serious"}, [], False))


class MatcherTest(unittest.TestCase):
    def test_chunks_merge_tones_and_require_every_chunk_to_accept(self) -> None:
        class Matcher:
            calls = 0
            def match_personas(self, ids, candidates, n):
                Matcher.calls += 1
                if Matcher.calls == 2:
                    raise RuntimeError("mixed up ids")        # second call fails once, then the retry works
                ok = ["h1", "h2"] if ids[0] == "a0" else ["h2"]
                return {"articles": [{"article_id": i, "desired_tone": "neutral"} for i in ids], "agent_ids": ok}
        ids = [f"a{i}" for i in range(30)]
        tones, accepted, error = run_matcher(Matcher(), ids, ["h1", "h2", "h3"])
        self.assertEqual(len(tones), 30)
        self.assertEqual(accepted, {"h2"})
        self.assertIsNone(error)

    def test_a_chunk_that_keeps_failing_is_reported(self) -> None:
        class Broken:
            def match_personas(self, *args):
                raise RuntimeError("down")
        tones, accepted, error = run_matcher(Broken(), ["a"], ["h1"])
        self.assertEqual((tones, accepted, error), ({}, set(), "down"))

    def test_incomplete_chunk_preserves_earlier_tones_without_approving_hosts(self) -> None:
        ids = [f"a{i}" for i in range(26)]
        matcher = Mock()
        first = {"articles": [{"article_id": i, "desired_tone": "neutral"} for i in ids[:25]], "agent_ids": ["h1"]}
        incomplete = {"articles": [], "agent_ids": ["h1"]}
        matcher.match_personas.side_effect = [first, incomplete, incomplete]
        tones, accepted, error = run_matcher(matcher, ids, ["h1"])
        self.assertEqual(list(tones), ids[:25])
        self.assertEqual(accepted, set())
        self.assertIn("input order", error)

    def test_no_guest_candidates_still_gets_tones(self) -> None:
        matcher = Mock()
        matcher.match_personas.return_value = {
            "articles": [{"article_id": "a", "desired_tone": "neutral"}], "agent_ids": [],
        }
        self.assertEqual(run_matcher(matcher, ["a"], []), ({"a": "neutral"}, set(), None))
        matcher.match_personas.assert_called_once_with(["a"], [], 1)


class EndpointTest(unittest.TestCase):
    def test_route_and_validation(self) -> None:
        self.assertIn("post", app.openapi()["paths"]["/api/briefing/generate"])
        uid = "00000000-0000-0000-0000-000000000000"
        GenerateRequest(user_id=uid, article_ids=["a"], minutes=3)
        for body in ({"user_id": uid, "article_ids": [], "minutes": 10},
                     {"user_id": uid, "article_ids": ["a"], "minutes": 0},
                     {"user_id": uid, "article_ids": ["a"], "minutes": 10, "hosts": 2}):
            with self.assertRaises(ValidationError):
                GenerateRequest(**body)


if __name__ == "__main__":
    unittest.main()
