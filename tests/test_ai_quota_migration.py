from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flask_migrate import downgrade, upgrade
from sqlalchemy import MetaData, Table, text
from platform_api import create_app
from platform_api.extensions import db
from platform_api.ai_models import AIGrant, AIKey, AIProblemQuota, AIUsage
from platform_api.models import Competition, Problem, Team, Track, User, utcnow


class ProblemQuotaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "AUTO_CREATE_SCHEMA": False, "SEED_DATABASE": False,
            "UPLOAD_FOLDER": self.runtime.name,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (Path(self.runtime.name) / "test.sqlite3").as_posix()})
        self.addCleanup(self.close)
        with self.app.app_context():
            upgrade(revision="d8a2f19c630e")
            user = User(id="legacy-user", name="Legacy", email="legacy@example.com", role="member", password_hash="unused")
            competition = Competition(id="legacy-competition", name="Legacy", slug="legacy", summary="Legacy")
            db.session.add_all([user, Team(id="legacy-team", competition=competition, name="Legacy Team", captain_id=user.id, invite_code="legacy-invite")])
            db.session.commit()
            table = Table("ai_grants", MetaData(), autoload_with=db.engine)
            db.session.execute(table.insert().values(id="legacy-grant", team_id="legacy-team", enabled=True,
                allowed_models=["model"], allowed_channels=[], max_calls=100, max_tokens=1000000,
                max_output_tokens=4096, requests_per_minute=60, max_concurrent=2, calls_used=3,
                tokens_used=40, cost_used_micros=0, created_at=utcnow(), updated_at=utcnow()))
            db.session.add(AIKey(id="legacy-key", grant_id="legacy-grant", created_by=user.id,
                name="Legacy Key", key_hash="a" * 64, prefix="sk-legacy"))
            db.session.add(AIUsage(id="legacy-usage", grant_id="legacy-grant", key_id="legacy-key",
                model="model", endpoint="chat/completions", status="completed", deadline=utcnow(),
                input_tokens=7, output_tokens=3, charged_tokens=10, charged_cost_micros=0))
            db.session.commit()

    def close(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.runtime.cleanup()

    def test_upgrade_and_roundtrip_preserve_existing_budgets_keys_and_usage(self):
        with self.app.app_context():
            upgrade()
            grant = db.session.get(AIGrant, "legacy-grant")
            self.assertIsNone(grant.problem_id)
            self.assertEqual((grant.calls_used, grant.tokens_used), (3, 40))
            self.assertEqual(db.session.get(AIKey, "legacy-key").grant_id, grant.id)
            self.assertEqual(db.session.get(AIUsage, "legacy-usage").charged_tokens, 10)
            self.assertEqual(db.session.execute(text("PRAGMA foreign_key_check")).all(), [])
            db.session.remove()
            downgrade(revision="d8a2f19c630e")
            upgrade()
            self.assertEqual(db.session.get(AIGrant, "legacy-grant").tokens_used, 40)

    def test_downgrade_refuses_to_merge_or_remove_problem_scopes(self):
        with self.app.app_context():
            upgrade()
            track = Track(id="legacy-track", competition_id="legacy-competition", name="Track", slug="track")
            problem = Problem(id="scoped-problem", track=track, title="Scoped", slug="scoped", code="SCOPED")
            db.session.add(AIGrant(id="scoped-grant", team_id="legacy-team", problem=problem,
                allowed_models=["model"], max_calls=10, max_tokens=10000))
            db.session.commit()
            db.session.remove()
            # Flask-Migrate turns a rejected migration into a nonzero CLI exit.
            with self.assertRaises(SystemExit) as rejected:
                downgrade(revision="d8a2f19c630e")
            self.assertEqual(rejected.exception.code, 1)
            self.assertEqual(db.session.get(AIGrant, "scoped-grant").problem_id, "scoped-problem")
            self.assertEqual(db.session.get(AIGrant, "legacy-grant").tokens_used, 40)

    def test_uniform_policy_migration_preserves_policy_and_refuses_lossy_downgrade(self):
        with self.app.app_context():
            upgrade()
            track = Track(id="policy-track", competition_id="legacy-competition", name="Track", slug="policy")
            problem = Problem(id="policy-problem", track=track, title="Policy", slug="policy", code="POLICY")
            db.session.add(AIProblemQuota(problem=problem, config={"max_calls": 42, "max_tokens": 10000}))
            db.session.commit()
            db.session.remove()
            with self.assertRaises(SystemExit) as rejected:
                downgrade(revision="e9c3a72b108f")
            self.assertEqual(rejected.exception.code, 1)
            self.assertEqual(db.session.get(AIProblemQuota, "policy-problem").config["max_calls"], 42)
            self.assertEqual(db.session.get(AIGrant, "legacy-grant").tokens_used, 40)
            self.assertEqual(db.session.execute(text("PRAGMA foreign_key_check")).all(), [])
