from __future__ import annotations

from datetime import datetime, timezone
import re

from flask import Blueprint, jsonify, request

from ..extensions import db
from ..models import Content, MarkdownAsset, new_id
from ..security import current_user, require_user
from ..utils import audit, payload


content_bp = Blueprint("content", __name__)


@content_bp.get("/content")
def list_content():
    user = current_user()
    include_all = request.args.get("scope") == "all" and user and user.role in {"admin", "organizer", "editor"}
    query = Content.query if include_all else Content.query.filter_by(status="published")
    if request.args.get("kind"):
        query = query.filter_by(kind=request.args["kind"])
    items = query.order_by(Content.published_at.desc()).all()
    return jsonify([item.to_dict(include_body=include_all) for item in items])


@content_bp.get("/content/<slug>")
def content_detail(slug: str):
    item = Content.query.filter_by(slug=slug).first_or_404()
    user = current_user()
    if item.status != "published" and (not user or (user.id != item.author_id and user.role not in {"admin", "organizer", "editor"})):
        return jsonify({"error": "content not found"}), 404
    return jsonify(item.to_dict(include_body=True))


@content_bp.post("/content")
@require_user("admin", "organizer", "editor")
def create_content():
    data, error = payload(("kind", "slug", "title", "body_md"))
    if error:
        return error
    if Content.query.filter_by(slug=data["slug"]).first():
        return jsonify({"error": "content slug already exists"}), 409
    status = data.get("status", "draft")
    item = Content(kind=data["kind"], slug=data["slug"], title=data["title"], excerpt=data.get("excerpt", ""), body_md=data["body_md"], status=status, author_id=current_user().id, published_at=datetime.now(timezone.utc) if status == "published" else None)
    db.session.add(item)
    db.session.flush()
    audit("content.created", "content", item.id)
    db.session.commit()
    return jsonify(item.to_dict(include_body=True)), 201


@content_bp.patch("/content/<content_id>")
@require_user("admin", "organizer", "editor")
def update_content(content_id: str):
    item = db.session.get(Content, content_id, with_for_update=True)
    if not item:
        return jsonify({"error": "content not found"}), 404
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or any(not isinstance(value, str) for key, value in data.items() if key in {"kind", "slug", "title", "excerpt", "body_md", "status", "review_note"}):
        return jsonify({"error": "invalid contribution fields"}), 400
    if "status" in data and data["status"] not in {"draft", "pending", "published", "rejected"}:
        return jsonify({"error": "invalid contribution status"}), 400
    if "slug" in data:
        duplicate = Content.query.filter(Content.slug == data["slug"], Content.id != item.id).first()
        if duplicate:
            return jsonify({"error": "content slug already exists"}), 409
    for field in ("kind", "slug", "title", "excerpt", "body_md", "status", "review_note"):
        if field in data:
            setattr(item, field, data[field])
    if item.status == "published" and not item.published_at:
        item.published_at = datetime.now(timezone.utc)
    audit("content.updated", "content", item.id)
    db.session.commit()
    return jsonify(item.to_dict(include_body=True))


@content_bp.delete("/content/<content_id>")
@require_user("admin", "organizer", "editor")
def delete_content(content_id: str):
    item = db.session.get(Content, content_id)
    if not item:
        return jsonify({"error": "content not found"}), 404
    audit("content.deleted", "content", item.id, {"title": item.title, "slug": item.slug})
    db.session.delete(item)
    db.session.commit()
    return "", 204


@content_bp.get("/me/contributions")
@require_user()
def my_contributions():
    return jsonify([item.to_dict(include_body=True) for item in Content.query.filter_by(author_id=current_user().id).filter(Content.kind.in_(("blog", "work"))).order_by(Content.updated_at.desc()).all()])


def contribution_data():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or any(not isinstance(data.get(key, ""), str) for key in ("kind", "title", "excerpt", "body_md", "status")):
        return None, (jsonify({"error": "invalid contribution fields"}), 400)
    if data.get("kind") not in {"blog", "work"} or data.get("status", "draft") not in {"draft", "pending"}:
        return None, (jsonify({"error": "invalid contribution status"}), 400)
    if not data.get("title", "").strip() or len(data["title"].strip()) > 240 or not data.get("body_md", "").strip() or len(data["body_md"]) > 200_000 or len(data.get("excerpt", "")) > 2000:
        return None, (jsonify({"error": "invalid contribution fields"}), 400)
    for asset_id in set(re.findall(r"/api/markdown-assets/([0-9a-f-]{36})", data["body_md"])):
        asset = db.session.get(MarkdownAsset, asset_id)
        if not asset or (asset.visibility == "private" and asset.uploaded_by != current_user().id):
            return None, (jsonify({"error": "contribution attachment is unavailable"}), 403)
    return data, None


@content_bp.post("/me/contributions")
@require_user()
def create_contribution():
    data, error = contribution_data()
    if error:
        return error
    item = Content(kind=data["kind"], slug="community-" + new_id(), title=data["title"].strip(), excerpt=data.get("excerpt", ""), body_md=data["body_md"], status=data.get("status", "draft"), author_id=current_user().id)
    db.session.add(item)
    db.session.flush()
    audit("contribution.created", "content", item.id)
    db.session.commit()
    return jsonify(item.to_dict(include_body=True)), 201


@content_bp.patch("/me/contributions/<content_id>")
@require_user()
def update_contribution(content_id):
    item = db.session.get(Content, content_id, with_for_update=True)
    if not item or item.author_id != current_user().id or item.kind not in {"blog", "work"}:
        return jsonify({"error": "content not found"}), 404
    data, error = contribution_data()
    if error:
        return error
    # Any author revision goes through review again, including previously published work.
    for field in ("kind", "title", "excerpt", "body_md"):
        setattr(item, field, data.get(field, ""))
    item.status = data.get("status", "draft")
    item.published_at = None
    item.review_note = ""
    audit("contribution.updated", "content", item.id)
    db.session.commit()
    return jsonify(item.to_dict(include_body=True))
