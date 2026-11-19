import unittest

from main import app, health


class HealthTest(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        self.assertIn("get", app.openapi()["paths"]["/api/health"])
        self.assertEqual(health(), {"status": "ok"})
