"""Create missing draft competition records through authenticated API validation.

Existing records are preserved. Requires the application's database environment
and a valid administrator credential file or INITIAL_ADMIN_* environment values.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path

from platform_api import create_app


def install(client, directory: Path, credentials: dict):
    def checked(response, codes=(200,)):
        if response.status_code not in codes:
            raise RuntimeError(f"API rejected operation ({response.status_code}): {response.get_json()}")
        return response.get_json()

    login = checked(client.post("/api/auth/login", json={"email": credentials["email"], "password": credentials["password"]}))
    if login["user"]["role"] != "admin":
        raise RuntimeError("An administrator account is required")
    previous_authorization = client.environ_base.get("HTTP_AUTHORIZATION")
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + login["token"]
    try:
        catalogue = json.loads((directory / "competition.json").read_text(encoding="utf-8"))
        document = catalogue["competition"]
        if document["status"] != "draft" or any(track["problem"]["status"] != "draft" for track in catalogue["tracks"]):
            raise ValueError("This installer only creates drafts")
        saved = checked(client.get("/api/manage/catalog"))
        competition = next((item for item in saved if item["slug"] == document["slug"]), None)
        if competition is None:
            competition = checked(client.post("/api/competitions", json=document), (201,))
            competition["tracks"] = []
        result = {"id": competition["id"], "slug": competition["slug"], "status": competition["status"], "tracks": []}
        for item in catalogue["tracks"]:
            track = next((track for track in competition["tracks"] if track["slug"] == item["slug"]), None)
            if track is None:
                track = checked(client.post(f"/api/competitions/{competition['slug']}/tracks", json={
                    key: item[key] for key in ("name", "slug", "description", "config", "position")}), (201,))
                track["problems"] = []
            problem = next((problem for problem in track["problems"] if problem["slug"] == item["problem"]["slug"]), None)
            if problem is None:
                package = directory / item["package"]
                if package.resolve().parent != directory.resolve():
                    raise ValueError("Package path must remain in the bundle directory")
                problem = checked(client.post(f"/api/manage/tracks/{track['id']}/problem-packages", data={
                    "file": (io.BytesIO(package.read_bytes()), package.name)}), (201,))
            setup = checked(client.get(f"/api/manage/problems/{problem['id']}/setup"))
            result["tracks"].append({"id": track["id"], "name": track["name"], "problem_id": problem["id"],
                "problem_title": problem["title"], "status": problem["status"], "ready": setup["ready"], "checks": setup["checks"]})
        return result
    finally:
        try:
            checked(client.post("/api/auth/logout"))
        finally:
            if previous_authorization is None:
                client.environ_base.pop("HTTP_AUTHORIZATION", None)
            else:
                client.environ_base["HTTP_AUTHORIZATION"] = previous_authorization


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    app = create_app()
    credentials = json.loads(args.credentials.read_text(encoding="utf-8")) if args.credentials else {
        "email": os.environ.get("INITIAL_ADMIN_EMAIL", "admin@uestcai.top"),
        "password": os.environ.get("INITIAL_ADMIN_PASSWORD", "")}
    result = install(app.test_client(), args.bundle, credentials)
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
