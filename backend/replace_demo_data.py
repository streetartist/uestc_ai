from __future__ import annotations

import argparse
import os
import secrets

from platform_api import create_app
from platform_api.extensions import db
from platform_api.seed import replace_demo_data, validate_demo_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Replace an untouched demo catalog with the current fictional sample")
    parser.add_argument("--competition-slug", required=True)
    parser.add_argument("--content-slug", action="append", required=True)
    parser.add_argument("--purge-catalog-history", action="store_true", help="Remove audit rows for the old catalog")
    parser.add_argument("--apply", action="store_true", help="Perform the replacement after validation")
    args = parser.parse_args()

    app = create_app({
        "SEED_DATABASE": False,
        "INITIAL_ADMIN_EMAIL": os.environ.get("INITIAL_ADMIN_EMAIL", "admin@uestcai.top"),
        "INITIAL_REVIEWER_EMAIL": os.environ.get("INITIAL_REVIEWER_EMAIL", "reviewer@uestc.ai"),
    })
    with app.app_context():
        try:
            if args.apply:
                app.config["INITIAL_ADMIN_PASSWORD"] = secrets.token_urlsafe(32)
                app.config["INITIAL_REVIEWER_PASSWORD"] = secrets.token_urlsafe(32)
                replace_demo_data(args.competition_slug, set(args.content_slug), args.purge_catalog_history)
                print("Demo data replaced; existing accounts and passwords were preserved.")
            else:
                competition, contents, history = validate_demo_data(
                    args.competition_slug, set(args.content_slug), args.purge_catalog_history,
                )
                print(f"Ready to replace 1 competition, {len(competition.tracks)} tracks, 10 problems, {len(contents)} content items, and {len(history)} catalog audit rows.")
        except Exception:
            db.session.rollback()
            raise


if __name__ == "__main__":
    main()
