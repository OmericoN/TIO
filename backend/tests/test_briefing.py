import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from api.briefing import GenerateRequest
from briefing.generate import choose_hosts, group_stories, run_matcher
from briefing.timing import (MAX_S, MIN_S, OVERHEAD_S, TRANSITION_S, Story, fit, story_seconds, total_seconds)
from main import app
from recommender.recommend import Article

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


class HostsTest(unittest.TestCase):
    def test_our_order_minus_vetoes(self) -> None:
        ranked = ["a", "b", "c", "d", "e", "f"]
        self.assertEqual(choose_hosts(ranked, {"c", "b"}, 1), (["b"], False))
        self.assertEqual(choose_hosts(ranked, {"c", "b", "a"}, 2), (["a", "b"], False))
        self.assertEqual(choose_hosts(ranked, {"f"}, 1), (["a"], True))   # outside our top 5: fall back, flagged
        self.assertEqual(choose_hosts(ranked, None, 2), (["a", "b"], True))


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
        self.assertEqual((tones, error), ({}, "down"))


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
