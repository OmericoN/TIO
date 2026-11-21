import asyncio
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException

from api import panel as api
from discussion.costs import project
from discussion.panel import Citation, EvidenceReview, Finding, Panel, SpokenCitation, Turn, Verdict, evidence, passages, review_event, spoken_claim, spoken_text, figures_supported
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
        loose = cite.model_copy(update={"quote": "  ".join(report.body.split())})  # spacing differs from the source
        self.assertEqual(evidence(line("The report puts growth at 6%.", citations=[loose]), story,
                                  passages(story, "revenue"))[0]["quote"], report.body)
        for invalid in [line("Revenue rose 16%.", citations=[cite]),
                        line("It was 6%.", citations=[cite.model_copy(update={"article_id": articles[0].id})])]:
            with self.assertRaises(ValueError):
                evidence(invalid, story, passages(story, ""))

    def test_reworded_claim_maps_to_its_sentence_but_needs_its_numbers(self):
        text = "The company said the loss was 20 cents a share. Net sales rose to $6.2 million."
        self.assertEqual(spoken_claim("the loss narrowed to 20 cents per share", text),
                         "The company said the loss was 20 cents a share.")
        self.assertIsNone(spoken_claim("the loss was 30 cents per share", text))
        self.assertIsNone(spoken_claim("margins improved sharply this year", text))
        self.assertEqual(spoken_claim("Net sales rose", text), "Net sales rose")

    def test_paraphrased_claim_spread_over_sentences_is_located(self):
        text = ("I’m Tio. The real news is Trump’s Manila arrival for the 49th ASEAN Summit. IBTimes UK says he "
                "arrived on Tuesday, 17 November 2026, and that Air Force One landed at 5.33 pm local time. "
                "Leila Grant, what stands out?")
        arrival = spoken_claim("Trump’s arrival in Manila on Tuesday, 17 November 2026, for the final day of the "
                               "49th ASEAN Summit is the main development.", text)
        self.assertIn("49th ASEAN", arrival)
        self.assertIn("17 November 2026", arrival)
        self.assertNotIn("Leila", arrival)
        landing = spoken_claim("Air Force One landed at Ninoy Aquino International Airport in Pasay City at "
                               "5.33 pm local time.", text)
        self.assertIn("5.33 pm", landing)
        self.assertIsNone(spoken_claim("Air Force One landed at 6.10 pm local time.", text))

    def test_signature_lines_are_offered_only_where_they_fit(self):
        panel = fixture()
        host, guest = panel.host, panel.roster[0]
        for i in (host, guest):
            panel.personas[i] = panel.personas[i].model_copy(update={
                "intro_template": "I'm here.", "outro_template": "Bye for now.", "catchphrases": ["Follow the money."]})
        offered, notes = panel.signature_lines(guest)
        self.assertEqual(offered, {"intro_template"})
        self.assertIn("Do not recite it", notes)
        panel.speaker_turns[guest] = 1
        offered, notes = panel.signature_lines(guest)
        self.assertEqual(offered, {"catchphrases"})                    # second turn: a reworded catchphrase may fit
        self.assertIn("Do not say your name", notes)
        self.assertEqual(panel.speaker(), host)
        panel.heard(line("Hi, I'm Tio. Rates fell."), [], 1)
        self.assertEqual((panel.speaker_turns[host], panel.openings[host]), (1, ["Hi, I'm Tio."]))
        panel.speaker_turns[host] = 2
        panel.index, panel.phase = len(panel.stories) - 1, "close"
        self.assertEqual(panel.signature_lines(host)[0], {"outro_template"})

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

    def test_quote_matches_despite_quote_marks_and_spacing_and_keeps_source_text(self):
        review_event.cache_clear()
        self.addCleanup(review_event.cache_clear)
        source = "The bank said it “won’t  raise\nrates” this year."
        rows = json.dumps([{"id": "a", "passages": [source]}])
        loose = Finding(point="Rates", status="reported",
                        citations=[Citation(article_id="a", quote='it "won\'t raise rates"')])
        with patch("discussion.panel.model") as provider:
            provider.return_value.generate.return_value = EvidenceReview(findings=[loose])
            [kept] = review_event("test", rows).findings
        self.assertEqual(kept.citations[0].quote, "it “won’t  raise\nrates”")

    def test_untraceable_finding_is_dropped_and_the_rest_kept(self):
        review_event.cache_clear()
        self.addCleanup(review_event.cache_clear)
        rows = json.dumps([{"id": "a", "passages": ["Revenue rose 5% in March."]}])
        good = Finding(point="Revenue", status="reported", citations=[Citation(article_id="a", quote="Revenue rose 5%")])
        bad = Finding(point="Profit", status="reported", citations=[Citation(article_id="a", quote="Profit doubled")])
        with patch("discussion.panel.model") as provider:
            provider.return_value.generate.return_value = EvidenceReview(findings=[bad, good])
            with self.assertLogs("discussion.panel", level="WARNING") as logs:
                self.assertEqual(review_event("test", rows).findings, [good])
        self.assertIn("Profit doubled", logs.output[0])

    def test_story_fails_only_when_no_finding_is_traceable(self):
        review_event.cache_clear()
        self.addCleanup(review_event.cache_clear)
        rows = json.dumps([{"id": "a", "passages": ["Revenue rose 5% in March."]}])
        bad = Finding(point="Profit", status="reported", citations=[Citation(article_id="a", quote="Profit doubled")])
        with patch("discussion.panel.model") as provider:
            provider.return_value.generate.return_value = EvidenceReview(findings=[bad])
            with self.assertLogs("discussion.panel", level="WARNING"), \
                    self.assertRaisesRegex(ValueError, "no traceable findings \\(untraceable citation\\)"):
                review_event("test", rows)

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
            return "audio", 1.0

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
            await item.prefetch.task  # let the speculative next turn finish while the mocks are active
            await api.interrupt(key, api.Step(generation=2, heard=segment["id"], seconds=0.5))
            self.assertIsNone(item.pending)
            self.assertEqual([row.speaker for row in item.panel.history], ["Listener"])
            self.assertIn("0.5s", item.panel.reference)


