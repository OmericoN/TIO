import io
import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from urllib.error import HTTPError

from fastapi import HTTPException

from api import profile
from main import app
from recommender.recommend import NoProfileSignal

USER_ID = "5f0c8a52-6b1f-4a59-9d4c-2f3c7d0e9a11"
ENV = {"SUPABASE_URL": "https://example.supabase.co/", "SUPABASE_SECRET_KEY": "sb_secret_test"}


def supabase_returns(user: dict):
    seen = {}

    @contextmanager
    def fake_urlopen(request, timeout):
        seen["url"], seen["headers"] = request.full_url, dict(request.header_items())
        yield io.BytesIO(json.dumps(user).encode())

    return fake_urlopen, seen


@contextmanager
def fake_connect():
    yield object()


class ProfileEmbedTest(unittest.TestCase):
    def test_route_is_registered(self) -> None:
        self.assertIn("post", app.openapi()["paths"]["/api/profile/embed"])

    def test_missing_token_is_rejected_before_calling_supabase(self) -> None:
        for header in (None, "", "Basic abc", "Bearer  "):
            with self.subTest(header=header), self.assertRaises(HTTPException) as caught:
                profile.signed_in_user_id(header)
            self.assertEqual(caught.exception.status_code, 401)

    def test_token_is_checked_with_supabase_auth(self) -> None:
        fake_urlopen, seen = supabase_returns({"id": USER_ID})
        with patch.dict(os.environ, ENV), patch.object(profile, "urlopen", fake_urlopen):
            self.assertEqual(profile.signed_in_user_id("Bearer token-123"), USER_ID)
        self.assertEqual(seen["url"], "https://example.supabase.co/auth/v1/user")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer token-123")

    def test_expired_session_is_401(self) -> None:
        def expired(request, timeout):
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        with patch.dict(os.environ, ENV), patch.object(profile, "urlopen", expired), \
                self.assertRaises(HTTPException) as caught:
            profile.signed_in_user_id("Bearer old")
        self.assertEqual(caught.exception.status_code, 401)

    def test_signed_in_user_is_embedded(self) -> None:
        fake_urlopen, _ = supabase_returns({"id": USER_ID})
        calls = []

        def fake_ensure(conn, user_id):
            calls.append(user_id)
            return [0.0], {"interest_embedding_source": "onboarding"}

        with patch.dict(os.environ, ENV), patch.object(profile, "urlopen", fake_urlopen), \
                patch.object(profile, "connect", fake_connect), patch.object(profile, "ensure_user_embedding", fake_ensure):
            result = profile.embed_profile("Bearer token-123")
        self.assertEqual(calls, [USER_ID])
        self.assertEqual(result.source, "onboarding")

    def test_profile_without_signal_is_422(self) -> None:
        fake_urlopen, _ = supabase_returns({"id": USER_ID})

        def no_signal(conn, user_id):
            raise NoProfileSignal("empty")

        with patch.dict(os.environ, ENV), patch.object(profile, "urlopen", fake_urlopen), \
                patch.object(profile, "connect", fake_connect), patch.object(profile, "ensure_user_embedding", no_signal), \
                self.assertRaises(HTTPException) as caught:
            profile.embed_profile("Bearer token-123")
        self.assertEqual(caught.exception.status_code, 422)


class FakeConnection:
    def __init__(self, row):
        self.row, self.sql, self.params, self.committed = row, None, None, False

    def execute(self, sql, params):
        self.sql, self.params = sql, params
        return self

    def fetchone(self):
        return self.row

    def commit(self):
        self.committed = True


def connection_returning(row):
    conn = FakeConnection(row)

    @contextmanager
    def fake():
        yield conn

    return fake, conn


class ProfileInterestsTest(unittest.TestCase):
    def test_routes_are_registered(self) -> None:
        paths = app.openapi()["paths"]["/api/profile/interests"]
        self.assertIn("get", paths)
        self.assertIn("put", paths)

    def test_interest_list_puts_the_strongest_topics_first(self) -> None:
        levels = {"Technology": 2, "Artificial intelligence": 3, "Currencies": 1, "Science and space": 3}
        self.assertEqual(profile.interests_text(levels, "  Dutch   housing "),
                         "Artificial intelligence, Science and space, Technology, Currencies, Dutch housing")
        self.assertIsNone(profile.interests_text({}, " "))

    def test_levels_outside_the_radar_are_rejected(self) -> None:
        for levels in ({"Technology": 4}, {"Technology": 0}, {"A, B": 2}, {"": 1},
                       {f"Topic {i}": 1 for i in range(41)}):
            with self.subTest(levels=levels), self.assertRaises(ValueError):
                profile.InterestUpdate(levels=levels)

    def test_saving_rewrites_the_interest_list_and_drops_a_stale_vector(self) -> None:
        fake_urlopen, _ = supabase_returns({"id": USER_ID})
        saved = {"Artificial intelligence": 3, "Technology": 1}
        fake, conn = connection_returning((saved, "Artificial intelligence, Technology, City budgets"))
        body = profile.InterestUpdate(levels={"Technology": 1, "Artificial intelligence": 3}, other="City budgets")
        with patch.dict(os.environ, ENV), patch.object(profile, "urlopen", fake_urlopen), \
                patch.object(profile, "connect", fake):
            result = profile.save_interests(body, "Bearer token-123")
        levels, text, compared, compared_levels, user_id = conn.params
        self.assertEqual(levels.obj, {"Technology": 1, "Artificial intelligence": 3})
        self.assertEqual(text, "Artificial intelligence, Technology, City budgets")
        self.assertEqual(compared, text)
        self.assertEqual(compared_levels.obj, levels.obj)
        self.assertEqual(user_id, USER_ID)
        self.assertIn("or interest_levels is distinct from", conn.sql)
        self.assertTrue(conn.committed)
        self.assertEqual(result.levels, saved)

    def test_reading_without_a_profile_is_404(self) -> None:
        fake_urlopen, _ = supabase_returns({"id": USER_ID})
        fake, _ = connection_returning(None)
        with patch.dict(os.environ, ENV), patch.object(profile, "urlopen", fake_urlopen), \
                patch.object(profile, "connect", fake), self.assertRaises(HTTPException) as caught:
            profile.read_interests("Bearer token-123")
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
