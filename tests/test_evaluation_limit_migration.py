import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flask_migrate import downgrade, upgrade
from sqlalchemy import MetaData, Table, inspect, text
from platform_api import create_app
from platform_api.extensions import db
from platform_api.models import Competition, Problem, Team, User
from platform_api.seed import seed_database


class EvaluationLimitMigrationTests(unittest.TestCase):
    def test_backfill_and_roundtrip_preserve_budget_history(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app({"AUTO_CREATE_SCHEMA": False, "SEED_DATABASE": False, "TESTING": True,
                "INITIAL_ADMIN_PASSWORD": "ChangeMe123!", "INITIAL_REVIEWER_PASSWORD": "ChangeMe123!",
                "UPLOAD_FOLDER": root,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (Path(root) / "migration.sqlite").as_posix()})
            with app.app_context():
                try:
                    upgrade(revision="a2c5e94d320b")
                    seed_database()
                    user = User.query.first()
                    competition = Competition.query.first()
                    problem = Problem.query.first()
                    team = Team(name="migration", competition_id=competition.id, captain_id=user.id, invite_code="migration")
                    db.session.add(team)
                    db.session.flush()
                    metadata = MetaData()
                    submissions = Table("submissions", metadata, autoload_with=db.engine)
                    versions = Table("submission_versions", metadata, autoload_with=db.engine)
                    runs = Table("evaluation_runs", metadata, autoload_with=db.engine)
                    now = datetime.now(timezone.utc)
                    db.session.execute(submissions.insert().values(id="submitted", problem_id=problem.id, team_id=team.id,
                        title="Before upgrade", status="submitted", current_version=1, created_at=now, updated_at=now))
                    db.session.execute(versions.insert().values(id="version", submission_id="submitted", version=1,
                        readme_md="", fields={}, snapshot={}, status="submitted", created_by=user.id, created_at=now))
                    for status in ("failed", "superseded", "queued"):
                        # Use the historical schema rather than the latest ORM,
                        # which now also includes frozen runtime snapshots.
                        db.session.execute(runs.insert().values(id=str(uuid4()), problem_id=problem.id,
                            submission_version_id="version", status=status, config_snapshot={}, submission_snapshot={},
                            attempts=0, metrics={}, episodes=[], api_calls_used=0, created_at=now, updated_at=now))
                    db.session.commit()
                    db.session.remove()
                    upgrade()
                    self.assertEqual(db.session.execute(text("SELECT evaluation_runs_used FROM submissions")).scalar(), 3)
                    self.assertEqual(db.session.execute(text("PRAGMA foreign_key_check")).all(), [])
                    db.session.remove()
                    with self.assertRaises(SystemExit):
                        downgrade(revision="a2c5e94d320b")
                    self.assertIn("evaluation_runs_used", {column["name"] for column in inspect(db.engine).get_columns("submissions")})
                    # Empty test fixtures can be downgraded and upgraded safely.
                    db.session.execute(text("DELETE FROM evaluation_runs"))
                    db.session.execute(text("UPDATE submissions SET evaluation_runs_used = 0"))
                    db.session.commit()
                    db.session.remove()
                    downgrade(revision="a2c5e94d320b")
                    upgrade()
                    self.assertEqual(db.session.execute(text("SELECT evaluation_runs_used FROM submissions")).scalar(), 0)
                finally:
                    db.session.remove()
                    db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
