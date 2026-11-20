import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from pydantic import ValidationError

from api.recommend import RecommendRequest, recommend
from main import app
from research.config import EMBED_MODEL
from research.recommend import (Candidate, NoProfileSignal, ensure_user_embedding, profile_text, rerank,
                                story_count)

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def candidate(id, embedding, similarity, *, days_old=0.0, translation_of=None) -> Candidate:
    return Candidate(id=id, headline=id, permalink=f"https://example.com/{id}",
                     published_at=NOW - timedelta(days=days_old), publication="Outlet", language="en",
                     translation_of=translation_of, main_subject=None, event_type=None, topics=[], people=[],
                     embedding=embedding, similarity=similarity)


class FakeConn:
    """Answers the profile query with `row` and records every other statement."""

    def __init__(self, row):
        self.row, self.writes, self.commits = row, [], 0

    def execute(self, sql, params=None):
        if sql.lstrip().startswith("select"):
            return self
        self.writes.append((sql, params))
        return self

    def fetchone(self):
        return self.row

    def commit(self):
        self.commits += 1


def profile_row(*, interests="ECB decisions and the euro", goal="FX trading", country="NL", risk=4,
                embedding=None, model=None, history=None):
    return (interests, goal, None, country, risk, "en", ["nl"], 10,
            embedding, None, model, history, ["seen-1"])


class RecommendTest(unittest.TestCase):
    def test_profile_text_uses_the_filled_fields(self) -> None:
        text = profile_text({"interests_text": " Oil, gold ", "listener_goal": "Trading", "risk_appetite": 5})
        self.assertEqual(text, "Interests: Oil, gold\nGoal: Trading\nRisk appetite: speculative")

    def test_story_count_follows_minutes_within_bounds(self) -> None:
        self.assertEqual(story_count(15), 10)
        self.assertEqual(story_count(1), 3)
        self.assertEqual(story_count(60), 15)
        self.assertEqual(story_count(None), 7)

    def test_missing_embedding_is_created_and_saved_with_the_current_model(self) -> None:
        conn = FakeConn(profile_row())
        calls = []
        vector, profile = ensure_user_embedding(conn, "u1", embed=lambda t: calls.append(t) or [3.0, 4.0])

        self.assertEqual(vector, [0.6, 0.8])
        self.assertEqual(profile["interest_embedding_source"], "onboarding")
        self.assertIn("Interests: ECB decisions and the euro", calls[0])
        sql, params = conn.writes[0]
        self.assertIn("update public.profiles", sql)
        self.assertEqual(params[1:], ("onboarding", EMBED_MODEL, "u1"))
        self.assertEqual(conn.commits, 1)

    def test_history_is_blended_into_a_hybrid(self) -> None:
        conn = FakeConn(profile_row(history="[0, 1]"))
        vector, profile = ensure_user_embedding(conn, "u1", embed=lambda t: [1.0, 0.0])
        self.assertEqual(profile["interest_embedding_source"], "hybrid")
        self.assertAlmostEqual(vector[0], vector[1])

    def test_current_embedding_is_reused_without_calling_openai(self) -> None:
        conn = FakeConn(profile_row(embedding="[1, 0]", model=EMBED_MODEL))
        vector, _ = ensure_user_embedding(conn, "u1", embed=lambda t: self.fail("should not embed"))
        self.assertEqual(vector, [1, 0])
        self.assertEqual(conn.writes, [])

    def test_embedding_from_another_model_is_replaced(self) -> None:
        conn = FakeConn(profile_row(embedding="[1, 0]", model="nomic-embed-text-v2-moe"))
        ensure_user_embedding(conn, "u1", embed=lambda t: [0.0, 1.0])
        self.assertEqual(len(conn.writes), 1)

    def test_empty_profile_without_history_is_rejected(self) -> None:
        with self.assertRaises(NoProfileSignal):
            ensure_user_embedding(FakeConn(profile_row(interests=None, goal=None, country=None, risk=None)),
                                  "u1", embed=lambda t: [1.0])

    def test_rerank_skips_duplicates_and_translations(self) -> None:
        picked = rerank([
            candidate("ecb-en", [1.0, 0.0, 0.0], 0.90),
            candidate("ecb-nl", [0.6, 0.8, 0.0], 0.88, translation_of="ecb-en"),
            candidate("ecb-copy", [0.99, 0.14, 0.0], 0.89),
            candidate("oil", [0.0, 0.0, 1.0], 0.70),
        ], count=3)
        self.assertEqual([c.id for c in picked], ["ecb-en", "oil"])

    def test_rerank_prefers_newer_articles_when_similarity_ties(self) -> None:
        picked = rerank([
            candidate("old", [1.0, 0.0], 0.80, days_old=5),
            candidate("new", [0.0, 1.0], 0.80, days_old=0),
        ], count=1)
        self.assertEqual(picked[0].id, "new")

    def test_endpoint_needs_exactly_one_listener_and_local_client(self) -> None:
        self.assertIn("post", app.openapi()["paths"]["/api/recommend"])
        for body in ({}, {"user_id": "00000000-0000-0000-0000-000000000000", "profile_text": "both"}):
            with self.assertRaises(ValidationError):
                RecommendRequest(**body)
        remote = Request({"type": "http", "client": ("192.0.2.1", 50000), "headers": []})
        with self.assertRaises(HTTPException) as caught:
            recommend(RecommendRequest(profile_text="FX news"), remote)
        self.assertEqual(caught.exception.status_code, 403)

if __name__ == "__main__":
    unittest.main()
