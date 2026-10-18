import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_api  # exposes backend modules
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from platform_api import create_app
from platform_api.extensions import db


class PrivateWeightMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_accounts_and_adds_only_weight_table(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app({'TESTING': True, 'AUTO_CREATE_SCHEMA': False, 'SEED_DATABASE': False,
                'SECRET_KEY': 'test-only-secret-private-weight-migration', 'UPLOAD_FOLDER': root,
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(root)/'migration.sqlite').as_posix()})
            with app.app_context():
                try:
                    upgrade(revision='e16a20261016')
                    before = set(inspect(db.engine).get_table_names())
                    db.session.execute(text("INSERT INTO users (id,email,name,password_hash,role,created_at,updated_at) VALUES ('old','old@example.com','Old','unused','member',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                    db.session.commit()
                    upgrade(revision='f18c20261018')
                    self.assertEqual(set(inspect(db.engine).get_table_names()) - before, {'model_weight_assets'})
                    self.assertEqual(db.session.execute(text("SELECT name FROM users WHERE id='old'")).scalar(), 'Old')
                    self.assertEqual(db.session.execute(text('PRAGMA foreign_key_check')).all(), [])
                    db.session.remove()
                    downgrade(revision='e16a20261016')
                    self.assertEqual(set(inspect(db.engine).get_table_names()), before)
                finally:
                    db.session.remove()
                    db.engine.dispose()


if __name__ == '__main__':
    unittest.main()
