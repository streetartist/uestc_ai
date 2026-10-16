from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import secrets

from flask import Blueprint, current_app, jsonify, make_response, request
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import text

from ..extensions import db
from ..mailer import MailDeliveryError, send_verification_email
from ..models import EmailVerificationCode, RegistrationInvite, Session, User
from ..security import current_user, hash_token, require_user
from ..utils import audit, payload
from ..captcha import numeric_image


auth_bp = Blueprint("auth", __name__)
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalized_email(value) -> str:
    return str(value or "").strip().lower()


def is_campus_email(email: str) -> bool:
    return email.rpartition("@")[2] == "std.uestc.edu.cn"


def normalized_invite_code(value) -> str:
    return str(value or "").strip().upper().replace("-", "")


def active_invite(raw_code) -> RegistrationInvite | None:
    code = normalized_invite_code(raw_code)
    if not code:
        return None
    invite = RegistrationInvite.query.filter_by(code_hash=hash_token(code)).with_for_update().first()
    return invite if invite and invite.status() == "active" else None


def verification_hash(email: str, code: str, purpose: str = "register") -> str:
    return hash_token(f"{current_app.config['SECRET_KEY']}:{purpose}:{email}:{code}")


def request_ip_hash():
    return hash_token(f"{current_app.config['SECRET_KEY']}:{request.remote_addr or 'unknown'}")


@auth_bp.post("/captcha")
def captcha():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("purpose", "register"), str) or data.get("purpose", "register") not in {"register", "reset"}:
        return jsonify({"error": "invalid captcha request"}), 400
    email = normalized_email(data.get("email"))
    if not EMAIL_PATTERN.fullmatch(email) or len(email) > 255:
        return jsonify({"error": "invalid email address"}), 400
    now = datetime.now(timezone.utc)
    ip_hash = request_ip_hash()
    EmailVerificationCode.query.filter(EmailVerificationCode.purpose.like("captcha-%"), EmailVerificationCode.expires_at < now - timedelta(days=1)).delete(synchronize_session=False)
    # Bound challenge generation as well as actual outgoing messages.
    if EmailVerificationCode.query.filter(
        EmailVerificationCode.request_ip_hash == ip_hash,
        EmailVerificationCode.purpose.like("captcha-%"),
        EmailVerificationCode.created_at >= now - timedelta(hours=1),
    ).count() >= 120:
        return jsonify({"error": "captcha requested too frequently"}), 429
    purpose = "captcha-" + data.get("purpose", "register")
    code = f"{secrets.randbelow(1_000_000):06d}"
    record = EmailVerificationCode(email=email, purpose=purpose, code_hash=verification_hash(email, code, purpose),
                                   request_ip_hash=ip_hash, expires_at=now + timedelta(minutes=5))
    db.session.add(record)
    db.session.commit()
    result = {"id": record.id, "image": numeric_image(code), "expires_in": 300}
    if current_app.testing:
        result["debug_code"] = code
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


def consume_captcha(data, email, purpose):
    if not isinstance(data.get("captcha_id"), str) or not isinstance(data.get("captcha_code"), str):
        return jsonify({"error": "numeric captcha expired"}), 400
    record = db.session.get(EmailVerificationCode, data.get("captcha_id", ""), with_for_update=True)
    now = datetime.now(timezone.utc)
    if (not record or record.purpose != "captcha-" + purpose or record.email != email
            or record.request_ip_hash != request_ip_hash() or record.consumed_at
            or aware(record.expires_at) <= now):
        return jsonify({"error": "numeric captcha expired"}), 400
    supplied = data.get("captcha_code")
    if not isinstance(supplied, str) or not secrets.compare_digest(record.code_hash, verification_hash(email, supplied, record.purpose)):
        record.attempts += 1
        if record.attempts >= 5:
            record.consumed_at = now
        db.session.commit()
        return jsonify({"error": "numeric captcha incorrect"}), 400
    record.consumed_at = now
    # Consumption survives a downstream mail error and cannot be replayed.
    db.session.commit()
    return None


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def issue_session(user: User):
    raw = secrets.token_urlsafe(36)
    session = Session(token_hash=hash_token(raw), user=user, expires_at=datetime.now(timezone.utc) + timedelta(days=14))
    db.session.add(session)
    audit("auth.login", "user", user.id, actor_id=user.id)
    db.session.commit()
    response = make_response(jsonify({"user": user.to_dict(), "token": raw}))
    response.set_cookie(
        "session_token",
        raw,
        httponly=True,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite=current_app.config["SESSION_COOKIE_SAMESITE"],
        max_age=14 * 86400,
    )
    return response


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or any(not isinstance(data.get(key), str) for key in ("email", "name", "password", "confirm_password", "verification_code")):
        return jsonify({"error": "registration fields must be strings"}), 400
    email = normalized_email(data["email"])
    if not EMAIL_PATTERN.fullmatch(email):
        return jsonify({"error": "invalid email address"}), 400
    if not data["password"].strip() or not 8 <= len(data["password"]) <= 128:
        return jsonify({"error": "new password must contain 8 to 128 characters"}), 400
    if data["password"] != data["confirm_password"]:
        return jsonify({"error": "password confirmation does not match"}), 400
    if not data["name"].strip() or len(data["name"].strip()) > 80 or len(email) > 255:
        return jsonify({"error": "invalid registration name"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "email already registered"}), 409
    invite = None
    if not is_campus_email(email):
        invite = active_invite(data.get("invite_code"))
        if not invite:
            return jsonify({"error": "valid registration invite required"}), 403

    verification = (
        EmailVerificationCode.query
        .filter_by(email=email, purpose="register", consumed_at=None)
        .order_by(EmailVerificationCode.created_at.desc())
        .with_for_update()
        .first()
    )
    now = datetime.now(timezone.utc)
    if not verification or aware(verification.expires_at) <= now:
        return jsonify({"error": "verification code expired"}), 400
    code = str(data["verification_code"]).strip()
    if not secrets.compare_digest(verification.code_hash, verification_hash(email, code)):
        verification.attempts += 1
        if verification.attempts >= current_app.config["VERIFICATION_CODE_MAX_ATTEMPTS"]:
            verification.consumed_at = now
        db.session.commit()
        return jsonify({"error": "invalid verification code"}), 400

    user = User(email=email, name=data["name"].strip(), password_hash=generate_password_hash(data["password"]), role="member", email_verified_at=now)
    db.session.add(user)
    db.session.flush()
    verification.consumed_at = now
    if invite:
        invite.used_at = now
        invite.used_by = user.id
    audit("user.registered", "user", user.id, actor_id=user.id)
    db.session.commit()
    return issue_session(user), 201


