from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

from .csrf import init_origin_guard
from .extensions import db, migrate
from .routes.auth import auth_bp
from .routes.competitions import competitions_bp
from .routes.content import content_bp
from .routes.manage import manage_bp
from .routes.markdown_assets import markdown_assets_bp
from .routes.submissions import submissions_bp
from .routes.evaluations import evaluations_bp
from .routes.evaluation_trials import evaluation_trials_bp
from .routes.ai import ai_bp
from .routes.compute import compute_bp
from .routes.problem_setup import setup_bp
from .routes.profiles import profiles_bp
from .routes.checkpoints import checkpoints_bp
from .routes.evaluation_evidence import evidence_bp


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

WEAK_SECRET_KEYS = {
    "dev-change-me",
    "dev-only-change-me-before-production",
    "change-me",
    "changeme",
}
MIN_SECRET_KEY_LENGTH = 16


def _require_strong_secret_key(app: Flask) -> None:
    """Refuse to start a non-development app with a missing or placeholder SECRET_KEY."""
    if (
        app.config.get("TESTING")
        or app.debug
        or app.config.get("AUTO_CREATE_SCHEMA")
        or app.config.get("SEED_DATABASE")
    ):
        return
    secret = str(app.config.get("SECRET_KEY") or "").strip()
    if not secret or secret.lower() in WEAK_SECRET_KEYS or len(secret) < MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(
            "SECRET_KEY must be set to a strong random value of at least "
            f"{MIN_SECRET_KEY_LENGTH} characters (not a placeholder) unless running locally "
            "with AUTO_CREATE_SCHEMA=1 or SEED_DATABASE=1. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL",
            "sqlite:///" + str(Path(__file__).resolve().parents[1] / "data" / "uestc_ai.sqlite3"),
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER") or str(Path(__file__).resolve().parents[1] / "uploads"),
        REDIS_URL=os.environ.get("REDIS_URL", ""),
        REDIS_CACHE_PREFIX=os.environ.get("REDIS_CACHE_PREFIX", "uestc-ai:public:v1"),
        REDIS_CACHE_TTL=int(os.environ.get("REDIS_CACHE_TTL", "30")),
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
        EVALUATION_API_BASE=os.environ.get("EVALUATION_API_BASE", ""),
        EVALUATION_ENABLED_ADAPTERS=os.environ.get("EVALUATION_ENABLED_ADAPTERS", ""),
        EVALUATION_API_URL=os.environ.get("EVALUATION_API_URL", ""),
        EVALUATION_API_KEY=os.environ.get("EVALUATION_API_KEY", ""),
        EVALUATION_API_ORIGIN_IP=os.environ.get("EVALUATION_API_ORIGIN_IP", ""),
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
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql"):
        options = app.config["SQLALCHEMY_ENGINE_OPTIONS"]
        for name, value in {"pool_size": 4, "max_overflow": 4, "pool_timeout": 10, "pool_recycle": 300}.items():
            options.setdefault(name, value)
    if os.environ.get("TRUST_PROXY", "0") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    _require_strong_secret_key(app)

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
    init_origin_guard(app, allowed_origins)
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
    app.register_blueprint(evaluation_trials_bp, url_prefix="/api")
    app.register_blueprint(ai_bp, url_prefix="/api")
    app.register_blueprint(compute_bp, url_prefix="/api")
    app.register_blueprint(setup_bp, url_prefix="/api")
    app.register_blueprint(content_bp, url_prefix="/api")
    app.register_blueprint(profiles_bp, url_prefix="/api")
    app.register_blueprint(checkpoints_bp, url_prefix="/api")
    app.register_blueprint(evidence_bp, url_prefix="/api")
    app.register_blueprint(manage_bp, url_prefix="/api")
    app.register_blueprint(markdown_assets_bp, url_prefix="/api")
    from .public_cache import init_public_cache
    init_public_cache(app)

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
        from . import judge_models  # Register organizer pool metadata before schema creation.
        if app.config["AUTO_CREATE_SCHEMA"]:
            db.create_all()
        if app.config["SEED_DATABASE"]:
            from .seed import seed_database
            seed_database()
    return app
