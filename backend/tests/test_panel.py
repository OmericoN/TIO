import asyncio
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from api import panel as api
from discussion.costs import project
from discussion.panel import Citation, EvidenceReview, Finding, Panel, SpokenCitation, Turn, Verdict, evidence, passages, review_event
from discussion.personas import load_starter_personas
from discussion.stories import Group, Groups, group_articles, load_index


def fixture():
    catalog = load_starter_personas()
    host, *guests = catalog
    sample = Path(__file__).resolve().parents[1] / "discussion" / "sample.json"
    stories = load_index(sample, 0)
    return Panel(stories, catalog, host, {s.id: guests for s in stories}, "Explain the figures", "panel", 4)


def line(text="Let's hear from the panel.", action="respond", citations=(), handoff=None):
    return Turn(text=text, action=action, citations=list(citations), handoff=handoff)


class PanelTest(unittest.TestCase):
    def test_session_expert_count_and_minutes(self):
        panel = fixture()
        catalog = dict(panel.personas)
        original = next(p for p in catalog.values() if p.slug != "main-host")
        for number in (3, 4):
            persona = original.model_copy(update={"id": uuid4(), "slug": f"expert-{number}", "name": f"Expert {number}"})
            catalog[persona.id] = persona
        with patch.object(api, "news_events", return_value=panel.stories), patch.object(api, "INDEX") as index, \
                patch.object(api, "load_starter_personas", return_value=catalog), patch.object(api, "review_event"), patch.object(api, "model"):
            index.exists.return_value = False
            for count in (2, 3, 4):
                result = api.make_panel(api.Start(profile="Listener", story_ids=[panel.story.id], minutes=7, expert_count=count))
                self.assertEqual((result.minutes, len(result.roster)), (7, count))
                self.assertNotIn(result.host, result.roster)
                self.assertEqual(len(set(result.roster)), count)
            solo = api.make_panel(api.Start(profile="Listener", story_ids=[panel.story.id], mode="presenter", expert_count=4))
            self.assertEqual(solo.roster, [])
            catalog.pop(persona.id)
            with self.assertRaisesRegex(ValueError, "Only 3 experts are available"):
                api.make_panel(api.Start(profile="Listener", story_ids=[panel.story.id], expert_count=4))
        for invalid in (1, 5, 2.5, "3", True):
            with self.assertRaises(ValidationError):
                api.Start(profile="Listener", expert_count=invalid)

    def test_expert_count_reaches_database_matcher(self):
        panel = fixture()
        catalog = list(panel.personas.values())
        original = catalog[-1]
        catalog += [original.model_copy(update={"id": uuid4(), "slug": f"expert-{i}"}) for i in (3, 4)]
        with patch.object(api, "news_events", return_value=panel.stories), patch.object(api, "INDEX") as index, \
                patch.object(api.psycopg, "connect") as db, patch.object(api, "matcher") as matcher:
            index.exists.return_value = True
            db.return_value.__enter__.return_value.execute.return_value = [p.model_dump() for p in catalog]
            for count in (3, 4):
                ids = [str(p.id) for p in catalog if p.slug != "main-host"][:count]
                matcher.return_value.match_personas.return_value = {"agent_ids": ids, "articles": []}
                result = api.indexed_or_sample_panel(api.Start(profile="Listener", story_ids=[panel.story.id], expert_count=count))
                self.assertEqual(len(result.roster), count)
                self.assertEqual(matcher.return_value.match_personas.call_args.args[-1], count)

    def test_feed_uses_matching_database_thumbnails(self):
        stories = fixture().stories
        article = stories[0].articles[1]
        with patch.object(api, "news_events", return_value=stories), patch.object(api, "INDEX") as index, \
                patch.object(api, "connect") as connection:
            index.exists.return_value = True
            database = connection.return_value.__enter__.return_value
            database.execute.return_value = [(article.id, "https://publisher.example/image.jpg")]
            result = api.feed()
            self.assertEqual(result["stories"][0]["thumb_url"], "https://publisher.example/image.jpg")
            self.assertIsNone(result["stories"][1]["thumb_url"])
            sql, params = database.execute.call_args.args
            self.assertIn("id = ANY(%s)", sql)
            self.assertIn(article.id, params[0])
            index.exists.return_value = False
            connection.reset_mock()
            self.assertTrue(api.feed()["sample"])
            connection.assert_not_called()

    def test_feed_survives_thumbnail_database_failure(self):
        with patch.object(api, "news_events", return_value=fixture().stories), patch.object(api, "INDEX") as index, \
                patch.object(api, "connect", side_effect=RuntimeError("Database unavailable")):
            index.exists.return_value = True
            self.assertTrue(all(s["thumb_url"] is None for s in api.feed()["stories"]))

    def test_swiped_lineup_is_exact_and_ordered(self):
        stories = fixture().stories
        ids = [story.id for story in reversed(stories)]
        with patch.object(api, "news_events", return_value=stories), patch.object(api, "INDEX") as index, \
                patch.object(api, "review_event"), patch.object(api, "model"), patch.object(api, "select_stories") as rank:
            index.exists.return_value = False
            for selected in (ids, ids[:1]):
                panel = api.make_panel(api.Start(profile="A wildcard listener", story_ids=selected))
                self.assertEqual([s.id for s in panel.stories], selected)
            rank.assert_not_called()
            for invalid in ([ids[0], ids[0]], ["missing"]):
                with self.assertRaises(HTTPException) as caught:
                    api.make_panel(api.Start(profile="A listener", story_ids=invalid))
                self.assertEqual(caught.exception.status_code, 422)

    def test_multilingual_event_and_quantity_checks(self):
        panel = fixture()
        articles = panel.story.articles
        client = Mock()
        client.generate.return_value = Groups(groups=[Group(headline="Quarterly revenue",
            existing_story=None, article_ids=[a.id for a in articles])])
        duplicate = articles[0].model_copy(update={"id": "syndicated", "publisher": "Other outlet"})
        story, = group_articles([*articles, duplicate], client)
        self.assertEqual(story.articles[-1].duplicate_of, articles[0].id)
        report = articles[1]
        cite = SpokenCitation(claim="6%", article_id=report.id, quote=report.body)
        turn = line("The report puts growth at 6%.", citations=[cite])
        cards = evidence(turn, story, passages(story, "revenue"))
        self.assertEqual((cards[0]["language"], cards[0]["id"]), ("de", report.id))
        for invalid in [line("Revenue rose 16%.", citations=[cite]),
                        line("It was 6%.", citations=[cite.model_copy(update={"article_id": articles[0].id})])]:
            with self.assertRaises(ValueError):
                evidence(invalid, story, passages(story, ""))

    def test_repair_once_and_withhold_unsupported_interpretation(self):
        panel = fixture()
        author, verifier = Mock(), Mock()
        author.generate.return_value = line("Revenue growth caused hiring to accelerate.")
        verifier.generate.return_value = Verdict(supported=False, issue="No hiring evidence.")
        approved, cards = panel.draft(author, verifier)
        self.assertEqual(author.generate.call_count, 2)
        self.assertEqual(verifier.generate.call_count, 2)
        self.assertEqual(cards, [])
        self.assertNotIn("hiring", approved.text)

    def test_cross_source_finding_needs_original_quotes_from_both_sides(self):
        review_event.cache_clear()
        self.addCleanup(review_event.cache_clear)
        a, b = fixture().story.articles[1:]
        rows = json.dumps([{"id": row.id, "passages": [row.body]} for row in (a, b)])
        cites = [Citation(article_id=row.id, quote=row.body) for row in (a, b)]
        finding = Finding(point="Different estimates for all operations", status="disputed", citations=cites)
        with patch("discussion.panel.model") as provider:
            provider.return_value.generate.side_effect = [
                EvidenceReview(findings=[finding.model_copy(update={"citations": cites[:1]})]),
                EvidenceReview(findings=[finding])]
            with self.assertRaisesRegex(ValueError, "both sources"):
                review_event("test", rows)
            self.assertEqual(review_event("test", rows).findings, [finding])
            review_event("test", rows)
            self.assertEqual(provider.return_value.generate.call_count, 2)

    def test_skipped_words_do_not_enter_history(self):
        panel = fixture()
        panel.heard(line(), [], 1, fully_heard=False)
        self.assertEqual(panel.phase, "guest")
        self.assertFalse(panel.history)

    def test_moderator_guest_exchange_and_conflict_probe(self):
        panel = fixture()
        first, second = panel.roster
        self.assertEqual(panel.speaker(), panel.host)
        panel.heard(line(), [], 2)
        self.assertEqual(panel.speaker(), first)
        panel.heard(line(handoff=panel.personas[second].name), [], 4)
        self.assertEqual(panel.speaker(), second)
        panel.reviews[panel.story.id] = EvidenceReview(findings=[
            Finding(point="Different scopes", status="different_basis", citations=[])])
        panel.heard(line(action="close"), [], 4)
        self.assertEqual((panel.phase, panel.speaker()), ("clarify", panel.host))
        panel.heard(line(handoff=panel.personas[second].name), [], 1)
        self.assertEqual(panel.speaker(), second)
        for _ in range(2):
            panel.heard(line(), [], 3)
        self.assertEqual(panel.phase, "close")
        panel.heard(line(), [], 2)
        self.assertEqual(panel.index, 1)
        self.assertFalse(panel.history)

    def test_direct_address_and_single_speaker(self):
        panel = fixture()
        expert = panel.roster[0]
        panel.guests[panel.story.id] = [expert]
        panel.question = "Markets, which source gives that number?"
        self.assertEqual(panel.speaker(), expert)
        panel.question = ""
        panel.mode = "presenter"
        panel.heard(line(), [], 2)
        self.assertEqual((panel.phase, panel.speaker()), ("close", panel.host))

    def test_cost_projection_keeps_shared_work_separate(self):
        costs = project(["stage=event_grouping model=m input_tokens=100 cached_input_tokens=10 output_tokens=20",
                         "panel_tts characters=1000"], {"models": {"m": [1, 0.5, 2]},
                         "tts_per_1000_chars": 1, "stt_per_hour": 2}, 2, 3, 1800)
        self.assertAlmostEqual(costs["shared_usd"], 0.00027)
        self.assertEqual(costs["listener_usd"], 6)


class InterruptionTest(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_during_render_and_playback(self):
        key = uuid4()
        item = api.Session(fixture())
        item.touched = time.monotonic()
        api.sessions[key] = item
        self.addCleanup(api.sessions.pop, key, None)
        entered, release = threading.Event(), threading.Event()

        def slow_render(*args):
            entered.set()
            release.wait(3)
            return "audio"

        with patch.object(api, "model"), patch.object(Panel, "draft", return_value=(line(), [])), \
                patch.object(api, "render", side_effect=slow_render):
            pending = asyncio.create_task(api.next_segment(key, api.Step(generation=0)))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            await api.interrupt(key, api.Step(generation=1))
            release.set()
            with self.assertRaises(HTTPException) as exc:
                await pending
            self.assertEqual(exc.exception.status_code, 409)
            segment = await api.next_segment(key, api.Step(generation=1, question="Which source?"))
            self.assertEqual(segment["story_id"], item.panel.story.id)
            await api.interrupt(key, api.Step(generation=2, heard=segment["id"], seconds=0.5))
            self.assertIsNone(item.pending)
            self.assertEqual([row.speaker for row in item.panel.history], ["Listener"])
            self.assertIn("0.5s", item.panel.reference)
