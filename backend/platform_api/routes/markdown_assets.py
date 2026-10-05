from __future__ import annotations

import mimetypes
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from ..extensions import db
from ..models import Content, MarkdownAsset, new_id
from ..security import current_user, require_user
from ..uploading import file_extension, original_filename, save_with_limit
from ..utils import audit


markdown_assets_bp = Blueprint("markdown_assets", __name__)

IMAGE_EXTENSIONS = {"gif", "jpeg", "jpg", "png", "webp"}
DOCUMENT_EXTENSIONS = {"csv", "docx", "json", "md", "pdf", "pptx", "txt", "xlsx", "zip"}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS
CONTENT_TYPES = {
    "csv": "text/csv",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "gif": "image/gif",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "json": "application/json",
    "md": "text/markdown",
    "pdf": "application/pdf",
    "png": "image/png",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "webp": "image/webp",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}


@markdown_assets_bp.post("/markdown-assets")
@require_user()
def upload_markdown_asset():
    user = current_user()
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "file is required"}), 400

    display_name = original_filename(file.filename)
    extension = file_extension(display_name)
    if not display_name or extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "markdown asset type is not allowed"}), 400

    asset_id = new_id()
    storage_name = f"markdown-{asset_id}.{extension}"
    target = Path(current_app.config["UPLOAD_FOLDER"]) / storage_name
    size_limit = int(current_app.config["MARKDOWN_ASSET_MAX_SIZE"])
    try:
        size = save_with_limit(file.stream, target, size_limit)
    except ValueError:
        return jsonify({"error": "markdown asset exceeds size limit", "limit": size_limit}), 413

    content_type = CONTENT_TYPES.get(extension) or mimetypes.guess_type(display_name)[0] or "application/octet-stream"
    asset = MarkdownAsset(
        id=asset_id,
        uploaded_by=user.id,
        original_name=display_name,
        storage_name=storage_name,
        content_type=content_type,
        size=size,
        kind="image" if extension in IMAGE_EXTENSIONS else "file",
        visibility="private" if request.form.get("scope") == "contribution" else "public",
    )
    db.session.add(asset)
    db.session.flush()
    audit("markdown_asset.uploaded", "markdown_asset", asset.id, {"name": display_name, "size": size})
    db.session.commit()
    return jsonify(asset.to_dict()), 201


@markdown_assets_bp.get("/markdown-assets/<asset_id>")
def serve_markdown_asset(asset_id: str):
    asset = db.session.get(MarkdownAsset, asset_id)
    if not asset:
        return jsonify({"error": "markdown asset not found"}), 404
    if asset.visibility == "private":
        user = current_user()
        allowed = user and (user.id == asset.uploaded_by or user.role in {"admin", "organizer", "editor"})
        if not allowed:
            # Exact asset UUIDs may only be disclosed by a published contribution.
            allowed = Content.query.filter(Content.status == "published", Content.body_md.contains(f"/api/markdown-assets/{asset.id}")).first() is not None
        if not allowed:
            return jsonify({"error": "markdown asset not found"}), 404
    response = send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        asset.storage_name,
        as_attachment=asset.kind != "image",
        download_name=asset.original_name,
        mimetype=asset.content_type,
        max_age=86400,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    if asset.visibility == "private":
        response.headers["Cache-Control"] = "private, no-store"
    return response
