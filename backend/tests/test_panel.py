import asyncio
import json
import tempfile
import os
import threading
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from api import panel as api
from discussion.costs import project
from discussion.agents import DiscussionLine
from discussion.panel import AnswerTurn, Citation, EvidenceReview, Finding, Panel, SpokenCitation, Turn, Verdict, evidence, passages, review_event, spoken_claim, spoken_text, figures_supported
from discussion.personas import load_starter_personas
from discussion.stories import Group, Groups, group_articles, load_index


def fixture():
    catalog = load_starter_personas()
    host, *guests = catalog
    sample = Path(__file__).resolve().parents[1] / "discussion" / "sample.json"
    stories = load_index(sample, 0)
    return Panel(stories, catalog, host, {s.id: guests for s in stories}, "Explain the figures", 4)


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
            with self.assertRaises(ValidationError):
                api.Start(profile="Listener", mode="presenter")
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
        loose = cite.model_copy(update={"quote": "  ".join(report.body.split())})  # spacing differs from the source
        self.assertEqual(evidence(line("The report puts growth at 6%.", citations=[loose]), story,
                                  passages(story, "revenue"))[0]["quote"], report.body)
        with self.assertRaises(ValueError):                         # a figure in no passage is invented
            evidence(line("Revenue rose 16%.", citations=[cite]), story, passages(story, ""))
        wrong_article = line("It was 6%.", citations=[cite.model_copy(update={"article_id": articles[0].id})])
        self.assertEqual(evidence(wrong_article, story, passages(story, "")), [])  # line plays, bad card dropped

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
                "intro_template": "I'm here.", "outro_template": "Bye for now.",
                "catchphrases": ["A warm 'yeah' when acknowledging a guest"]})
        offered, notes = panel.signature_lines(guest)
        self.assertFalse(offered)
        self.assertNotIn("warm", notes)
        self.assertIn("Do not introduce yourself", notes)
        self.assertEqual(panel.speaker(), host)
        panel.heard(line("Hi there. Rates fell."), [], 1)
        self.assertEqual((panel.speaker_turns[host], panel.openings[host]), (1, ["Hi there."]))
        panel.index, panel.phase = len(panel.stories) - 1, "close"
        self.assertEqual(panel.signature_lines(host)[0], {"outro_template"})
        self.assertIn("warm", panel.signature_lines(host)[1])

    def test_a_line_that_restates_an_earlier_one_is_redrafted(self):
        panel = fixture()
        said = "Revenue grew six percent this quarter, driven by stronger demand from industrial customers."
        panel.history.append(DiscussionLine(speaker="Tio", text=said))
        self.assertEqual(panel.repeated_line("So revenue grew six percent this quarter, driven by stronger "
                                             "industrial customer demand."), said)
        self.assertIsNone(panel.repeated_line("Margins are the bigger question: costs rose faster than prices "
                                              "and the guidance stayed cautious."))

    def test_line_without_figures_plays_without_a_model_check(self):
        panel = fixture()
        author, verifier = Mock(), Mock()
        author.generate.return_value = line("That's a big claim. Leila, do you buy it?")
        approved, _ = panel.draft(author, verifier)
        self.assertEqual(approved.text, "That's a big claim. Leila, do you buy it?")
        verifier.generate.assert_not_called()

    def test_repair_once_and_withhold_unsupported_interpretation(self):
        panel = fixture()
        author, verifier = Mock(), Mock()
        author.generate.return_value = line("Revenue grew 6%, which caused hiring to accelerate.")
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
            self.assertEqual(segment["story_id"], item.panel.story.id)
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


class HoldingTest(unittest.TestCase):
    def test_holding_lines_are_voiced_once_then_read_from_disk(self):
        panel = fixture()
        with tempfile.TemporaryDirectory() as folder, patch.object(api, "HOLDING_CACHE", Path(folder)), \
                patch.object(api, "eleven", return_value=b"m" * 4096) as voice, \
                patch.dict(os.environ, {"PANEL_VOICE_" + panel.personas[panel.host].slug.upper().replace("-", "_"): "v"}):
            first = api.holding_lines(panel)
            second = api.holding_lines(panel)
        self.assertEqual(len(first), len(api.HOLDING_LINES))
        self.assertEqual(first, second)
        self.assertEqual(voice.call_count, len(api.HOLDING_LINES))   # the second session reused the cache
        self.assertTrue(all(len(line.split()) >= 20 for line in api.HOLDING_LINES))


