from __future__ import annotations
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from werkzeug.security import check_password_hash
from tests import test_backend_api as base_tests
from platform_api.extensions import db
from platform_api.models import EmailVerificationCode, Content, User, Competition
from platform_api.mailer import MailDeliveryError


class CommunityAccountsTestCase(unittest.TestCase):
    setUp = base_tests.PlatformApiTestCase.setUp
    tearDown = base_tests.PlatformApiTestCase.tearDown
    login = base_tests.PlatformApiTestCase.login
    register = base_tests.PlatformApiTestCase.register
    code_request = base_tests.PlatformApiTestCase.code_request

    def challenge(self, client, email, purpose="register"):
        result = client.post("/api/auth/captcha", json={"email": email, "purpose": purpose})
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()

    def test_captcha_binding_attempts_expiry_and_single_use(self):
        email = "captcha@std.uestc.edu.cn"
        self.assertEqual(self.member.post("/api/auth/verification-codes", json={"email": email}).status_code, 400)
        challenge = self.challenge(self.member, email)
        self.assertTrue(challenge["image"].startswith("data:image/png;base64,"))
        data = {"email": email, "captcha_id": challenge["id"], "captcha_code": challenge["debug_code"]}
        self.assertEqual(self.member.post("/api/auth/verification-codes", json={**data, "email": "other@std.uestc.edu.cn"}).status_code, 400)
        self.assertEqual(self.member.post("/api/auth/verification-codes", json={**data, "purpose": "reset"}).status_code, 400)
        self.assertEqual(self.member.post("/api/auth/verification-codes", json=data, environ_overrides={"REMOTE_ADDR": "10.0.0.2"}).status_code, 400)
        self.assertEqual(self.member.post("/api/auth/verification-codes", json=data).status_code, 202)
        self.assertEqual(self.member.post("/api/auth/verification-codes", json=data).status_code, 400)
        expired = self.challenge(self.member, email)
        with self.app.app_context():
            record = db.session.get(EmailVerificationCode, expired["id"])
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.session.commit()
        self.assertEqual(self.member.post("/api/auth/verification-codes", json={**data, "captcha_id": expired["id"], "captcha_code": expired["debug_code"]}).status_code, 400)
        challenge = self.challenge(self.member, email)
        wrong = "111111" if challenge["debug_code"] != "111111" else "222222"
        for _ in range(5):
            self.assertEqual(self.member.post("/api/auth/verification-codes", json={**data, "captcha_id": challenge["id"], "captcha_code": wrong}).status_code, 400)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(EmailVerificationCode, challenge["id"]).consumed_at)

    def test_reset_verifies_email_revokes_sessions_and_cannot_replay(self):
        registered = self.register(self.member, "recovery@std.uestc.edu.cn", "Recovery").get_json()
        second = self.app.test_client()
        self.login(second, "recovery@std.uestc.edu.cn", "secure-pass-123")
        token = registered["token"]
        sent = self.code_request(self.app.test_client(), "recovery@std.uestc.edu.cn", purpose="reset").get_json()
        data = {"email": "recovery@std.uestc.edu.cn", "verification_code": sent["debug_code"], "new_password": "New-secure-pass!", "confirm_password": "New-secure-pass!"}
        self.assertEqual(self.app.test_client().post("/api/auth/reset-password", json={**data, "confirm_password": "Mismatch!"}).status_code, 400)
        self.assertEqual(self.app.test_client().post("/api/auth/reset-password", json={**data, "verification_code": "invalid"}).status_code, 400)
        response = self.app.test_client().post("/api/auth/reset-password", json=data)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.member.get("/api/auth/me").status_code, 401)
        self.assertEqual(second.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.app.test_client().get("/api/auth/me", headers={"Authorization": "Bearer " + token}).status_code, 401)
        self.assertEqual(self.app.test_client().post("/api/auth/reset-password", json=data).status_code, 400)
        self.assertEqual(self.member.post("/api/auth/login", json={"email": data["email"], "password": "secure-pass-123"}).status_code, 401)
        self.login(self.member, data["email"], data["new_password"])
        with self.app.app_context():
            self.assertTrue(check_password_hash(User.query.filter_by(email=data["email"]).first().password_hash, data["new_password"]))

    def test_reset_unknown_email_uses_generic_response_and_does_not_send(self):
        with patch("platform_api.routes.auth.send_verification_email") as mail:
            response = self.code_request(self.member, "unregistered@example.com", purpose="reset")
            self.assertEqual(response.status_code, 202)
            mail.assert_not_called()
        result = response.get_json()
        self.assertEqual(self.member.post("/api/auth/reset-password", json={"email": "unregistered@example.com", "verification_code": result["debug_code"], "new_password": "secure-new!", "confirm_password": "secure-new!"}).status_code, 400)

    def test_mail_failure_does_not_allow_captcha_replay_and_production_hides_digits(self):
        challenge = self.challenge(self.member, "mail@std.uestc.edu.cn")
        data = {"email": "mail@std.uestc.edu.cn", "captcha_id": challenge["id"], "captcha_code": challenge["debug_code"]}
        with patch("platform_api.routes.auth.send_verification_email", side_effect=MailDeliveryError("email provider is unavailable")):
            self.assertEqual(self.member.post("/api/auth/verification-codes", json=data).status_code, 503)
        self.assertEqual(self.member.post("/api/auth/verification-codes", json=data).status_code, 400)
        self.app.config.update(TESTING=False, EXPOSE_VERIFICATION_CODE=True)
        challenge = self.member.post("/api/auth/captcha", json={"email": "hidden@std.uestc.edu.cn"}).get_json()
        self.assertNotIn("debug_code", challenge)
        self.assertEqual(self.member.post("/api/auth/verification-codes", json=[]).status_code, 400)
        self.assertEqual(self.member.post("/api/auth/register", json=[]).status_code, 400)
        self.assertEqual(self.member.post("/api/auth/reset-password", json=[]).status_code, 400)

    def test_registration_requires_matching_confirmation(self):
        email = "confirmation@std.uestc.edu.cn"
        code = self.code_request(self.member, email).get_json()["debug_code"]
        data = {"email": email, "name": "Student", "password": "secure-pass!", "verification_code": code}
        self.assertEqual(self.member.post("/api/auth/register", json=data).status_code, 400)
        self.assertEqual(self.member.post("/api/auth/register", json={**data, "confirm_password": "different!"}).status_code, 400)
        self.assertEqual(self.member.post("/api/auth/register", json={**data, "confirm_password": data["password"]}).status_code, 201)

    def test_contributions_review_ownership_and_public_profile(self):
        user_id = self.register(self.member, "writer@std.uestc.edu.cn", "Writer").get_json()["user"]["id"]
        other = self.app.test_client()
        self.register(other, "other@std.uestc.edu.cn", "Other")
        anonymous = self.app.test_client()
        data = {"kind": "work", "title": "Independent project", "excerpt": "My project", "body_md": "# Original work", "status": "pending"}
        self.assertEqual(anonymous.post("/api/me/contributions", json=data).status_code, 401)
        self.assertEqual(self.member.post("/api/me/contributions", json={**data, "status": "published"}).status_code, 400)
        self.assertEqual(self.member.post("/api/me/contributions", json={**data, "kind": "announcement"}).status_code, 400)
        created = self.member.post("/api/me/contributions", json={**data, "author_id": "other"}).get_json()
        self.assertEqual(created["author_id"], user_id)
        self.assertEqual(anonymous.get(f"/api/content/{created['slug']}").status_code, 404)
        self.assertEqual(other.patch(f"/api/me/contributions/{created['id']}", json=data).status_code, 404)
        self.assertEqual(self.member.get(f"/api/content/{created['slug']}").status_code, 200)
        profile = anonymous.get(f"/api/profiles/{user_id}").get_json()
        self.assertEqual(profile["contributions"], [])
        for key in ("email", "role", "password_hash", "invite_code"):
            self.assertNotIn(key, profile)
        self.login(self.admin, "admin@uestcai.top")
        self.admin.patch(f"/api/content/{created['id']}", json={"status": "rejected", "review_note": "Please add an example"})
        self.assertEqual(self.member.get("/api/me/contributions").get_json()[0]["review_note"], "Please add an example")
        self.member.patch(f"/api/me/contributions/{created['id']}", json=data)
        self.assertEqual(self.member.get("/api/me/contributions").get_json()[0]["review_note"], "")
        published = self.admin.patch(f"/api/content/{created['id']}", json={"status": "published"})
        self.assertEqual(published.status_code, 200, published.get_json())
        profile = anonymous.get(f"/api/profiles/{user_id}").get_json()
        self.assertEqual(profile["contributions"][0]["id"], created["id"])
        self.assertEqual(anonymous.get("/api/content?kind=work").get_json()[0]["author_id"], user_id)
        self.member.patch(f"/api/me/contributions/{created['id']}", json={**data, "body_md": "Updated"})
        self.assertEqual(anonymous.get(f"/api/content/{created['slug']}").status_code, 404)
        self.assertEqual(anonymous.get(f"/api/profiles/{user_id}").get_json()["contributions"], [])

    def test_private_attachments_follow_publication_and_cannot_be_stolen(self):
        self.register(self.member, "files@std.uestc.edu.cn", "Files")
        self.register(self.reviewer, "files-other@std.uestc.edu.cn", "Other")
        anonymous = self.app.test_client()
        asset = self.member.post("/api/markdown-assets", data={"scope": "contribution", "file": (io.BytesIO(b"private-notes"), "notes.txt")}, content_type="multipart/form-data").get_json()
        self.assertEqual(anonymous.get(asset["url"]).status_code, 404)
        self.assertEqual(self.reviewer.get(asset["url"]).status_code, 404)
        data = {"kind": "blog", "title": "Article", "body_md": f"[notes]({asset['url']})", "status": "pending"}
        self.assertEqual(self.reviewer.post("/api/me/contributions", json=data).status_code, 403)
        item = self.member.post("/api/me/contributions", json=data).get_json()
        self.login(self.admin, "admin@uestcai.top")
        self.admin.patch(f"/api/content/{item['id']}", json={"status": "published"})
        response = anonymous.get(asset["url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"private-notes")
        self.assertIn("no-store", response.headers["Cache-Control"])
        response.close()
        self.admin.patch(f"/api/content/{item['id']}", json={"status": "rejected"})
        self.assertEqual(anonymous.get(asset["url"]).status_code, 404)

    def test_profile_has_real_registration_and_hides_private_competitions(self):
        user_id = self.register(self.member, "history@std.uestc.edu.cn", "History").get_json()["user"]["id"]
        competition = self.member.get("/api/competitions/paper-city-2027").get_json()
        team = self.member.post("/api/teams", json={"competition_id": competition["id"], "name": "History team"}).get_json()
        registered = self.member.post("/api/registrations", json={"competition_id": competition["id"], "team_id": team["id"], "track_id": competition["tracks"][0]["id"], "fields": {"phone": "private"}})
        self.assertEqual(registered.status_code, 201, registered.get_json())
        profile = self.app.test_client().get(f"/api/profiles/{user_id}").get_json()
        self.assertEqual(profile["competitions"][0]["team_name"], "History team")
        self.assertNotIn("private", str(profile))
        self.assertNotIn(team["invite_code"], str(profile))
        with self.app.app_context():
            db.session.get(Competition, competition["id"]).status = "draft"
            db.session.commit()
        self.assertEqual(self.app.test_client().get(f"/api/profiles/{user_id}").get_json()["competitions"], [])


if __name__ == "__main__":
    unittest.main()
