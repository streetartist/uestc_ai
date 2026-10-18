#!/usr/bin/env bash
# Explicit one-shot execution inside the existing scheduler GPU reservation.
set -euo pipefail
if [[ $# != 7 ]]; then
  echo 'Usage: script PYTHON BACKEND BASELINE VALIDATION REFERENCE_NPZ AUDIT_JSON NEW_OUTPUT' >&2
  exit 2
fi
python=$1 backend=$2 baseline=$3 validation=$4 reference=$5 audit=$6 output=$7
: "${CUDA_VISIBLE_DEVICES:?Use the existing single-GPU reservation}"
if [[ "$CUDA_VISIBLE_DEVICES" == *,* ]]; then echo 'Expected one reserved GPU' >&2; exit 2; fi
"$python" -c 'import json,sys; d=json.load(open(sys.argv[1])); assert len(d["samples"])==466, "Expected all 466 public validation clips"' "$validation/manifest.json"
"$python" "$backend/classification_v2_acceptance.py" \
  --baseline "$baseline" --validation "$validation" --output "$output" \
  --gpu "$CUDA_VISIBLE_DEVICES" --samples 466 --seconds 7200 --uid "${ACCEPTANCE_UID:-21000}"
"$python" "$backend/compare_classification_v2_acceptance.py" \
  --acceptance "$output" --reference "$reference" --audit "$audit"