class ListenerIntentTest(unittest.TestCase):
    def answer(self, intent, style=None):
        return AnswerTurn(text="Sure.", citations=[], handoff=None, action="respond", listener_intent=intent,
                          style_request=style)

    def test_questions_use_the_answer_schema_and_style_requests_last(self):
        panel = fixture()
        panel.question = "What does that even mean?"
        author = Mock()
        author.generate.return_value = self.answer("simpler", "Explain terms in plain words.")
        panel.draft(author, Mock())
        self.assertIs(author.generate.call_args.args[3], AnswerTurn)
        self.assertIn("listener_intent", author.generate.call_args.args[1])
        panel.heard(author.generate.return_value, [], 2)
        self.assertEqual(panel.listener_style, ["Explain terms in plain words."])
        author.generate.return_value = line("Back to the panel.")
        panel.draft(author, Mock())
        self.assertIs(author.generate.call_args.args[3], Turn)
        self.assertEqual(author.generate.call_args.args[2]["listener_style_requests"], ["Explain terms in plain words."])

    def test_skip_wraps_the_story_and_deeper_buys_time(self):
        panel = fixture()
        panel.question, panel.seconds = "Can we move on?", 100
        panel.heard(self.answer("skip"), [], 2)
        self.assertEqual(panel.phase, "close")
        panel.question, panel.phase = "Tell me more", "guest"
        panel.heard(self.answer("deeper"), [], 2)
        self.assertEqual(panel.seconds, 44)  # 100 + 2 + 2 heard, minus a minute


class HandoffTest(unittest.TestCase):
    def test_the_named_guest_speaks_next_even_with_a_loose_handoff(self):
        for handoff, text in [("Leila", "Over to you."), (None, "Priya made a point. Leila, what do you make of it?"),
                              ("leila grant", "Thoughts?")]:
            panel = fixture()
            first, second = panel.roster
            panel.personas[first] = panel.personas[first].model_copy(update={"name": "Priya Shah"})
            panel.personas[second] = panel.personas[second].model_copy(update={"name": "Leila Grant"})
            panel.heard(line(text, handoff=handoff), [], 2)
            with self.subTest(handoff=handoff):
                self.assertEqual(panel.next_guest, second)


class PanelFloorTest(unittest.TestCase):
    def test_each_booked_expert_gets_a_completed_turn_even_when_time_runs_out(self):
        from discussion.realtime import show_step
        for size in (2, 3, 4):
            panel = fixture()
            while len(panel.roster) < size:
                n = len(panel.roster)
                expert = panel.personas[panel.roster[0]].model_copy(update={
                    "id": uuid4(), "name": f"Guest {n}", "slug": f"guest-{n}"})
                panel.personas[expert.id] = expert
                panel.roster.append(expert.id)
            panel.story_budgets[panel.story.id] = 20
            self.assertEqual(panel.turn_seconds(), 8)
            for guest in panel.roster:
                target = panel.personas[guest].name
                self.assertEqual(show_step(panel)[0], target)
                panel.heard(line(f"{target}, what changed?", handoff=target), [], 10)
                self.assertEqual(panel.speaker(), guest)
                panel.heard(line("I was about to say…"), [], 2, fully_heard=False)
                self.assertNotIn(guest, panel.guest_turns)
                self.assertEqual(panel.speaker(), guest)
            self.assertIsNone(show_step(panel)[0])
            panel.heard(line("The two estimates cover different activities."), [], 8)
            self.assertEqual((panel.speaker(), panel.phase), (panel.host, "close"))
            self.assertEqual(panel.guest_turns, set(panel.roster))

    def test_spoken_address_beats_dispute_budget_and_planned_guest(self):
        panel = fixture()
        a, b = panel.roster
        panel.personas[b] = panel.personas[b].model_copy(update={"name": "Leila Grant"})
        c = panel.personas[a].model_copy(update={"id": uuid4(), "name": "Leila Jones", "slug": "accounts"})
        panel.personas[c.id] = c
        panel.roster.append(c.id)
        panel.phase, panel.next_guest = "guest", a
        panel.guest_turns = {b, c.id}
        panel.reviews[panel.story.id] = EvidenceReview(findings=[
            Finding(point="Different periods", status="different_basis", citations=[])])
        self.assertIsNone(panel.asked_guest("Leila, what changed?"))
        self.assertIsNone(panel.asked_guest("Leila Grant said growth slowed. How come?"))
        self.assertIsNone(panel.asked_guest("What did Leila Grant report?"))
        panel.heard(line("Leila Grant, what explains that slowdown?", action="close", handoff=c.name), [], 130)
        self.assertEqual(panel.speaker(), b)
        self.assertLessEqual(panel.turn_seconds(), 12)
        panel.heard(line("The reports cover different periods."), [], 8)
        self.assertEqual((panel.speaker(), panel.phase), (panel.host, "close"))

    def test_spoken_reply_precedes_a_host_probe_while_time_remains(self):
        panel = fixture()
        a, b = panel.roster
        panel.guest_turns = {b}
        panel.phase, panel.next_guest = "guest", a
        panel.heard(line(f"What period is this, {panel.personas[b].name}?", action="clarify"), [], 3)
        self.assertEqual(panel.speaker(), b)


    def test_listener_interruption_preserves_the_original_expert_question(self):
        from discussion import realtime
        panel = fixture()
        first, second = panel.roster
        question = f"{panel.personas[second].name}, why do the periods differ?"
        panel.phase, panel.next_guest = "guest", first
        panel.heard(line(question), [], 8)
        panel.question = "Markets, explain the headline"
        self.assertEqual(panel.speaker(), first)
        panel.heard(line("The headline covers continuing operations."), [], 8)
        self.assertEqual(panel.speaker(), second)
        brief = realtime.TurnBrief(take="A view", facts=[], visual=None, goal="Explain", points=["Periods"], card_ids=[], handoff=None, action="respond", max_seconds=8)
        self.assertIn(question, realtime.render_brief(panel, brief))

class LiveConversationTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_session_is_briefed_and_folded_back_into_the_panel(self):
        key = uuid4()
        item = api.Session(fixture())
        item.touched = time.monotonic()
        item.panel.listener_style = ["Explain terms in plain words."]
        api.sessions[key] = item
        self.addCleanup(api.sessions.pop, key, None)
        token = SimpleNamespace(token="tok", conversation_id="c")
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "k", "ELEVENLABS_AGENT_ID": "a"}), \
                patch.object(api, "request_session", return_value=token):
            live = await api.start_live(key)
        self.assertEqual((live["token"], live["speaker"]), ("tok", item.panel.personas[item.panel.host].name))
        for expected in (item.panel.story.headline, item.panel.profile, "Explain terms in plain words.",
                         "back to the panel"):
            self.assertIn(expected, live["prompt"])
        with self.assertRaises(HTTPException):
            await api.end_live(key, api.LiveEnd(generation=5, transcript=[]))
        await api.end_live(key, api.LiveEnd(generation=0, transcript=[
            api.LiveLine(role="listener", text="Why did revenue grow?"),
            api.LiveLine(role="host", text="Mostly stronger demand, the report says.")]))
        self.assertEqual([line.speaker for line in item.panel.history][-2:],
                         ["Listener", item.panel.personas[item.panel.host].name])
        self.assertEqual(item.panel.phase, "resume")

    async def test_live_needs_an_agent(self):
        key = uuid4()
        item = api.Session(fixture())
        item.touched = time.monotonic()
        api.sessions[key] = item
        self.addCleanup(api.sessions.pop, key, None)
        with patch.dict(os.environ, {"ELEVENLABS_AGENT_ID": ""}), self.assertRaises(HTTPException) as caught:
            await api.start_live(key)
        self.assertEqual(caught.exception.status_code, 503)


class LiveHostTest(unittest.TestCase):
    def panel(self):
        panel = fixture()
        report = panel.story.articles[1]
        panel.reviews[panel.story.id] = EvidenceReview(findings=[Finding(
            point="Revenue grew 6% in the quarter", status="reported",
            citations=[Citation(article_id=report.id, quote=report.body)])])
        return panel

    def test_cards_number_each_story_and_keep_their_sources(self):
        cards = self.panel().fact_cards()
        self.assertEqual(cards[0]["id"], "1.1")
        self.assertEqual(cards[0]["sources"][0]["claim"], "Revenue grew 6% in the quarter")
        self.assertEqual({c["story"] for c in cards}, set(range(1, len(fixture().stories) + 1)))

    def test_audit_shows_the_card_used_and_flags_invented_figures(self):
        panel = self.panel()
        cards, correction = panel.audit_live("So revenue grew about 6% this quarter, the report says.")
        self.assertEqual(cards[0]["id"], "1.1")
        self.assertIsNone(correction)
        _, correction = panel.audit_live("Revenue jumped 14% this quarter.")
        self.assertIn("14", correction)


