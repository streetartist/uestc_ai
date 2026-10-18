import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from prepare_classification_inference_config import configure


class InferenceConfigTests(unittest.TestCase):
    def test_config_tracks_cache_and_verifies_crop_detector(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / 'cache'; cache.mkdir()
            weights = root / 'last_ema.pt'; weights.write_bytes(b'trained weights')
            detector = root / 'detector.pt'; detector.write_bytes(b'original detector')
            meta = {'shape_per_sample': [4, 32, 128, 128], 'depth_mode': 'color',
                    'person_crop': True, 'detector_sha256': hashlib.sha256(detector.read_bytes()).hexdigest()}
            (cache / 'manifest.json').write_text(json.dumps(meta))
            out = root / 'package' / 'config.json'
            with self.assertRaisesRegex(ValueError, 'Provide --detector'):
                configure(cache, weights, out)
            config = configure(cache, weights, out, detector)
            self.assertEqual(config['frames'], 32)
            self.assertTrue(config['person_crop'])
            self.assertEqual((out.parent / config['weights_file']).resolve(), weights)
            self.assertEqual((out.parent / config['detector_file']).read_bytes(), detector.read_bytes())
            detector.write_bytes(b'wrong detector')
            with self.assertRaisesRegex(ValueError, 'Detector differs'):
                configure(cache, weights, out, detector)
            meta.update(person_crop=False, shape_per_sample=[3, 16, 112, 112], depth_mode='gray')
            (cache / 'manifest.json').write_text(json.dumps(meta))
            config = configure(cache, weights, out)
            self.assertEqual((config['channels'], config['frames'], config['size']), (3, 16, 112))
            self.assertNotIn('detector_file', config)
            (weights.parent / 'metadata.json').write_text(json.dumps({'cache_manifest_sha256': '0' * 64}))
            with self.assertRaisesRegex(ValueError, 'different cache'):
                configure(cache, weights, out)
            (weights.parent / 'metadata.json').write_text(json.dumps({
                'cache_manifest_sha256': hashlib.sha256((cache / 'manifest.json').read_bytes()).hexdigest()}))
            self.assertEqual(configure(cache, weights, out)['frames'], 16)


if __name__ == '__main__':
    unittest.main()