@auth_bp.post("/verification-codes")
def request_verification_code():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("email"), str):
        return jsonify({"error": "invalid email address"}), 400
    email = normalized_email(data["email"])
    purpose = data.get("purpose", "register")
    if not isinstance(purpose, str) or purpose not in {"register", "reset"}:
        return jsonify({"error": "invalid verification purpose"}), 400
    if not EMAIL_PATTERN.fullmatch(email) or len(email) > 255:
        return jsonify({"error": "invalid email address"}), 400
    registered_user = User.query.filter_by(email=email).first()
    if purpose == "register" and registered_user:
        return jsonify({"error": "email already registered"}), 409
    if purpose == "register" and not is_campus_email(email) and not active_invite(data.get("invite_code")):
        return jsonify({"error": "valid registration invite required"}), 403

    captcha_error = consume_captcha(data, email, purpose)
    if captcha_error:
        return captcha_error

    now = datetime.now(timezone.utc)
    if db.engine.dialect.name == "postgresql":
        # Serialize delivery quotas for this address and client across workers.
        keys = sorted({int(hash_token("mail:" + email)[:15], 16), int(request_ip_hash()[:15], 16)})
        for key in keys:
            db.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    resend_seconds = current_app.config["VERIFICATION_CODE_RESEND_SECONDS"]
    latest = EmailVerificationCode.query.filter_by(email=email, purpose=purpose).order_by(EmailVerificationCode.created_at.desc()).first()
    if latest and aware(latest.created_at) > now - timedelta(seconds=resend_seconds):
        retry_after = max(1, int(resend_seconds - (now - aware(latest.created_at)).total_seconds()))
        return jsonify({"error": "verification code requested too frequently", "retry_after": retry_after}), 429

    hour_ago = now - timedelta(hours=1)
    email_count = EmailVerificationCode.query.filter(
        EmailVerificationCode.email == email,
        EmailVerificationCode.purpose.in_(("register", "reset")),
        EmailVerificationCode.created_at >= hour_ago,
    ).count()
    if email_count >= current_app.config["VERIFICATION_CODE_HOURLY_LIMIT"]:
        return jsonify({"error": "verification code hourly limit reached"}), 429
    ip_hash = request_ip_hash()
    ip_count = EmailVerificationCode.query.filter(
        EmailVerificationCode.request_ip_hash == ip_hash,
        EmailVerificationCode.purpose.in_(("register", "reset")),
        EmailVerificationCode.created_at >= hour_ago,
    ).count()
    if ip_count >= current_app.config["VERIFICATION_CODE_IP_HOURLY_LIMIT"]:
        return jsonify({"error": "verification code hourly limit reached"}), 429

    EmailVerificationCode.query.filter_by(email=email, purpose=purpose, consumed_at=None).update(
        {EmailVerificationCode.consumed_at: now},
        synchronize_session=False,
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    record = EmailVerificationCode(
        email=email,
        code_hash=verification_hash(email, code, purpose),
        purpose=purpose,
        request_ip_hash=ip_hash,
        expires_at=now + timedelta(seconds=current_app.config["VERIFICATION_CODE_TTL_SECONDS"]),
    )
    db.session.add(record)
    db.session.flush()
    try:
        if purpose == "register" or registered_user:
            send_verification_email(email, code, purpose=purpose)
    except MailDeliveryError as delivery_error:
        db.session.rollback()
        return jsonify({"error": str(delivery_error)}), 503
    audit("auth.verification_code_requested", "email_verification", record.id, {"email_domain": email.rpartition("@")[2]})
    db.session.commit()
    response = {
        "status": "sent",
        "expires_in": current_app.config["VERIFICATION_CODE_TTL_SECONDS"],
        "retry_after": resend_seconds,
    }
    if current_app.testing:
        response["debug_code"] = code
    return jsonify(response), 202


@auth_bp.post("/reset-password")
def reset_password():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or any(not isinstance(data.get(key), str) for key in ("email", "verification_code", "new_password", "confirm_password")):
        return jsonify({"error": "password fields must be strings"}), 400
    if not data["new_password"].strip() or not 8 <= len(data["new_password"]) <= 128:
        return jsonify({"error": "new password must contain 8 to 128 characters"}), 400
    if data["new_password"] != data["confirm_password"]:
        return jsonify({"error": "password confirmation does not match"}), 400
    email = normalized_email(data["email"])
    user = User.query.filter_by(email=email).with_for_update().first()
    verification = EmailVerificationCode.query.filter_by(email=email, purpose="reset", consumed_at=None).order_by(EmailVerificationCode.created_at.desc()).with_for_update().first()
    now = datetime.now(timezone.utc)
    if not verification or aware(verification.expires_at) <= now:
        return jsonify({"error": "verification code expired"}), 400
    if not secrets.compare_digest(verification.code_hash, verification_hash(email, data["verification_code"].strip(), "reset")):
        verification.attempts += 1
        if verification.attempts >= current_app.config["VERIFICATION_CODE_MAX_ATTEMPTS"]:
            verification.consumed_at = now
        db.session.commit()
        return jsonify({"error": "invalid verification code"}), 400
    if not user:
        return jsonify({"error": "invalid verification code"}), 400
    user.password_hash = generate_password_hash(data["new_password"])
    verification.consumed_at = now
    # Also expire pre-reset registration/recovery codes for this address.
    EmailVerificationCode.query.filter_by(email=email, consumed_at=None).update({EmailVerificationCode.consumed_at: now}, synchronize_session=False)
    Session.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    audit("auth.password_reset", "user", user.id, actor_id=user.id)
    db.session.commit()
    response = jsonify({"status": "password_reset"})
    response.delete_cookie("session_token", httponly=True, secure=current_app.config["SESSION_COOKIE_SECURE"], samesite=current_app.config["SESSION_COOKIE_SAMESITE"])
    return response


@auth_bp.post("/login")
def login():
    data, error = payload(("email", "password"))
    if error:
        return error
    # Serialize credential checks with password changes so an old-password
    # login cannot create a new session after existing sessions are revoked.
    user = User.query.filter_by(email=data["email"].strip().lower()).with_for_update().first()
    if not user or not check_password_hash(user.password_hash, data["password"]):
        return jsonify({"error": "invalid email or password"}), 401
    return issue_session(user)


@auth_bp.get("/me")
@require_user()
def me():
    return jsonify({"user": current_user().to_dict()})


@auth_bp.post("/password")
@require_user()
def change_password():
    data = request.get_json(silent=True)
    fields = ("current_password", "new_password", "confirm_password")
    if not isinstance(data, dict) or any(not isinstance(data.get(key), str) for key in fields):
        return jsonify({"error": "password fields must be strings"}), 400
    if not data["new_password"].strip() or not 8 <= len(data["new_password"]) <= 128:
        return jsonify({"error": "new password must contain 8 to 128 characters"}), 400
    if data["new_password"] != data["confirm_password"]:
        return jsonify({"error": "password confirmation does not match"}), 400

    user = db.session.get(User, current_user().id, populate_existing=True, with_for_update=True)
    if not check_password_hash(user.password_hash, data["current_password"]):
        return jsonify({"error": "current password is incorrect"}), 403
    if data["current_password"] == data["new_password"]:
        return jsonify({"error": "new password must differ from current password"}), 400

    user.password_hash = generate_password_hash(data["new_password"])
    Session.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    audit("auth.password_changed", "user", user.id, actor_id=user.id)
    db.session.commit()
    response = make_response(jsonify({"status": "password_changed"}))
    response.delete_cookie(
        "session_token", httponly=True,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite=current_app.config["SESSION_COOKIE_SAMESITE"],
    )
    return response


@auth_bp.post("/logout")
@require_user()
def logout():
    raw = request.cookies.get("session_token")
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        raw = header[7:]
    if raw:
        session = db.session.get(Session, hash_token(raw))
        if session:
            db.session.delete(session)
            db.session.commit()
    response = make_response(jsonify({"status": "signed_out"}))
    response.delete_cookie("session_token")
    return response
