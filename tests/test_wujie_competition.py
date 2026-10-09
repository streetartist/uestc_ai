import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_api as fixtures
from build_wujie_competition import write_bundle


class WujieCompetitionTests(unittest.TestCase):
    setUp = fixtures.PlatformApiTestCase.setUp
    tearDown = fixtures.PlatformApiTestCase.tearDown
    login = fixtures.PlatformApiTestCase.login

    def test_bundle_imports_three_private_drafts_with_stage_weights(self):
        self.login(self.admin, "admin@uestcai.top")
        with tempfile.TemporaryDirectory() as directory:
            images = {key: {"controller": "sha256:" + "a" * 64, "agent": "sha256:" + "b" * 64}
                      for key in ("minecraft", "robot-arm")}
            catalogue = write_bundle(Path(directory), images)
            response = self.admin.post("/api/competitions", json=catalogue["competition"])
            self.assertEqual(response.status_code, 201, response.get_json())
            competition = response.get_json()
            for track in catalogue["tracks"]:
                response = self.admin.post(f"/api/competitions/{competition['slug']}/tracks", json={
                    key: track[key] for key in ("name", "slug", "description", "config", "position")})
                self.assertEqual(response.status_code, 201, response.get_json())
                track_id = response.get_json()["id"]
                response = self.admin.post(f"/api/manage/tracks/{track_id}/problem-packages", data={
                    "file": (io.BytesIO((Path(directory) / track["package"]).read_bytes()), track["package"])})
                self.assertEqual(response.status_code, 201, response.get_json())
                problem = response.get_json()
                self.assertEqual(problem["status"], "draft")
                self.assertEqual(problem["scoring_config"]["external_weight_percent"], 50)
                self.assertEqual(problem["judging_schema"]["rubric"], {"模型或智能体实际表现": .8, "技术路线说明": .2})
                self.assertEqual(next(a for a in problem["submission_schema"]["attachments"] if a["key"] == "research_report")["min_count"], 0)
                if track["slug"] == "world-exploration":
                    scenes = track["setup"]["runtime"]["scenarios"]
                    self.assertEqual([s["max_steps"] for s in scenes], [600, 1500, 3000])
                    self.assertEqual([len(s["goals"]) for s in scenes], [3, 4, 3])
                    self.assertEqual(scenes[1]["objective_labels"],
                                     ["制作木镐", "开采圆石", "制作石镐", "制作熔炉"])
                    self.assertTrue(all(s["initial_inventory"] == [] for s in scenes))
                if track["slug"] == "embodied-agent":
                    self.assertEqual(problem["evaluation_config"]["adapter"], "libero-agent-v1")
                    self.assertEqual(problem["scoring_config"]["performance_scoring"]["preset"], "wujie-libero-v1")
                self.assertEqual(self.member.get(f"/api/problems/{problem['slug']}").status_code, 404)
                self.assertNotIn("scenarios", problem["evaluation_config"])
            detail = self.admin.get(f"/api/competitions/{competition['slug']}").get_json()
            self.assertEqual(len(detail["tracks"]), 3)
            self.assertIn("30,000元", detail["config"]["overview_md"])
            self.assertIsNone(detail["starts_at"])
            self.assertEqual(self.member.get(f"/api/leaderboards/{competition['slug']}").status_code, 404)

    def test_weighted_review_and_defense_ignore_forged_total_and_wait_for_both(self):
        self.login(self.admin, "admin@uestcai.top")
        self.login(self.reviewer, "reviewer@uestc.ai")
        competition = self.admin.get("/api/manage/catalog").get_json()[0]
        problem = competition["tracks"][0]["problems"][0]
        self.assertEqual(self.admin.patch(f"/api/manage/problems/{problem['id']}", json={
            "status": "published",
            "judging_schema": {"rubric": {"表现": .8, "报告": .2}},
            "scoring_config": {"external_weight_percent": 50, "review_score_mode": "weighted"}}).status_code, 200)
        team = self.admin.post("/api/teams", json={"competition_id": competition["id"], "name": "评分验证"}).get_json()
        self.assertEqual(self.admin.post("/api/registrations", json={"competition_id": competition["id"],
            "team_id": team["id"], "track_id": problem["track_id"]}).status_code, 201)
        response = self.admin.post("/api/submissions", json={"problem_id": problem["id"], "team_id": team["id"],
            "title": "加权验证", "readme_md": "# 可复现研究", "fields": {}, "status": "submitted"})
        self.assertEqual(response.status_code, 201, response.get_json())
        version = response.get_json()["version"]["id"]
        response = self.reviewer.post("/api/reviews", json={"submission_version_id": version,
            "scores": {"表现": 90, "报告": 70}, "total_score": 999, "status": "submitted"})
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["total_score"], 86)
        self.assertEqual(self.reviewer.post("/api/reviews", json={"submission_version_id": version,
            "scores": {"表现": 101, "报告": 70}}).status_code, 400)
        self.assertEqual(self.reviewer.post("/api/reviews", json={"submission_version_id": version,
            "scores": {"表现": 90}}).status_code, 400)
        from platform_api.models import SubmissionVersion
        from platform_api.extensions import db
        from platform_api.reviewing import combined_score
        with self.app.app_context():
            self.assertIsNone(combined_score(db.session.get(SubmissionVersion, version)))
        response = self.admin.post("/api/scores/import", json={"competition_id": competition["id"],
            "problem_id": problem["id"], "source": "onsite-defense", "label": "现场答辩", "status": "confirmed",
            "records": [{"submission_version_id": version, "total_score": 80}]})
        self.assertEqual(response.status_code, 201, response.get_json())
        with self.app.app_context():
            self.assertEqual(combined_score(db.session.get(SubmissionVersion, version))["total_score"], 83)

    def test_repeated_install_preserves_existing_edits(self):
        from install_competition_bundle import install
        with tempfile.TemporaryDirectory() as directory:
            write_bundle(Path(directory))
            credentials = {"email": "admin@uestcai.top", "password": "ChangeMe123!"}
            first = install(self.admin, Path(directory), credentials)
            self.login(self.admin, credentials["email"])
            self.assertEqual(self.admin.patch(f"/api/manage/competitions/{first['id']}", json={"name": "已审定名称"}).status_code, 200)
            second = install(self.admin, Path(directory), credentials)
            self.assertEqual(first["id"], second["id"])
            self.assertEqual([item["problem_id"] for item in first["tracks"]], [item["problem_id"] for item in second["tracks"]])
            self.login(self.admin, credentials["email"])
            self.assertEqual(self.admin.get("/api/competitions/wujie-cup-2026").get_json()["name"], "已审定名称")

    def test_track_rankings_are_independent_and_can_be_hidden(self):
        self.login(self.admin, "admin@uestcai.top")
        competition = self.admin.get("/api/manage/catalog").get_json()[0]
        self.admin.patch(f"/api/manage/competitions/{competition['id']}", json={"ends_at": "2000-01-01T00:00:00Z",
            "config": {"leaderboard": {"visible": True, "rank_scope": "track"}}})
        first, second = competition["tracks"][:2]
        for index, (track, score) in enumerate([(first, 90), (first, 80), (second, 70)]):
            problem = track["problems"][0]
            self.admin.patch(f"/api/manage/problems/{problem['id']}", json={"status": "published",
                "scoring_config": {"external_weight_percent": 100}})
            team = self.admin.post("/api/teams", json={"competition_id": competition["id"], "name": f"独立赛道{index}"}).get_json()
            self.admin.post("/api/registrations", json={"competition_id": competition["id"], "team_id": team["id"], "track_id": track["id"]})
            response = self.admin.post("/api/submissions", json={"problem_id": problem["id"], "team_id": team["id"],
                "title": f"作品{index}", "readme_md": "# 实验", "fields": {}, "status": "submitted"})
            self.assertEqual(response.status_code, 201, response.get_json())
            response = self.admin.post("/api/scores/import", json={"competition_id": competition["id"], "problem_id": problem["id"],
                "source": "test", "label": "独立排名", "records": [{"submission_version_id": response.get_json()["version"]["id"], "total_score": score}]})
            self.assertEqual(response.status_code, 201, response.get_json())
        rows = self.member.get(f"/api/leaderboards/{competition['slug']}").get_json()
        self.assertEqual([row["rank"] for row in rows], [1, 2, 1])
        self.assertEqual([row["rank"] for row in self.member.get(f"/api/leaderboards/{competition['slug']}?track={second['slug']}").get_json()], [1])
        self.admin.patch(f"/api/manage/competitions/{competition['id']}", json={"config": {"leaderboard": {"visible": False}}})
        self.assertEqual(self.member.get(f"/api/leaderboards/{competition['slug']}").get_json(), [])


if __name__ == "__main__":
    unittest.main()
