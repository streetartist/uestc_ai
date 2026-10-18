"""Stage original depth/IR clips using existing split labels and class order."""
import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from evaluation_adapters.depth_ir_dataset import copy_clip_anonymous, clip_digest, validate_manifest, paired_files


def compact_clip(source, target):
    """Apply the published 32-frame 304x228 recipe to original paired frames."""
    import numpy as np
    from PIL import Image
    files = paired_files(source)
    n = len(files['IR'])
    indices = np.linspace(0, n - 1, min(n, 32)).round().astype(int).tolist()
    for modality in ('Depth_Color', 'IR'):
        folder = target / modality
        folder.mkdir(parents=True, mode=0o700)
        folder.chmod(0o700)
        for index in indices:
            source_path = files[modality][index]
            dest = folder / source_path.name
            with Image.open(source_path) as original:
                converted = original.convert('RGB' if modality == 'Depth_Color' else 'L')
                converted = converted.resize((304, 228), Image.Resampling.BILINEAR)
                converted.save(dest, format='PNG', compress_level=6)
            dest.chmod(0o600)
    return indices


def prepare(clips, labels, classes_path, output, compact=False):
    if output.exists():
        raise FileExistsError('Use a new versioned output directory')
    classes = json.loads(classes_path.read_text(encoding='utf-8'))
    with labels.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ['file', 'label']:
            raise ValueError('CSV columns must be file,label; file names a source clip directory')
        rows = list(reader)
    seen = set()
    for row in rows:
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', row['file']) or row['file'] in seen:
            raise ValueError('Clip IDs must be unique plain names')
        seen.add(row['file'])
        if not 0 <= int(row['label']) < 40:
            raise ValueError('Labels must be 0..39')
        clip_digest(clips / row['file'], verify_images=True)
    output.mkdir(parents=True, mode=0o700)
    output.chmod(0o700)
    samples = []
    provenance = []
    for row in rows:
        target = output / row['file']
        if compact:
            indices = compact_clip(clips / row['file'], target)
            provenance.append({'file': row['file'], 'selected_source_indices': indices})
        else:
            copy_clip_anonymous(clips / row['file'], target, file_mode=0o600, directory_mode=0o700)
        samples.append({'file': row['file'], 'label': int(row['label']), 'sha256': clip_digest(target)})
    data = {'version': 2, 'modality': 'depth_ir', 'classes': classes, 'samples': samples}
    validate_manifest(output, data)
    manifest = output / 'manifest.json'
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest.chmod(0o600)
    if compact:
        sidecar = output / 'provenance.json'
        sidecar.write_text(json.dumps({'recipe': {'max_frames': 32, 'resolution': [304, 228],
            'sampling': 'uniform rounded indices shared by both modalities',
            'resize': 'Pillow BILINEAR', 'png_compression': 6}, 'samples': provenance}, indent=2), encoding='utf-8')
        sidecar.chmod(0o600)
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('clips', 'labels', 'classes', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--compact', action='store_true', help='Original frames to published 32-pair 304x228 PNG recipe')
    args = p.parse_args()
    print(json.dumps({'manifest_sha256': prepare(args.clips, args.labels, args.classes, args.output, args.compact)}))
