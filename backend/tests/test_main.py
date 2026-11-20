import unittest

from api.health import health
from main import app


class HealthTest(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        self.assertIn("get", app.openapi()["paths"]["/api/health"])
        self.assertEqual(health(), {"status": "ok"})
