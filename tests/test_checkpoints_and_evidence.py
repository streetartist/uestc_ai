import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_independent_evaluation_trials import IndependentTrialTests
from platform_api.extensions import db
from platform_api.models import Competition, EvaluationRun, Submission


class ProgressTests(unittest.TestCase):
    setUp = IndependentTrialTests.setUp
    tearDown = IndependentTrialTests.tearDown
    register = IndependentTrialTests.register
    login = IndependentTrialTests.login
    fixture = IndependentTrialTests.fixture
    stage = IndependentTrialTests.stage
    start = IndependentTrialTests.start
    claim = IndependentTrialTests.claim
    complete = IndependentTrialTests.complete
    formal = IndependentTrialTests.formal

    def prepare(self):
        self.fixture(limit=2)
        self.competition = {"id": self.team["competition_id"]}
        with self.app.app_context():
            competition = db.session.get(Competition, self.competition["id"])
            competition.config = {**competition.config, "checkpoints": [{"id": "checkpoint-1", "label": "首次跑通", "due_at": None}]}
            db.session.commit()
        self.url = f"/api/problems/{self.problem['id']}/checkpoints"

    def test_progress_freezes_package_without_trial_or_formal_submission(self):
        self.prepare()
        asset = self.stage(b"checkpoint package")
        response = self.member.post(self.url + '/checkpoint-1', json={"team_id": self.team['id'],
            "staged_asset_id": asset, "content_md": "# 当前版本\n已跑通本地练习"})
        self.assertEqual(response.status_code, 201, response.get_json())
        entry = response.get_json()
        self.assertEqual(entry['revision'], 1)
        self.assertEqual(self.member.delete(f"/api/submission-assets/stage/{asset}").status_code, 204)
        downloaded = self.member.get(entry['package_url'], buffered=True)
        self.assertEqual(downloaded.data, b'checkpoint package')
        downloaded.close()
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'], 0)
        with self.app.app_context():
            self.assertEqual(EvaluationRun.query.count(), 0)
            self.assertEqual(Submission.query.count(), 0)
        self.assertEqual(self.member.get(self.url + '?team_id=' + self.team['id']).get_json()['entries'][0]['id'], entry['id'])
        self.login(self.reviewer, 'reviewer@uestc.ai')
        self.assertEqual(self.reviewer.get(entry['package_url']).status_code, 404)
        self.assertEqual(self.reviewer.get(self.url + '?team_id=' + self.team['id']).status_code, 403)
        feedback = self.admin.patch('/api/manage/checkpoints/' + entry['id'], json={'revision': 1, 'feedback_md': '检查部署依赖'})
        self.assertEqual(feedback.status_code, 200, feedback.get_json())
        update = self.member.post(self.url + '/checkpoint-1', json={'team_id': self.team['id'], 'content_md': '已修复依赖', 'repository': ''})
        self.assertEqual(update.status_code, 201, update.get_json())
        self.assertEqual(update.get_json()['revision'], 2)
        self.assertEqual(update.get_json()['feedback_md'], '')
        self.assertEqual(self.admin.patch('/api/manage/checkpoints/' + entry['id'], json={'revision': 1, 'feedback_md': '旧反馈'}).status_code, 409)

    def test_deadline_and_unconfigured_checkpoint_cannot_consume_budget(self):
        self.prepare()
        fields = {'team_id': self.team['id'], 'repository': 'https://example.com/repo/commit/123', 'content_md': '当前进度'}
        self.assertEqual(self.member.post(self.url + '/checkpoint-1', json={**fields, 'repository':'https://[invalid'}).status_code, 400)
        self.assertEqual(self.member.post(self.url + '/checkpoint-3', json=fields).status_code, 404)
        self.admin.patch('/api/manage/competitions/' + self.competition['id'], json={'config': {'checkpoints': [
            {'id': 'checkpoint-1', 'label': '首次跑通', 'due_at': '2000-01-01T00:00:00Z'}]}})
        self.assertEqual(self.member.post(self.url + '/checkpoint-1', json=fields).status_code, 409)
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'], 0)
        invalid = self.admin.patch('/api/manage/competitions/' + self.competition['id'], json={'config': {'checkpoints': [
            {'id': 'checkpoint-1', 'label': '无时区', 'due_at': '2026-10-10T12:00:00'}]}})
        self.assertEqual(invalid.status_code, 400)

    def test_trusted_artifacts_share_formal_result_visibility_and_require_live_lease(self):
        self.prepare()
        asset = self.stage()
        trial = self.start(asset).get_json()
        job = self.claim()
        upload = f"/api/evaluation-worker/runs/{job['id']}/evidence"
        headers = {'X-Evaluation-Lease': job['lease_token']}
        contents = json.dumps({'steps': [{'action': [0]*7, 'success': False}]}).encode()
        response = self.worker.post(upload, headers=headers, data={'name': 'scene-1-trajectory.json', 'file': (io.BytesIO(contents), 'trajectory.json')})
        self.assertEqual(response.status_code, 201, response.get_json())
        artifact = response.get_json()
        downloaded = self.member.get(artifact['url'], buffered=True)
        self.assertEqual(downloaded.data, contents)
        downloaded.close()
        self.assertEqual(self.app.test_client().get(artifact['url']).status_code, 401)
        self.login(self.reviewer, 'reviewer@uestc.ai')
        self.assertEqual(self.reviewer.get(artifact['url']).status_code, 404)
        self.assertEqual(self.worker.post(upload, data={'name': 'scene-1-trajectory.json', 'file': (io.BytesIO(contents), 'trajectory.json')}).status_code, 403)
        self.assertEqual(self.worker.post(upload, headers=headers, data={'name': '../escape.json', 'file': (io.BytesIO(contents), 'x.json')}).status_code, 400)
        self.complete(job)
        self.assertEqual(self.formal(asset, trial['id']).status_code, 201)
        downloaded = self.reviewer.get(artifact['url'], buffered=True)
        self.assertEqual(downloaded.status_code, 200)
        downloaded.close()
        self.assertEqual(self.worker.post(upload, headers=headers, data={'name': 'scene-1-trajectory.json', 'file': (io.BytesIO(contents), 'x.json')}).status_code, 403)
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'], 1)


if __name__ == '__main__': unittest.main()
