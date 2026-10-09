from __future__ import annotations

import re
from urllib.parse import urlsplit

from flask import Flask, jsonify, request

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _origin_allowed(origin: str, allowed_origins: list[str]) -> bool:
    """Match like flask-cors: exact string or regex full match."""
    for allowed in allowed_origins:
        if origin == allowed:
            return True
        try:
            if re.fullmatch(allowed, origin):
                return True
        except re.error:
            continue
    return False


def _same_hostname(origin: str) -> bool:
    origin_host = urlsplit(origin).hostname
    request_host = urlsplit("//" + request.host).hostname
    return bool(origin_host) and origin_host == request_host


def init_origin_guard(app: Flask, allowed_origins: list[str]) -> None:
    """Reject cross-origin unsafe requests that rely solely on the session cookie (CSRF)."""

    @app.before_request
    def reject_cross_origin_cookie_writes():
        if request.method not in UNSAFE_METHODS or not request.path.startswith("/api/"):
            return None
        if not request.cookies.get("session_token"):
            return None
        if request.headers.get("Authorization") or request.headers.get("x-api-key"):
            return None
        origin = request.headers.get("Origin")
        if origin is None:
            return None
        if origin != "null" and (_origin_allowed(origin, allowed_origins) or _same_hostname(origin)):
            return None
        return jsonify({"error": "cross-origin request rejected"}), 403
