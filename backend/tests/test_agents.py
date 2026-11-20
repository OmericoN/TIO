import unittest

from discussion.agents import DiscussionLine, SourceExcerpt, TurnContext, prepare_agent_turn
from discussion.personas import load_starter_personas


class AgentTurnTest(unittest.TestCase):
    def test_starter_personas_prepare_individual_turns(self) -> None:
        personas = load_starter_personas()
        ids = {persona.slug: persona.id for persona in personas.values()}
        self.assertEqual(set(ids), {"main-host", "markets", "economy"})
        ranked = [ids["markets"], ids["economy"]]
        prior_turn = DiscussionLine(speaker="Markets Specialist", text="What is the comparison period?")
        context = TurnContext(
            headline="Sample data release",
            headline_article_id="sample-1",
            article_tones=[{"article_id": "sample-1", "desired_tone": "serious"}],
            listener_preferences="Explain economic terms in plain English.",
            question="What does this measure?",
            recent_turns=[prior_turn],
            source_excerpts=[SourceExcerpt(article_id="sample-1", excerpt="The measure is year over year.")],
        )
        first = prepare_agent_turn(personas, ranked, context)
        reply = prepare_agent_turn(personas, ranked, context, reply_to_host_id=ids["markets"])

        assert first is not None and reply is not None
        self.assertEqual(first.persona.id, ids["markets"])
        self.assertEqual(reply.persona.id, ids["economy"])
        self.assertIn("observed change", first.instructions)
        self.assertIn("central bank decisions", reply.instructions)
        self.assertIn("desired_tone", reply.instructions)
        self.assertIn("exact quote", reply.instructions)
        self.assertNotIn("Sample data release", reply.instructions)
        self.assertNotIn("What does this measure?", reply.instructions)
        self.assertEqual(TurnContext.model_validate_json(reply.input), context)

    def test_catalog_mismatch_is_reported(self) -> None:
        personas = load_starter_personas()
        context = TurnContext(headline="Sample data release")
        self.assertIsNone(prepare_agent_turn(personas, [], context))
        with self.assertRaisesRegex(ValueError, "missing from the catalog"):
            prepare_agent_turn({}, list(personas), context)
