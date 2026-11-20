import unittest
from uuid import UUID

from fastapi import HTTPException

from api.discussion import SpeakerRequest, next_speaker
from main import app

MARKETS = UUID(int=1)
EXPLAINER = UUID(int=2)
ENERGY = UUID(int=3)


class DiscussionTest(unittest.TestCase):
    def test_routes_listener_questions_and_host_replies(self) -> None:
        self.assertIn("post", app.openapi()["paths"]["/api/discussion/next-speaker"])
        request = SpeakerRequest(ranked_host_ids=[EXPLAINER, MARKETS, ENERGY])
        plan = next_speaker(request)
        self.assertEqual(plan.roster, [EXPLAINER, MARKETS])
        self.assertEqual(plan.next_host_id, EXPLAINER)

        reply = next_speaker(request.model_copy(update={"reply_to_host_id": EXPLAINER}))
        self.assertEqual(reply.next_host_id, MARKETS)

        addressed = next_speaker(request.model_copy(update={"addressed_host_id": ENERGY}))
        self.assertIn(ENERGY, addressed.roster)
        self.assertEqual(addressed.next_host_id, ENERGY)

        single_host = next_speaker(request.model_copy(update={
            "ranked_host_ids": [MARKETS],
            "reply_to_host_id": MARKETS,
        }))
        self.assertIsNone(single_host.next_host_id)

        empty = next_speaker(SpeakerRequest(ranked_host_ids=[]))
        self.assertEqual(empty.roster, [])
        self.assertIsNone(empty.next_host_id)

    def test_rejects_addressing_an_ineligible_host(self) -> None:
        request = SpeakerRequest(ranked_host_ids=[MARKETS], addressed_host_id=ENERGY)
        with self.assertRaises(HTTPException) as failure:
            next_speaker(request)
        self.assertEqual(failure.exception.status_code, 422)
