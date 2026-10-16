"""An existing account survives upgrade without fabricating student identity or publication."""
import tempfile
import unittest
from pathlib import Path

from tests import test_backend_api  # noqa: F401 - adds backend to sys.path
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from platform_api import create_app
from platform_api.extensions import db
from platform_api.models import User


class ShowcaseMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_existing_accounts_and_roundtrips_empty_fields(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app({'TESTING': True, 'AUTO_CREATE_SCHEMA': False, 'SEED_DATABASE': False,
                              'UPLOAD_FOLDER': root, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(root) / 'migration.sqlite').as_posix()})
            with app.app_context():
                try:
                    upgrade(revision='d5b8e3a91c47')
                    self.assertNotIn('student_number', {c['name'] for c in inspect(db.engine).get_columns('users')})
                    db.session.execute(text("INSERT INTO users (id,email,name,password_hash,role,created_at,updated_at) VALUES ('old','old@example.com','Old','unused','member',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                    db.session.commit()
                    upgrade()
                    existing = db.session.get(User, 'old')
                    self.assertEqual(existing.name, 'Old')
                    self.assertIsNone(existing.student_number)
                    self.assertIn('publication', {c['name'] for c in inspect(db.engine).get_columns('submissions')})
                    db.session.remove(); downgrade(revision='d5b8e3a91c47'); upgrade()
                    self.assertEqual(db.session.get(User, 'old').email, 'old@example.com')
                    self.assertEqual(db.session.execute(text('PRAGMA foreign_key_check')).all(), [])
                finally:
                    db.session.remove(); db.engine.dispose()


if __name__ == '__main__': unittest.main()
