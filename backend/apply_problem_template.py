"""Preview or apply a template to one existing, unused draft problem."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from platform_api import create_app
from platform_api.extensions import db
from platform_api.models import Problem, Submission
from platform_api.problem_templates import templates
from platform_api.utils import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", required=True, help="Existing problem slug")
    parser.add_argument("--template", required=True, help="Template identifier")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    app = create_app({"SEED_DATABASE": False, "AUTO_CREATE_SCHEMA": False})
    with app.app_context():
        matches = Problem.query.filter_by(slug=args.problem).all()
        if len(matches) != 1:
            raise ValueError("problem slug must resolve to exactly one draft")
        problem = matches[0]
        if problem.status != "draft" or Submission.query.filter_by(problem_id=problem.id).first():
            raise ValueError("only unused draft problems can receive a template")
        template = next((item for item in templates() if item["id"] == args.template), None)
        if not template:
            raise ValueError("unknown problem template")
        print(f"Draft {problem.slug}: apply {template['name']}; preserve ID, track, slug and code.")
        if not args.apply:
            print("Preview only. Pass --apply to write this draft.")
            return
        url = db.engine.url
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            raise ValueError("this local helper requires SQLite; use the management API for other databases")
        source = Path(url.database).resolve()
        backup = source.with_name(source.stem + ".before-template-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + source.suffix)
        with sqlite3.connect(source) as connection, sqlite3.connect(backup) as destination:
            connection.backup(destination)
        for key, value in template["problem"].items():
            if key not in {"slug", "code", "status"}:
                setattr(problem, key, value)
        audit("problem.template_applied", "problem", problem.id, {"template": template["id"]})
        db.session.commit()
        print(f"Updated private draft. SQLite backup: {backup}")


if __name__ == "__main__":
    main()
