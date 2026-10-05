"""AutoDL Pro contract tested through a real local HTTP service, without billing."""
import concurrent.futures
import json
import sys
import tempfile
import threading
import unittest
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from platform_api import create_app
from platform_api.extensions import db
from platform_api.models import Competition, CompetitionReviewer, Problem, Session, Team, TeamMember, Track, User, utcnow
from platform_api.security import hash_token
from platform_api.compute import claim_session, finish, heartbeat, process_session
from platform_api.compute_models import ComputeGrant, ComputeInstance, ComputeProvider, ComputeSession, ComputeWorkerHeartbeat


class AutoDLService(BaseHTTPRequestHandler):
    calls = []
    instances = {}
    price = 3600
    lose_create_reply = False
    fail_stop = False
    null_empty_list = False
    reported_total = 0
    private_images = []
    image_max_page = 1
    snapshot_extra = {}

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.do_POST()

    def do_POST(self):
        cls = type(self)
        parsed = urlsplit(self.path)
        action = parsed.path.split("/pro/")[-1]
        if self.command == "GET":
            assert not int(self.headers.get("Content-Length", "0")), "Real Pro GET endpoints require query parameters"
            body = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            assert body.get("instance_uuid"), "Missing Pro GET query instance_uuid"
        else:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cls.calls.append((self.command, action, body, self.headers.get("Authorization")))
        assert self.headers.get("Authorization") == "private-autodl-token"
        code = 200
        if action == "create":
            remote = "pro-local-" + str(len(cls.instances) + 1)
            cls.instances[remote] = {"uuid": remote, "name": body["instance_name"], "status": "running"}
            if cls.lose_create_reply:
                cls.lose_create_reply = False
                self.connection.shutdown(2)
                self.connection.close()
                return
            result = remote
        elif action in {"list", "image/private/list"}:
            rows = list(cls.instances.values()) if action == "list" else cls.private_images
            result = {"list": None if not rows and cls.null_empty_list else rows,
                "max_page": 1 if action == "list" else cls.image_max_page,
                "result_total": len(rows) if rows else cls.reported_total}
        else:
            instance = cls.instances[body["instance_uuid"]]
            if action == "power_off":
                if cls.fail_stop:
                    code = 503
                else:
                    instance["status"] = "stopped"
                result = None
            elif action == "power_on":
                instance["status"] = "running"
                result = None
            elif action == "status":
                result = instance["status"]
            elif action == "snapshot":
                result = {"payg_price": cls.price, "proxy_host": "ssh.autodl.example", "ssh_port": 12345,
                    "root_password": "private-root-password", "ssh_command": "arbitrary upstream shell text", **cls.snapshot_extra}
            else:
                raise AssertionError(action)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"code": "Success", "data": result}).encode())


class ComputeResourcesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), AutoDLService)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        AutoDLService.calls, AutoDLService.instances = [], {}
        AutoDLService.price, AutoDLService.lose_create_reply, AutoDLService.fail_stop = 3600, False, False
        AutoDLService.null_empty_list, AutoDLService.reported_total = False, 0
        AutoDLService.private_images, AutoDLService.image_max_page = [], 1
        AutoDLService.snapshot_extra = {"jupyter_port": 0, "jupyter_domain": "a1-test.cqa1.seetacloud.com:8443",
            "jupyter_token": "private-jupyter&token=value", "service_6006_port": 0,
            "service_6006_domain": "u1-test.cqa1.seetacloud.com:8443", "service_6006_port_protocol": "http",
            "service_6008_port": 0, "service_6008_domain": "u2-test.cqa1.seetacloud.com:8443",
            "service_6008_port_protocol": "tcp", "usage_info": {"valid": True, "valid_at": "2026-10-05T09:00:00+00:00",
                "cpu_usage_percent": 25.5, "mem_usage_percent": 12.5, "mem_usage": 1024**3,
                "mem_limit": 8 * 1024**3, "root_fs_used_size": 2 * 1024**3, "root_fs_total_size": 30 * 1024**3,
                "data_disk_used_size": 0, "data_disk_total_size": 0, "secret": "upstream-secret"}}
        self.runtime = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "AUTO_CREATE_SCHEMA": True, "SEED_DATABASE": False,
            "UPLOAD_FOLDER": self.runtime.name, "COMPUTE_ALLOW_LOCAL_HTTP": True,
            "AI_GATEWAY_ENCRYPTION_KEY": "test-credential-encryption",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (Path(self.runtime.name) / "compute.sqlite").as_posix()})
        self.addCleanup(self.close)
        self.admin, self.member, self.other, self.reviewer = [self.app.test_client() for _ in range(4)]
        with self.app.app_context():
            for role in ("admin", "member", "other", "reviewer"):
                db.session.add(User(id=role, email=role + "@example.com", name=role, password_hash="unused", role="member" if role == "other" else role))
                db.session.add(Session(token_hash=hash_token(role), user_id=role, expires_at=utcnow() + timedelta(days=1)))
            competition = Competition(id="comp", name="Competition", slug="competition", summary="test", status="published")
            track = Track(id="track", competition=competition, slug="track", name="Track")
            db.session.add_all([Problem(id="problem", track=track, title="Problem", code="P", slug="problem"),
                Problem(id="problem2", track=track, title="Problem 2", code="P2", slug="problem2")])
            for name, user in (("team", "member"), ("team2", "other")):
                team = Team(id=name, competition=competition, name=name, captain_id=user, invite_code=name)
                db.session.add(team)
                db.session.add(TeamMember(team=team, user_id=user))
            db.session.add(CompetitionReviewer(competition_id="comp", reviewer_id="reviewer"))
            db.session.commit()
            heartbeat("test-worker")
        for client, role in ((self.admin, "admin"), (self.member, "member"), (self.other, "other"), (self.reviewer, "reviewer")):
            client.set_cookie("session_token", role)
        self.provider_config = {"name": "Local AutoDL", "base_url": f"http://127.0.0.1:{self.server.server_port}",
            "token": "private-autodl-token", "image_uuid": "test-image", "gpu_spec_uuid": "4090D", "gpu_label": "4090 D", "gpu_count": 2, "hourly_price_millis": 3600}
        response = self.admin.post("/api/compute/manage/providers", json=self.provider_config)
        self.assertEqual(response.status_code, 201, response.json)
        self.provider = response.json
        self.config = {"provider_id": self.provider["id"], "max_gpu_seconds": 7200, "max_cost_millis": 4000, "enabled": True}
        self.save_quota(self.config)
        self.grant = self.member.get("/api/compute/overview").json["grants"][0]

    def close(self):
        with self.app.app_context():
            db.session.remove(); db.engine.dispose()
        self.runtime.cleanup()

    def save_quota(self, config, problem="problem", status=200):
        response = self.admin.put(f"/api/compute/manage/problems/{problem}/quota", json=config)
        self.assertEqual(response.status_code, status, response.json)
        return response

    def start(self, request_id="request-1", seconds=60, client=None, grant=None, status=202):
        response = (client or self.member).post(f"/api/compute/grants/{grant or self.grant['id']}/start", json={"request_id": request_id, "duration_seconds": seconds})
        self.assertEqual(response.status_code, status, response.json)
        return response

    def cycle(self, rewind=False):
        with self.app.app_context():
            heartbeat("test-worker")
            if rewind:
                for session in ComputeSession.query.all():
                    session.next_check_at = utcnow() - timedelta(seconds=1)
                db.session.commit()
            session = claim_session("test-worker")
            if session:
                process_session(session, "test-worker")

    def elapsed(self, seconds=40, deadline=False):
        with self.app.app_context():
            session = ComputeSession.query.first()
            session.dispatched_at = utcnow() - timedelta(seconds=seconds)
            if deadline:
                session.deadline = utcnow() - timedelta(seconds=1)
            db.session.commit()

    def test_channel_options_permission_and_compatible_save(self):
        response = self.admin.get("/api/compute/manage/options")
        self.assertEqual(response.status_code, 200)
        catalog = response.json
        self.assertTrue(any(g["id"] == "4090D" for g in catalog["gpu_specs"]))
        image = next(i for i in catalog["public_images"] if i["id"] == "base-image-l2t43iu6uk")
        self.assertEqual(image["cuda_v_from"], 118)
        for client in (self.member, self.reviewer):
            self.assertEqual(client.get("/api/compute/manage/options").status_code, 403)
        saved = self.admin.put(f"/api/compute/manage/providers/{self.provider['id']}", json={
            "gpu_spec_uuid": "4090D", "gpu_label": "RTX 4090 D", "image_uuid": image["id"],
            "gpu_count": 2, "cuda_v_from": image["cuda_v_from"], "data_centers": ["westDC3", "beijingDC2"]})
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json["data_centers"], ["westDC3", "beijingDC2"])
        self.start(); self.cycle()
        create = next(c[2] for c in AutoDLService.calls if c[1] == "create")
        self.assertEqual((create["gpu_spec_uuid"], create["image_uuid"], create["cuda_v_from"], create["req_gpu_amount"]),
            ("4090D", image["id"], 118, 2))
        self.assertEqual(create["data_center_list"], saved.json["data_centers"])

    def test_private_image_selection_filters_secrets_and_does_not_save_token(self):
        AutoDLService.private_images = [
            {"image_uuid": "image-ready", "name": "Agent runtime", "status": "finished", "secret": "upstream-secret"},
            {"image_uuid": "image-pending", "name": "Building", "status": "saving", "root_password": "upstream-password"}]
        AutoDLService.image_max_page = 2
        with self.app.app_context():
            original = db.session.get(ComputeProvider, self.provider["id"]).secret
        response = self.admin.post("/api/compute/manage/private-images", json={
            "provider_id": self.provider["id"], "token": "private-autodl-token", "page": 1})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.json, {"images": [{"id": "image-ready", "name": "Agent runtime", "ready": True},
            {"id": "image-pending", "name": "Building", "ready": False}], "page": 1, "has_more": True})
        self.assertEqual(AutoDLService.calls[0][1:3], ("image/private/list", {"page_index": 1, "page_size": 100}))
        response = self.admin.post("/api/compute/manage/private-images", json={"provider_id": self.provider["id"], "page": 2})
        self.assertEqual(response.json["has_more"], False)
        self.assertEqual(AutoDLService.calls[-1][2]["page_index"], 2)
        self.assertTrue(all(c[1] == "image/private/list" for c in AutoDLService.calls))
        with self.app.app_context():
            self.assertEqual(ComputeProvider.query.count(), 1)
            self.assertEqual(db.session.get(ComputeProvider, self.provider["id"]).secret, original)
            self.assertEqual(ComputeInstance.query.count(), 0)

    def test_private_images_empty_account_is_usable(self):
        AutoDLService.null_empty_list = True
        AutoDLService.image_max_page = 0
        response = self.admin.post("/api/compute/manage/private-images", json={"provider_id": self.provider["id"]})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json, {"images": [], "page": 1, "has_more": False})

    def test_private_images_reject_invalid_input_and_permissions_before_upstream(self):
        path = "/api/compute/manage/private-images"
        for client in (self.member, self.other, self.reviewer):
            self.assertEqual(client.post(path, json={"provider_id": self.provider["id"]}).status_code, 403)
        for body in ({}, {"token": "bad\ntoken"}, {"token": []}, {"provider_id": []},
            {"provider_id": self.provider["id"], "page": 0}):
            self.assertEqual(self.admin.post(path, json=body).status_code, 400)
        self.assertEqual(self.admin.post(path, json={"provider_id": "missing"}).status_code, 404)
        self.assertFalse(AutoDLService.calls)

    def test_private_images_reject_bad_upstream_without_leaking_details(self):
        path = "/api/compute/manage/private-images"
        for images, maximum in (([{"image_uuid": "upstream-secret/bad"}], 1),
            ([{"image_uuid": "image-ok"}], "upstream-secret"), (["upstream-secret"], 1)):
            AutoDLService.private_images, AutoDLService.image_max_page = images, maximum
            response = self.admin.post(path, json={"provider_id": self.provider["id"]})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json["code"], "provider_invalid_result")
            self.assertNotIn("upstream-secret", response.get_data(as_text=True))

    def test_official_contract_and_private_ssh(self):
        session = self.start().json
        self.assertFalse(AutoDLService.calls)
        self.cycle()
        data = self.member.get("/api/compute/overview").json
        self.assertEqual(data["sessions"][0]["state"], "running")
        self.assertEqual(data["grants"][0]["gpu_seconds_used"], 180)
        create = next(call for call in AutoDLService.calls if call[1] == "create")
        self.assertEqual(create[2]["req_gpu_amount"], 2)
        self.assertIn("/usr/bin/shutdown", create[2]["start_command"])
        self.assertTrue(any(method == "GET" and action == "status" and body["instance_uuid"] for method, action, body, _ in AutoDLService.calls))
        instance_id = session["instance_id"]
        response = self.member.post(f"/api/compute/instances/{instance_id}/ssh")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.json["command"], "ssh -p 12345 root@ssh.autodl.example")
        self.assertEqual(self.other.post(f"/api/compute/instances/{instance_id}/ssh").status_code, 403)
        self.assertEqual(self.reviewer.post(f"/api/compute/instances/{instance_id}/ssh").status_code, 403)
        for client in (self.member, self.admin, self.reviewer):
            raw = client.get("/api/compute/overview").get_data(as_text=True)
            self.assertNotIn("private-root-password", raw)
            self.assertNotIn("private-autodl-token", raw)

    def test_same_limits_independent_counters_and_future_teams(self):
        self.start()
        other = self.other.get("/api/compute/overview").json["grants"][0]
        self.assertEqual((other["max_gpu_seconds"], other["gpu_seconds_used"]), (7200, 0))
        team = self.other.post("/api/teams", json={"competition_id": "comp", "name": "future"})
        self.assertEqual(team.status_code, 201, team.json)
        grants = self.other.get("/api/compute/overview").json["grants"]
        future = next(g for g in grants if g["team_id"] == team.json["id"])
        self.assertEqual((future["max_gpu_seconds"], future["max_cost_millis"]), (7200, 4000))
        self.save_quota({**self.config, "max_gpu_seconds": 9000})
        current = self.member.get("/api/compute/overview").json["grants"][0]
        self.assertEqual((current["id"], current["gpu_seconds_used"]), (self.grant["id"], 180))

    def test_tools_launch_matches_vendor_paths_and_encodes_token(self):
        session = self.start().json; self.cycle()
        path = f"/api/compute/instances/{session['instance_id']}/tools"
        for tool, expected in (("jupyter", "/jupyter"), ("autopanel", "/")):
            response = self.member.get(path + "/" + tool)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
            target = urlsplit(response.headers["Location"])
            self.assertEqual((target.scheme, target.netloc, target.path),
                ("https", "a1-test.cqa1.seetacloud.com:8443", expected))
            self.assertEqual(parse_qs(target.query), {"token": ["private-jupyter&token=value"]})
        self.assertEqual(self.admin.get(path + "/autopanel").status_code, 302)
        self.assertEqual(self.member.get(path + "/unknown").status_code, 404)
        overview = self.admin.get("/api/compute/overview").get_data(as_text=True)
        self.assertNotIn("private-jupyter", overview)
        self.assertNotIn("jupyter_domain", overview)
        self.assertEqual(sum(c[1] == "create" for c in AutoDLService.calls), 1)
        self.assertFalse(any(c[1] in {"power_on", "power_off"} for c in AutoDLService.calls))

    def test_tools_monitor_and_custom_service_disclose_only_allowed_fields(self):
        session = self.start().json; self.cycle()
        response = self.member.post(f"/api/compute/instances/{session['instance_id']}/tools")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.json["available"], {"jupyter": True, "autopanel": True})
        self.assertEqual(response.json["monitor"]["cpu_usage_percent"], 25.5)
        self.assertTrue(response.json["monitor"]["valid"])
        first, second = response.json["services"]
        self.assertEqual(first["url"], "https://u1-test.cqa1.seetacloud.com:8443")
        self.assertEqual((second["protocol"], second["url"]), ("tcp", None))
        for secret in ("private-jupyter", "private-root-password", "private-autodl-token", "upstream-secret"):
            self.assertNotIn(secret, response.get_data(as_text=True))

    def test_tools_reject_other_teams_reviewer_organizer_and_revoked_members(self):
        session = self.start().json; self.cycle()
        path = f"/api/compute/instances/{session['instance_id']}/tools"
        organizer = self.app.test_client()
        with self.app.app_context():
            db.session.add(User(id="organizer", email="organizer@example.com", name="organizer", password_hash="unused", role="organizer"))
            db.session.add(Session(token_hash=hash_token("organizer"), user_id="organizer", expires_at=utcnow()+timedelta(days=1)))
            db.session.commit()
        organizer.set_cookie("session_token", "organizer")
        before = len(AutoDLService.calls)
        for client in (self.other, self.reviewer, organizer):
            self.assertEqual(client.post(path).status_code, 403)
            self.assertEqual(client.get(path + "/jupyter").status_code, 403)
        with self.app.app_context():
            TeamMember.query.filter_by(team_id="team", user_id="member").delete(); db.session.commit()
        self.assertEqual(self.member.post(path).status_code, 403)
        self.assertEqual(len(AutoDLService.calls), before)

    def test_tools_reject_stopped_upstream_and_expired_or_stopping_session(self):
        session = self.start().json; self.cycle()
        path = f"/api/compute/instances/{session['instance_id']}/tools"
        remote = next(iter(AutoDLService.instances))
        AutoDLService.instances[remote]["status"] = "stopped"
        self.assertEqual(self.member.get(path + "/autopanel").status_code, 409)
        with self.app.app_context():
            ComputeSession.query.first().deadline = utcnow() - timedelta(seconds=1); db.session.commit()
        before = len(AutoDLService.calls)
        self.assertEqual(self.member.post(path).status_code, 409)
        self.assertEqual(len(AutoDLService.calls), before)

    def test_tools_revalidate_after_slow_snapshot(self):
        from platform_api.autodl import AutoDL
        session = self.start().json; self.cycle()
        path = f"/api/compute/instances/{session['instance_id']}/tools"
        original_snapshot = AutoDL.snapshot
        def late(client, remote):
            result = original_snapshot(client, remote)
            ComputeSession.query.first().stop_requested = True
            db.session.commit()
            return result
        with patch.object(AutoDL, "snapshot", late):
            self.assertEqual(self.member.get(path + "/jupyter").status_code, 409)

    def test_tools_unsafe_addresses_and_invalid_metrics_are_not_exposed(self):
        session = self.start().json; self.cycle()
        path = f"/api/compute/instances/{session['instance_id']}/tools"
        AutoDLService.snapshot_extra.update({"jupyter_domain": "a1.autodl.com@evil.example:8443",
            "service_6006_domain": "127.0.0.1:5000", "service_6008_domain": "a1.autodl.com/evil",
            "usage_info": {"valid": False, "valid_at": "<script>", "cpu_usage_percent": 1e300,
                "mem_usage_percent": True, "mem_usage": -1, "mem_limit": 10**200}})
        response = self.member.post(path)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["available"], {"jupyter": False, "autopanel": False})
        self.assertEqual(response.json["services"], [])
        self.assertIsNone(response.json["monitor"]["cpu_usage_percent"])
        self.assertIsNone(response.json["monitor"]["mem_limit"])
        self.assertIsNone(response.json["monitor"]["valid_at"])
        self.assertEqual(self.member.get(path + "/jupyter").status_code, 409)

    def test_monitor_false_provider_flag_keeps_samples_and_marks_age(self):
        from platform_api.compute_tools import snapshot_tools
        usage = AutoDLService.snapshot_extra["usage_info"]
        usage.update({"valid": False, "valid_at": (utcnow()-timedelta(minutes=5)).isoformat(), "cpu_usage_percent": 250.5})
        tools = snapshot_tools(AutoDLService.snapshot_extra)
        self.assertTrue(tools["monitor"]["valid"])
        self.assertTrue(tools["monitor"]["stale"])
        self.assertEqual(tools["monitor"]["cpu_usage_percent"], 250.5)
        usage["valid_at"] = utcnow().isoformat()
        self.assertFalse(snapshot_tools(AutoDLService.snapshot_extra)["monitor"]["stale"])

    def test_tools_legacy_direct_ports_are_supported(self):
        from platform_api.compute_tools import snapshot_tools
        sample = {**AutoDLService.snapshot_extra, "proxy_host": "connect.test.autodl.com",
            "jupyter_port": 12345, "service_6006_port": 60000, "service_6008_port": True}
        tools = snapshot_tools(sample)
        self.assertTrue(tools["jupyter_url"].startswith("http://connect.test.autodl.com:12345/jupyter?"))
        self.assertEqual(tools["services"][0]["url"], "http://connect.test.autodl.com:60000")
        self.assertEqual(len(tools["services"]), 1)

    def test_idempotency_and_overlapping_starts(self):
        first = self.start().json
        second = self.start(status=200).json
        self.assertEqual(first["id"], second["id"])
        self.start(seconds=120, status=409)
        self.start(request_id="request-2", status=409)
        self.assertEqual(self.member.get("/api/compute/overview").json["grants"][0]["gpu_seconds_used"], 180)

    def test_concurrent_start_reserves_once(self):
        def run(index):
            client = self.app.test_client(); client.set_cookie("session_token", "member")
            return client.post(f"/api/compute/grants/{self.grant['id']}/start", json={"request_id": f"concurrent-{index}", "duration_seconds": 60}).status_code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(run, range(2))), [202, 409])
        with self.app.app_context():
            self.assertEqual(ComputeSession.query.count(), 1)
            self.assertEqual(db.session.get(ComputeGrant, self.grant["id"]).gpu_seconds_used, 180)

    def test_budget_exhaustion_happens_before_provider_call(self):
        self.save_quota({**self.config, "max_gpu_seconds": 179})
        self.start(status=429)
        self.save_quota({**self.config, "max_cost_millis": 89})
        self.start(status=429)
        self.assertFalse(AutoDLService.calls)

    def test_offline_worker_blocks_paid_requests(self):
        with self.app.app_context():
            ComputeWorkerHeartbeat.query.delete(); db.session.commit()
        self.start(status=503)
        self.assertFalse(AutoDLService.calls)

    def test_stop_settlement_and_same_instance_restart(self):
        session = self.start().json; self.cycle(); self.elapsed()
        self.assertEqual(self.member.post(f"/api/compute/instances/{session['instance_id']}/stop").status_code, 200)
        self.cycle()
        data = self.member.get("/api/compute/overview").json
        settled = data["sessions"][0]
        self.assertEqual(settled["state"], "stopped")
        self.assertGreaterEqual(settled["charged_gpu_seconds"], 80)
        self.assertLess(settled["charged_gpu_seconds"], 180)
        self.assertEqual(data["grants"][0]["gpu_seconds_used"], settled["charged_gpu_seconds"])
        self.start("request-2"); self.cycle()
        self.assertEqual(sum(c[1] == "create" for c in AutoDLService.calls), 1)
        self.assertEqual(sum(c[1] == "power_on" for c in AutoDLService.calls), 1)

    def test_deadline_automatically_shuts_down(self):
        self.start(); self.cycle(); self.elapsed(deadline=True); self.cycle(rewind=True)
        self.assertEqual(self.member.get("/api/compute/overview").json["sessions"][0]["state"], "stopped")
        self.assertTrue(any(c[1] == "power_off" for c in AutoDLService.calls))

    def test_uncertain_create_reconciles_without_duplicate_billing(self):
        AutoDLService.lose_create_reply = True
        self.start(); self.cycle()
        data = self.member.get("/api/compute/overview").json
        self.assertEqual(data["sessions"][0]["state"], "uncertain")
        self.assertEqual(data["grants"][0]["gpu_seconds_used"], 180)
        self.cycle(rewind=True)
        self.assertEqual(self.member.get("/api/compute/overview").json["sessions"][0]["state"], "running")
        self.assertEqual(sum(c[1] == "create" for c in AutoDLService.calls), 1)

    def test_running_instance_recovers_from_rejected_status_without_restarting(self):
        self.start(); self.cycle()
        with self.app.app_context():
            session = ComputeSession.query.first()
            session.state, session.error_code = "uncertain", "provider_rejected"
            session.instance.provider_status = "uncreated"
            db.session.commit()
        self.cycle(rewind=True)
        data = self.member.get("/api/compute/overview").json
        self.assertEqual(data["sessions"][0]["state"], "running")
        self.assertIsNone(data["sessions"][0]["error_code"])
        self.assertEqual(data["instances"][0]["provider_status"], "running")
        self.assertEqual(sum(c[1] == "create" for c in AutoDLService.calls), 1)
        self.assertEqual(sum(c[1] == "power_on" for c in AutoDLService.calls), 0)
        self.assertFalse(any(c[1] == "power_off" for c in AutoDLService.calls))

    def test_failed_shutdown_preserves_reservation_then_recovers(self):
        session = self.start().json; self.cycle(); AutoDLService.fail_stop = True
        self.member.post(f"/api/compute/instances/{session['instance_id']}/stop"); self.cycle()
        data = self.member.get("/api/compute/overview").json
        self.assertEqual(data["grants"][0]["gpu_seconds_used"], 180)
        self.assertEqual(data["sessions"][0]["state"], "uncertain")
        self.start("request-2", status=409)
        AutoDLService.fail_stop = False; self.cycle(rewind=True)
        self.assertEqual(self.member.get("/api/compute/overview").json["sessions"][0]["state"], "stopped")

    def test_worker_crash_recovers_and_settlement_is_idempotent(self):
        self.start(); self.cycle()
        with self.app.app_context():
            session = ComputeSession.query.first()
            session.lease_owner = "crashed-worker"
            session.lease_expires_at = utcnow() - timedelta(seconds=1)
            session.deadline = utcnow() - timedelta(seconds=1)
            session.next_check_at = utcnow() - timedelta(seconds=1)
            db.session.commit()
        self.cycle()
        with self.app.app_context():
            session = ComputeSession.query.first()
            used = session.grant.gpu_seconds_used
            finish(session, utcnow())
            self.assertEqual(session.grant.gpu_seconds_used, used)

    def test_disabling_problem_stops_instance_and_preserves_usage(self):
        self.start(); self.cycle()
        self.save_quota({**self.config, "enabled": False})
        self.cycle(rewind=True)
        self.assertEqual(self.member.get("/api/compute/overview").json["sessions"][0]["state"], "stopped")
        self.start("request-2", status=403)

    def test_price_above_ceiling_stops_early(self):
        AutoDLService.price = 5000
        self.start(); self.cycle()
        session = self.member.get("/api/compute/overview").json["sessions"][0]
        self.assertEqual((session["state"], session["error_code"], session["rate_millis"]), ("stopped", "price_above_ceiling", 5000))

    def test_quota_lowering_is_atomic_and_channel_immutable_after_allocation(self):
        self.start()
        self.save_quota({**self.config, "max_gpu_seconds": 100}, status=409)
        grants = self.admin.get("/api/compute/overview").json["grants"]
        self.assertEqual({g["max_gpu_seconds"] for g in grants}, {7200})
        response = self.admin.put(f"/api/compute/manage/providers/{self.provider['id']}", json={"gpu_count": 3})
        self.assertEqual(response.status_code, 409, response.json)

    def test_permissions_encryption_and_no_credential_logs(self):
        self.start(client=self.other, status=403)
        self.start(client=self.reviewer, status=403)
        self.assertEqual(self.member.get("/api/compute/manage/providers").status_code, 403)
        with self.app.app_context():
            self.assertNotIn("private-autodl-token", db.session.get(ComputeProvider, self.provider["id"]).secret)
            self.assertEqual(ComputeSession.query.count(), 0)
        listed = self.admin.get("/api/compute/manage/providers").get_data(as_text=True)
        self.assertNotIn("private-autodl-token", listed)
        self.assertEqual(self.reviewer.get("/api/compute/overview").json["grants"].__len__(), 2)

    def test_cancel_queued_request_refunds_without_creation(self):
        session = self.start().json
        self.member.post(f"/api/compute/instances/{session['instance_id']}/stop"); self.cycle()
        self.assertFalse(AutoDLService.calls)
        self.assertEqual(self.member.get("/api/compute/overview").json["grants"][0]["gpu_seconds_used"], 0)

    def test_other_problems_independent_and_history_blocks_parent_deletion(self):
        self.save_quota(self.config, problem="problem2")
        self.start()
        self.assertEqual(self.admin.delete("/api/manage/problems/problem").status_code, 409)
        self.assertEqual(self.admin.delete("/api/manage/problems/problem2").status_code, 204)
        with self.app.app_context():
            self.assertEqual(ComputeGrant.query.filter_by(problem_id="problem2").count(), 0)
        self.assertEqual(self.member.delete("/api/teams/team").status_code, 409)

    def test_url_validation_and_read_only_connection_test(self):
        response = self.admin.post("/api/compute/manage/providers", json={**self.provider_config, "name": "bad", "base_url": "https://evil.example"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.admin.post(f"/api/compute/manage/providers/{self.provider['id']}/test").status_code, 200)
        self.assertEqual([c[1] for c in AutoDLService.calls], ["list"])

    def test_real_api_null_empty_lists_are_supported(self):
        AutoDLService.null_empty_list = True
        self.assertEqual(self.admin.post(f"/api/compute/manage/providers/{self.provider['id']}/test").status_code, 200)
        with self.app.app_context():
            from platform_api.autodl import AutoDL
            client = AutoDL(db.session.get(ComputeProvider, self.provider["id"]))
            self.assertEqual(client.list_page(action="image/private/list")["list"], [])
            self.assertIsNone(client.find_created("not-created"))

    def test_null_list_with_positive_count_is_not_treated_as_empty(self):
        AutoDLService.null_empty_list, AutoDLService.reported_total = True, 1
        result = self.admin.post(f"/api/compute/manage/providers/{self.provider['id']}/test")
        self.assertEqual(result.status_code, 502, result.json)

    def test_queued_credential_failure_can_be_cancelled_without_billing(self):
        session = self.start().json
        self.app.config["AI_GATEWAY_ENCRYPTION_KEY"] = "incorrect-key"
        self.cycle()
        data = self.member.get("/api/compute/overview").json
        self.assertEqual(data["sessions"][0]["state"], "queued")
        self.assertFalse(AutoDLService.calls)
        self.member.post(f"/api/compute/instances/{session['instance_id']}/stop")
        self.cycle()
        self.assertEqual(self.member.get("/api/compute/overview").json["grants"][0]["gpu_seconds_used"], 0)

    def test_disabled_provider_stops_existing_session(self):
        self.start(); self.cycle()
        response = self.admin.put(f"/api/compute/manage/providers/{self.provider['id']}", json={"enabled": False})
        self.assertEqual(response.status_code, 200, response.json)
        self.cycle(rewind=True)
        self.assertEqual(self.member.get("/api/compute/overview").json["sessions"][0]["state"], "stopped")

    def test_stale_worker_does_not_mutate_or_refund_new_owner(self):
        self.start(); self.cycle()
        with self.app.app_context():
            session = ComputeSession.query.first()
            session.lease_owner = "new-worker"
            session.lease_expires_at = utcnow() + timedelta(seconds=60)
            db.session.commit()
            before = len(AutoDLService.calls)
            process_session(session, "old-worker")
            self.assertEqual(len(AutoDLService.calls), before)
            self.assertEqual(session.grant.gpu_seconds_used, 180)

    def test_active_session_remains_visible_beyond_recent_history_limit(self):
        active = self.start().json
        with self.app.app_context():
            for index in range(101):
                db.session.add(ComputeSession(grant_id=self.grant["id"], instance_id=active["instance_id"],
                    request_id=f"historical-{index}", requested_by="member", state="stopped", duration_seconds=60,
                    gpu_count=2, rate_millis=3600, reserved_gpu_seconds=0, reserved_cost_millis=0,
                    charged_gpu_seconds=0, charged_cost_millis=0, next_check_at=utcnow(), finished_at=utcnow()))
            db.session.commit()
        data = self.member.get("/api/compute/overview").json
        self.assertEqual(len(data["sessions"]), 100)
        self.assertEqual([s["id"] for s in data["active_sessions"]], [active["id"]])
        self.assertEqual(self.other.get("/api/compute/overview").json["active_sessions"], [])


if __name__ == "__main__":
    unittest.main()