class RealtimePanelTest(unittest.IsolatedAsyncioTestCase):
    async def test_briefs_are_planned_ahead_and_record_what_agents_said(self):
        from discussion import realtime
        key = uuid4()
        item = api.Session(fixture())
        item.touched = time.monotonic()
        api.sessions[key] = item
        self.addCleanup(api.sessions.pop, key, None)
        briefs = iter(realtime.TurnBrief(take="A view", goal=f"Goal {i}", points=[f"Point {i}"], card_ids=[], facts=[], handoff=None,
                                         action="respond", max_seconds=12, visual=None) for i in range(10))
        with patch.object(realtime, "make_brief", side_effect=lambda panel: next(briefs)) as plan:
            first = await api.realtime_next(key, api.RealtimeStep(generation=0))
            self.assertIn("Goal 0", first["brief"])
            await item.brief_prefetch.task
            self.assertEqual(plan.call_count, 2)                     # brief 2 planned while brief 1 is spoken
            second = await api.realtime_next(key, api.RealtimeStep(
                generation=0, heard=first["id"], text="So revenue is up, Leila?", seconds=9))
            self.assertIn("Goal 1", second["brief"])
            self.assertEqual(plan.call_count, 2)                     # served from the prefetch
            self.assertEqual(item.panel.history[-1].text, "So revenue is up, Leila?")
            self.assertIn("So revenue is up, Leila?", second["context"])
            self.assertIn("So revenue is up, Leila?", second["brief"])
            self.assertNotIn("Point 0", second["brief"])
            await item.brief_prefetch.task

    async def test_changed_spoken_addressee_discards_prefetch_and_refreshes_persona(self):
        from discussion import realtime
        panel = fixture()
        first, second = panel.roster
        panel.openings[second] = ["My last opener."]
        key = uuid4()
        item = api.sessions[key] = api.Session(panel, touched=time.monotonic())
        self.addCleanup(api.sessions.pop, key, None)
        def draft(stage, instructions, data, output):
            self.assertIn("persona", data)
            return output(take="A view", facts=[], visual=None, goal=data["speaker"], points=["Scope matters."], card_ids=["1.1"],
                          handoff=None, action="respond", max_seconds=12)
        with patch.object(realtime, "model") as provider:
            provider.return_value.generate.side_effect = draft
            opening = await api.realtime_next(key, api.RealtimeStep(generation=0))
            await item.brief_prefetch.task
            self.assertEqual(provider.return_value.generate.call_args.args[2]["speaker"], panel.personas[first].name)
            question = f"{panel.personas[second].name}, which period is this?"
            reply = await api.realtime_next(key, api.RealtimeStep(
                generation=0, heard=opening["id"], text=question, seconds=8))
            self.assertEqual(reply["speaker_id"], str(second))
            self.assertIn(question, reply["brief"])
            self.assertIn(panel.personas[second].personality_md.splitlines()[-1], reply["brief"])
            self.assertIn("My last opener.", reply["brief"])
            await item.brief_prefetch.task

    async def test_listener_answers_use_one_planner_call_and_normal_speaker_and_sources(self):
        from discussion import realtime
        for name in (True, False):
            panel = fixture()
            guest = panel.roster[-1]
            question = ("Economy Specialist, " if name else "") + "which period does this cover?"
            key = uuid4()
            item = api.sessions[key] = api.Session(panel, touched=time.monotonic())
            self.addCleanup(api.sessions.pop, key, None)
            calls = []
            def draft(stage, instructions, data, output):
                extra = {}
                if data["question"]:
                    calls.append(data)
                    if not name:
                        self.assertEqual(output, realtime.RoutedBrief)
                        self.assertIn(str(guest), data["candidates"])
                        extra["speaker_id"] = guest
                    else:
                        self.assertIs(output, realtime.TurnBrief)
                return output(take="A view", facts=[], visual=None, goal="Explain the reporting period", points=["Compare periods"], card_ids=["1.1"],
                              handoff=None, action="respond", max_seconds=12, **extra)
            with patch.object(realtime, "model") as provider:
                provider.return_value.generate.side_effect = draft
                reply = await api.realtime_next(key, api.RealtimeStep(generation=0, question=question))
                self.assertEqual((reply["speaker_id"], reply["speaker"]), (str(guest), panel.personas[guest].name))
                self.assertEqual(reply["sources"], realtime.story_cards(panel)[0]["sources"])
                self.assertIn(question, reply["brief"])
                await item.brief_prefetch.task
                self.assertEqual(len(calls), 1)
                await api.realtime_next(key, api.RealtimeStep(generation=0, heard=reply["id"],
                                            text="The reports compare the same quarter.", seconds=8))
                self.assertEqual(panel.history[-1].speaker, panel.personas[guest].name)
                if item.brief_prefetch:
                    await item.brief_prefetch.task

    async def test_listener_skip_releases_interrupted_floor_and_finishes_last_story(self):
        from discussion import realtime
        key = uuid4()
        panel = fixture()
        item = api.sessions[key] = api.Session(panel, touched=time.monotonic())
        self.addCleanup(api.sessions.pop, key, None)
        with patch.object(realtime, "make_brief", return_value=realtime.TurnBrief(
                take="A view", facts=[], visual=None, goal="Open", points=["A fact"], card_ids=[], handoff=None, action="respond", max_seconds=8)):
            first = await api.realtime_next(key, api.RealtimeStep(generation=0))
            second = await api.realtime_next(key, api.RealtimeStep(generation=0, heard=first["id"],
                                             interrupted=True, question="Skip this story"))
            self.assertEqual((second["story_id"], second["speaker_id"]), (panel.stories[1].id, str(panel.host)))
            self.assertFalse(panel.history)
            done = await api.realtime_next(key, api.RealtimeStep(generation=0, heard=second["id"],
                                           interrupted=True, question="Next story"))
            self.assertTrue(done["done"])

    def test_signature_saying_rotates_only_on_even_completed_turns(self):
        from discussion import realtime
        panel = fixture()
        speaker = panel.roster[0]
        panel.personas[speaker] = panel.personas[speaker].model_copy(update={"catchphrases": ["First saying", "Second saying"]})
        self.assertNotIn("First saying", realtime.agent_instructions(panel, speaker))
        brief = realtime.TurnBrief(take="A view", facts=[], visual=None, goal="Explain", points=["Scope"], card_ids=[], handoff=None, action="respond", max_seconds=8)
        for completed, expected in enumerate([None, "First saying", None, "Second saying"]):
            panel.phase, panel.next_guest = "guest", speaker
            for _ in range(2):
                self.assertEqual(panel.optional_phrase(speaker), expected)
                reminder = realtime.render_brief(panel, brief)
                self.assertEqual("Optional verbal habit" in reminder, bool(expected))
                if expected:
                    self.assertIn(expected, reminder)
                panel.heard(line(), [], 1, fully_heard=False)
            self.assertEqual(panel.speaker_turns.get(speaker, 0), completed)
            panel.heard(line(), [], 8)

    def test_every_speaker_gets_a_distinct_voice(self):
        from discussion import realtime
        panel = fixture()
        cast = lambda persona: realtime.VoiceProfile(voices=["marin", "cedar", "coral"], direction=f"{persona.name} voice.")
        chosen = realtime.voices(panel, cast)                        # everyone's first choice is marin
        self.assertEqual(chosen[panel.host][0], "marin")
        self.assertEqual(len({voice for voice, _ in chosen.values()}), len(chosen))
        instructions = realtime.agent_instructions(panel, panel.host, chosen[panel.host][1])
        self.assertIn("TURN BRIEF", instructions)
        self.assertIn("# Your voice", instructions)


