from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from platform_api import create_app  # noqa: E402
from platform_api.extensions import db  # noqa: E402

PASSWORD = "ChangeMe123!"
ADMIN = "admin@uestcai.top"
PROTECTED = "/api/auth/logout"
EVIL = "http://evil.example"


class OriginGuardBase:
    # Mixin: concrete subclasses must also inherit unittest.TestCase.
    cors_origins = "https://app.example.com"

    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        path = Path(self.runtime.name)
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (path / "test.sqlite3").as_posix(),
            "UPLOAD_FOLDER": str(path / "uploads"),
            "AUTO_CREATE_SCHEMA": True,
            "SEED_DATABASE": True,
            "ENFORCE_COMPETITION_DEADLINES": False,
            "CORS_ORIGINS": self.cors_origins,
            "INITIAL_ADMIN_PASSWORD": PASSWORD,
            "INITIAL_REVIEWER_PASSWORD": PASSWORD,
        })

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.runtime.cleanup()

    def logged_in_client(self):
        client = self.app.test_client()
        response = client.post("/api/auth/login", json={"email": ADMIN, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_json())
        return client, response.get_json()["token"]

    def assertRejected(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"error": "cross-origin request rejected"})

    def test_cookie_with_same_hostname_origin_passes(self):
        client, _ = self.logged_in_client()
        response = client.post(PROTECTED, headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_cookie_with_foreign_or_null_origin_is_rejected(self):
        for origin in (EVIL, "null"):
            with self.subTest(origin=origin):
                client, token = self.logged_in_client()
                self.assertRejected(client.post(PROTECTED, headers={"Origin": origin}))
                # The session must still be valid because the request never ran.
                me = client.get("/api/auth/me")
                self.assertEqual(me.status_code, 200, me.get_json())

    def test_bearer_header_with_foreign_origin_passes(self):
        client, token = self.logged_in_client()
        response = client.post(PROTECTED, headers={"Origin": EVIL, "Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_missing_origin_passes(self):
        client, _ = self.logged_in_client()
        self.assertEqual(client.post(PROTECTED).status_code, 200)

    def test_safe_method_with_foreign_origin_passes(self):
        client, _ = self.logged_in_client()
        response = client.get("/api/auth/me", headers={"Origin": EVIL})
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_request_without_cookie_is_not_blocked(self):
        client = self.app.test_client()
        response = client.post("/api/auth/login", json={"email": ADMIN, "password": PASSWORD}, headers={"Origin": EVIL})
        self.assertEqual(response.status_code, 200, response.get_json())


class DefaultOriginsGuardTestCase(OriginGuardBase, unittest.TestCase):
    cors_origins = ""

    def test_default_local_origin_passes(self):
        client, _ = self.logged_in_client()
        response = client.post(PROTECTED, headers={"Origin": "http://127.0.0.1:30011"})
        self.assertEqual(response.status_code, 200, response.get_json())

class ConfiguredOriginsGuardTestCase(OriginGuardBase, unittest.TestCase):
    def test_cookie_with_configured_cors_origin_passes(self):
        client, _ = self.logged_in_client()
        response = client.post(PROTECTED, headers={"Origin": "https://app.example.com"})
        self.assertEqual(response.status_code, 200, response.get_json())


if __name__ == "__main__":
    unittest.main()
