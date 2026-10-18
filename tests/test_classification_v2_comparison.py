import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from compare_classification_v2_acceptance import collect
from evaluation_adapters.classification_runner import classification_metrics, digest_file


class FullValidationComparisonTests(unittest.TestCase):
    def test_id_alignment_and_input_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / 'reference.npz'
            scores = np.zeros((3, 40))
            scores[0, 1], scores[1, 2], scores[2, 3] = 1, 1, 1
            np.savez(reference, sample_ids=['a.npy', 'b.npy', 'c.npy'], labels=[1, 2, 3], probabilities=scores)
            audit = {'status': 'passed', 'test_accessed': False, 'checkpoint_sha256': 'weight',
                     'deployment_predictions_sha256': digest_file(reference)}
            (root / 'audit.json').write_text(json.dumps(audit))
            evidence = {'predictions': [{'file': 'c', 'prediction': 3, 'latency_ms': 500},
                                       {'file': 'a', 'prediction': 0, 'latency_ms': 100},
                                       {'file': 'b', 'prediction': 2, 'latency_ms': 200}]}
            (root / 'evidence.json').write_text(json.dumps(evidence))
            metrics, _ = classification_metrics([3, 1, 2], [3, 0, 2])
            acceptance = {'status': 'passed', 'full_public_validation': True, 'weights_sha256': 'weight',
                          'samples': 3, 'correct': 2, 'metrics': {**metrics, 'latency_ms': 150},
                          'project_score': {'score': 60}, 'judge_wall_seconds': 4, 'wall_seconds': 5,
                          'timing_scope': 'excludes first sample', 'validation_manifest_sha256': 'manifest'}
            (root / 'acceptance.json').write_text(json.dumps(acceptance))
            result = collect(root, reference, root / 'audit.json')
            self.assertEqual(result['mismatched_sample_ids'], ['a.npy'])
            self.assertEqual(result['correct'], 2)
            self.assertEqual(result['mean_pipeline_ms'], 150)
            self.assertTrue((root / 'fp32-comparison.csv').is_file())
            audit['checkpoint_sha256'] = 'other-weight'
            (root / 'audit.json').write_text(json.dumps(audit))
            with self.assertRaisesRegex(ValueError, 'must match'):
                collect(root, reference, root / 'audit.json')
            audit['checkpoint_sha256'] = 'weight'
            (root / 'audit.json').write_text(json.dumps(audit))
            evidence['predictions'][0]['file'] = 'missing'
            (root / 'evidence.json').write_text(json.dumps(evidence))
            with self.assertRaisesRegex(ValueError, 'IDs differ'):
                collect(root, reference, root / 'audit.json')


if __name__ == '__main__':
    unittest.main()
