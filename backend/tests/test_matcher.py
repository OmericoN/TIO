import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from uuid import UUID

from research.matcher import ARTICLE_TONES, CLASSIFY, Classification, Classifications, PersonaMatcher, Ratings, create_matcher
from research.openai_client import MatchProcessingError, OpenAIClient
from research.repository import PersonaMatchRepository


CALM, WITTY, CHEERFUL, EXPERT = [str(UUID(int=i)) for i in range(1, 5)]


def article(id, language, headline, body):
    return dict(id=id, language=language, headline=headline, excerpt=headline,
                text_content=body, categories=["BUSINESS"])


def persona(id, personality, tone="calm", secondary=None):
    return dict(id=id, personality_md=personality, tone=tone, secondary_tones=secondary,
                humor_level=None, do_rules=[], dont_rules=[], expertise_topics=["BUSINESS"])


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.articles = {
            "de": article("de", "de", "Umsatz steigt", "Der Umsatz stieg. Bei einem Unfall starb ein Mitarbeiter."),
            "nl": article("nl", "nl", "Resultaten bekend", "Het bedrijf publiceerde de kwartaalcijfers."),
            "en": article("en", "en", "Market update", "The rally died as prices settled."),
        }
        self.personas = {
            CALM: persona(CALM, "Patient and factual."),
            WITTY: persona(WITTY, "Enjoys wordplay.", secondary=["witty"]),
            CHEERFUL: persona(CHEERFUL, "Always makes a joke.", tone="conversational"),
            EXPERT: persona(EXPERT, "A measured industry analyst."),
        }
        self.repo = Mock(spec=PersonaMatchRepository)
        self.repo.get_articles_by_ids.side_effect = lambda ids: {
            k: v for k, v in self.articles.items() if k in ids
        }
        self.repo.get_personas_by_ids.side_effect = lambda ids: {
            k: v for k, v in self.personas.items() if k in ids
        }
        self.client = Mock(spec=OpenAIClient, model="gpt-5-mini")
        self.client.generate.side_effect = self.generate
        self.matcher = PersonaMatcher(self.repo, self.client)

    def generate(self, stage, instructions, data, output_type):
        if stage == "classification":
            tones = {"en": "analytical", "de": "somber", "nl": "neutral"}
            return output_type(articles=[
                dict(id=a["id"], desired_tone=tones[a["id"]], event=a["headline"])
                for a in reversed(data["articles"])
            ])
        return output_type(personas=[
            dict(id=p["id"], delivery_fit=0 if p["id"] == CHEERFUL else 2, expertise=1)
            for p in reversed(data["personas"])
        ])

    def test_mixed_briefing_preserves_sources_filters_hosts_and_reuses_classifications(self):
        articles = ["en", "de", "nl", "en"]
        hosts = [WITTY, CHEERFUL, UUID(EXPERT), CALM, EXPERT]
        result = self.matcher.match_personas(articles, hosts, 3)
        self.assertEqual(result, {
            "articles": [
                {"article_id": a, "desired_tone": tone}
                for a, tone in zip(articles, ["analytical", "somber", "neutral", "analytical"])
            ],
            "agent_ids": [EXPERT, CALM],
        })
        self.repo.get_articles_by_ids.assert_called_once_with(["en", "de", "nl"])
        self.repo.get_personas_by_ids.assert_called_once_with([WITTY, CHEERFUL, EXPERT, CALM])
        classify, rank = self.client.generate.call_args_list
        self.assertEqual(classify.args[2]["articles"][1]["text_content"], self.articles["de"]["text_content"])
        self.assertEqual([p["id"] for p in rank.args[2]["personas"]], [CHEERFUL, EXPERT, CALM])
        self.assertEqual(rank.args[2]["articles"][1]["desired_tone"], "somber")
        self.client.generate.reset_mock()
        self.assertEqual(self.matcher.match_personas(["de", "en"], [CALM], 1)["agent_ids"], [CALM])
        self.assertEqual([c.args[0] for c in self.client.generate.call_args_list], ["ranking"])

    def test_no_suitable_host_preserves_article_tones(self):
        self.personas[WITTY]["tone"] = "witty"
        self.personas[WITTY]["secondary_tones"] = None
        for hosts in ([WITTY], [CHEERFUL], []):
            self.assertEqual(self.matcher.match_personas(["de"], hosts, 1), {
                "articles": [{"article_id": "de", "desired_tone": "somber"}], "agent_ids": []
            })
        self.assertEqual([c.args[0] for c in self.client.generate.call_args_list],
                         ["classification", "ranking"])

    def test_batching_cache_eviction_and_content_change(self):
        with patch("research.matcher.BATCH_BYTES", 1), patch("research.matcher.CACHE_SIZE", 1):
            self.matcher.match_personas(["en", "nl"], [], 1)
            self.matcher.match_personas(["nl"], [], 1)
            self.matcher.match_personas(["en"], [], 1)
            self.articles["en"]["text_content"] += " Updated."
            self.matcher.match_personas(["en"], [], 1)
        self.assertEqual(self.client.generate.call_count, 4)

    def test_invalid_inputs_do_not_reach_provider(self):
        for args in [(["missing"], [], 1), (["en"], [str(UUID(int=9))], 1),
                     (["en"], ["bad"], 1), (["en"], [], 0), (["en"], [], True), ([], [], 1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.matcher.match_personas(*args)
        self.client.generate.assert_not_called()

    def test_invalid_classification_is_not_cached(self):
        for ids in ([], ["en", "en"], ["other"]):
            self.client.generate.side_effect = None
            self.client.generate.return_value = Classifications(articles=[
                dict(id=id, desired_tone="neutral", event="Update") for id in ids
            ])
            with self.assertRaises(MatchProcessingError):
                self.matcher.match_personas(["en"], [], 1)
        self.client.generate.side_effect = self.generate
        self.assertEqual(self.matcher.match_personas(["en"], [], 1)["articles"][0]["desired_tone"], "analytical")

    def test_outside_or_excluded_candidate_is_a_processing_error(self):
        self.matcher.match_personas(["de"], [], 1)
        for id in [WITTY, EXPERT]:
            self.client.generate.side_effect = None
            self.client.generate.return_value = Ratings(personas=[dict(id=id, delivery_fit=3, expertise=2)])
            with self.assertRaises(MatchProcessingError):
                self.matcher.match_personas(["de"], [CALM, WITTY], 1)

    def test_fit_then_expertise_then_shortlist_order(self):
        self.matcher.match_personas(["en"], [], 1)
        self.client.generate.side_effect = None
        self.client.generate.return_value = Ratings(personas=[
            dict(id=CALM, delivery_fit=3, expertise=0),
            dict(id=WITTY, delivery_fit=2, expertise=2),
            dict(id=EXPERT, delivery_fit=3, expertise=1),
        ])
        result = self.matcher.match_personas(["en"], [WITTY, CALM, EXPERT], 2)
        self.assertEqual(result["agent_ids"], [EXPERT, CALM])


class AdapterTest(unittest.TestCase):
    def test_shared_article_tones_match_classifier_contract(self):
        schema = Classification.model_json_schema()["properties"]["desired_tone"]
        self.assertEqual(schema["enum"], list(ARTICLE_TONES))
        for tone, guidance in ARTICLE_TONES.items():
            with self.subTest(tone=tone):
                self.assertIn(guidance, CLASSIFY)
                row = Classification(id="story", event="Update", desired_tone=tone)
                self.assertEqual(row.desired_tone, tone)

    @patch("research.openai_client.urlopen")
    def test_openai_wire_format_usage_and_errors(self, send):
        client = OpenAIClient("test-key")

        def response(tone, finish_reason="stop"):
            return io.BytesIO(json.dumps({
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "choices": [{"finish_reason": finish_reason, "message": {"content": json.dumps({
                    "articles": [dict(id="en", desired_tone=tone, event="An event")]
                })}}],
            }).encode())

        send.side_effect = [response("serious"), response("happy"), response("somber", "length"),
                            io.BytesIO(b"{}"), URLError("offline")]
        with self.assertLogs("research.openai_client", level="INFO") as logs:
            result = client.generate("classification", "instructions", {}, Classifications)
        self.assertEqual(result.articles[0].desired_tone, "serious")
        self.assertIn("stage=classification model=gpt-5-mini input_tokens=100", logs.output[0])
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        schema = json.loads(request.data)["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertFalse(schema["schema"]["additionalProperties"])
        for _ in range(4):
            with self.assertRaises(MatchProcessingError):
                client.generate("classification", "instructions", {}, Classifications)

    @patch("research.openai_client.urlopen")
    @patch.dict("os.environ", {"OPENAI_API_KEY": ""})
    def test_blank_key_fails_before_any_api_call(self, send):
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            create_matcher()
        send.assert_not_called()

    @patch("research.repository.psycopg.connect")
    def test_repository_binds_each_batch_and_keys_rows_by_id(self, connect):
        database = connect.return_value.__enter__.return_value
        repo = PersonaMatchRepository("test-dsn")
        for fetch, ids in [(repo.get_articles_by_ids, ["ar'1", "ar2"]),
                           (repo.get_personas_by_ids, [CALM, EXPERT])]:
            database.execute.return_value = [{"id": item} for item in reversed(ids)]
            self.assertEqual(fetch(ids), {item: {"id": item} for item in ids})
            sql, params = database.execute.call_args.args
            self.assertIn("ANY(%s", sql)
            self.assertEqual(params, (ids,))
            self.assertNotIn(ids[0], sql)
        self.assertEqual(database.execute.call_count, 2)


@unittest.skipUnless(os.getenv("TIO_LIVE_MATCHER_HOSTS"), "requires two demo hosts and a live OpenAI key")
class LiveMatcherTest(unittest.TestCase):
    def test_demo_hosts_on_multilingual_briefing(self):
        cheerful, sober = [str(UUID(s.strip())) for s in os.environ["TIO_LIVE_MATCHER_HOSTS"].split(",")]
        stories = {
            "en": article("en", "en", "Closing prices", "The rally died as the index gave back its early gains."),
            "de": article("de", "de", "Umsatz steigt", "Der Umsatz stieg. Bei einem Betriebsunfall starb ein Mitarbeiter."),
            "nl": article("nl", "nl", "Jaarverslag verschenen", "Het bedrijf heeft vandaag zijn jaarverslag gepubliceerd."),
        }
        matcher = create_matcher()
        with patch.object(matcher.repository, "get_articles_by_ids", return_value=stories):
            result = matcher.match_personas(list(stories), [cheerful, sober], 2)
        tones = {row["article_id"]: row["desired_tone"] for row in result["articles"]}
        self.assertEqual(tones["de"], "somber")
        self.assertIn(tones["en"], ("analytical", "neutral"))
        self.assertIn(tones["nl"], ("analytical", "neutral"))
        self.assertEqual(result["agent_ids"], [sober])
