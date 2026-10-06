import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flask_migrate import upgrade, downgrade
from sqlalchemy import inspect, text
from platform_api import create_app
from platform_api.extensions import db
from platform_api.models import Competition, Track, Problem, ProblemRuntime


class ProblemRuntimeMigrationTest(unittest.TestCase):
    def test_incompatible_precreated_table_is_rejected_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True, "AUTO_CREATE_SCHEMA": False, "SEED_DATABASE": False,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (Path(directory) / "incompatible.sqlite").as_posix(), "UPLOAD_FOLDER": directory})
            with app.app_context():
                try:
                    upgrade(revision="b3f6a2d91804")
                    db.session.execute(text('CREATE TABLE checkpoint_entries (id INTEGER PRIMARY KEY)'))
                    db.session.execute(text('INSERT INTO checkpoint_entries (id) VALUES (7)'))
                    db.session.commit(); db.session.remove()
                    with self.assertRaises(SystemExit): upgrade()
                    self.assertEqual(db.session.execute(text('SELECT id FROM checkpoint_entries')).scalar(), 7)
                finally:
                    db.session.remove(); db.engine.dispose()

    def test_dev_schema_creation_and_private_data_downgrade_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True, "AUTO_CREATE_SCHEMA": False, "SEED_DATABASE": False,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (Path(directory) / "migration.sqlite").as_posix(), "UPLOAD_FOLDER": directory})
            with app.app_context():
                try:
                    upgrade(revision="b3f6a2d91804")
                    # Mirrors the development server reloading models before migration.
                    db.create_all()
                    upgrade()
                    self.assertIn("runtime_snapshot", {c["name"] for c in inspect(db.engine).get_columns("evaluation_runs")})
                    competition = Competition(slug="migration", name="Migration", summary="test")
                    track = Track(competition=competition, slug="test", name="test")
                    problem = Problem(track=track, code="M", slug="test", title="test")
                    db.session.add(problem); db.session.flush()
                    problem_id = problem.id
                    db.session.add(ProblemRuntime(problem=problem, config={"image": "sha256:" + "a" * 64, "scenarios": [{"private_case": "retain"}]}))
                    db.session.commit(); db.session.remove()
                    with self.assertRaises(SystemExit): downgrade(revision="b3f6a2d91804")
                    self.assertEqual(db.session.get(ProblemRuntime, problem_id).config["scenarios"], [{"private_case": "retain"}])
                    self.assertEqual(db.session.execute(text("PRAGMA foreign_key_check")).all(), [])
                finally:
                    db.session.remove(); db.engine.dispose()
