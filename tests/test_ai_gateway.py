"""Real loopback HTTP tests; no provider credentials or external billing."""
from __future__ import annotations

import concurrent.futures
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from platform_api import create_app
from platform_api.extensions import db
from platform_api.models import (Competition, CompetitionReviewer, EvaluationRun, Session, Submission,
    SubmissionVersion, Team, TeamMember, Track, Problem, User, utcnow)
from platform_api.ai_models import AIChannel, AIGrant, AIKey, AIProblemQuota, AIUsage
from platform_api.security import hash_token


class Provider(BaseHTTPRequestHandler):
    calls = []
    lock = threading.Lock()
    require_user_agent = False

    def log_message(self, *_args):
        pass

    def send(self, code, body, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

    def do_GET(self):
        self.send(200, {"data": [{"id": "provider-model"}]})

    def do_POST(self):
        if self.require_user_agent and self.headers.get("User-Agent") != "uestc-ai-platform/1.0":
            self.send(403, {"error": "client identification required"})
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.lock:
            self.calls.append((self.path, body, self.headers.get("Authorization")))
        if self.headers.get("Authorization") == "Bearer rejected-key":
            self.send(429, {"error": "internal provider detail must not leak"})
            return
        if body["model"] == "error":
            self.send(500, {"error": "private-provider-key"})
            return
        if body["model"] == "slow":
            time.sleep(.1)
        usage = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10,
                 "prompt_tokens_details": {"cached_tokens": 2}}
        if self.path.endswith("/messages"):
            usage = {"input_tokens": 5, "output_tokens": 3, "cache_read_input_tokens": 2}
            if body.get("stream"):
                events = [{"type": "message_start", "message": {"id": "msg-test", "usage": {**usage, "output_tokens": 0}}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "真实链路测试"}},
                    {"type": "message_delta", "usage": {"output_tokens": 3}, "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"}]
                self.send(200, "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events).encode(), "text/event-stream")
            else:
                self.send(200, {"id": "msg-test", "content": [{"type": "text", "text": "真实链路测试"}], "stop_reason": "end_turn", "usage": usage})
            return
        if body.get("stream"):
            events = [{"choices": [{"delta": {"content": "真实链路测试"}}]}, {"choices": [], "usage": usage}]
            if self.path.endswith("/responses"):
                events = [{"type": "response.output_text.delta", "delta": "真实链路测试"}, {"type": "response.completed", "response": {"usage": usage}}]
            tail = "data: [DONE]\n\n"
            if body["model"] == "missing":
                events = events[:1]
            if body["model"] == "truncated":
                events, tail = events[:1], ""
            self.send(200, ("".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events) + tail).encode(), "text/event-stream")
        else:
            result = {"id": "chat-test", "model": body["model"], "choices": [{"message": {"role": "assistant", "content": "真实链路测试"}}], "usage": usage}
            if body["model"] == "missing":
                result.pop("usage")
            if body["model"] == "over":
                result["usage"] = {"prompt_tokens": 10000000, "completion_tokens": 3}
            self.send(200, result)


class AIGatewayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "AUTO_CREATE_SCHEMA": True, "SEED_DATABASE": False,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (Path(self.temp.name) / "test.sqlite3").as_posix(),
            "UPLOAD_FOLDER": self.temp.name, "AI_GATEWAY_ENCRYPTION_KEY": "test-encryption-key",
            "AI_GATEWAY_ALLOW_LOCAL_HTTP": True})
        self.addCleanup(self.close_runtime)
        self.admin, self.member, self.other, self.reviewer = [self.app.test_client() for _ in range(4)]
        with self.app.app_context():
            users = [User(id=role, email=role + "@example.com", name=role, password_hash="not-used", role="member" if role == "other" else role) for role in ("admin", "member", "other", "reviewer")]
            db.session.add_all(users)
            comp = Competition(id="competition", slug="test", name="Test Competition", summary="test", status="published")
            team = Team(id="team", competition=comp, name="Test Team", captain_id="member", invite_code="invite")
            db.session.add(team)
            db.session.add(TeamMember(team=team, user_id="member"))
            for user in users:
                db.session.add(Session(token_hash=hash_token(user.id + "-session"), user_id=user.id, expires_at=utcnow() + timedelta(days=1)))
            db.session.commit()
        for client, role in ((self.admin, "admin"), (self.member, "member"), (self.other, "other"), (self.reviewer, "reviewer")):
            client.set_cookie("session_token", role + "-session")
        self.channel = self.channel_create()
        result = self.admin.put("/api/ai/manage/grants/team", json={"allowed_models": ["contest-model"], "max_calls": 100, "max_tokens": 1000000})
        self.assertEqual(result.status_code, 201, result.get_json())
        self.grant = result.get_json()
        result = self.member.post("/api/ai/keys", json={"grant_id": self.grant["id"], "name": "Agent"})
        self.assertEqual(result.status_code, 201, result.get_json())
        self.key = result.get_json()
        self.headers = {"Authorization": "Bearer " + self.key["token"]}

    def close_runtime(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def channel_create(self, **kwargs):
        data = {"name": "primary", "base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
            "api_keys": ["working-key"], "models": {"contest-model": "provider-model"}, "input_price": 2, "output_price": 4, **kwargs}
        result = self.admin.post("/api/ai/manage/channels", json=data)
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()

    def call(self, **changes):
        body = {"model": "contest-model", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 8, **changes}
        return self.member.post("/api/ai/v1/chat/completions", headers=self.headers, json=body)

    def wire_model(self, value):
        result = self.admin.patch("/api/ai/manage/channels/" + self.channel["id"], json={"models": {"contest-model": value}})
        self.assertEqual(result.status_code, 200)

    def test_provider_requiring_client_identification_accepts_metered_request(self):
        with patch.object(Provider, "require_user_agent", True):
            response = self.call()
        self.assertEqual(response.status_code, 200, response.get_json())
        overview = self.member.get("/api/ai/overview").get_json()
        self.assertEqual(overview["summary"]["charged_tokens"], 10)

    def test_normal_flow_model_alias_usage_and_secret_isolation(self):
        self.assertEqual(self.member.get("/api/ai/v1/models", headers=self.headers).get_json()["data"][0]["id"], "contest-model")
        response = self.call()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["model"], "contest-model")
        overview = self.member.get("/api/ai/overview").get_json()
        self.assertEqual(overview["summary"]["charged_tokens"], 10)
        self.assertEqual(overview["summary"]["cost_micros"], 26)
        logs = self.member.get("/api/ai/usage").get_json()["items"]
        self.assertEqual(logs[0]["cached_tokens"], 2)
        self.assertNotIn("hello", json.dumps(logs))
        self.assertNotIn("token", self.member.get("/api/ai/keys").get_json()[0])
        with self.app.app_context():
            self.assertNotIn("working-key", db.session.get(AIChannel, self.channel["id"]).secrets)
        self.assertNotIn("working-key", json.dumps(self.admin.get("/api/ai/manage/channels").get_json()))

    def test_shared_quota_across_keys_and_revoke(self):
        self.admin.put("/api/ai/manage/grants/team", json={"allowed_models": ["contest-model"], "max_calls": 1, "max_tokens": 1000000})
        second = self.member.post("/api/ai/keys", json={"grant_id": self.grant["id"], "name": "Second"}).get_json()
        self.assertEqual(self.call().status_code, 200)
        self.headers = {"Authorization": "Bearer " + second["token"]}
        self.assertEqual(self.call().status_code, 429)
        self.member.delete("/api/ai/keys/" + second["id"])
        self.assertEqual(self.call().status_code, 401)

    def test_cross_team_and_reviewer_scope(self):
        self.assertEqual(self.other.post("/api/ai/keys", json={"grant_id": self.grant["id"], "name": "Intruder"}).status_code, 403)
        self.assertEqual(self.other.delete("/api/ai/keys/" + self.key["id"]).status_code, 403)
        self.call()
        self.assertEqual(self.other.get("/api/ai/usage").get_json()["total"], 0)
        self.assertEqual(self.reviewer.get("/api/ai/overview").get_json()["grants"], [])
        with self.app.app_context():
            db.session.add(CompetitionReviewer(competition_id="competition", reviewer_id="reviewer"))
            db.session.commit()
        self.assertEqual(self.reviewer.get("/api/ai/usage").get_json()["total"], 1)
        self.assertEqual(self.member.get("/api/ai/manage/channels").status_code, 403)

    def test_failover_discovery_disable_model(self):
        self.admin.patch("/api/ai/manage/channels/" + self.channel["id"], json={"api_keys": ["rejected-key"]})
        self.channel_create(name="backup", priority=0)
        self.admin.patch("/api/ai/manage/channels/" + self.channel["id"], json={"priority": 10})
        self.assertEqual(self.call().status_code, 200)
        log = self.member.get("/api/ai/usage").get_json()["items"][0]
        self.assertEqual([a["status"] for a in log["attempts"]], [429, 200])
        result = self.admin.post("/api/ai/manage/channels/" + self.channel["id"] + "/test")
        self.assertEqual(result.get_json()["models"], ["provider-model"])
        for channel in self.admin.get("/api/ai/manage/channels").get_json():
            self.admin.patch("/api/ai/manage/channels/" + channel["id"], json={"disabled_models": ["contest-model"]})
        self.assertEqual(self.call().status_code, 503)

    def test_stream_usage_and_missing_usage_preserves_reserve(self):
        response = self.call(stream=True)
        self.assertIn("真实链路测试", response.get_data(as_text=True))
        self.assertEqual(self.member.get("/api/ai/overview").get_json()["summary"]["charged_tokens"], 10)
        self.wire_model("missing")
        self.assertEqual(self.call().status_code, 200)
        logs = self.member.get("/api/ai/usage").get_json()["items"]
        self.assertEqual(logs[0]["status"], "uncertain")
        self.assertGreater(logs[0]["charged_tokens"], 2000)
        self.assertIsNone(logs[0]["input_tokens"])

    def test_truncated_stream_does_not_refund(self):
        self.wire_model("truncated")
        self.assertIn("stream_interrupted", self.call(stream=True).get_data(as_text=True))
        log = self.member.get("/api/ai/usage").get_json()["items"][0]
        self.assertEqual(log["status"], "uncertain")
        self.assertGreater(log["charged_tokens"], 2000)

    def test_unconsumed_stream_close_is_settled_without_free_generation(self):
        from platform_api.ai_gateway import proxy
        with self.app.test_request_context():
            grant = db.session.get(AIGrant, self.grant["id"])
            key = db.session.get(AIKey, self.key["id"])
            response = proxy(grant, {"model": "contest-model", "messages": [{"role": "user", "content": "hello"}], "stream": True, "max_tokens": 8}, key=key)
            response.close()
        record = self.member.get("/api/ai/usage").get_json()["items"][0]
        self.assertEqual(record["status"], "uncertain")
        self.assertGreater(record["charged_tokens"], 2000)

    def test_mixed_channels_skip_incompatible_conversion(self):
        self.channel_create(name="anthropic", protocol="anthropic", priority=10)
        self.assertEqual(self.call(response_format={"type": "json_object"}).status_code, 200)

    def test_anthropic_conversion_and_native_stream(self):
        self.admin.patch("/api/ai/manage/channels/" + self.channel["id"], json={"protocol": "anthropic"})
        response = self.call()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["usage"]["total_tokens"], 10)
        self.assertIn('"total_tokens": 10', self.call(stream=True).get_data(as_text=True))
        native = self.member.post("/api/ai/anthropic/v1/messages", json={"model": "contest-model", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 8, "stream": True}, headers={"x-api-key": self.key["token"]})
        self.assertIn("message_stop", native.get_data(as_text=True))
        self.assertEqual(self.member.get("/api/ai/overview").get_json()["summary"]["charged_tokens"], 30)

    def test_responses_and_invalid_payload(self):
        response = self.member.post("/api/ai/v1/responses", headers=self.headers,
            json={"model": "contest-model", "input": "hello", "max_output_tokens": 8, "stream": True})
        self.assertIn("response.completed", response.get_data(as_text=True))
        self.assertEqual(self.call(model="forbidden").status_code, 403)
        self.assertEqual(self.call(max_tokens=100000).status_code, 400)
        self.assertEqual(self.call(messages=[1]).status_code, 400)
        self.assertEqual(self.call(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://external/image"}}]}]).status_code, 400)
        self.assertEqual(self.call(stream="true").status_code, 400)
        self.assertEqual(self.call(max_completion_tokens=100000).status_code, 400)
        self.assertEqual(self.call(max_output_tokens=100000).status_code, 400)
        self.assertEqual(self.call(best_of=2).status_code, 400)

    def test_atomic_concurrent_quota(self):
        self.wire_model("slow")
        self.admin.put("/api/ai/manage/grants/team", json={"allowed_models": ["contest-model"], "max_calls": 1, "max_tokens": 1000000})
        def call(_index):
            with self.app.test_client() as client:
                return client.post("/api/ai/v1/chat/completions", headers=self.headers,
                    json={"model": "contest-model", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 8}).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(call, range(4)))
        self.assertEqual(sorted(results), [200, 429, 429, 429])

    def test_upstream_error_never_leaks_and_overusage_pauses(self):
        self.wire_model("error")
        response = self.call()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-provider-key", response.get_data(as_text=True))
        self.wire_model("over")
        self.assertEqual(self.call().status_code, 200)
        self.assertEqual(self.call().status_code, 401)

    def test_gateway_requires_explicit_local_http_and_secret(self):
        self.app.config["AI_GATEWAY_ALLOW_LOCAL_HTTP"] = False
        rejected = self.admin.post("/api/ai/manage/channels", json={"name": "local", "base_url": "http://127.0.0.1:1/v1", "api_keys": ["key"], "models": {}})
        self.assertEqual(rejected.status_code, 400)
        self.app.config["AI_GATEWAY_ENCRYPTION_KEY"] = ""
        rejected = self.admin.post("/api/ai/manage/channels", json={"name": "public", "base_url": "https://example.com/v1", "api_keys": ["key"], "models": {}})
        self.assertEqual(rejected.status_code, 503)

    def test_token_and_cost_limits_and_minute_limit(self):
        for values in ({"max_tokens": 100}, {"max_cost_micros": 1}):
            response = self.admin.put("/api/ai/manage/grants/team", json={"allowed_models": ["contest-model"], **values})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.call().status_code, 429)
        self.admin.put("/api/ai/manage/grants/team", json={"allowed_models": ["contest-model"], "requests_per_minute": 1})
        self.assertEqual(self.call().status_code, 200)
        self.assertEqual(self.call().status_code, 429)

    def test_removed_member_and_paused_grant_cannot_call(self):
        with self.app.app_context():
            db.session.delete(db.session.get(TeamMember, ("team", "member")))
            db.session.commit()
        self.assertEqual(self.call().status_code, 401)

    def test_stale_request_is_observable_and_retains_reservation(self):
        from platform_api.ai_gateway import reserve
        with self.app.app_context():
            grant = db.session.get(AIGrant, self.grant["id"])
            key = db.session.get(AIKey, self.key["id"])
            row = reserve(grant, key, None, "contest-model", "chat/completions", 3000, 1)
            row.deadline = utcnow() - timedelta(seconds=1)
            db.session.commit()
        row = self.member.get("/api/ai/usage").get_json()["items"][0]
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["error_code"], "worker_interrupted")
        self.assertEqual(row["charged_tokens"], 3000)

    def test_evaluation_worker_uses_same_team_budget_and_run_counter(self):
        with self.app.app_context():
            track = Track(id="track", competition_id="competition", slug="track", name="Track")
            problem = Problem(id="problem", track=track, slug="problem", code="TEST", title="Test")
            submission = Submission(id="submission", problem=problem, team_id="team", title="Agent")
            version = SubmissionVersion(id="version", submission=submission, version=1, readme_md="Agent", created_by="member", status="submitted")
            run = EvaluationRun(id="run", problem=problem, submission_version=version,
                config_snapshot={"adapter": "minecraft-agent-v1", "api": {"enabled": True, "max_calls": 1}},
                submission_snapshot={}, status="running", api_token_hash=hash_token("evaluation-token"), lease_expires_at=utcnow()+timedelta(minutes=5))
            db.session.add_all([track, run])
            db.session.commit()
        body = {"model": "contest-model", "messages": [{"role": "user", "content": "observe"}], "max_tokens": 8}
        path = "/api/evaluation-worker/runs/run/api"
        headers = {"X-Evaluation-API-Token": "evaluation-token"}
        self.assertEqual(self.member.post(path, json=body, headers=headers).status_code, 200)
        self.assertEqual(self.member.post(path, json=body, headers=headers).status_code, 429)
        with self.app.app_context():
            self.assertEqual(db.session.get(EvaluationRun, "run").api_calls_used, 1)
        row = self.member.get("/api/ai/usage").get_json()["items"][0]
        self.assertEqual(row["evaluation_run_id"], "run")
        self.assertEqual(row["charged_tokens"], 10)

    def add_problems(self):
        with self.app.app_context():
            track = Track(id="quota-track", competition_id="competition", slug="quotas", name="Quotas")
            db.session.add(track)
            db.session.add_all([Problem(id=identifier, track=track, slug=identifier, code=identifier, title=identifier)
                                for identifier in ("problem-a", "problem-b")])
            db.session.commit()

    def problem_grant(self, problem_id, **values):
        response = self.admin.put("/api/ai/manage/grants/team", json={
            "problem_id": problem_id, "allowed_models": ["contest-model"], **values})
        self.assertIn(response.status_code, (200, 201), response.get_json())
        return response.get_json()

    def scoped_key(self, grant):
        response = self.member.post("/api/ai/keys", json={"grant_id": grant["id"], "name": "Scoped Agent"})
        self.assertEqual(response.status_code, 201, response.get_json())
        return {"Authorization": "Bearer " + response.get_json()["token"]}

    def test_problem_team_budgets_are_independent_and_existing_default_is_preserved(self):
        self.add_problems()
        grant_a = self.problem_grant("problem-a", max_calls=1)
        grant_b = self.problem_grant("problem-b", max_calls=2)
        headers_a, headers_b = self.scoped_key(grant_a), self.scoped_key(grant_b)
        self.headers = headers_a
        self.assertEqual(self.call().status_code, 200)
        self.assertEqual(self.call().status_code, 429)
        self.headers = headers_b
        self.assertEqual(self.call().status_code, 200)
        self.assertEqual(self.call().status_code, 200)
        self.assertEqual(self.call().status_code, 429)
        overview = self.member.get("/api/ai/overview").get_json()
        grants = {g["problem_id"]: g for g in overview["grants"]}
        self.assertEqual(grants["problem-a"]["tokens_used"], 10)
        self.assertEqual(grants["problem-b"]["tokens_used"], 20)
        self.assertEqual(grants[None]["calls_used"], 0)
        self.assertEqual(grants[None]["id"], self.grant["id"])
        filtered = self.member.get("/api/ai/usage?problem_id=problem-a").get_json()
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["items"][0]["problem_title"], "problem-a")
        changed = self.problem_grant("problem-a", max_calls=2)
        self.assertEqual(changed["id"], grant_a["id"])
        self.assertEqual(changed["calls_used"], 1)

    def test_problem_scope_cannot_cross_competitions(self):
        self.add_problems()
        with self.app.app_context():
            competition = Competition(id="different", slug="different", name="Different", summary="Different")
            track = Track(id="different-track", competition=competition, slug="different", name="Different")
            db.session.add(Problem(id="foreign-problem", track=track, slug="foreign", code="FOREIGN", title="Foreign"))
            db.session.commit()
        self.assertEqual(self.admin.put("/api/ai/manage/grants/team", json={"problem_id": "foreign-problem", "allowed_models": ["contest-model"]}).status_code, 400)
        self.assertEqual(self.admin.put("/api/ai/manage/grants/team", json={"problem_id": "missing", "allowed_models": ["contest-model"]}).status_code, 404)
        self.assertEqual(self.member.get("/api/ai/manage/problems").status_code, 403)
        self.assertEqual(len(self.admin.get("/api/ai/manage/problems?competition_id=competition").get_json()), 2)

    def test_same_problem_can_allocate_different_budgets_to_each_team(self):
        self.add_problems()
        self.problem_grant("problem-a", max_calls=1)
        with self.app.app_context():
            team = Team(id="other-team", competition_id="competition", name="Other Team", captain_id="other", invite_code="other-invite")
            db.session.add(team)
            db.session.add(TeamMember(team=team, user_id="other"))
            db.session.commit()
        allocated = self.admin.put("/api/ai/manage/grants/other-team", json={
            "problem_id": "problem-a", "allowed_models": ["contest-model"], "max_calls": 2})
        self.assertEqual(allocated.status_code, 201)
        self.assertEqual(self.member.post("/api/ai/keys", json={"grant_id": allocated.get_json()["id"], "name": "Wrong Team"}).status_code, 403)
        key = self.other.post("/api/ai/keys", json={"grant_id": allocated.get_json()["id"], "name": "Other Agent"})
        self.assertEqual(key.status_code, 201)
        self.headers = {"Authorization": "Bearer " + key.get_json()["token"]}
        self.assertEqual(self.call().status_code, 200)
        own = self.member.get("/api/ai/overview").get_json()["grants"]
        self.assertEqual(next(g for g in own if g["problem_id"] == "problem-a")["calls_used"], 0)

    def uniform_quota(self, problem_id="problem-a", **changes):
        return self.admin.put(f"/api/ai/manage/problems/{problem_id}/quota", json={
            "allowed_models": ["contest-model"], "max_calls": 2, "max_tokens": 1000000, **changes})

    def test_uniform_policy_gives_same_limits_and_independent_team_usage(self):
        self.add_problems()
        result = self.uniform_quota(max_calls=1)
        self.assertEqual(result.status_code, 201, result.get_json())
        new_team = self.other.post("/api/teams", json={"competition_id": "competition", "name": "Later Team"})
        self.assertEqual(new_team.status_code, 201, new_team.get_json())
        own = next(g for g in self.member.get("/api/ai/overview").get_json()["grants"] if g["problem_id"] == "problem-a")
        later = self.other.get("/api/ai/overview").get_json()["grants"][0]
        self.assertEqual((own["max_calls"], later["max_calls"]), (1, 1))
        self.assertEqual(own["allowed_models"], later["allowed_models"])
        key = self.other.post("/api/ai/keys", json={"grant_id": later["id"], "name": "Later"}).get_json()
        self.headers = {"Authorization": "Bearer " + key["token"]}
        self.assertEqual(self.call().status_code, 200)
        self.assertEqual(self.call().status_code, 429)
        self.assertEqual(next(g for g in self.member.get("/api/ai/overview").get_json()["grants"] if g["problem_id"] == "problem-a")["calls_used"], 0)
        self.assertEqual(self.admin.put("/api/ai/manage/grants/team", json={"problem_id": "problem-a", "allowed_models": ["contest-model"], "max_calls": 99}).status_code, 409)
        self.assertEqual(self.member.get("/api/ai/manage/problems/problem-a/quota").status_code, 403)
        self.assertEqual(self.member.put("/api/ai/manage/problems/problem-a/quota", json={"allowed_models": ["contest-model"]}).status_code, 403)

    def test_uniform_policy_can_be_saved_before_teams_and_later_inherited(self):
        with self.app.app_context():
            comp = Competition(id="empty", slug="empty", name="Empty", summary="Empty", status="published")
            track = Track(id="empty-track", competition=comp, slug="track", name="Track")
            db.session.add(Problem(id="empty-problem", track=track, slug="problem", code="EMPTY", title="Empty Problem"))
            db.session.commit()
        result = self.uniform_quota("empty-problem", max_tokens=123456, enabled=False)
        self.assertEqual((result.status_code, result.get_json()["team_count"]), (201, 0))
        self.assertEqual(self.admin.get("/api/ai/manage/problems/empty-problem/quota").get_json()["config"]["max_tokens"], 123456)
        created = self.other.post("/api/teams", json={"competition_id": "empty", "name": "First Team"})
        self.assertEqual(created.status_code, 201, created.get_json())
        grants = self.other.get("/api/ai/overview").get_json()["grants"]
        self.assertEqual(len(grants), 1)
        self.assertEqual((grants[0]["problem_id"], grants[0]["max_tokens"], grants[0]["enabled"]), ("empty-problem", 123456, False))
        self.assertEqual(self.admin.get("/api/ai/manage/problems/empty-problem/quota").get_json()["team_count"], 1)
        self.assertFalse(any(g["problem_id"] == "empty-problem" for g in self.member.get("/api/ai/overview").get_json()["grants"]))

    def test_uniform_updates_preserve_keys_usage_and_reject_lower_limit_atomically(self):
        self.add_problems()
        legacy = self.problem_grant("problem-a", max_calls=10)
        self.assertEqual(self.uniform_quota(max_calls=3).status_code, 201)
        self.key = self.member.post("/api/ai/keys", json={"grant_id": legacy["id"], "name": "Uniform Agent"}).get_json()
        self.headers = {"Authorization": "Bearer " + self.key["token"]}
        self.assertEqual(self.call().status_code, 200)
        later = self.other.post("/api/teams", json={"competition_id": "competition", "name": "Later Team"})
        self.assertEqual(later.status_code, 201)
        result = self.uniform_quota(max_calls=0)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.admin.get("/api/ai/manage/problems/problem-a/quota").get_json()["config"]["max_calls"], 3)
        self.assertTrue(all(g["max_calls"] == 3 for g in self.admin.get("/api/ai/overview").get_json()["grants"] if g["problem_id"] == "problem-a"))
        self.assertEqual(self.uniform_quota(max_calls=4, max_tokens=2000000, enabled=False).status_code, 200)
        own = next(g for g in self.member.get("/api/ai/overview").get_json()["grants"] if g["problem_id"] == "problem-a")
        self.assertEqual((own["id"], own["calls_used"], own["tokens_used"]), (legacy["id"], 1, 10))
        self.assertTrue(all(not g["enabled"] and g["max_calls"] == 4 for g in self.admin.get("/api/ai/overview").get_json()["grants"] if g["problem_id"] == "problem-a"))
        with self.app.app_context():
            self.assertEqual(db.session.get(AIKey, self.key["id"]).grant_id, legacy["id"])
        self.assertEqual(self.call().status_code, 401)

    def test_concurrent_first_policy_and_team_creation_do_not_miss_allocation(self):
        self.add_problems()
        def save():
            client = self.app.test_client()
            client.set_cookie("session_token", "admin-session")
            return client.put("/api/ai/manage/problems/problem-a/quota", json={"allowed_models": ["contest-model"], "max_calls": 17}).status_code
        def create():
            client = self.app.test_client()
            client.set_cookie("session_token", "other-session")
            return client.post("/api/teams", json={"competition_id": "competition", "name": "Concurrent Team"}).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda action: action(), (save, create)))
        self.assertEqual(results, [201, 201])
        grant = self.other.get("/api/ai/overview").get_json()["grants"][0]
        self.assertEqual((grant["problem_id"], grant["max_calls"]), ("problem-a", 17))

    def test_automatic_unused_grants_do_not_prevent_deleting_empty_team_or_problem(self):
        self.add_problems()
        self.assertEqual(self.uniform_quota().status_code, 201)
        created = self.other.post("/api/teams", json={"competition_id": "competition", "name": "Unused Team"}).get_json()
        self.assertEqual(self.other.delete(f"/api/teams/{created['id']}").status_code, 204)
        with self.app.app_context():
            self.assertEqual(AIGrant.query.filter_by(team_id=created["id"]).count(), 0)
            self.assertIsNotNone(db.session.get(AIProblemQuota, "problem-a"))
        self.assertEqual(self.admin.delete("/api/manage/problems/problem-a").status_code, 204)
        with self.app.app_context():
            self.assertEqual(AIGrant.query.filter_by(problem_id="problem-a").count(), 0)
            self.assertIsNone(db.session.get(AIProblemQuota, "problem-a"))

    def test_api_history_prevents_deletion_and_budget_reset(self):
        self.add_problems()
        self.assertEqual(self.uniform_quota().status_code, 201)
        created = self.other.post("/api/teams", json={"competition_id": "competition", "name": "Used Team"}).get_json()
        grant = self.other.get("/api/ai/overview").get_json()["grants"][0]
        self.assertEqual(self.other.post("/api/ai/keys", json={"grant_id": grant["id"], "name": "Keep History"}).status_code, 201)
        self.assertEqual(self.other.delete(f"/api/teams/{created['id']}").status_code, 409)
        self.assertEqual(self.admin.delete("/api/manage/problems/problem-a").status_code, 409)
        self.assertEqual(self.admin.delete("/api/manage/tracks/quota-track").status_code, 409)

    def test_evaluation_prefers_problem_grant_and_never_bypasses_exhaustion_or_pause(self):
        self.add_problems()
        scoped = self.problem_grant("problem-a", max_calls=1)
        with self.app.app_context():
            problem = db.session.get(Problem, "problem-a")
            submission = Submission(id="scoped-submission", problem=problem, team_id="team", title="Scoped Agent")
            version = SubmissionVersion(id="scoped-version", submission=submission, version=1, readme_md="Agent", created_by="member", status="submitted")
            db.session.add(EvaluationRun(id="scoped-run", problem=problem, submission_version=version,
                config_snapshot={"adapter": "minecraft-agent-v1", "api": {"enabled": True, "max_calls": 10}},
                submission_snapshot={}, status="running", api_token_hash=hash_token("scoped-run-token"), lease_expires_at=utcnow()+timedelta(minutes=5)))
            db.session.commit()
        path = "/api/evaluation-worker/runs/scoped-run/api"
        body = {"model": "contest-model", "messages": [{"role": "user", "content": "observe"}], "max_tokens": 8}
        headers = {"X-Evaluation-API-Token": "scoped-run-token"}
        self.assertEqual(self.member.post(path, headers=headers, json=body).status_code, 200)
        self.assertEqual(self.member.post(path, headers=headers, json=body).status_code, 429)
        self.problem_grant("problem-a", max_calls=2, enabled=False)
        self.assertEqual(self.member.post(path, headers=headers, json=body).status_code, 429)
        with self.app.app_context():
            self.assertEqual(db.session.get(AIGrant, self.grant["id"]).calls_used, 0)
            self.assertEqual(db.session.get(AIGrant, scoped["id"]).calls_used, 1)
            self.assertEqual(db.session.get(EvaluationRun, "scoped-run").api_calls_used, 1)


if __name__ == "__main__":
    unittest.main()
