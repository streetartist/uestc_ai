"""Regression cases for registration aliases and private competition submissions."""
import unittest
from unittest.mock import Mock, patch

from tests import test_independent_evaluation_trials as trials
from platform_api.extensions import db
from platform_api.models import CompetitionReviewer, SubmissionAsset, User
from platform_api.public_cache import init_public_cache
from platform_api.routes.auth import is_campus_email


class OpenIssueRegressions(unittest.TestCase):
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

    def test_primary_student_mailbox_format(self):
        for email in ['2025120903029@std.uestc.edu.cn', ' 2025120903029@STD.UESTC.EDU.CN ']:
            self.assertTrue(is_campus_email(email))
        for email in ['alias@std.uestc.edu.cn', '2025120903029+alias@std.uestc.edu.cn',
                      '202512090302@std.uestc.edu.cn', '20251209030290@std.uestc.edu.cn',
                      '２０２５１２０９０３０２９@std.uestc.edu.cn', '2025120903029@std.uestc.edu.cn.evil.com']:
            with self.subTest(email=email): self.assertFalse(is_campus_email(email))

    def test_alias_needs_invite_for_both_sending_and_registration(self):
        email='new-alias@std.uestc.edu.cn'
        with patch('platform_api.routes.auth.send_verification_email') as mail:
            response=self.member.post('/api/auth/verification-codes', json={'email':email})
            self.assertEqual(response.status_code,403)
            mail.assert_not_called()
        self.assertEqual(self.member.post('/api/auth/register',json={
            'email':email,'name':'Alias','password':'strong-pass!','confirm_password':'strong-pass!',
            'verification_code':'123456'}).status_code,403)
        # Administrator invitation is the supported exception, including aliases.
        self.assertEqual(self.register(self.member,email,'Approved Alias').status_code,201)

    def test_formal_materials_metrics_logs_and_profiles_require_assignment(self):
        self.fixture()
        owner=self.member.get('/api/auth/me').json['user']['id']
        asset=self.stage(b'private contestant code')
        trial=self.start(asset).json
        job=self.claim()
        log_url=f"/api/evaluation-worker/runs/{job['id']}/logs/agent"
        self.assertEqual(self.worker.post(log_url,headers={'X-Evaluation-Lease':job['lease_token']},
            json={'offset':0,'text':'private trace\n'}).status_code,200)
        self.complete(job)
        submitted=self.formal(asset,trial['id']);self.assertEqual(submitted.status_code,201,submitted.json)
        work_url=f"/api/works/{trial['submission_id']}"
        attachment=self.member.get(work_url).json['assets'][0]['id']
        # Old rows labelled public must not bypass the authorization check.
        with self.app.app_context():
            db.session.get(SubmissionAsset,attachment).visibility='public';db.session.commit()
        self.login(self.reviewer,'reviewer@uestc.ai')
        unrelated=self.app.test_client(); other_reviewer=self.app.test_client()
        self.register(unrelated,'outside-team@std.uestc.edu.cn','Other')
        user=self.register(other_reviewer,'unassigned-reviewer@std.uestc.edu.cn','Reviewer').json['user']
        with self.app.app_context():
            db.session.get(User,user['id']).role='reviewer';db.session.commit()
        anonymous=self.app.test_client()
        for client in [self.member,self.reviewer,self.admin]:
            self.assertEqual(client.get(work_url).status_code,200)
            self.assertEqual(client.get(f'/api/assets/{attachment}',buffered=True).status_code,200)
            self.assertIn(trial['submission_id'],[i['id'] for i in client.get('/api/works').json])
            self.assertEqual(client.get(work_url).headers['Cache-Control'],'private, no-store')
        for client in [unrelated,other_reviewer,anonymous]:
            self.assertEqual(client.get(work_url).status_code,404)
            self.assertEqual(client.get(f'/api/assets/{attachment}').status_code,404)
            self.assertNotIn(trial['submission_id'],[i['id'] for i in client.get('/api/works').json])
            self.assertEqual(client.get(f'/api/profiles/{owner}').json['works'],[])
        result=f"/api/evaluation-runs/{trial['id']}"
        self.assertEqual(other_reviewer.get(result).status_code,403)
        self.assertEqual(other_reviewer.get(result+'/logs').status_code,404)
        logs=self.reviewer.get(result+'/logs').json['items'];self.assertEqual(len(logs),1)
        self.assertEqual(other_reviewer.get(logs[0]['url']).status_code,404)
        self.assertEqual(self.reviewer.get(result).status_code,200)
        with self.app.app_context():
            reviewer=User.query.filter_by(email='reviewer@uestc.ai').one()
            CompetitionReviewer.query.filter_by(reviewer_id=reviewer.id).delete();db.session.commit()
        self.assertEqual(self.reviewer.get(work_url).status_code,404)
        self.assertEqual(self.reviewer.get(result+'/logs').status_code,404)

    def test_old_anonymous_redis_work_cache_is_never_served(self):
        self.app.config.update(REDIS_URL='redis://localhost/15')
        init_public_cache(self.app)
        cache=Mock();cache.get.return_value=b'{"secret":"old public ZIP"}'
        self.app.extensions['public_cache']=cache
        self.assertEqual(self.member.get('/api/works').json,[])
        self.assertEqual(self.member.get('/api/works/old-public-id').status_code,404)
        with self.app.app_context():
            owner = User.query.first().id
        profile = self.member.get(f'/api/profiles/{owner}')
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json['works'], [])
        self.assertEqual(profile.headers['Cache-Control'], 'private, no-store')
        cache.get.assert_not_called()


if __name__=='__main__':unittest.main()
