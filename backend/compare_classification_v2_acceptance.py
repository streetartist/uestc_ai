"""Compare isolated platform predictions with the matching full FP32 audit by sample ID."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

from evaluation_adapters.classification_runner import classification_metrics, digest_file


def collect(acceptance_dir, reference, audit_path):
    acceptance = json.loads((acceptance_dir / 'acceptance.json').read_text())
    evidence = json.loads((acceptance_dir / 'evidence.json').read_text())
    audit = json.loads(audit_path.read_text())
    if acceptance['status'] != 'passed' or not acceptance.get('full_public_validation'):
        raise ValueError('Expected a completed full public validation acceptance')
    if (audit.get('status') != 'passed' or audit.get('test_accessed') is not False
            or audit['checkpoint_sha256'] != acceptance['weights_sha256']
            or audit['deployment_predictions_sha256'] != digest_file(reference)):
        raise ValueError('FP32 audit, checkpoint and reference predictions must match')
    with np.load(reference, allow_pickle=False) as saved:
        ids = [str(value) for value in saved['sample_ids']]
        labels = saved['labels'].tolist()
        probabilities = saved['probabilities']
        if probabilities.shape != (len(ids), 40) or not np.isfinite(probabilities).all():
            raise ValueError('Expected 40 finite scores per reference sample')
        predictions = probabilities.argmax(axis=1).tolist()
    if len(set(ids)) != len(ids) or len(labels) != len(ids):
        raise ValueError('Reference IDs must be unique and aligned with labels')
    refs = dict(zip(ids, zip(labels, predictions)))
    measured = evidence['predictions']
    actual_ids = [row['file'] + '.npy' for row in measured]
    if (len(set(actual_ids)) != len(actual_ids) or set(actual_ids) != set(refs)
            or len(actual_ids) != acceptance['samples']):
        raise ValueError('Platform and FP32 sample IDs differ')
    rows = []
    for row, sid in zip(measured, actual_ids):
        label, predicted = refs[sid]
        rows.append({'sample_id': sid, 'label': label, 'platform_prediction': row['prediction'],
                     'reference_fp32_prediction': predicted, 'agrees': row['prediction'] == predicted,
                     'platform_correct': row['prediction'] == label, 'latency_ms': row['latency_ms']})
    metric, _ = classification_metrics([r['label'] for r in rows], [r['platform_prediction'] for r in rows])
    for key in ('accuracy', 'macro_f1'):
        if abs(metric[key] - acceptance['metrics'][key]) > 1e-6:
            raise ValueError('Reference labels disagree with actual judge metrics')
    correct = sum(r['platform_correct'] for r in rows)
    if correct != acceptance['correct']:
        raise ValueError('Correct counts differ')
    with (acceptance_dir / 'fp32-comparison.csv').open('w', newline='', encoding='utf-8') as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result = {'status': 'compared', 'scope': 'full public validation; not hidden test',
              'samples': len(rows), 'correct': correct, 'accuracy': metric['accuracy'],
              'macro_f1': metric['macro_f1'], 'mean_pipeline_ms': acceptance['metrics']['latency_ms'],
              'project_score_100': acceptance['project_score']['score'],
              'judge_wall_seconds': acceptance['judge_wall_seconds'], 'wall_seconds': acceptance['wall_seconds'],
              'prediction_differences': sum(not r['agrees'] for r in rows),
              'mismatched_sample_ids': [r['sample_id'] for r in rows if not r['agrees']],
              'weights_sha256': acceptance['weights_sha256'],
              'validation_manifest_sha256': acceptance['validation_manifest_sha256'],
              'reference_sha256': digest_file(reference), 'audit_sha256': digest_file(audit_path),
              'platform_evidence_sha256': digest_file(acceptance_dir / 'evidence.json'),
              'timing_scope': acceptance['timing_scope'], 'comparison_csv': 'fp32-comparison.csv'}
    (acceptance_dir / 'full-validation-comparison.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.acceptance, args.reference, args.audit)))
