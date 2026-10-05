from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

from .extensions import db, migrate
from .routes.auth import auth_bp
from .routes.competitions import competitions_bp
from .routes.content import content_bp
from .routes.manage import manage_bp
from .routes.markdown_assets import markdown_assets_bp
from .routes.submissions import submissions_bp
from .routes.evaluations import evaluations_bp
from .routes.ai import ai_bp
from .routes.compute import compute_bp
from .routes.problem_setup import setup_bp


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL",
            "sqlite:///" + str(Path(__file__).resolve().parents[1] / "data" / "uestc_ai.sqlite3"),
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=str(Path(__file__).resolve().parents[1] / "uploads"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        MARKDOWN_ASSET_MAX_SIZE=8 * 1024 * 1024,
        SUBMISSION_ASSET_MAX_SIZE=20 * 1024 * 1024,
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
        SESSION_COOKIE_SAMESITE=os.environ.get("SESSION_COOKIE_SAMESITE", "Lax"),
        CORS_ORIGINS=os.environ.get("CORS_ORIGINS", ""),
        RESEND_API_KEY=os.environ.get("RESEND_API_KEY", ""),
        RESEND_FROM=os.environ.get("RESEND_FROM", "UESTC AI <noreply@example.com>"),
        EXPOSE_VERIFICATION_CODE=os.environ.get("EXPOSE_VERIFICATION_CODE", "0") == "1",
        VERIFICATION_CODE_TTL_SECONDS=600,
        VERIFICATION_CODE_RESEND_SECONDS=60,
        VERIFICATION_CODE_HOURLY_LIMIT=5,
        VERIFICATION_CODE_IP_HOURLY_LIMIT=20,
        VERIFICATION_CODE_MAX_ATTEMPTS=5,
        ENFORCE_COMPETITION_DEADLINES=os.environ.get("ENFORCE_COMPETITION_DEADLINES", "1") == "1",
        AUTO_CREATE_SCHEMA=os.environ.get("AUTO_CREATE_SCHEMA", "0") == "1",
        SEED_DATABASE=os.environ.get("SEED_DATABASE", "0") == "1",
        EVALUATION_WORKER_TOKEN=os.environ.get("EVALUATION_WORKER_TOKEN", ""),
        EVALUATION_ENABLED_ADAPTERS=os.environ.get("EVALUATION_ENABLED_ADAPTERS", ""),
        EVALUATION_API_URL=os.environ.get("EVALUATION_API_URL", ""),
        EVALUATION_API_KEY=os.environ.get("EVALUATION_API_KEY", ""),
        AI_GATEWAY_ENCRYPTION_KEY=os.environ.get("AI_GATEWAY_ENCRYPTION_KEY", ""),
        AI_GATEWAY_ALLOW_LOCAL_HTTP=os.environ.get("AI_GATEWAY_ALLOW_LOCAL_HTTP", "0") == "1",
        AI_GATEWAY_TIMEOUT_SECONDS=120,
        AI_GATEWAY_MAX_REQUEST_BYTES=4 * 1024 * 1024,
        AI_GATEWAY_MAX_RESPONSE_BYTES=16 * 1024 * 1024,
        COMPUTE_ALLOW_LOCAL_HTTP=os.environ.get("COMPUTE_ALLOW_LOCAL_HTTP", "0") == "1",
        INITIAL_ADMIN_EMAIL=os.environ.get("INITIAL_ADMIN_EMAIL", "admin@uestcai.top"),
        INITIAL_ADMIN_PASSWORD=os.environ.get("INITIAL_ADMIN_PASSWORD", ""),
        INITIAL_REVIEWER_EMAIL=os.environ.get("INITIAL_REVIEWER_EMAIL", "reviewer@uestc.ai"),
        INITIAL_REVIEWER_PASSWORD=os.environ.get("INITIAL_REVIEWER_PASSWORD", ""),
    )
    if test_config:
        app.config.update(test_config)

    if app.config["SEED_DATABASE"] and (
        not app.config["INITIAL_ADMIN_PASSWORD"]
        or not app.config["INITIAL_REVIEWER_PASSWORD"]
    ):
        raise RuntimeError(
            "INITIAL_ADMIN_PASSWORD and INITIAL_REVIEWER_PASSWORD are required "
            "when SEED_DATABASE=1"
        )

    (Path(__file__).resolve().parents[1] / "data").mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    configured_origins = app.config["CORS_ORIGINS"]
    allowed_origins = (
        [origin.strip() for origin in configured_origins.split(",") if origin.strip()]
        if configured_origins
        else [r"http://127\.0\.0\.1:\d+", r"http://localhost:\d+"]
    )
    CORS(
        app,
        resources={r"/api/*": {"origins": allowed_origins}},
        supports_credentials=True,
    )
    db.init_app(app)
    migrate.init_app(
        app,
        db,
        directory=str(Path(__file__).resolve().parents[1] / "migrations"),
    )
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(competitions_bp, url_prefix="/api")
    app.register_blueprint(submissions_bp, url_prefix="/api")
    app.register_blueprint(evaluations_bp, url_prefix="/api")
    app.register_blueprint(ai_bp, url_prefix="/api")
    app.register_blueprint(compute_bp, url_prefix="/api")
    app.register_blueprint(setup_bp, url_prefix="/api")
    app.register_blueprint(content_bp, url_prefix="/api")
    app.register_blueprint(manage_bp, url_prefix="/api")
    app.register_blueprint(markdown_assets_bp, url_prefix="/api")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "uestc-ai-platform", "mode": "broker"})

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"error": "resource not found"}), 404

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"error": "payload exceeds 25 MB"}), 413

    with app.app_context():
        if app.config["AUTO_CREATE_SCHEMA"]:
            db.create_all()
        if app.config["SEED_DATABASE"]:
            from .seed import seed_database
            seed_database()
    return app
