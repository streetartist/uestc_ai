from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from platform_api import create_app  # noqa: E402
from platform_api.extensions import db  # noqa: E402

STRONG_KEY = "k3Yq9-test-only-0123456789abcdef"
MESSAGE = "SECRET_KEY must be set"


class SecretKeyPolicyTestCase(unittest.TestCase):
    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        self.addCleanup(self.runtime.cleanup)
        path = Path(self.runtime.name)
        self.base = {
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (path / "test.sqlite3").as_posix(),
            "UPLOAD_FOLDER": str(path / "uploads"),
            "AUTO_CREATE_SCHEMA": False,
            "SEED_DATABASE": False,
            "TESTING": False,
        }

    def build(self, secret_key=None, **overrides):
        config = {**self.base, **overrides}
        if secret_key is not None:
            config["SECRET_KEY"] = secret_key
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECRET_KEY", None)
            app = create_app(config)
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        return app

    def test_missing_secret_key_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, MESSAGE):
            self.build()

    def test_placeholder_and_blank_keys_are_rejected(self):
        for value in ("dev-change-me", "Dev-Only-Change-Me-Before-Production", "change-me", "CHANGEME", "", "   "):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, MESSAGE):
                self.build(value)

    def test_short_key_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, MESSAGE):
            self.build("short-key-15chr")

    def test_strong_key_is_accepted(self):
        self.assertEqual(self.build(STRONG_KEY).config["SECRET_KEY"], STRONG_KEY)

    def test_testing_mode_skips_check(self):
        self.build(TESTING=True)

    def test_auto_create_schema_skips_check(self):
        self.build(AUTO_CREATE_SCHEMA=True)

    def test_seed_database_with_passwords_skips_check(self):
        # Seeding itself is out of scope here (it needs tables); only the startup check is exercised.
        with patch("platform_api.seed.seed_database") as seed:
            self.build(
                SEED_DATABASE=True,
                INITIAL_ADMIN_PASSWORD="ChangeMe123!",
                INITIAL_REVIEWER_PASSWORD="ChangeMe123!",
            )
        seed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
