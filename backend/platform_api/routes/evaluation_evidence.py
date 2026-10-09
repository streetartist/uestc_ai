import json
import re
import struct
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from ..extensions import db
from ..models import new_id
from ..progress_models import EvaluationArtifact
from ..evaluation_trials import can_view_result
from ..security import require_user
from ..uploading import save_with_limit
from .evaluations import leased_run

evidence_bp = Blueprint("evaluation_evidence", __name__)
MAX_FILE = 25 * 1024 * 1024


@evidence_bp.post("/evaluation-worker/runs/<run_id>/evidence")
def upload_evidence(run_id):
    run = leased_run(run_id)
    if not run:
        return jsonify({"error": "evaluation lease invalid or expired"}), 403
    file = request.files.get("file")
    name = request.form.get("name", "")
    if not file or not re.fullmatch(r"scene-[1-9][0-9]?-(?:replay\.gif|trajectory\.json|classification\.json)", name):
        return jsonify({"error": "invalid evidence name"}), 400
    if int(name.split('-')[1]) > run.config_snapshot["resources"]["episodes"]:
        return jsonify({"error": "invalid evidence scene"}), 400
    existing = EvaluationArtifact.query.filter_by(run_id=run.id, name=name).first()
    if not existing and EvaluationArtifact.query.filter_by(run_id=run.id).count() >= 90:
        return jsonify({"error": "evidence limit reached"}), 413
    storage = new_id() + Path(name).suffix
    target = Path(current_app.config["UPLOAD_FOLDER"]) / storage
    try:
        size = save_with_limit(file.stream, target, MAX_FILE)
        if name.endswith('.gif'):
            with target.open('rb') as image:
                header = image.read(10)
            if len(header) != 10 or header[:6] not in {b'GIF87a', b'GIF89a'}:
                raise ValueError("invalid replay image")
            width, height = struct.unpack('<HH', header[6:10])
            if not 1 <= width <= 1024 or not 1 <= height <= 1024:
                raise ValueError("invalid replay dimensions")
        else:
            content = json.loads(target.read_text(encoding='utf-8'))
            if not isinstance(content, (dict, list)):
                raise ValueError("invalid evidence JSON")
    except (ValueError, OSError) as error:
        target.unlink(missing_ok=True)
        return jsonify({"error": str(error)[:200]}), 400
    old = existing.storage_name if existing else None
    if existing:
        existing.storage_name, existing.size = storage, size
    else:
        existing = EvaluationArtifact(run=run, name=name, storage_name=storage, size=size)
        db.session.add(existing)
    db.session.commit()
    if old:
        (target.parent / old).unlink(missing_ok=True)
    return jsonify(existing.to_dict()), 201


@evidence_bp.get("/evaluation-artifacts/<artifact_id>")
@require_user()
def evidence(artifact_id):
    artifact = db.session.get(EvaluationArtifact, artifact_id)
    if not artifact or not can_view_result(artifact.run):
        return jsonify({"error": "evidence not found"}), 404
    response = send_from_directory(current_app.config["UPLOAD_FOLDER"], artifact.storage_name,
                                   as_attachment=True, download_name=artifact.name)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
