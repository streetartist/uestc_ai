"""A separately entered student number is required only for new campus registrations."""
import unittest
from tests import test_backend_api as fixtures
from platform_api.extensions import db
from platform_api.models import User


class CampusStudentNumberTests(unittest.TestCase):
    setUp = fixtures.PlatformApiTestCase.setUp
    tearDown = fixtures.PlatformApiTestCase.tearDown
    login = fixtures.PlatformApiTestCase.login
    register = fixtures.PlatformApiTestCase.register
    code_request = fixtures.PlatformApiTestCase.code_request

    def test_campus_alias_requires_student_number_without_fixed_year_or_length(self):
        email = 'my.alias@std.uestc.edu.cn'
        verification = self.code_request(self.member, email).json['debug_code']
        data = {'email': email, 'name': 'Student', 'password': 'secure-pass!',
                'confirm_password': 'secure-pass!', 'verification_code': verification}
        for invalid in [None, '', '  ', 'student123', 2026123456789, '1' * 33, '２０２６']:
            response = self.member.post('/api/auth/register', json={**data, 'student_number': invalid})
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(response.json['error'], 'valid student number required')
        result = self.member.post('/api/auth/register', json={**data, 'student_number': ' 1999123456 '})
        self.assertEqual(result.status_code, 201, result.json)
        user_id = result.json['user']['id']
        with self.app.app_context():
            self.assertEqual(db.session.get(User, user_id).student_number, '1999123456')
        self.assertNotIn('student_number', result.json['user'])
        self.assertNotIn('student_number', self.app.test_client().get(f'/api/profiles/{user_id}').json)
        self.login(self.admin, 'admin@uestcai.top')
        admin_user = next(user for user in self.admin.get('/api/admin/users').json if user['id'] == user_id)
        self.assertEqual(admin_user['student_number'], '1999123456')
        self.assertEqual(self.member.get('/api/admin/users').status_code, 403)

    def test_external_registration_uses_invite_without_student_number(self):
        self.login(self.admin, 'admin@uestcai.top')
        invite = self.admin.post('/api/admin/registration-invites', json={}).json['code']
        result = self.register(self.member, 'external@example.com', 'Guest', invite_code=invite)
        with self.app.app_context():
            self.assertIsNone(db.session.get(User, result.json['user']['id']).student_number)


if __name__ == '__main__': unittest.main()