class PrefetchTest(unittest.IsolatedAsyncioTestCase):
    async def test_next_turn_is_prepared_while_the_current_one_plays(self):
        key = uuid4()
        item = api.Session(fixture())
        item.touched = time.monotonic()
        api.sessions[key] = item
        self.addCleanup(api.sessions.pop, key, None)
        drafts = iter(line(f"Line {i}.") for i in range(10))
        with patch.object(api, "model"), patch.object(Panel, "draft", side_effect=lambda *a: (next(drafts), [])) as draft, \
                patch.object(api, "render", return_value=("audio", 4.0)):
            first = await api.next_segment(key, api.Step(generation=0))
            await item.prefetch.task
            self.assertEqual(draft.call_count, 2)                   # the second line was drafted during playback
            second = await api.next_segment(key, api.Step(generation=0, heard=first["id"], seconds=4))
            self.assertEqual((second["text"], draft.call_count), ("Line 1.", 2))   # served without waiting
            await item.prefetch.task
            # Skipped audio changes what comes next, so the speculative line is discarded and redrafted.
            third = await api.next_segment(key, api.Step(generation=0, heard=second["id"], seconds=1,
                                                         fully_heard=False))
            self.assertEqual((third["text"], draft.call_count), ("Line 3.", 4))
            await item.prefetch.task

    def test_voice_settings_follow_the_story_tone(self):
        self.assertLess(api.speech_request("Hi", "upbeat")["voice_settings"]["stability"],
                        api.speech_request("Hi", "somber")["voice_settings"]["stability"])
        with patch.dict(os.environ, {"PANEL_TTS_MODEL": "eleven_v3"}):
            request = api.speech_request("[laughs] Hi", "upbeat")
        self.assertEqual((request["model_id"], request["voice_settings"]), ("eleven_v3", {"stability": 0.0}))

    def test_audio_tags_stay_out_of_transcripts(self):
        self.assertEqual(spoken_text("[laughs] Well, [sighs] rates rose."), "Well, rates rose.")


class FigureTest(unittest.TestCase):
    def test_honest_rounding_passes_but_changed_figures_do_not(self):
        quote = "regrowth covering approximately 97.85% of the area, sales of 1,200 units in 2026, a 3,5 % rate"
        for spoken in ("nearly 98%", "98.0%", "97.9%", "1,200 units", "1200", "2026", "3.5%"):
            self.assertTrue(figures_supported(spoken, quote), spoken)
        for spoken in ("99%", "2025", "4%", "100%"):
            self.assertFalse(figures_supported(spoken, quote), spoken)