class ShowStructureTest(unittest.TestCase):
    def test_tio_opens_guests_answer_in_turn_and_tio_closes(self):
        from discussion import realtime
        panel = fixture()
        first, second = (panel.personas[i].name for i in panel.roster)
        target, direction = realtime.show_step(panel)
        self.assertEqual(panel.speaker(), panel.host)
        self.assertEqual(target, first)
        self.assertIn("opening the show", direction)
        self.assertIn(second, direction)                              # both guests are introduced
        panel.heard(line(f"Welcome! {first}, what stands out?", handoff=first), [], 10)
        target, direction = realtime.show_step(panel)
        self.assertEqual((panel.personas[panel.speaker()].name, target), (first, second))
        panel.story_budgets[panel.story.id] = 30
        panel.heard(line(f"Growth is real. {second}?", handoff=second), [], 10)
        target, _ = realtime.show_step(panel)
        self.assertEqual((panel.personas[panel.speaker()].name, target), (second, None))


class RealtimeRoleTest(unittest.TestCase):
    def test_guests_are_told_they_are_not_the_host(self):
        from discussion import realtime
        panel = fixture()
        guest = panel.roster[0]
        host_name = panel.personas[panel.host].name
        self.assertIn("Your role: HOST", realtime.agent_instructions(panel, panel.host))
        instructions = realtime.agent_instructions(panel, guest)
        self.assertIn("Your role: GUEST EXPERT", instructions)
        self.assertIn(f"{host_name} is the host", instructions)
        panel.heard(line("Welcome. What stands out?", handoff=panel.personas[guest].name), [], 5)
        brief = realtime.TurnBrief(take="A view", goal="Answer", points=["Growth"], card_ids=[], facts=[], handoff=None, action="respond",
                                   max_seconds=12, visual=None)
        self.assertIn("You are a GUEST, not the host", realtime.render_brief(panel, brief))


