"""Generate inference settings from the cache used to train a depth/IR model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


def configure(cache, weights, output, detector=None):
    cache, weights, output = Path(cache), Path(weights).resolve(), Path(output).resolve()
    meta = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
    training_record = weights.parent / 'metadata.json'
    if training_record.is_file():
        expected = json.loads(training_record.read_text(encoding='utf-8')).get('cache_manifest_sha256')
        if expected and hashlib.sha256((cache / 'manifest.json').read_bytes()).hexdigest() != expected:
            raise ValueError('Selected weights were trained with a different cache manifest')
    shape = meta['shape_per_sample']
    if (len(shape) != 4 or shape[0] not in (3, 4) or shape[2] != shape[3]
            or any(type(x) is not int or x <= 0 for x in shape)
            or meta.get('depth_mode') not in ('color', 'gray')
            or type(meta.get('person_crop')) is not bool or not weights.is_file()):
        raise ValueError('Expected a depth/IR cache manifest and an existing trained weight file')
    config = {'num_classes': 40, 'input_version': 2, 'channels': shape[0],
              'depth_mode': meta['depth_mode'], 'person_crop': meta['person_crop'],
              'frames': shape[1], 'size': shape[2],
              'weights_file': os.path.relpath(weights, output.parent).replace('\\', '/'),
              'weights_format': 'safetensors' if weights.suffix == '.safetensors' else 'pytorch'}
    output.parent.mkdir(parents=True, exist_ok=True)
    if config['person_crop']:
        if detector is None:
            raise ValueError('Provide --detector with the same detector used to build the cache')
        detector = Path(detector).resolve()
        if not detector.is_file() or hashlib.sha256(detector.read_bytes()).hexdigest() != meta.get('detector_sha256'):
            raise ValueError('Detector differs from the training cache')
        target = output.parent / detector.name
        if target != detector:
            if target.exists() and target.read_bytes() != detector.read_bytes():
                raise ValueError('A different detector already exists beside the output config')
            shutil.copyfile(detector, target)
        config['detector_file'] = detector.name
    output.write_text(json.dumps(config, indent=2), encoding='utf-8')
    return config


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', required=True)
    p.add_argument('--weights', required=True)
    p.add_argument('--output', default='config.json')
    p.add_argument('--detector')
    a = p.parse_args()
    print(json.dumps(configure(a.cache, a.weights, a.output, a.detector), indent=2))
