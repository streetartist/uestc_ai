"""Private chunk uploads: no public URLs, no change to evaluation trial limits."""
import hashlib
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ..ai_quotas import lock_problem
from ..extensions import db
from ..model_weights import CHUNK_BYTES, MAX_WEIGHT_BYTES, ModelWeightAsset
from ..models import EvaluationRun, Problem, Registration, Team, new_id
from ..security import current_user, require_user, team_member
from ..uploading import original_filename
from .submissions import deadline_passed, deadline_response

model_weights_bp = Blueprint('model_weights', __name__)


def access(problem_id, team_id):
    if not current_app.config.get('CLASSIFICATION_WEIGHT_UPLOAD_ENABLED'):
        return jsonify({'error': 'model weight uploads are not enabled'}), 503
    if not isinstance(team_id, str) or not team_member(team_id, current_user().id):
        return jsonify({'error': 'team membership required'}), 403
    problem, team = db.session.get(Problem, problem_id), db.session.get(Team, team_id)
    if not problem or not team:
        return jsonify({'error': 'problem or team not found'}), 404
    if (problem.status != 'published' or problem.track.competition.status != 'published'
            or (problem.evaluation_config or {}).get('adapter') != 'classification-v1'):
        return jsonify({'error': 'classification problem is not open'}), 409
    if not problem.runtime or problem.runtime.config.get('execution') != 'autodl-native':
        return jsonify({'error': 'private weight uploads require the native classification runtime'}), 409
    if team.competition_id != problem.track.competition_id or not Registration.query.filter(
            Registration.team_id == team_id, Registration.status == 'confirmed',
            (Registration.track_id == problem.track_id) | Registration.track_id.is_(None)).first():
        return jsonify({'error': 'team is not registered for this track'}), 403
    if deadline_passed(problem.track.competition):
        return deadline_response()


def owned(asset_id):
    asset = db.session.get(ModelWeightAsset, asset_id)
    if not asset:
        return None, (jsonify({'error': 'weight asset not found'}), 404)
    error = access(asset.problem_id, asset.team_id)
    return asset, error


@model_weights_bp.post('/problems/<problem_id>/model-weights')
@require_user()
def create_weight(problem_id):
    import re
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'weight metadata required'}), 400
    error = access(problem_id, data.get('team_id'))
    if error:
        return error
    size, sha, name = data.get('size'), data.get('sha256'), data.get('name')
    if (type(size) is not int or not 1 <= size <= MAX_WEIGHT_BYTES or not isinstance(sha, str)
            or not re.fullmatch(r'[a-f0-9]{64}', sha) or not isinstance(name, str) or not original_filename(name)):
        return jsonify({'error': 'name, byte size (up to 2 GiB) and SHA256 required'}), 400
    lock_problem(problem_id)
    assets = ModelWeightAsset.query.filter_by(team_id=data['team_id'], problem_id=problem_id)
    existing = assets.filter_by(size=size, sha256=sha).first()
    if existing:
        return jsonify(existing.to_dict()), 201
    if assets.count() >= 32 or sum(a.size for a in assets) + size > current_app.config['CLASSIFICATION_WEIGHT_TEAM_BYTES']:
        return jsonify({'error': 'team weight storage quota reached; remove unused assets'}), 409
    identity = new_id()
    asset = ModelWeightAsset(id=identity, team_id=data['team_id'], problem_id=problem_id,
        original_name=original_filename(name), storage_name=f'model-{identity}.bin', size=size, sha256=sha)
    db.session.add(asset)
    db.session.commit()
    return jsonify(asset.to_dict()), 201


@model_weights_bp.get('/model-weights/<asset_id>')
@require_user()
def weight_status(asset_id):
    asset, error = owned(asset_id)
    if error:
        return error
    root = Path(current_app.config['UPLOAD_FOLDER'])
    data = asset.to_dict()
    data['uploaded_chunks'] = [i for i in range(data['chunks']) if (root / f'{asset.storage_name}.part-{i}').is_file()]
    return jsonify(data)


@model_weights_bp.put('/model-weights/<asset_id>/chunks/<int:index>')
@require_user()
def upload_chunk(asset_id, index):
    asset, error = owned(asset_id)
    if error:
        return error
    if asset.state != 'uploading' or index >= asset.to_dict()['chunks']:
        return jsonify({'error': 'weight upload is closed or chunk index is invalid'}), 409
    expected = min(CHUNK_BYTES, asset.size - index * CHUNK_BYTES)
    if request.content_length != expected:
        return jsonify({'error': 'chunk byte size differs from metadata'}), 400
    target = Path(current_app.config['UPLOAD_FOLDER']) / f'{asset.storage_name}.part-{index}'
    # At most 16 MiB per request, below the existing reverse proxy / Flask cap.
    content = request.get_data(cache=False)
    if len(content) != expected:
        return jsonify({'error': 'incomplete chunk'}), 400
    lock_problem(asset.problem_id)
    asset = db.session.get(ModelWeightAsset, asset_id, populate_existing=True)
    if not asset or asset.state != 'uploading':
        return jsonify({'error': 'weight upload is closed'}), 409
    if target.exists():
        if target.read_bytes() != content:
            return jsonify({'error': 'chunk already exists with different content'}), 409
    else:
        temporary = target.with_name(target.name + '.incoming')
        try:
            with temporary.open('wb') as stream:
                stream.write(content)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    db.session.commit()
    return '', 204


@model_weights_bp.post('/model-weights/<asset_id>/complete')
@require_user()
def complete_weight(asset_id):
    asset, error = owned(asset_id)
    if error:
        return error
    lock_problem(asset.problem_id)
    db.session.refresh(asset)
    root = Path(current_app.config['UPLOAD_FOLDER'])
    parts = [root / f'{asset.storage_name}.part-{i}' for i in range(asset.to_dict()['chunks'])]
    if asset.state == 'ready':
        for part in parts:
            part.unlink(missing_ok=True)
        return jsonify(asset.to_dict())
    if any(not p.is_file() or p.stat().st_size != min(CHUNK_BYTES, asset.size-i*CHUNK_BYTES) for i,p in enumerate(parts)):
        return jsonify({'error': 'upload all chunks before completion'}), 409
    temporary = root / (asset.storage_name + '.assembling')
    digest = hashlib.sha256()
    try:
        with temporary.open('wb') as output:
            for part in parts:
                with part.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        output.write(chunk)
        if digest.hexdigest() != asset.sha256:
            return jsonify({'error': 'uploaded weights failed SHA256 verification'}), 400
        temporary.replace(root / asset.storage_name)
        asset.state = 'ready'
        db.session.commit()
        for part in parts:
            part.unlink(missing_ok=True)
        return jsonify(asset.to_dict())
    finally:
        temporary.unlink(missing_ok=True)


@model_weights_bp.delete('/model-weights/<asset_id>')
@require_user()
def delete_weight(asset_id):
    asset, error = owned(asset_id)
    if error:
        return error
    lock_problem(asset.problem_id)
    if EvaluationRun.query.filter(EvaluationRun.submission_snapshot['weights_asset']['id'].as_string() == asset.id).first():
        return jsonify({'error': 'weight asset is retained by an evaluation trial'}), 409
    names = asset.file_names()
    db.session.delete(asset)
    db.session.commit()
    for name in names:
        (Path(current_app.config['UPLOAD_FOLDER']) / name).unlink(missing_ok=True)
    return '', 204