class SkipRequestTest(unittest.IsolatedAsyncioTestCase):
    def test_skip_requests_pick_the_next_or_the_named_story(self):
        panel = fixture()
        later = panel.stories[1].headline
        self.assertIsNone(panel.requested_story("Why did revenue grow?"))
        self.assertIsNone(panel.requested_story("What did the analysts talk about?"))  # weak cue, no story named
        self.assertEqual(panel.requested_story("Can we fast forward to something else?"), 1)
        self.assertEqual(panel.requested_story(f"Let's talk about {later.lower()}"), 1)

    async def test_skip_plans_the_new_story_while_the_host_bridges(self):
        from discussion import realtime
        key = uuid4()
        item = api.Session(fixture())
        item.touched = time.monotonic()
        api.sessions[key] = item
        self.addCleanup(api.sessions.pop, key, None)
        briefs = iter(realtime.TurnBrief(take="A view", goal=f"Goal {i}", points=["p"], card_ids=[], facts=[], handoff=None, action="respond",
                                         max_seconds=12, visual=None) for i in range(10))
        with patch.object(realtime, "make_brief", side_effect=lambda panel: next(briefs)) as plan:
            first = await api.realtime_next(key, api.RealtimeStep(generation=0))
            await item.brief_prefetch.task
            skip = await api.realtime_listen(key, api.ListenerRequest(generation=0, text="Skip this one please"))
            self.assertEqual((skip["intent"], skip["headline"]), ("skip", item.panel.stories[1].headline))
            await item.jump[1].task                                     # planned during the host's teaser
            calls = plan.call_count
            resumed = await api.realtime_next(key, api.RealtimeStep(
                generation=0, heard=first["id"], text="Revenue grew.", interrupted=True,
                listener=[api.LiveLine(role="listener", text="Skip this one please"),
                          api.LiveLine(role="host", text="Sure, next up: the Fed.")]))
            self.assertEqual(resumed["story"], item.panel.stories[1].headline)
            self.assertEqual(plan.call_count, calls)                     # no wait: the opening was ready
            await item.brief_prefetch.task


class VisualTest(unittest.TestCase):
    cards = [{"id": "1.1", "point": "Net sales rose to $6.2 million from $5.8 million at the Hauppauge, New York plant",
              "status": "reported",
              "sources": [{"publisher": "Zacks", "quote": "Net sales increased 6.5% to $6.2 million from $5.8 million."}]},
             {"id": "1.2", "point": "Revenue was $27.1 billion; comparable sales rose 5.6% and transactions 5%",
              "status": "reported", "sources": [{"publisher": "CNBC", "quote": "Adjusted EPS was $2.56."}]}]

    def visual(self, kind, **parts):
        from discussion import visuals
        empty = dict(chart=None, flow=None, places=None, picture_article_id=None)
        return visuals.Visual(kind=kind, title="T", **{**empty, **parts})

    def chart(self, *values, unit="$M"):
        from discussion import visuals
        return visuals.ChartSpec(kind="bars", title="T", unit=unit, points=[
            visuals.ChartPoint(label=f"P{i}", value=v, card_id=card) for i, (v, card) in enumerate(values)])

    def problem(self, visual, pictures=None):
        from discussion import visuals
        return visuals.visual_problem(visual, self.cards, pictures or {})

    def test_charts_need_their_figures_on_the_cards_in_one_unit(self):
        self.assertIsNone(self.problem(self.visual("chart", chart=self.chart((5.8, "1.1"), (6.2, "1.1")))))
        self.assertIn("not on card", self.problem(self.visual("chart", chart=self.chart((5.8, "1.1"), (7.1, "1.1")))))
        self.assertIn("not on card", self.problem(self.visual("chart", chart=self.chart((5.8, "1.1"), (6.2, "9.9")))))
        self.assertEqual(self.problem(self.visual("chart", chart=self.chart((6.2, "1.1")))), "1 points")
        mixed = self.chart((27.1, "1.2"), (5.6, "1.2"), unit="")
        self.assertEqual(self.problem(self.visual("chart", chart=mixed)), "mixed units")
        same = self.chart((5.6, "1.2"), (5, "1.2"), unit="%")
        self.assertIsNone(self.problem(self.visual("chart", chart=same)))

    def test_flow_steps_and_map_places_come_from_the_cards(self):
        from discussion import visuals
        step = lambda label, card="1.1": visuals.FlowStep(label=label, card_id=card)
        self.assertIsNone(self.problem(self.visual("flow", flow=[step("Net sales $5.8 million"), step("Rose to $6.2 million")])))
        self.assertIn("not in the reporting", self.problem(self.visual("flow", flow=[step("Net sales"), step("Layoffs announced")])))
        self.assertIn("figure not on its card", self.problem(self.visual("flow", flow=[step("Net sales"), step("Sales $9 million")])))
        place = lambda name: visuals.MapPlace(name=name, latitude=40.8, longitude=-73.2, card_id="1.1")
        self.assertIsNone(self.problem(self.visual("map", places=[place("Hauppauge, New York")])))
        self.assertIn("not named", self.problem(self.visual("map", places=[place("Chicago")])))

    def test_maps_are_earth_only_and_flows_dont_promise_causes(self):
        from discussion import visuals
        mars = [{"id": "1.1", "point": "Curiosity found sulphur at Gediz Vallis on Mars", "status": "reported",
                 "sources": [{"publisher": "NASA", "quote": "Gediz Vallis channel"}]}]
        place = visuals.MapPlace(name="Gediz Vallis", latitude=-4.6, longitude=137.4, card_id="1.1")
        self.assertEqual(visuals.visual_problem(self.visual("map", places=[place]), mars, {}),
                         "maps show places on Earth only")
        flow = self.visual("flow", flow=[visuals.FlowStep(label="Net sales", card_id="1.1")] * 2)
        self.assertEqual(visuals.tidy(flow.model_copy(update={"title": "Why SpaceX stock rose Tuesday"})).title,
                         "SpaceX stock rose Tuesday: step by step")

    def test_figures_are_listed_with_their_units_for_charts(self):
        from discussion import visuals
        figures = visuals.card_figures(self.cards[1])
        self.assertIn({"value": 27.1, "unit": "$ billion"}, [{k: f[k] for k in ("value", "unit")} for f in figures])
        self.assertEqual(visuals.plain(20240500.0), "20240500")

    def test_pictures_are_only_the_storys_own_photos(self):
        photos = {"a1": {"url": "https://x/p.jpg", "publisher": "Zacks", "headline": "H"}}
        self.assertIsNone(self.problem(self.visual("picture", picture_article_id="a1"), photos))
        self.assertIsNotNone(self.problem(self.visual("picture", picture_article_id="zz"), photos))
        from discussion import visuals
        shown = visuals.payload(self.visual("picture", picture_article_id="a1"), self.cards, photos)
        self.assertEqual((shown["picture"]["url"], shown["sources"]), ("https://x/p.jpg", ["Zacks"]))


