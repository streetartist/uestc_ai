"""Immutable team-owned classification weights, transferred outside the code ZIP."""
from pathlib import Path
import json
import re
import zipfile

from flask import current_app

from .extensions import db
from .models import new_id, utcnow

CHUNK_BYTES = 16 * 1024 * 1024
MAX_WEIGHT_BYTES = 2 * 1024**3


class ModelWeightAsset(db.Model):
    __tablename__ = 'model_weight_assets'
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    team_id = db.Column(db.String(36), db.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False, index=True)
    problem_id = db.Column(db.String(36), db.ForeignKey('problems.id', ondelete='CASCADE'), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    storage_name = db.Column(db.String(255), unique=True, nullable=False)
    size = db.Column(db.BigInteger, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    state = db.Column(db.String(16), nullable=False, default='uploading')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    def descriptor(self):
        return {'id': self.id, 'sha256': self.sha256, 'size': self.size}

    def to_dict(self):
        return {**self.descriptor(), 'state': self.state, 'name': self.original_name,
                'chunk_bytes': CHUNK_BYTES, 'chunks': (self.size + CHUNK_BYTES - 1) // CHUNK_BYTES}

    def file_names(self):
        chunks = [f'{self.storage_name}.part-{i}' for i in range((self.size + CHUNK_BYTES - 1) // CHUNK_BYTES)]
        return [self.storage_name, self.storage_name + '.assembling', *chunks, *(name + '.incoming' for name in chunks)]


def package_weight_reference(package):
    # Legacy package validation remains the evaluator's responsibility.
    if not zipfile.is_zipfile(package):
        return None
    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        configs = [x for x in entries if x.filename == 'config.json']
        if not configs:
            return None
        if len(configs) != 1 or configs[0].file_size > 64 * 1024:
            raise ValueError('invalid inference config.json')
        config = json.loads(archive.read(configs[0]))
    if not isinstance(config, dict):
        raise ValueError('config.json must be an object')
    ref = config.get('weights_asset')
    if ref is None:
        return None
    if not isinstance(ref, dict) or set(ref) != {'id', 'sha256', 'size'}:
        raise ValueError('weights_asset requires id, sha256 and size')
    if (not isinstance(ref['id'], str) or not re.fullmatch(r'[a-f0-9-]{36}', ref['id'])
            or not isinstance(ref['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256'])
            or type(ref['size']) is not int or not 1 <= ref['size'] <= MAX_WEIGHT_BYTES
            or 'weights' in config or 'weights_file' in config or 'weights_path' in config):
        raise ValueError('use one valid weight source in config.json')
    return ref


def resolve_package_weight(package, team_id, problem_id):
    ref = package_weight_reference(package)
    if ref is None:
        return None
    asset = db.session.get(ModelWeightAsset, ref['id'])
    if (not asset or asset.team_id != team_id or asset.problem_id != problem_id
            or asset.state != 'ready' or asset.descriptor() != ref
            or not (Path(current_app.config['UPLOAD_FOLDER']) / asset.storage_name).is_file()):
        raise ValueError('weight asset is unavailable for this team and problem')
    return ref
