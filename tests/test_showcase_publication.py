"""Publishing a showcase never implicitly publishes private submission materials."""
import unittest
from datetime import datetime, timedelta, timezone

from tests import test_independent_evaluation_trials as trials
from platform_api.extensions import db
from platform_api.models import Submission


class ShowcasePublicationTests(unittest.TestCase):
    setUp = trials.IndependentTrialTests.setUp
    tearDown = trials.IndependentTrialTests.tearDown
    login = trials.IndependentTrialTests.login
    register = trials.IndependentTrialTests.register
    fixture = trials.IndependentTrialTests.fixture
    stage = trials.IndependentTrialTests.stage
    start = trials.IndependentTrialTests.start
    claim = trials.IndependentTrialTests.claim
    complete = trials.IndependentTrialTests.complete
    formal = trials.IndependentTrialTests.formal

    def prepare(self):
        self.fixture()
        self.fields.update(readme_md="PRIVATE README", fields={"repository": "https://private.example/code"})
        asset = self.stage()
        self.run = self.start(asset).json
        self.complete(self.claim(), 75)
        result = self.formal(asset, self.run["id"])
        self.assertEqual(result.status_code, 201, result.json)
        self.work = result.json
        self.url = f"/api/works/{self.work['id']}"
        self.settings_url = f"/api/manage/submissions/{self.work['id']}/publication"
        self.settings = self.admin.get(self.settings_url).json
        self.asset_id = self.settings["assets"][0]["id"]
        self.data = {"published": True, "version_id": self.settings["version_id"], "version_created_at": self.settings["version_created_at"],
                     "title": "PUBLIC TITLE", "description_md": "Public project introduction"}
        self.visitor = self.app.test_client()

    def end_competition(self):
        with self.app.app_context():
            submission = db.session.get(Submission, self.work["id"])
            submission.problem.track.competition.ends_at = datetime.now(timezone.utc) - timedelta(days=1)
            db.session.commit()

    def test_showcase_requires_explicit_admin_approval_after_competition(self):
        self.prepare()
        self.assertEqual(self.member.patch(self.settings_url, json=self.data).status_code, 403)
        self.assertEqual(self.visitor.patch(self.settings_url, json=self.data).status_code, 401)
        self.assertEqual(self.admin.patch(self.settings_url, json=self.data).status_code, 409)
        self.end_competition()
        # An old published status alone never discloses materials.
        with self.app.app_context():
            submission = db.session.get(Submission, self.work["id"])
            submission.status = submission.versions[-1].status = "published"
            db.session.commit()
        self.assertEqual(self.visitor.get(self.url).status_code, 404)
        response = self.admin.patch(self.settings_url, json=self.data)
        self.assertEqual(response.status_code, 200, response.json)
        public = self.visitor.get(self.url)
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json["title"], "PUBLIC TITLE")
        self.assertEqual(public.json["version"]["readme_md"], self.data["description_md"])
        self.assertEqual(public.json["version"]["fields"], {})
        self.assertEqual(public.json["version"]["snapshot"], {})
        self.assertEqual(public.json["assets"], [])
        self.assertNotIn("evaluation", public.json)
        self.assertNotIn("public_metrics", public.json)
        self.assertNotIn("PRIVATE README", str(public.json))
        self.assertNotIn("private.example", str(public.json))
        self.assertEqual(self.visitor.get(f"/api/assets/{self.asset_id}").status_code, 404)
        self.assertEqual(self.visitor.get(f"/api/evaluation-runs/{self.run['id']}").status_code, 401)
        self.assertEqual(public.headers["Cache-Control"], "private, no-store")
        self.assertEqual(self.visitor.get('/api/works').json[0]["title"], "PUBLIC TITLE")
        owner = self.member.get('/api/auth/me').json['user']['id']
        self.assertEqual(self.visitor.get(f'/api/profiles/{owner}').json['works'][0]['title'], 'PUBLIC TITLE')
        self.assertEqual(self.member.get(self.url).json['version']['readme_md'], 'PRIVATE README')

    def test_files_and_metrics_are_independent_and_revocable(self):
        self.prepare(); self.end_competition()
        response = self.admin.patch(self.settings_url, json={**self.data, "metrics_public": True})
        self.assertEqual(response.status_code, 200, response.json)
        public = self.visitor.get(self.url).json
        self.assertEqual(public["public_metrics"]["metrics"], {"task_success": 75})
        self.assertNotIn("episodes", str(public["public_metrics"]))
        self.assertEqual(public["assets"], [])
        self.assertEqual(self.visitor.get(f"/api/evaluation-runs/{self.run['id']}/logs").status_code, 401)
        response = self.admin.patch(self.settings_url, json={**self.data, "asset_ids": [self.asset_id]})
        self.assertEqual(response.status_code, 200, response.json)
        public = self.visitor.get(self.url).json
        self.assertNotIn('public_metrics', public)
        self.assertEqual(public['assets'][0]['id'], self.asset_id)
        download = self.visitor.get(f'/api/assets/{self.asset_id}', buffered=True)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b'first tested package'); download.close()
        self.admin.patch(self.settings_url, json={"published": False, "version_id": self.settings['version_id'], "version_created_at": self.settings['version_created_at']})
        self.assertEqual(self.visitor.get(self.url).status_code, 404)
        self.assertEqual(self.visitor.get(f'/api/assets/{self.asset_id}').status_code, 404)
        self.assertEqual(self.visitor.get('/api/works').json, [])

    def test_invalid_materials_and_replacements_do_not_preserve_public_approval(self):
        self.prepare(); self.end_competition()
        for invalid in [{"version_id": "stale"}, {"asset_ids": ["foreign-asset"]}, {"metrics_public": "true"}, {"description_md": ""}]:
            response = self.admin.patch(self.settings_url, json={**self.data, **invalid})
            self.assertIn(response.status_code, [400, 409], response.json)
        self.assertEqual(self.admin.patch(self.settings_url, json=self.data).status_code, 200)
        # Test mode permits late replacements, which must still revoke approval.
        self.assertEqual(self.formal(self.stage(), self.run['id']).status_code, 201)
        self.assertEqual(self.visitor.get(self.url).status_code, 404)
        self.assertEqual(self.admin.get(self.settings_url).json['publication'], {})
        # Replacing the single stored version keeps its ID but changes its revision.
        self.assertEqual(self.admin.patch(self.settings_url, json=self.data).status_code, 409)


if __name__ == '__main__': unittest.main()