class VisualRequestTest(unittest.IsolatedAsyncioTestCase):
    async def test_asking_to_see_something_puts_it_on_screen(self):
        from discussion import visuals
        key = uuid4()
        item = api.Session(fixture())
        item.touched = time.monotonic()
        api.sessions[key] = item
        self.addCleanup(api.sessions.pop, key, None)
        report = item.panel.story.articles[1]
        item.panel.reviews[item.panel.story.id] = EvidenceReview(findings=[Finding(
            point="Growth was 6% by one estimate", status="reported",
            citations=[Citation(article_id=report.id, quote=report.body)])])
        point = visuals.ChartPoint(label="A", value=6, card_id="1.1")
        visual = visuals.Visual(kind="chart", title="Growth", chart=visuals.ChartSpec(
            kind="bars", title="Growth", unit="%", points=[point, point]), flow=None, places=None, picture_article_id=None)
        with patch.object(visuals, "model") as provider, patch.object(visuals, "story_pictures", return_value={}):
            provider.return_value.generate.return_value = visuals.VisualAnswer(visual=visual)
            result = await api.realtime_listen(key, api.ListenerRequest(generation=0, text="Can you draw that for me?"))
        self.assertEqual((result["intent"], result["visual"]["kind"]), ("visual", "chart"))
        self.assertIn("on their screen", result["note"])
        self.assertTrue(item.panel.wants_visuals)
        self.assertEqual(item.panel.visuals_shown[item.panel.story.id], ["chart: Growth"])
        plain = await api.realtime_listen(key, api.ListenerRequest(generation=0, text="What does that mean?"))
        self.assertEqual(plain["intent"], "question")



