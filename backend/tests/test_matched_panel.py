import re
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from api import panel as api
from briefing.generate import generate
from discussion.costs import project
from discussion.panel import review_event
from recommender.recommend import Listener
from research.matcher import Classifications, PersonaMatcher
from research.openai_client import OpenAIClient
from tests.test_briefing import art
from tests.test_panel import fixture


class MatchedPanelTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source = fixture()
        self.revenue, self.rates = self.source.stories
        self.ordered = [self.rates.articles[1], *self.revenue.articles, self.rates.articles[0]]
        self.ids = [a.id for a in self.ordered]
        self.guests = self.source.roster
        self.enterContext(patch.dict("os.environ", {"DATABASE_URL": "fixture"}))
        self.db = self.enterContext(patch("discussion.matched.psycopg.connect")).return_value.__enter__.return_value
        self.cast = [dict(p.model_dump(), voice_id=p.slug) for p in self.source.personas.values()]
        self.articles = [dict(a.model_dump(exclude={"published"}), published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                              url="https://example.org/" + a.id) for a in reversed(self.ordered)]
        self.client = Mock(model="fixture", **{"generate.side_effect": self.generate})
        self.enterContext(patch.object(api, "model", return_value=self.client))
        self.enterContext(patch("discussion.panel.model", return_value=self.client))
        self.enterContext(patch.object(api, "available_stories", side_effect=AssertionError("used index")))
        self.get_matcher = self.enterContext(patch.object(api, "matcher"))
        review_event.cache_clear()
        self.addCleanup(review_event.cache_clear)

    def generate(self, stage, instructions, data, output):
        if stage == "classification":
            return output(articles=[dict(id=a.id, desired_tone="serious" if a.language == "nl" else "neutral",
                                         event=a.headline) for a in self.ordered])
        if stage == "ranking":
            return output(personas=[dict(id=str(g), delivery_fit=2, expertise=1) for g in self.guests])
        if stage == "session_grouping":
            return output(groups=[dict(headline=s.headline, article_ids=[a.id for a in s.articles],
                                       existing_story=None) for s in (self.revenue, self.rates)])
        if stage == "session_evidence":
            return output(findings=[])
        if stage == "check_panel_turn":
            return output(supported=True, issue="")
        self.assertEqual(stage, "panel_turn")
        source = data["source_excerpts"][0]
        figure = re.search(r"\d+%", source["excerpt"])[0]
        return output(text=f"The figure is {figure}.", citations=[dict(article_id=source["article_id"],
                      claim=figure, quote=source["excerpt"])], action="close", handoff=None)

    async def test_shortlist_to_two_topic_playback(self):
        self.db.execute.side_effect = [
            Mock(**{"fetchall.return_value": [(i,) for i in self.ids]}),
            Mock(**{"fetchall.return_value": [(i,) for i in self.guests]}),
            self.cast, self.articles,
        ]
        repository = Mock()
        repository.get_articles_by_ids.return_value = {a.id: dict(id=a.id, categories=[]) for a in self.ordered}
        repository.get_personas_by_ids.return_value = {str(i): dict(id=str(i)) for i in self.guests}
        matcher = PersonaMatcher(repository, self.client)
        self.get_matcher.return_value = matcher
        start = api.Start(profile="Explain interest rates", test_shortlist=True, minutes=4)
        with patch.object(matcher, "match_personas", wraps=matcher.match_personas) as match:
            response = await api.start_panel(start)
            match.assert_called_once_with(self.ids, self.guests, 2)
        key = response["id"]
        self.addCleanup(api.sessions.pop, key, None)
        self.assertFalse(response["sample"])
        panel = api.sessions[key].panel
        self.assertEqual([s.headline for s in panel.stories], [self.rates.headline, self.revenue.headline])
        self.assertEqual(list(panel.guests.values()), [self.guests, self.guests])
        self.assertEqual([c.args[0] for c in self.client.generate.call_args_list].count("session_evidence"), 2)

        step = api.Step(generation=0)
        with patch.object(api, "eleven", return_value=b"mp3") as audio:
            for who in [panel.host, *self.guests, panel.host]:
                segment = await api.next_segment(key, step)
                self.assertEqual(segment["speaker"], panel.personas[who].name)
                self.assertEqual(audio.call_args.args[0], "text-to-speech/" + panel.personas[who].slug)
                self.assertEqual(segment["audio"], "bXAz")
                card, = segment["sources"]
                self.assertIn(card["id"], [a.id for a in self.rates.articles])
                self.assertEqual(str(card["url"]), "https://example.org/" + card["id"])
                self.assertIn(card["claim"], segment["text"])
                self.assertIn(card["claim"], card["quote"])
                prompt = self.client.generate.call_args.args[2]["context"]
                self.assertEqual(prompt["listener_preferences"], start.profile)
                self.assertEqual({t["article_id"]: t["desired_tone"] for t in prompt["article_tones"]},
                                 {a.id: "serious" if a.language == "nl" else "neutral" for a in self.rates.articles})
                step = api.Step(generation=0, heard=segment["id"], seconds=3)
            opening = await api.next_segment(key, step)
            self.assertEqual((opening["story"], opening["speaker"]), (self.revenue.headline, "Tio"))
            self.assertIn(opening["sources"][0]["id"], [a.id for a in self.revenue.articles])
            self.assertFalse(self.client.generate.call_args.args[2]["context"]["first_story"])
            self.assertEqual(panel.roster, self.guests)

    async def test_completed_match_cast_and_duration(self):
        for mode, guests, expected in [("panel", self.guests[:1], self.guests[:1]),
                                       ("panel", [], []), ("presenter", self.guests, [])]:
            with self.subTest(mode=mode, guests=guests):
                self.db.execute.side_effect = [self.cast, self.articles]
                start = api.Start(profile="Listener", mode=mode, minutes=1, match_result={
                    "articles": [dict(article_id=i, desired_tone="neutral") for i in self.ids],
                    "agent_ids": [str(i) for i in guests],
                })
                result = await api.start_panel(start)
                panel = api.sessions.pop(result["id"]).panel
                self.assertFalse(result["sample"])
                self.assertEqual([s.headline for s in panel.stories], [self.rates.headline])
                self.assertEqual(panel.roster, expected)
                self.get_matcher.assert_not_called()

    async def test_generated_plan_plays_every_selected_story_with_sources(self):
        host_id = str(self.source.host)
        guests = list(map(str, self.guests))
        article_ids = [a.id for a in self.revenue.articles + self.rates.articles]
        article_rows = dict(art(a.id, (1, 0) if a in self.rates.articles else (0, 1),
                                pub=a.publisher) for a in self.ordered)
        repository = Mock()
        repository.get_articles_by_ids.return_value = {i: dict(id=i, categories=[]) for i in article_ids}
        repository.get_personas_by_ids.return_value = {i: dict(id=i) for i in guests}
        matcher = PersonaMatcher(repository, self.client)
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = (host_id,)
        conn.execute.return_value.fetchall.return_value = [(144,)]
        listener = Listener([1, 0], ["en", "de", "nl"], preferences="Explain the numbers")
        selected = [self.rates.articles[1].id, self.revenue.articles[0].id]
        with (patch("briefing.generate.load_listener", return_value=listener),
              patch("briefing.generate.rank_hosts", return_value=[host_id, *guests]),
              patch("briefing.generate.host_affinity", return_value={}),
              patch("briefing.generate.load_articles", return_value=article_rows)):
            plan = generate(conn, "fixture", article_ids, selected, 1, "debate", matcher=matcher)
        self.assertGreater(plan["estimated_minutes"], 1)
        self.assertEqual(plan["hosts"], guests)
        self.assertEqual(list(plan["tones"]), plan["article_ids"])
        self.db.execute.side_effect = [self.cast, self.articles]
        result = await api.start_panel(api.Start(profile=plan["profile"], mode="panel", minutes=1,
            briefing_plan={key: plan[key] for key in ("segments", "hosts", "tones")}))
        self.assertFalse(result["sample"])
        key = result["id"]
        self.addCleanup(api.sessions.pop, key, None)
        panel = api.sessions[key].panel
        self.assertEqual([story.id for story in panel.stories], selected)
        self.assertEqual(panel.story_budgets, {s["article_ids"][0]: s["estimated_seconds"] for s in plan["segments"]})
        self.assertEqual([a.id for story in panel.stories for a in story.articles], plan["article_ids"])
        self.assertNotIn("session_grouping", [call.args[0] for call in self.client.generate.call_args_list])
        with patch.object(api, "eleven", return_value=b"mp3"):
            step = api.Step(generation=0)
            for story in panel.stories:
                for _ in range(2):  # opening and closing at the planned duration
                    segment = await api.next_segment(key, step)
                    self.assertEqual(segment["story"], story.headline)
                    card, = segment["sources"]
                    source = next(a for a in story.articles if a.id == card["id"])
                    self.assertIn(card["quote"], source.body)
                    self.assertIn(card["claim"], segment["text"])
                    self.assertEqual(self.client.generate.call_args.args[2]["context"]["listener_preferences"],
                                     listener.preferences)
                    step = api.Step(generation=0, heard=segment["id"], seconds=panel.story_budgets[story.id])
            self.assertEqual(await api.next_segment(key, step), {"done": True})

    async def test_planned_presenter_and_more_than_fifteen_sources(self):
        article_ids = [f"source-{i}" for i in range(16)]
        source = self.articles[0]
        self.db.execute.side_effect = [self.cast, [{**source, "id": i} for i in article_ids]]
        start = api.Start(profile="Listener", mode="presenter", minutes=16.5, briefing_plan={
            "segments": [{"article_ids": article_ids, "estimated_seconds": 60}],
            "hosts": [self.guests[0]], "tones": {},
        })
        result = await api.start_panel(start)
        panel = api.sessions.pop(result["id"]).panel
        self.assertEqual(panel.speaker(), self.guests[0])
        self.assertEqual([a.id for a in panel.story.articles], article_ids)
        self.assertEqual(panel.roster, [])

    async def test_completed_match_honors_explicit_expert_count(self):
        extra = [self.source.personas[self.guests[0]].model_copy(update={"id": uuid4(), "slug": f"extra-{i}"}) for i in (3, 4)]
        guests = [*self.guests, *(p.id for p in extra)]
        cast = [*self.cast, *(p.model_dump() for p in extra)]
        for count in (3, 4):
            self.db.execute.side_effect = [cast, self.articles]
            response = await api.start_panel(api.Start(profile="Listener", expert_count=count, match_result={
                "articles": [dict(article_id=i, desired_tone="neutral") for i in self.ids], "agent_ids": guests,
            }))
            panel = api.sessions.pop(response["id"]).panel
            self.assertEqual(panel.roster, guests[:count])
        self.db.execute.side_effect = [self.cast, self.articles]
        with self.assertRaises(HTTPException) as exc:
            await api.start_panel(api.Start(profile="Listener", expert_count=4, match_result={
                "articles": [dict(article_id=i, desired_tone="neutral") for i in self.ids], "agent_ids": self.guests,
            }))
        self.assertEqual(exc.exception.status_code, 422)

    async def test_invalid_handoff_fails_before_grouping(self):
        result = {"articles": [{"article_id": "missing", "desired_tone": "neutral"}], "agent_ids": []}
        for invalid in [dict(test_shortlist=True), dict(ranked_guests={}), dict(story_ids=["selected"])]:
            with self.assertRaises(ValidationError):
                api.Start(profile="Listener", match_result=result, **invalid)
        for guests, message in [([], "Unknown matched article"), ([UUID(int=99)], "guest IDs"),
                                ([self.source.host], "excluding Tio")]:
            self.db.execute.side_effect = [self.cast, []]
            with self.assertRaises(HTTPException) as exc:
                await api.start_panel(api.Start(profile="Listener", match_result={**result, "agent_ids": guests}))
            self.assertEqual(exc.exception.status_code, 422)
            self.assertIn(message, exc.exception.detail)
        self.client.generate.assert_not_called()

    async def test_setup_reports_provider_status_and_editorial_failure(self):
        def provider_failure(start):
            OpenAIClient("test").generate("classification", "", {}, Classifications)

        rejected = HTTPError("https://example.org", 401, "Unauthorized", {}, None)
        with patch("research.openai_client.urlopen", side_effect=rejected):
            for failure, message in [(provider_failure, "HTTP 401 during classification"),
                                     (ValueError("Event review has an untraceable citation."), "untraceable citation")]:
                with self.subTest(message=message), patch.object(api, "make_panel", side_effect=failure):
                    with self.assertLogs("api.panel", level="WARNING") as logs, self.assertRaises(HTTPException) as exc:
                        await api.start_panel(api.Start(profile="Listener", test_shortlist=True))
                    self.assertEqual(exc.exception.status_code, 503)
                    self.assertIn(message, exc.exception.detail)
                    self.assertIn(message, logs.output[0])

    def test_session_spend_scales_with_listeners(self):
        stages = ["session_grouping", "session_evidence", "panel_turn", "check_panel_turn", "ranking"]
        lines = [f"stage={s} model=m input_tokens=1000 cached_input_tokens=0 output_tokens=0" for s in stages]
        costs = project([*lines, "panel_tts characters=1000"],
                        {"models": {"m": [1000, 0, 0]}, "tts_per_1000_chars": 1, "stt_per_hour": 0}, 2, 3)
        self.assertEqual(costs["shared_usd"], 0)
        self.assertEqual(costs["full_usd"], 18)
        self.assertEqual(costs["10x_articles_same_listeners_usd"], 18)
        self.assertEqual(costs["10x_articles_and_listeners_usd"], 180)
