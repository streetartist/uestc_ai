import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from platform_api import create_app
from platform_api.extensions import db
from platform_api.ai_gateway import encrypt_keys
from platform_api.compute_models import ComputeProvider


class ComputeMigrationTest(unittest.TestCase):
    def test_upgrade_roundtrip_and_protection_of_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app({"AUTO_CREATE_SCHEMA": False, "SEED_DATABASE": False, "TESTING": True,
                "UPLOAD_FOLDER": root, "AI_GATEWAY_ENCRYPTION_KEY": "migration-test",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (Path(root) / "migration.sqlite").as_posix()})
            with app.app_context():
                try:
                    upgrade(revision="f1d4b83c219a")
                    upgrade()
                    tables = set(inspect(db.engine).get_table_names())
                    self.assertTrue({"compute_providers", "compute_grants", "compute_instances", "compute_sessions", "compute_policies", "compute_worker_heartbeats"} <= tables)
                    self.assertEqual(db.session.execute(text("PRAGMA foreign_key_check")).all(), [])
                    db.session.remove()
                    downgrade(revision="f1d4b83c219a")
                    upgrade()
                    provider = ComputeProvider(name="preserved", base_url="https://api.autodl.com", secret=encrypt_keys(["private"]), image_uuid="image", gpu_spec_uuid="4090D", gpu_label="4090 D", hourly_price_millis=2000)
                    db.session.add(provider); db.session.commit(); provider_id = provider.id
                    db.session.remove()
                    with self.assertRaises(SystemExit):
                        downgrade(revision="f1d4b83c219a")
                    self.assertEqual(db.session.get(ComputeProvider, provider_id).name, "preserved")
                finally:
                    db.session.remove(); db.engine.dispose()
