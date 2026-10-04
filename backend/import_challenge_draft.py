"""Import a private JSON challenge draft without touching existing catalog records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from platform_api import create_app
from platform_api.evaluation import validate_evaluation_config
from platform_api.extensions import db
from platform_api.models import Competition, Problem, Track
from platform_api.reviewing import validate_scoring_config
from platform_api.submission_schema import validate_submission_schema


DEFAULT_DRAFT = Path(__file__).resolve().parent / "data" / "challenge_drafts.json"


def import_draft(document: dict) -> Competition:
    details = document["competition"]
    tracks = document["tracks"]
    if not isinstance(tracks, list) or not tracks:
        raise ValueError("draft needs at least one track")
    if Competition.query.filter_by(slug=details["slug"]).first():
        raise ValueError("draft competition already exists; edit it in the management UI")
    competition = Competition(
        slug=details["slug"], name=details["name"], summary=details["summary"],
        status="draft", config=details.get("config", {}),
    )
    db.session.add(competition)
    seen_tracks = set()
    for position, spec in enumerate(tracks, 1):
        if spec["slug"] in seen_tracks:
            raise ValueError("duplicate track slug")
        seen_tracks.add(spec["slug"])
        track = Track(
            competition=competition, slug=spec["slug"], name=spec["name"],
            description=spec["description"], position=position, config=spec.get("config", {}),
        )
        db.session.add(track)
        for problem in spec["problems"]:
            db.session.add(Problem(
                track=track, code=problem["code"], slug=problem["slug"],
                title=problem["title"], summary=problem["summary"],
                statement_md="\n".join(problem["statement_lines"]), status="draft",
                submission_schema=validate_submission_schema(problem["submission_schema"]),
                judging_schema=problem["judging_schema"],
                scoring_config=validate_scoring_config(problem.get("scoring_config")),
                evaluation_config=validate_evaluation_config(problem.get("evaluation_config")),
                difficulty=problem.get("difficulty", 3),
                compute_note=problem.get("compute_note", ""),
                source_url=problem.get("source_url"),
            ))
    db.session.commit()
    return competition


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a private, unpublished challenge draft")
    parser.add_argument("--file", type=Path, default=DEFAULT_DRAFT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    document = json.loads(args.file.read_text(encoding="utf-8"))
    app = create_app({"SEED_DATABASE": False})
    with app.app_context():
        try:
            if args.apply:
                competition = import_draft(document)
                print(f"Created private draft: {competition.slug} ({len(competition.tracks)} tracks)")
            else:
                print(f"Ready to import {len(document['tracks'])} draft tracks. Pass --apply to write them.")
        except Exception:
            db.session.rollback()
            raise


if __name__ == "__main__":
    main()