class AccuracyTest(unittest.TestCase):
    def panel(self):
        panel = fixture()
        report = panel.story.articles[1]
        panel.reviews[panel.story.id] = EvidenceReview(findings=[Finding(
            point="Revenue grew 6% in the quarter", status="reported",
            citations=[Citation(article_id=report.id, quote=report.body)])])
        return panel

    def test_brief_lines_the_reporting_doesnt_support_are_cut(self):
        from discussion import realtime
        panel = self.panel()
        invented = realtime.TurnBrief(take="A view", goal="Explain growth", points=["Ask what drove it", "Profit tripled to 40%"],
                                      facts=["Revenue grew 6% in the quarter", "Revenue grew 14% in the quarter"],
                                      card_ids=["1.1"], handoff=None, action="respond", max_seconds=15, visual=None)
        with patch.object(realtime, "model") as provider, self.assertLogs("discussion.realtime", level="WARNING"), \
                patch.object(realtime.visuals, "story_pictures", return_value={}):
            provider.return_value.generate.return_value = invented
            brief = realtime.make_brief(panel)
        self.assertEqual(brief.facts, ["Revenue grew 6% in the quarter"])
        self.assertEqual(brief.points, ["Ask what drove it"])
        self.assertIn("Revenue grew 6% in the quarter", realtime.render_brief(panel, brief))

    def test_a_wrong_figure_on_air_is_flagged_for_correction(self):
        from discussion import realtime
        panel = self.panel()
        self.assertEqual(realtime.figure_slips(panel, "So revenue grew about 6%, right?"), [])
        self.assertEqual(realtime.figure_slips(panel, "Revenue grew 14%, which is huge."), ["14"])


    def test_a_correction_may_quote_the_wrong_figure(self):
        from discussion import realtime
        panel = self.panel()
        panel.correction = "Ben said 14, which the reporting doesn't support. Correct it with the card's figure."
        fixing = realtime.TurnBrief(take="A view", goal="Correct it", points=["Put the figure right"],
                                    facts=["Quick correction: revenue grew 6% in the quarter, not 14%"],
                                    card_ids=["1.1"], handoff=None, action="respond", max_seconds=10, visual=None)
        with patch.object(realtime, "model") as provider, patch.object(realtime.visuals, "story_pictures", return_value={}):
            provider.return_value.generate.return_value = fixing
            self.assertEqual(realtime.make_brief(panel).facts, fixing.facts)


class VisualPacingTest(unittest.TestCase):
    def test_visuals_are_capped_per_story_and_photos_go_unmentioned(self):
        from discussion import realtime, visuals
        panel = fixture()
        self.assertEqual(visuals.expected(panel, {"a": {}}), "picture")
        panel.heard(line("Welcome.", handoff=panel.personas[panel.roster[0]].name), [], 5)
        self.assertEqual(visuals.expected(panel, {}), "chart, flow or map")
        panel.visuals_shown[panel.story.id] = ["picture: Photo", "chart: Growth"]
        self.assertEqual(visuals.expected(panel, {}), "none")
        panel.wants_visuals = True
        self.assertEqual(visuals.expected(panel, {}), "optional")
        photo = visuals.Visual(kind="picture", title="P", chart=None, flow=None, places=None, picture_article_id="a")
        brief = realtime.TurnBrief(take="A view", goal="g", points=["p"], facts=[], card_ids=[], handoff=None, action="respond",
                                   max_seconds=10, visual=photo)
        self.assertNotIn("screen", realtime.render_brief(panel, brief))


class ConversationTest(unittest.TestCase):
    def test_guests_reply_to_each_other_in_short_turns_without_forced_handoffs(self):
        from discussion import realtime
        panel = fixture()
        first, second = panel.roster
        panel.heard(line("Welcome. What stands out?", handoff=panel.personas[first].name), [], 5)
        answer = realtime.TurnBrief(take="It's overdone", goal="Answer", points=["p"], facts=[], card_ids=[],
                                    handoff=None, action="respond", max_seconds=35, visual=None)
        with patch.object(realtime, "model") as provider, \
                patch.object(realtime.visuals, "story_pictures", return_value={}):
            provider.return_value.generate.return_value = answer
            brief = realtime.make_brief(panel)
        self.assertEqual(brief.max_seconds, realtime.MAX_SECONDS)       # no more mini-speeches
        rendered = realtime.render_brief(panel, brief)
        self.assertIn("What you think", rendered)
        self.assertIn("Reply to what was just said", rendered)
        self.assertNotIn("End by putting a question", rendered)


class NoLoopTest(unittest.TestCase):
    def test_lines_from_earlier_stories_still_count_as_said(self):
        panel = fixture()
        said = "Revenue grew six percent this quarter, driven by stronger demand from industrial customers."
        panel.heard(line(said, handoff=panel.personas[panel.roster[0]].name), [], 5)
        panel.jump_to(1)                                             # a new story clears the story history…
        self.assertEqual(panel.history, [])
        self.assertTrue(panel.repeated_line("So revenue grew six percent this quarter on stronger industrial "
                                            "customer demand."))  # …but the briefing still remembers it
        self.assertIsNone(panel.repeated_line("The reserve held its policy rate and signalled patience."))
        self.assertEqual(panel.fork().said, panel.said)

    def test_used_cards_are_tracked_per_story(self):
        from discussion import realtime
        panel = fixture()
        brief = realtime.TurnBrief(take="A view", goal="g", points=["p"], facts=[], card_ids=["1.1", "1.2"],
                                   handoff=None, action="respond", max_seconds=10, visual=None)
        realtime.remember_cards(panel, brief)
        realtime.remember_cards(panel, brief)
        self.assertEqual(panel.cards_used[panel.story.id], ["1.1", "1.2"])
